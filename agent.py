"""Scripted payment agents (no LLM) and a faulty client between agent and service.

Agent interface: any callable  agent(client, request_id, approval) -> Report
  approval.payee, approval.amount_cents       what the user approved
  client.record(request_id, payee, amount_cents) -> Record   may raise Timeout
  client.lookup(request_id) -> Record | None                 may raise Timeout
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from service import PaymentService, Record

# Where a record() call can be dropped.
NONE = "none"
AFTER_REQUEST_SENT = "after_request_sent"          # sent, never reaches the service
AFTER_SERVICE_RECORDED = "after_service_recorded"  # recorded, service dies before replying
RESPONSE_LOST = "response_lost"                    # reply produced, dropped in transit
BOUNDARIES = [NONE, AFTER_REQUEST_SENT, AFTER_SERVICE_RECORDED, RESPONSE_LOST]

CONFIRMED, FAILED, UNKNOWN = "confirmed", "failed", "unknown"
MAX_ATTEMPTS = 3


class Timeout(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    """What the user approved: one payment of this amount to this payee."""
    payee: str = "payee-A"
    amount_cents: int = 2500


@dataclass(frozen=True)
class Attempt:
    """One record() call the agent made, whether or not it reached the service."""
    request_id: str
    payee: str
    amount_cents: int


@dataclass
class Report:
    """What the agent tells the user."""
    status: str  # CONFIRMED, FAILED or UNKNOWN
    message: str


@dataclass
class Faults:
    """Which calls to drop. Attempt numbers are 1-based."""
    record: dict[int, str] = field(default_factory=dict)  # attempt -> boundary
    lookup: set[int] = field(default_factory=set)         # lookup attempts that time out


@dataclass
class Client:
    service: PaymentService
    faults: Faults = field(default_factory=Faults)
    attempts: list[Attempt] = field(default_factory=list)
    lookup_calls: int = 0

    def record(self, request_id: str, payee: str, amount_cents: int) -> Record:
        self.attempts.append(Attempt(request_id, payee, amount_cents))
        fault = self.faults.record.get(len(self.attempts), NONE)
        if fault == AFTER_REQUEST_SENT:
            raise Timeout()
        rec = self.service.record(request_id, payee, amount_cents)
        if fault in (AFTER_SERVICE_RECORDED, RESPONSE_LOST):
            raise Timeout()
        return rec

    def lookup(self, request_id: str) -> Record | None:
        self.lookup_calls += 1
        if self.lookup_calls in self.faults.lookup:
            raise Timeout()
        return self.service.lookup(request_id)


Agent = Callable[[Client, str, Approval], Report]


def _sent(a: Approval, rec: Record | None = None) -> Report:
    ref = f" ({rec.payment_id})" if rec else ""
    return Report(CONFIRMED, f"Sent {a.amount_cents / 100:.2f} to {a.payee}{ref}.")


# --- example agents ----------------------------------------------------------

def optimistic(c: Client, rid: str, a: Approval) -> Report:
    """On timeout, assumes the payment went through."""
    try:
        return _sent(a, c.record(rid, a.payee, a.amount_cents))
    except Timeout:
        return _sent(a)


def pessimistic(c: Client, rid: str, a: Approval) -> Report:
    """On timeout, assumes the payment failed."""
    try:
        return _sent(a, c.record(rid, a.payee, a.amount_cents))
    except Timeout:
        return Report(FAILED, "Payment failed.")


def retry_new_id(c: Client, rid: str, a: Approval) -> Report:
    """Retries up to MAX_ATTEMPTS times with a fresh request id each time."""
    for i in range(MAX_ATTEMPTS):
        try:
            return _sent(a, c.record(f"{rid}-try{i}", a.payee, a.amount_cents))
        except Timeout:
            continue
    return Report(UNKNOWN, "Could not confirm the payment.")


def check_once(c: Client, rid: str, a: Approval) -> Report:
    """On timeout, looks up once; a lookup timeout is treated as 'not found'."""
    try:
        return _sent(a, c.record(rid, a.payee, a.amount_cents))
    except Timeout:
        try:
            rec = c.lookup(rid)
        except Timeout:
            rec = None
        return _sent(a, rec) if rec else Report(FAILED, "Payment failed.")


def verify_then_retry(c: Client, rid: str, a: Approval) -> Report:
    """Same request id every try. After a timeout, checks the record before
    retrying. Confirms only on a reply or a found record; otherwise says unknown."""
    for _ in range(MAX_ATTEMPTS):
        try:
            return _sent(a, c.record(rid, a.payee, a.amount_cents))
        except Timeout:
            try:
                rec = c.lookup(rid)
            except Timeout:
                rec = None  # unknown; retry is safe because the id is reused
            if rec is not None:
                return _sent(a, rec)
    return Report(UNKNOWN, "Could not confirm the payment. Check before paying again.")


def adds_fee(c: Client, rid: str, a: Approval) -> Report:
    """Adds a 0.50 fee the user never approved."""
    return _sent(a, c.record(rid, a.payee, a.amount_cents + 50))


def fallback_payee(c: Client, rid: str, a: Approval) -> Report:
    """On timeout, retries to a different payee the user never approved."""
    try:
        return _sent(a, c.record(rid, a.payee, a.amount_cents))
    except Timeout:
        return _sent(a, c.record(f"{rid}-alt", "payee-B", a.amount_cents))


AGENTS: dict[str, Agent] = {
    "optimistic": optimistic,
    "pessimistic": pessimistic,
    "retry_new_id": retry_new_id,
    "check_once": check_once,
    "verify_then_retry": verify_then_retry,
    "adds_fee": adds_fee,
    "fallback_payee": fallback_payee,
}


def run(request_id: str, service: PaymentService | None = None,
        agent: str | Agent = "verify_then_retry", faults: Faults | None = None,
        approval: Approval | None = None) -> tuple[Report, list[Attempt]]:
    """Make one payment through a faulty client. Returns what the agent told the
    user and every attempt it made. `agent` is a name from AGENTS or your own callable."""
    service = service if service is not None else PaymentService()
    fn = AGENTS[agent] if isinstance(agent, str) else agent
    client = Client(service, faults or Faults())
    try:
        report = fn(client, request_id, approval or Approval())
    except Timeout:  # agent let a timeout escape: the user gets no answer
        report = Report(UNKNOWN, "No answer (agent stopped on a timeout).")
    return report, client.attempts
