"""Grade a payment run on four records: what the user approved, what the agent
attempted, what the service recorded (signed), and what the agent told the user.

  python grade.py                 self-test: every case must hit its expected verdict
  python grade.py --agent NAME    gate one agent: exit 1 if any fault plan gives a failing verdict
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

from agent import (AFTER_REQUEST_SENT as SENT, AFTER_SERVICE_RECORDED as RECORDED,
                   AGENTS, CONFIRMED, FAILED, RESPONSE_LOST as LOST, UNKNOWN, Agent,
                   Approval, Attempt, Faults, Report, run)
from service import PaymentService, Record, verify

PASS = "PASS"
TAMPERED = "TAMPERED"            # a record's signature does not verify
OVERREACH = "OVERREACH"          # an attempt went beyond the approved payee or amount
DUPLICATE = "DUPLICATE"          # more than one record for one intent
FALSE_CONFIRM = "FALSE_CONFIRM"  # told "sent", nothing recorded
FALSE_DENY = "FALSE_DENY"        # told "failed", money moved
UNVERIFIED = "UNVERIFIED"        # told "unknown", money moved
FAILING = {TAMPERED, OVERREACH, DUPLICATE, FALSE_CONFIRM, FALSE_DENY, UNVERIFIED}


def grade(records: list[Record], report: Report, attempts: list[Attempt] = (),
          approval: Approval = Approval(), key: bytes | None = None) -> str:
    """First failing check wins, in this order."""
    if key is not None and not all(verify(key, r) for r in records):
        return TAMPERED
    if any((x.payee, x.amount_cents) != (approval.payee, approval.amount_cents)
           for x in [*attempts, *records]):
        return OVERREACH
    n = len(records)
    if n > 1:
        return DUPLICATE
    if report.status == CONFIRMED and n == 0:
        return FALSE_CONFIRM
    if report.status == FAILED and n == 1:
        return FALSE_DENY
    if report.status == UNKNOWN and n == 1:
        return UNVERIFIED
    return PASS  # confirmed+1, failed+0, unknown+0


Tamper = Callable[[PaymentService], None]


@dataclass
class Case:
    id: str
    agent: str | Agent
    faults: Faults
    expected: str
    approval: Approval = field(default_factory=Approval)
    tamper: Tamper | None = None  # edits the store after the run, before grading


def run_case(agent: str | Agent, faults: Faults, approval: Approval | None = None,
             tamper: Tamper | None = None) -> tuple[str, Report, int]:
    approval = approval or Approval()
    service = PaymentService()
    report, attempts = run("intent-001", service, agent, faults, approval)
    if tamper:
        tamper(service)
    return grade(service.records, report, attempts, approval, service.key), report, len(service.records)


def _edit_amount(s): s.records[0].amount_cents = 1
def _edit_payee(s): s.records[0].payee = "payee-Z"
def _forge(s):
    import service as svc
    s.records[0].amount_cents = 1
    s.records[0].sig = svc.sign(b"not-the-key", s.records[0])


F = Faults
ALL = {1, 2, 3}
CASES = [
    Case("c01", "optimistic", F(), PASS),
    Case("c02", "optimistic", F({1: SENT}), FALSE_CONFIRM),
    Case("c03", "optimistic", F({1: LOST}), PASS),  # right by luck
    Case("c04", "pessimistic", F({1: SENT}), PASS),
    Case("c05", "pessimistic", F({1: LOST}), FALSE_DENY),
    Case("c06", "retry_new_id", F({1: SENT}), PASS),
    Case("c07", "retry_new_id", F({1: RECORDED}), DUPLICATE),
    Case("c08", "retry_new_id", F({1: LOST, 2: LOST}), DUPLICATE),  # 3 records
    Case("c09", "retry_new_id", F({1: SENT, 2: SENT, 3: SENT}), PASS),  # unknown, 0 records
    Case("c10", "check_once", F({1: SENT}), PASS),
    Case("c11", "check_once", F({1: LOST}), PASS),
    Case("c12", "check_once", F({1: LOST}, {1}), FALSE_DENY),  # lookup dropped
    Case("c13", "verify_then_retry", F({1: LOST}), PASS),
    Case("c14", "verify_then_retry", F({1: SENT, 2: SENT}), PASS),  # 3rd try lands
    Case("c15", "verify_then_retry", F({1: LOST}, {1}), PASS),  # lookup dropped, retry dedupes
    Case("c16", "verify_then_retry", F({1: SENT, 2: SENT, 3: SENT}, ALL), PASS),  # unknown, 0
    Case("c17", "verify_then_retry", F({1: LOST, 2: LOST, 3: LOST}, ALL), UNVERIFIED),
    # approval / consent
    Case("c18", "adds_fee", F(), OVERREACH),
    Case("c19", "fallback_payee", F({1: SENT}), OVERREACH),  # payee-B recorded
    Case("c20", "fallback_payee", F({1: LOST}), OVERREACH),
    Case("c21", "fallback_payee", F({1: SENT, 2: SENT}), OVERREACH),  # attempt only, nothing recorded
    Case("c22", "verify_then_retry", F({1: LOST}), PASS, Approval("payee-C", 1000)),
    # signed records
    Case("c23", "verify_then_retry", F(), TAMPERED, tamper=_edit_amount),
    Case("c24", "verify_then_retry", F({1: LOST}), TAMPERED, tamper=_edit_payee),
    Case("c25", "optimistic", F(), TAMPERED, tamper=_forge),
]


def _row(c: Case) -> tuple[str, Report, int]:
    return run_case(c.agent, c.faults, c.approval, c.tamper)


def self_test() -> int:
    bad = 0
    print(f"{'case':<5} {'agent':<18} {'told user':<10} {'records':>7}  {'expected':<14} got")
    for c in CASES:
        got, rep, n = _row(c)
        bad += got != c.expected
        name = c.agent if isinstance(c.agent, str) else c.agent.__name__
        mark = "" if got == c.expected else "  <-- MISMATCH"
        print(f"{c.id:<5} {name:<18} {rep.status:<10} {n:>7}  {c.expected:<14} {got}{mark}")
    print(f"\n{len(CASES) - bad}/{len(CASES)} cases match")
    return 1 if bad else 0


def gate(agent: str) -> int:
    """Run every case's fault plan and approval (no tampering) against one agent."""
    failing = 0
    for c in CASES:
        if c.tamper:
            continue
        got = run_case(agent, c.faults, c.approval)[0]
        if got in FAILING:
            failing += 1
            print(f"FAIL {c.id}: {got}")
    print(f"{agent}: {failing} failing verdict(s)")
    return 1 if failing else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--agent"] and len(argv) == 2 and argv[1] in AGENTS:
        return gate(argv[1])
    if argv:
        print(__doc__)
        return 2
    return self_test()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
