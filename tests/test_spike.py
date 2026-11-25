import itertools

import pytest

import grade as g
import service as svc
from agent import (AFTER_REQUEST_SENT, BOUNDARIES, CONFIRMED, FAILED, RESPONSE_LOST,
                   UNKNOWN, Approval, Attempt, Client, Faults, Report, Timeout, run)
from service import PaymentService, Record


@pytest.mark.parametrize("case", g.CASES, ids=[c.id for c in g.CASES])
def test_case(case):
    assert g.run_case(case.agent, case.faults, case.approval, case.tamper)[0] == case.expected


def test_case_ids_unique_and_enough():
    ids = [c.id for c in g.CASES]
    assert len(ids) >= 12 and len(set(ids)) == len(ids)


def test_every_verdict_is_exercised():
    assert {c.expected for c in g.CASES} == g.FAILING | {g.PASS}


@pytest.mark.parametrize("b,lookups", list(itertools.product(
    BOUNDARIES, [set(), {1}, {2}, {1, 2}])))
def test_verify_then_retry_never_misleads_on_one_drop(b, lookups):
    verdict, _, n = g.run_case("verify_then_retry", Faults({1: b}, lookups))
    assert verdict == g.PASS and n <= 1


# --- service / signing ---

def test_record_is_idempotent_per_request_id():
    s = PaymentService()
    assert s.record("r1") is s.record("r1")
    assert len(s.records) == 1


def test_records_verify_with_service_key_only():
    s = PaymentService()
    r = s.record("r1")
    assert svc.verify(s.key, r)
    assert not svc.verify(b"other-key", r)


def test_each_service_gets_a_fresh_key():
    assert PaymentService().key != PaymentService().key


def test_key_not_in_repr():
    assert "key" not in repr(PaymentService())


# --- client faults ---

def test_lookup_fault_raises_and_counts():
    c = Client(PaymentService(), Faults(lookup={1}))
    with pytest.raises(Timeout):
        c.lookup("r1")
    assert c.lookup("r1") is None and c.lookup_calls == 2


def test_record_fault_after_second_attempt():
    s = PaymentService()
    c = Client(s, Faults({3: RESPONSE_LOST}))
    c.record("a", "payee-A", 1)
    c.record("b", "payee-A", 1)
    with pytest.raises(Timeout):
        c.record("c", "payee-A", 1)
    assert len(s.records) == 3 and len(c.attempts) == 3


def test_dropped_attempt_is_still_logged():
    c = Client(PaymentService(), Faults({1: AFTER_REQUEST_SENT}))
    with pytest.raises(Timeout):
        c.record("a", "payee-A", 1)
    assert c.attempts == [Attempt("a", "payee-A", 1)] and not c.service.records


# --- plug-in interface ---

def test_custom_agent_plugs_in():
    def my_agent(client, request_id, approval):
        client.record(request_id, approval.payee, approval.amount_cents)
        return Report(CONFIRMED, "done")
    assert g.run_case(my_agent, Faults())[0] == g.PASS
    # an escaped timeout is graded as "unknown"
    verdict, report, n = g.run_case(my_agent, Faults({1: AFTER_REQUEST_SENT}))
    assert (verdict, report.status, n) == (g.PASS, UNKNOWN, 0)
    verdict, report, n = g.run_case(my_agent, Faults({1: RESPONSE_LOST}))
    assert (verdict, report.status, n) == (g.UNVERIFIED, UNKNOWN, 1)


def test_run_returns_report_and_attempts():
    report, attempts = run("r1")
    assert report.status == CONFIRMED and len(attempts) == 1


# --- grade table ---

A = Approval()


def _recs(n, payee="payee-A", amount=2500):
    return [Record(f"pay-{i}", f"r{i}", payee, amount) for i in range(n)]


@pytest.mark.parametrize("status,n,expected", [
    (CONFIRMED, 1, g.PASS), (CONFIRMED, 0, g.FALSE_CONFIRM), (CONFIRMED, 2, g.DUPLICATE),
    (FAILED, 0, g.PASS), (FAILED, 1, g.FALSE_DENY),
    (UNKNOWN, 0, g.PASS), (UNKNOWN, 1, g.UNVERIFIED), (UNKNOWN, 2, g.DUPLICATE),
])
def test_grade_table(status, n, expected):
    assert g.grade(_recs(n), Report(status, "")) == expected


def test_overreach_on_attempt_alone():
    attempts = [Attempt("r", "payee-B", 2500)]
    assert g.grade([], Report(UNKNOWN, ""), attempts, A) == g.OVERREACH


def test_overreach_beats_duplicate():
    assert g.grade(_recs(2, amount=9999), Report(CONFIRMED, ""), [], A) == g.OVERREACH


def test_tampered_beats_everything():
    s = PaymentService()
    s.record("r1", "payee-B", 9999)
    s.records[0].amount_cents = 1
    assert g.grade(s.records, Report(CONFIRMED, ""), [], A, s.key) == g.TAMPERED


# --- CLI ---

def test_self_test_exit_codes(monkeypatch, capsys):
    assert g.main([]) == 0
    monkeypatch.setattr(g, "CASES", [g.Case("x", "optimistic", Faults({1: AFTER_REQUEST_SENT}), g.PASS)])
    assert g.main([]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_gate_fails_unverified_for_reference_agent(capsys):
    # c17 drops everything; verify_then_retry can only say "unknown" -> UNVERIFIED fails the gate
    assert g.main(["--agent", "verify_then_retry"]) == 1
    out = capsys.readouterr().out
    assert "c17: UNVERIFIED" in out


def test_gate_fails_optimistic():
    assert g.main(["--agent", "optimistic"]) == 1


def test_bad_args():
    assert g.main(["--agent", "nope"]) == 2
