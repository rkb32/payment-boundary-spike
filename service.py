"""Fake payment service with an append-only, HMAC-signed record store.

The signing key is generated in memory when the service starts and is never
written anywhere. Synthetic data only.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field


@dataclass
class Record:
    payment_id: str
    request_id: str
    payee: str
    amount_cents: int
    sig: str = ""

    def payload(self) -> bytes:
        return f"{self.payment_id}|{self.request_id}|{self.payee}|{self.amount_cents}".encode()


def sign(key: bytes, rec: Record) -> str:
    return hmac.new(key, rec.payload(), hashlib.sha256).hexdigest()


def verify(key: bytes, rec: Record) -> bool:
    """Offline check: needs only the record and the key, not the service."""
    return hmac.compare_digest(rec.sig, sign(key, rec))


@dataclass
class PaymentService:
    records: list[Record] = field(default_factory=list)
    key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)

    def record(self, request_id: str, payee: str = "payee-A", amount_cents: int = 2500) -> Record:
        """Record a payment. A repeat request_id returns the existing record and adds nothing."""
        existing = self.lookup(request_id)
        if existing is not None:
            return existing
        rec = Record(f"pay-{len(self.records) + 1}", request_id, payee, amount_cents)
        rec.sig = sign(self.key, rec)
        self.records.append(rec)
        return rec

    def lookup(self, request_id: str) -> Record | None:
        return next((r for r in self.records if r.request_id == request_id), None)
