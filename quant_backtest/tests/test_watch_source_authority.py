"""Offline-only adversarial tests for independently anchored watch sources."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from quant_backtest.watch_alert_contract import make_alert_ids
from quant_backtest.watch_source_authority import (
    OwnerInputProof,
    ProviderLicenseProof,
    RawHttpResponseProof,
    StrategyMembershipProof,
    TradingSessionProof,
    verify_watch_sources,
)


DAY = date(2026, 10, 19)
QUOTE = datetime(2026, 10, 19, 1, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)
BEFORE = datetime(2026, 10, 18, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 20, tzinfo=timezone.utc)


def raw_line(*, price: str = "12.5", when: str = "2026-10-19,09:30:00",
             extra: str = "") -> bytes:
    """Build an original-style HTTP body without canonicalizing its bytes."""
    fields = ["x", price] + ["0"] * 28 + when.split(",")
    return (f'var hq_str_sh600000="{",".join(fields)}";\n{extra}').encode("utf-8")


def event(*, pool: str = "A", strategy: str = "first", event_id: str = "member-a",
          source_ref: str = "http-one") -> dict[str, Any]:
    """Build a structurally valid WATCH1 event from each claimed identity."""
    identity: dict[str, Any] = ({"strategy": strategy, "strategy_version": "v1",
                                 "source_batch": "approved-batch", "owner": None,
                                 "watch_rule": None, "watch_rule_version": None}
                                if pool == "A" else
                                {"strategy": None, "strategy_version": None,
                                 "source_batch": None, "owner": "tenant-a",
                                 "watch_rule": "rule-a", "watch_rule_version": "v1"})
    membership = {"event_id": event_id, "kind": "ENTER", "as_of": BEFORE.isoformat(),
                  "market": "A", "code": "600000", "pool": pool,
                  "qualification": {key: value for key, value in identity.items()
                                    if value is not None}}
    snapshot = {"source_ref": source_ref, "source_kind": "DIRECT_QUOTE", "market": "A",
                "code": "600000", "instrument_type": "STOCK", "observed": "12.5",
                "quote_as_of": QUOTE.isoformat(), "visible_at": NOW.isoformat(),
                "quality": "VERIFIED"}
    result = {"schema_version": "WATCH1_V1", "alert_id": "", "trace_id": "",
              "dedupe_key": "", "pool": pool, "run_mode": "SHADOW", "market": "A",
              "code": "600000", "instrument_type": "STOCK", **identity,
              "membership_event": membership, "rule": "price", "comparator": "GT",
              "threshold": "10", "observed": "12.5", "quote_as_of": QUOTE.isoformat(),
              "visible_at": NOW.isoformat(), "source_hash": hashlib.sha256(
                  json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest(),
              "source_ref": source_ref, "source_snapshot": snapshot,
              "source_kind": "DIRECT_QUOTE", "quality": "VERIFIED",
              "facts": ["market-price"], "hypotheses": [],
              "supporting_refs": ["market-price"], "contradicting_refs": [],
              "evidence_assessment": "SUPPORTED",
              "notification": {"status": "NOT_ATTEMPTED", "receipt_id": None, "at": None},
              "sim_handoff": {"status": "NOT_ATTEMPTED", "receipt_id": None, "at": None}}
    result.update(make_alert_ids(result))
    return result


class Authority:
    """In-memory independently supplied trust root for synthetic unit tests."""

    def __init__(self) -> None:
        content = raw_line()
        self.raw = RawHttpResponseProof("http-one", "licensed-provider", content,
                                        hashlib.sha256(content).hexdigest(), len(content),
                                        "utf-8", "sh600000", 1, received_at=NOW)
        self.anchor = (self.raw.content_sha256, self.raw.content_length)
        self.members = {
            "member-a": StrategyMembershipProof("A", "600000", "first", "v1",
                                                   "approved-batch", "member-a", True,
                                                   False, BEFORE, AFTER),
            "member-two": StrategyMembershipProof("A", "600000", "second", "v1",
                                                   "approved-batch", "member-two", True,
                                                   False, BEFORE, AFTER),
        }
        self.roster = (("first", "v1", "approved-batch"),)
        self.owner = OwnerInputProof("A", "600000", "tenant-a", "rule-a", "v1",
                                     "owner-event", 3, "persisted-watch-1", "server-user-1",
                                     False, BEFORE, AFTER)
        self.floor = 3
        self.session = TradingSessionProof("A", DAY,
                                           datetime(2026, 10, 19, 1, tzinfo=timezone.utc),
                                           datetime(2026, 10, 19, 7, tzinfo=timezone.utc),
                                           "exchange-calendar", "2026.10")
        self.license = ProviderLicenseProof("licensed-provider", "A", "contract-1",
                                            BEFORE, AFTER, False)

    def strategy_roster(self, market: str, code: str, at: datetime):
        return self.roster if (market, code) == ("A", "600000") else None

    def strategy_membership(self, event_id: str):
        return self.members.get(event_id)

    def owner_input(self, event_id: str):
        return self.owner if event_id == "owner-event" else None

    def authorized_input_floor(self, principal: str, persisted_input_id: str):
        return self.floor if (principal, persisted_input_id) == (
            "server-user-1", "persisted-watch-1") else None

    def raw_response(self, source_ref: str):
        return self.raw if source_ref == "http-one" else None

    def raw_anchor(self, source_ref: str):
        return self.anchor if source_ref == "http-one" else None

    def trading_session(self, market: str, session_date: date):
        return self.session if (market, session_date) == ("A", DAY) else None

    def provider_license(self, provider_id: str, market: str):
        return self.license if (provider_id, market) == (
            "licensed-provider", "A") else None


class WatchSourceAuthorityTests(unittest.TestCase):
    """Source qualification rejects forged and missing external evidence."""

    def setUp(self) -> None:
        self.authority = Authority()

    def check(self, items: list[dict[str, Any]], expected: str) -> None:
        verdict = verify_watch_sources(items, evaluation_as_of=NOW,
                                       authority=self.authority)
        self.assertEqual(verdict.status, expected, verdict.code)
        self.assertFalse(verdict.production_permitted)

    def test_synthetic_success_remains_offline_only(self) -> None:
        self.check([event()], "QUALIFIED_OFFLINE")
        self.check([event(pool="B", event_id="owner-event")], "QUALIFIED_OFFLINE")

    def test_forged_owner_and_revocation_fail_closed(self) -> None:
        forged = event(pool="B", event_id="owner-event")
        forged["owner"] = "attacker"
        forged["membership_event"]["qualification"]["owner"] = "attacker"
        forged.update(make_alert_ids(forged))
        self.check([forged], "SOURCE_UNVERIFIED")
        self.authority.owner = replace(self.authority.owner, revoked=True)
        self.check([event(pool="B", event_id="owner-event")], "SOURCE_UNVERIFIED")

    def test_one_strategy_cannot_impersonate_two(self) -> None:
        self.authority.roster = (("first", "v1", "approved-batch"),
                                 ("second", "v1", "approved-batch"))
        self.check([event()], "SOURCE_UNVERIFIED")
        self.check([event(), event(strategy="second", event_id="member-two")],
                   "QUALIFIED_OFFLINE")
        self.check([event(), event()], "SOURCE_UNVERIFIED")

    def test_raw_bytes_cannot_be_reanchored_by_source_claim(self) -> None:
        altered = raw_line(price="12.5") + b" "
        self.authority.raw = replace(self.authority.raw, content=altered,
                                      content_sha256=hashlib.sha256(altered).hexdigest(),
                                      content_length=len(altered))
        self.check([event()], "SOURCE_UNVERIFIED")

    def test_duplicate_hq_key_rejected(self) -> None:
        duplicate = raw_line(extra=raw_line().decode())
        self.authority.raw = replace(self.authority.raw, content=duplicate,
                                      content_sha256=hashlib.sha256(duplicate).hexdigest(),
                                      content_length=len(duplicate))
        self.authority.anchor = (self.authority.raw.content_sha256,
                                 self.authority.raw.content_length)
        self.check([event()], "SOURCE_UNVERIFIED")

    def test_missing_or_future_native_time_rejected(self) -> None:
        for instant in ("2026-10-19,", "2026-10-19,10:30:00"):
            with self.subTest(instant=instant):
                data = raw_line(when=instant)
                self.authority.raw = replace(self.authority.raw, content=data,
                                              content_sha256=hashlib.sha256(data).hexdigest(),
                                              content_length=len(data))
                self.authority.anchor = (self.authority.raw.content_sha256,
                                         self.authority.raw.content_length)
                self.check([event()], "SOURCE_UNVERIFIED")

    def test_missing_calendar_and_license_rejected(self) -> None:
        saved_calendar = self.authority.session
        self.authority.session = None
        self.check([event()], "SOURCE_UNVERIFIED")
        self.authority.session = saved_calendar
        self.authority.license = None
        self.check([event()], "SOURCE_UNVERIFIED")

    def test_revoked_membership_and_rollback_rejected(self) -> None:
        self.authority.members["member-a"] = replace(self.authority.members["member-a"],
                                                      revoked=True)
        self.check([event()], "SOURCE_UNVERIFIED")
        self.authority.floor = 4
        self.check([event(pool="B", event_id="owner-event")], "SOURCE_UNVERIFIED")

    def test_resolver_outages_fail_closed_without_hiding_parser_errors(self) -> None:
        def unavailable(*args: Any) -> Any:
            raise RuntimeError("source store unavailable")

        cases = (
            ("strategy_membership", "A", "member-a"),
            ("owner_input", "B", "owner-event"),
            ("authorized_input_floor", "B", "owner-event"),
            ("raw_response", "A", "member-a"),
            ("raw_anchor", "A", "member-a"),
            ("trading_session", "A", "member-a"),
            ("provider_license", "A", "member-a"),
            ("strategy_roster", "A", "member-a"),
        )
        for method, pool, event_id in cases:
            with self.subTest(resolver=method):
                authority = Authority()
                setattr(authority, method, unavailable)
                verdict = verify_watch_sources([event(pool=pool, event_id=event_id)],
                                               evaluation_as_of=NOW, authority=authority)
                self.assertEqual(verdict.status, "SOURCE_UNVERIFIED")
                self.assertEqual(verdict.code, "AUTHORITY_UNAVAILABLE")
                self.assertFalse(verdict.production_permitted)

        # Bad original evidence is still reported as evidence, not a store outage.
        content = raw_line(extra=raw_line().decode())
        self.authority.raw = replace(self.authority.raw, content=content,
                                      content_sha256=hashlib.sha256(content).hexdigest(),
                                      content_length=len(content))
        self.authority.anchor = (self.authority.raw.content_sha256,
                                 self.authority.raw.content_length)
        verdict = verify_watch_sources([event()], evaluation_as_of=NOW,
                                       authority=self.authority)
        self.assertEqual(verdict.code, "DUPLICATE_HQ_KEY")

    def test_process_control_exceptions_are_not_swallowed(self) -> None:
        def interrupted(*args: Any) -> Any:
            raise KeyboardInterrupt()

        self.authority.raw_anchor = interrupted
        with self.assertRaises(KeyboardInterrupt):
            verify_watch_sources([event()], evaluation_as_of=NOW,
                                 authority=self.authority)

    def test_missing_authority_fails_closed(self) -> None:
        verdict = verify_watch_sources([event()], evaluation_as_of=NOW, authority=None)
        self.assertEqual(verdict.status, "SOURCE_UNVERIFIED")
        self.assertFalse(verdict.production_permitted)


if __name__ == "__main__":
    unittest.main()
