"""Synthetic, offline-only checks for the WATCH consumer's closed action gates."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from quant_backtest.watch_offline_consumption_gate import consume_watch_offline_sources
from quant_backtest.watch_source_authority import (
    OwnerInputProof,
    ProviderLicenseProof,
    RawHttpResponseProof,
    StrategyMembershipProof,
    TradingSessionProof,
)


QUOTE_AT = datetime(2026, 1, 5, 1, 30, tzinfo=timezone.utc)
EVALUATION_AT = QUOTE_AT + timedelta(minutes=2)
START = QUOTE_AT - timedelta(days=1)
END = QUOTE_AT + timedelta(days=1)


def _raw_bytes() -> bytes:
    """Build one fully synthetic quoted native line with indexed fields."""
    fields = [""] * 32
    fields[3] = "12.34"
    fields[30] = "2026-01-05"
    fields[31] = "09:30:00"
    return ('var hq_str_sh600000="' + ",".join(fields) + '";').encode("utf-8")


def _event(pool: str) -> dict[str, Any]:
    """Build normalized synthetic event consumed by the authority verifier."""
    event: dict[str, Any] = {
        "pool": pool,
        "market": "A",
        "code": "600000",
        "quote_as_of": "2026-01-05T01:30:00Z",
        "observed": "12.34",
        "source_ref": "synthetic-response-1",
        "membership_event": {
            "event_id": "synthetic-a" if pool == "A" else "synthetic-b",
            "as_of": "2026-01-05T01:29:00Z",
        },
    }
    if pool == "A":
        event.update(strategy="synthetic-strategy", strategy_version="v1", source_batch="batch-1")
    else:
        event.update(owner="synthetic-owner", watch_rule="synthetic-rule", watch_rule_version="v1")
    return event


class SyntheticAuthority:
    """In-memory synthetic resolver, never a production trust root."""

    def __init__(self) -> None:
        content = _raw_bytes()
        digest = hashlib.sha256(content).hexdigest()
        self.a_proof = StrategyMembershipProof(
            "A", "600000", "synthetic-strategy", "v1", "batch-1",
            "synthetic-a", True, False, START, END,
        )
        self.b_proof = OwnerInputProof(
            "A", "600000", "synthetic-owner", "synthetic-rule", "v1",
            "synthetic-b", 3, "synthetic-input", "synthetic-principal", False,
            START, END,
        )
        self.roster: list[tuple[str, str, str]] = [
            ("synthetic-strategy", "v1", "batch-1")
        ]
        self.floor = 3
        self.raw = RawHttpResponseProof(
            "synthetic-response-1", "synthetic-provider", content, digest,
            len(content), "utf-8", "sh600000", 3, received_at=QUOTE_AT + timedelta(minutes=1),
        )
        self.anchor: tuple[str, int] = digest, len(content)
        self.session = TradingSessionProof(
            "A", date(2026, 1, 5), QUOTE_AT - timedelta(hours=1),
            QUOTE_AT + timedelta(hours=7), "synthetic-calendar", "v1",
        )
        self.license = ProviderLicenseProof(
            "synthetic-provider", "A", "synthetic-license", START, END, False
        )

    def strategy_roster(self, market: str, code: str, at: datetime) -> list[tuple[str, str, str]]:
        return self.roster

    def strategy_membership(self, event_id: str) -> StrategyMembershipProof | None:
        return self.a_proof if event_id == "synthetic-a" else None

    def owner_input(self, event_id: str) -> OwnerInputProof | None:
        return self.b_proof if event_id == "synthetic-b" else None

    def authorized_input_floor(self, principal: str, persisted_input_id: str) -> int | None:
        if principal != "synthetic-principal" or persisted_input_id != "synthetic-input":
            return None
        return self.floor

    def raw_response(self, source_ref: str) -> RawHttpResponseProof | None:
        return self.raw if source_ref == "synthetic-response-1" else None

    def raw_anchor(self, source_ref: str) -> tuple[str, int] | None:
        return self.anchor if source_ref == "synthetic-response-1" else None

    def trading_session(self, market: str, session_date: date) -> TradingSessionProof | None:
        return self.session

    def provider_license(self, provider_id: str, market: str) -> ProviderLicenseProof | None:
        return self.license


class OfflineConsumptionGateTests(unittest.TestCase):
    """Exercise source-verifier decisions without accessing external stores."""

    def evaluate(self, event: dict[str, Any], authority: Any, at: Any = EVALUATION_AT) -> Any:
        # The alert schema belongs to a separate contract. Isolate it here so
        # these tests only exercise real downstream source checks and the gate.
        with patch(
            "quant_backtest.watch_source_authority.validate_alert",
            side_effect=lambda claimed, **kwargs: SimpleNamespace(
                event=claimed, code="OK"
            ),
        ):
            return consume_watch_offline_sources(
                [event], evaluation_as_of=at, authority=authority
            )

    def assert_closed(self, decision: Any, status: str, code: str) -> None:
        self.assertEqual((decision.status, decision.code), (status, code))
        self.assertIs(decision.production_permitted, False)
        self.assertIs(decision.notify_allowed, False)
        self.assertIs(decision.sim_handoff_allowed, False)
        self.assertNotEqual(decision.status, "VERIFIED")

    def test_missing_authority_never_falls_back_to_embedded_proof(self) -> None:
        event = _event("A")
        event["proof"] = SyntheticAuthority()
        self.assert_closed(self.evaluate(event, None), "SOURCE_UNVERIFIED", "EVALUATION_TIME")

    def test_missing_and_raising_resolver_fail_closed(self) -> None:
        self.assert_closed(self.evaluate(_event("A"), object()), "SOURCE_UNVERIFIED", "AUTHORITY_UNAVAILABLE")
        authority = SyntheticAuthority()
        authority.strategy_membership = lambda event_id: (_ for _ in ()).throw(RuntimeError("store down"))
        self.assert_closed(self.evaluate(_event("A"), authority), "SOURCE_UNVERIFIED", "AUTHORITY_UNAVAILABLE")

    def test_invalid_evaluation_time_fails_closed(self) -> None:
        self.assert_closed(self.evaluate(_event("A"), SyntheticAuthority(), datetime(2026, 1, 5, 1, 32)), "SOURCE_UNVERIFIED", "EVALUATION_TIME")

    def test_a_roster_requires_complete_set(self) -> None:
        authority = SyntheticAuthority()
        authority.roster.append(("unsubmitted-strategy", "v2", "batch-1"))
        self.assert_closed(self.evaluate(_event("A"), authority), "SOURCE_UNVERIFIED", "A_ROSTER_MISMATCH")

    def test_b_principal_version_and_revocation(self) -> None:
        authority = SyntheticAuthority()
        self.assert_closed(self.evaluate(_event("B"), authority), "QUALIFIED_OFFLINE", "OK")
        authority.b_proof = replace(authority.b_proof, authorized_principal="wrong-principal")
        self.assert_closed(self.evaluate(_event("B"), authority), "SOURCE_UNVERIFIED", "B_INPUT_ROLLBACK")
        authority.b_proof = replace(authority.b_proof, authorized_principal="synthetic-principal", input_version=2)
        self.assert_closed(self.evaluate(_event("B"), authority), "SOURCE_UNVERIFIED", "B_INPUT_ROLLBACK")
        authority.b_proof = replace(authority.b_proof, input_version=3, revoked=True)
        self.assert_closed(self.evaluate(_event("B"), authority), "SOURCE_UNVERIFIED", "B_AUTH_INVALID")

    def test_original_bytes_and_separate_anchor_must_match(self) -> None:
        authority = SyntheticAuthority()
        authority.raw = replace(authority.raw, content_sha256="0" * 64)
        self.assert_closed(self.evaluate(_event("A"), authority), "SOURCE_UNVERIFIED", "RAW_INTEGRITY")
        authority = SyntheticAuthority()
        authority.anchor = ("0" * 64, authority.raw.content_length)
        self.assert_closed(self.evaluate(_event("A"), authority), "SOURCE_UNVERIFIED", "RAW_ANCHOR")

    def test_synthetic_a_success_remains_offline_only(self) -> None:
        self.assert_closed(self.evaluate(_event("A"), SyntheticAuthority()), "QUALIFIED_OFFLINE", "OK")


if __name__ == "__main__":
    unittest.main()
