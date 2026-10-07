"""人工认证仅使用工程HMAC夹具，验证默认拒绝和持久审核边界。"""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from ai_operator.contracts import EVENT_FIELDS, canonical_json, normalize_time, utc_now
from ai_operator.memory import open_store
from ai_operator.review_gate import record_source_review
from ai_operator.capture_journal import capture_to_store


_AUTO_PROOF = object()


class TestHMACVerifier:
    """仅用于测试；完整声明与主体权限共同签名，不代表真实认证部署。"""

    def __init__(self):
        self.key = b"engineering-fixture-only"
        self.principals = {
            "alice": {"principal_id": "alice", "authority_id": "fixture-authority",
                      "policy_id": "fixture-policy", "role": "source_reviewer"},
            "bob": {"principal_id": "bob", "authority_id": "fixture-authority",
                    "policy_id": "fixture-policy", "role": "source_reviewer"},
            "admin": {"principal_id": "admin", "authority_id": "fixture-authority",
                      "policy_id": "fixture-policy", "role": "source_admin"}}
        self.authorizations = {identity: {"valid_from": "2026-01-01T00:00:00.000000+00:00",
                                          "valid_until": "2100-01-01T00:00:00.000000+00:00",
                                          "revoked_at": None} for identity in self.principals}

    def sign(self, statement, identity="alice", extra=None):
        normalized = copy.deepcopy(statement)
        for name in ("issued_at", "expires_at"):
            if normalized[name] is not None:
                normalized[name] = normalize_time(normalized[name])
        authorization = copy.deepcopy(self.authorizations[identity])
        result = {"identity": identity, "authorization": authorization,
                  "signature": self.signature(normalized, identity, authorization)}
        if extra:
            result.update(extra)
        return result

    def signature(self, statement, identity, authorization):
        message = canonical_json({"statement": statement, "principal": self.principals[identity],
                                  "authorization": authorization})
        return hmac.new(self.key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, statement, proof, *, received_at):
        identity = proof.get("identity")
        authorization = proof.get("authorization")
        if identity not in self.principals or not hmac.compare_digest(
                proof.get("signature", ""), self.signature(statement, identity, authorization)):
            raise ValueError("工程签名无效")
        # 签名包含归档授权窗口及撤销时点，依据系统原始接收时点重验历史权限。
        instant = normalize_time(received_at)
        if not authorization["valid_from"] <= instant < authorization["valid_until"]:
            raise ValueError("工程授权在接收时点尚未生效或已经到期")
        if authorization["revoked_at"] is not None and instant >= authorization["revoked_at"]:
            raise ValueError("工程授权在接收时点已经撤销")
        return copy.deepcopy(self.principals[identity])


class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.human_review"),
                             "缺少契约要求的人工审核模块")
        self.module = importlib.import_module("ai_operator.human_review")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.db = self.directory / "audit.sqlite"
        market, calendar = self.directory / "market.json", self.directory / "calendar.json"
        market.write_text(json.dumps({"bars": [{"session": "2026-01-05", "open": 10,
                          "high": 11, "low": 9, "close": 10, "volume": 0}]}), encoding="utf-8")
        calendar.write_text(json.dumps({"rows": [{"exchange": "SSE", "cal_date": "20260105",
                                                 "is_open": 1}]}), encoding="utf-8")
        self.capture_spec = {"path": str(market), "source_id": "fixture-source",
                             "code": "000001.SH", "price_basis": "raw", "revision": "fixture-v1"}
        self.calendar_spec = {"paths": [str(calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                              "revision": "fixture-v1", "coverage_start": "2026-01-05",
                              "coverage_end": "2026-01-05"}
        with open_store(self.db) as store:
            self.receipt = capture_to_store(store, self.capture_spec, self.calendar_spec)
            self.review = record_source_review(store, self.receipt["capture_event_id"], "工程夹具",
                                               self.evidence(self.receipt))
        self.verifier = TestHMACVerifier()

    def evidence(self, receipt):
        snapshot, calendar = receipt["snapshot"], receipt["calendar_capture"]
        documents = {"supplier_identity": {"source_id": snapshot["source_id"], "provider": "工程夹具"},
                     "price_basis": {"code": snapshot["code"], "price_basis": snapshot["price_basis"],
                                     "adjustment_reference": None},
                     "revision": {"revision": snapshot["revision"],
                                  "source_sha256": snapshot["source_file"]["sha256"]},
                     "official_calendar": {"exchange": calendar["exchange"], "rows": [
                         {"cal_date": day["session"], "is_open": day["is_open"]} for day in calendar["days"]]}}
        result = {}
        for kind, document in documents.items():
            path = self.directory / (kind + ".json")
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            result[kind] = {"path": str(path), "url": "https://www.sse.com.cn/engineering-fixture"
                           if kind == "official_calendar" else "https://example.test/engineering-fixture"}
        return result

    def statement(self, action="approve", **changes):
        now = utc_now()
        value = {"schema_version": 1, "request_id": uuid.uuid4().hex, "action": action,
                 "capture_event_id": self.receipt["capture_event_id"],
                 "review_event_id": self.review["review_event_id"], "target_decision_event_id": None,
                 "reason": "工程夹具人审", "issued_at": now,
                 "expires_at": (datetime.fromisoformat(now) + timedelta(days=1)).isoformat()
                 if action == "approve" else None}
        value.update(changes)
        return value

    def record(self, statement=None, identity="alice", proof=_AUTO_PROOF, verifier=None):
        statement = self.statement() if statement is None else statement
        proof = self.verifier.sign(statement, identity) if proof is _AUTO_PROOF else proof
        with open_store(self.db) as store:
            return self.module.record_human_decision(store, statement, proof,
                                                     verifier=self.verifier if verifier is None else verifier)

    def load(self, event_id, as_of=None, verifier=None):
        with open_store(self.db, readonly=True) as store:
            return self.module.load_human_decision(store, event_id, utc_now() if as_of is None else as_of,
                                                   verifier=self.verifier if verifier is None else verifier)

    def assert_no_write(self, operation):
        with open_store(self.db, readonly=True) as store:
            before = store.snapshot()
        with self.assertRaises(ValueError):
            operation()
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot(), before)

    def forge(self, original, change, *, rehash=True, recorded_at=None):
        with open_store(self.db) as store:
            event = store.get_event(original["decision_event_id"])
            body = {name: copy.deepcopy(event[name]) for name in EVENT_FIELDS}
            change(body)
            if rehash:
                event_id = store.append_event(body)
            else:
                event_id = event["event_id"]
                store._conn.execute("UPDATE events SET event_json=? WHERE event_id=?",
                                    (canonical_json(body), event_id))
            if recorded_at is not None:
                store._conn.execute("UPDATE events SET recorded_at=? WHERE event_id=?", (recorded_at, event_id))
            return event_id

    def test_default_authentication_refuses_without_writes(self):
        statement = self.statement()
        for adapter in (None, {}, True, lambda *_: self.verifier.principals["alice"]):
            with self.subTest(adapter=type(adapter).__name__):
                def attempt():
                    with open_store(self.db) as store:
                        self.module.record_human_decision(store, statement, self.verifier.sign(statement),
                                                         verifier=adapter)
                self.assert_no_write(attempt)

    def test_approve_roundtrip_and_exact_contract(self):
        statement = self.statement()
        statement["issued_at"] = datetime.fromisoformat(statement["issued_at"]).astimezone(
            timezone(timedelta(hours=8))).isoformat()
        result = self.record(statement)
        loaded = self.load(result["decision_event_id"])
        self.assertEqual(result, loaded)
        self.assertEqual(set(result), {"decision_event_id", "statement", "principal", "recorded_available_at",
                                       "event", "eligible", "production_eligible"})
        self.assertFalse(result["eligible"])
        self.assertFalse(result["production_eligible"])
        self.assertEqual(result["principal"], self.verifier.principals["alice"])
        self.assertEqual(result["statement"]["issued_at"], normalize_time(statement["issued_at"]))
        with open_store(self.db, readonly=True) as store:
            before = store.snapshot()
            with self.assertRaises(ValueError):
                self.module.load_human_decision(store, result["decision_event_id"], utc_now())
            self.assertEqual(store.snapshot(), before)

    def test_signature_binds_statement_and_identity(self):
        statement = self.statement()
        proof = self.verifier.sign(statement)
        statement["reason"] = "改过的原因"
        self.assert_no_write(lambda: self.record(statement, proof=proof))
        statement = self.statement()
        proof = self.verifier.sign(statement)
        proof["identity"] = "bob"
        self.assert_no_write(lambda: self.record(statement, proof=proof))

    def test_strict_principal_rejects_false_authority(self):
        class BadPrincipal:
            def __init__(self, value):
                self.value = value

            def verify(self, statement, proof, *, received_at):
                return self.value
        correct = self.verifier.principals["alice"]
        values = [None, True, {**correct, "extra": True}, {**correct, "role": "reviewer"}]
        for key in ("principal_id", "authority_id", "policy_id"):
            values.extend([{**correct, key: " "}, {**correct, key: 1}, {k: v for k, v in correct.items() if k != key}])
        for value in values:
            with self.subTest(value=value):
                self.assert_no_write(lambda: self.record(verifier=BadPrincipal(value)))

    def test_verifier_errors_are_sanitized_and_zero_write(self):
        class Broken:
            def verify(self, statement, proof, *, received_at):
                raise RuntimeError("authentication-secret-do-not-expose")
        with open_store(self.db) as store, self.assertRaises(ValueError) as failure:
            statement = self.statement()
            self.module.record_human_decision(store, statement, {}, verifier=Broken())
        self.assertNotIn("authentication-secret", str(failure.exception))
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot()["events"], 2)

    def test_statement_strict_fields_and_types(self):
        variants = [{"schema_version": True}, {"request_id": "A" * 32}, {"action": "accept"},
                    {"capture_event_id": "x" * 64}, {"review_event_id": 1}, {"reason": " "},
                    {"issued_at": "2026-01-01T00:00:00"}, {"expires_at": None},
                    {"target_decision_event_id": "a" * 64}, {"verifier": "injected"}]
        for change in variants:
            with self.subTest(change=change):
                value = self.statement(**change)
                self.assert_no_write(lambda: self.record(value, proof={}))
        value = self.statement()
        del value["reason"]
        self.assert_no_write(lambda: self.record(value, proof={}))

    def test_proof_strict_json_and_utf8_size(self):
        cycle = {}
        cycle["cycle"] = cycle
        for proof in (None, [], {1: "value"}, {"x": object()}, {"x": float("nan")},
                      {"x": (1, 2)}, cycle, {"x": "\ud800"}, {"x": "中" * 22000}):
            with self.subTest(kind=type(proof).__name__):
                self.assert_no_write(lambda: self.record(proof=proof))
        statement = self.statement()
        proof = self.verifier.sign(statement, extra={"padding": ""})
        proof["padding"] = "x" * (65536 - len(canonical_json(proof).encode("utf-8")))
        self.assertEqual(len(canonical_json(proof).encode("utf-8")), 65536)
        result = self.record(statement, proof=proof)
        self.assertEqual(result["event"]["payload"]["proof"], proof)
        proof["padding"] += "x"
        self.assert_no_write(lambda: self.record(statement, proof=proof))

    def test_approve_requires_matched_evidence_but_reject_accepts_gaps(self):
        with open_store(self.db) as store:
            missing = record_source_review(store, self.receipt["capture_event_id"], "工程夹具",
                                           dict.fromkeys(self.evidence(self.receipt)))
        statement = self.statement(review_event_id=missing["review_event_id"])
        self.assert_no_write(lambda: self.record(statement))
        rejected = self.record(self.statement("reject", review_event_id=missing["review_event_id"]))
        self.assertEqual(self.load(rejected["decision_event_id"])["statement"]["action"], "reject")

    def test_approve_cannot_bind_wrong_capture_or_mismatched_review(self):
        with open_store(self.db) as store:
            receipt = capture_to_store(store, self.capture_spec, self.calendar_spec)
            evidence = self.evidence(receipt)
            path = Path(evidence["revision"]["path"])
            document = json.loads(path.read_text(encoding="utf-8"))
            document["revision"] = "wrong"
            path.write_text(json.dumps(document), encoding="utf-8")
            mismatch = record_source_review(store, receipt["capture_event_id"], "工程夹具", evidence)
        self.assert_no_write(lambda: self.record(self.statement(capture_event_id=receipt["capture_event_id"])))
        self.assert_no_write(lambda: self.record(self.statement(capture_event_id=receipt["capture_event_id"],
                                                               review_event_id=mismatch["review_event_id"])))

    def test_reject_and_revoke_expiry_target_rules(self):
        for action in ("reject", "revoke"):
            self.assert_no_write(lambda: self.record(self.statement(action, expires_at=utc_now())))
        self.assert_no_write(lambda: self.record(self.statement("revoke")))

    def test_revoke_requires_approve_target_and_matching_pair(self):
        reject = self.record(self.statement("reject"))
        for target in (reject["decision_event_id"], self.review["review_event_id"], "a" * 64):
            self.assert_no_write(lambda: self.record(self.statement("revoke", target_decision_event_id=target)))
        approval = self.record()
        with open_store(self.db) as store:
            other = record_source_review(store, self.receipt["capture_event_id"], "工程夹具",
                                         self.evidence(self.receipt))
        self.assert_no_write(lambda: self.record(self.statement("revoke", review_event_id=other["review_event_id"],
                                     target_decision_event_id=approval["decision_event_id"])))

    def test_revoke_original_principal_or_admin_only(self):
        approval = self.record()
        statement = self.statement("revoke", target_decision_event_id=approval["decision_event_id"])
        self.assert_no_write(lambda: self.record(statement, identity="bob"))
        for identity in ("alice", "admin"):
            revoke = self.record(statement, identity=identity)
            self.assertEqual(self.load(revoke["decision_event_id"])["principal"]["principal_id"], identity)

    def test_issued_before_review_future_issue_and_expiry_rejected(self):
        old = (datetime.fromisoformat(self.review["recorded_available_at"]) - timedelta(seconds=1)).isoformat()
        future = (datetime.fromisoformat(utc_now()) + timedelta(days=1)).isoformat()
        for change in ({"issued_at": old}, {"issued_at": future}, {"expires_at": old}):
            self.assert_no_write(lambda: self.record(self.statement(**change)))

    def test_expired_today_approval_still_replays(self):
        statement = self.statement(expires_at=(datetime.fromisoformat(utc_now()) + timedelta(minutes=1)).isoformat())
        approval = self.record(statement)
        future = (datetime.fromisoformat(statement["expires_at"]) + timedelta(days=1)).isoformat()
        with patch("ai_operator.human_review.utc_now", return_value=future):
            self.assertEqual(self.load(approval["decision_event_id"], future)["statement"], approval["statement"])

    def test_readonly_record_and_load_zero_writes(self):
        approval = self.record()
        with open_store(self.db, readonly=True) as store:
            before = store.snapshot()
            statement = self.statement()
            with self.assertRaises(ValueError):
                self.module.record_human_decision(store, statement, self.verifier.sign(statement), verifier=self.verifier)
            self.module.load_human_decision(store, approval["decision_event_id"], utc_now(), verifier=self.verifier)
            self.assertEqual(store.snapshot(), before)

    def test_same_clock_replay_records_distinct_observations(self):
        statement = self.statement()
        now = utc_now()
        with patch("ai_operator.human_review.utc_now", return_value=now), patch("ai_operator.memory.utc_now", return_value=now):
            first, second = self.record(statement), self.record(statement)
        self.assertNotEqual(first["decision_event_id"], second["decision_event_id"])
        self.assertNotEqual(first["event"]["payload"]["observation_id"], second["event"]["payload"]["observation_id"])
        self.assertEqual(first["event"]["source_id"], second["event"]["source_id"])

    def test_invalid_query_event_hash_and_payload_hash_rejected(self):
        approval = self.record()
        for event_id, as_of in (("a" * 64, utc_now()), ("A" * 64, utc_now()),
                               (self.review["review_event_id"], utc_now()),
                               (approval["decision_event_id"], "2026-10-07T00:00:00"),
                               (approval["decision_event_id"], (datetime.fromisoformat(utc_now()) + timedelta(days=1)).isoformat())):
            with self.subTest(event_id=event_id, as_of=as_of), self.assertRaises(ValueError):
                self.load(event_id, as_of)
        tampered = self.forge(approval, lambda body: body["payload"]["statement"].update(reason="篡改"), rehash=False)
        with self.assertRaises(ValueError):
            self.load(tampered)
        with open_store(self.db) as store:
            store._conn.execute("UPDATE events SET payload_hash=? WHERE event_id=?", ("a" * 64, tampered))
        with self.assertRaises(ValueError):
            self.load(tampered)

    def test_rehashed_invalid_semantics_and_signature_refused(self):
        approval = self.record()
        mutations = [lambda body: body.update(kind="source.review"),
                     lambda body: body.update(source="forged"),
                     lambda body: body.update(environment="paper"),
                     lambda body: body.update(source_id="b" * 64),
                     lambda body: body["payload"].update(observation_id="A" * 32),
                     lambda body: body["payload"].update(schema_version=True),
                     lambda body: body["payload"].update(extra=True),
                     lambda body: body["payload"]["principal"].update(principal_id="bob"),
                     lambda body: body["payload"]["statement"].update(reason="改声明"),
                     lambda body: body["payload"].update(received_at="2026-01-01T00:00:00+00:00")]
        for change in mutations:
            with self.subTest(change=mutations.index(change)):
                event_id = self.forge(approval, change)
                with self.assertRaises(ValueError):
                    self.load(event_id)

    def test_rehashed_statement_still_requires_original_signature(self):
        approval = self.record()

        def change(body):
            body["payload"]["statement"]["reason"] = "改变声明后重算事件及来源哈希"
            body["source_id"] = hashlib.sha256(canonical_json(
                body["payload"]["statement"]).encode("utf-8")).hexdigest()

        event_id = self.forge(approval, change)
        with self.assertRaisesRegex(ValueError, "认证失败"):
            self.load(event_id)

    def test_new_signature_cannot_bypass_statement_time_semantics(self):
        approval = self.record()

        def change(body):
            statement = body["payload"]["statement"]
            statement["issued_at"] = "2026-01-01T00:00:00.000000+00:00"
            body["source_id"] = hashlib.sha256(canonical_json(statement).encode("utf-8")).hexdigest()
            body["payload"]["proof"] = self.verifier.sign(statement)

        event_id = self.forge(approval, change)
        with self.assertRaisesRegex(ValueError, "签发时间"):
            self.load(event_id)

    def test_verifier_cannot_change_inputs_or_saved_principal(self):
        class Mutating:
            def verify(inner, statement, proof, *, received_at):
                statement["reason"] = "认证时换声明"
                return self.verifier.principals["alice"]
        self.assert_no_write(lambda: self.record(verifier=Mutating()))
        approval = self.record()

        class ChangedPolicy:
            def verify(inner, statement, proof, *, received_at):
                value = self.verifier.verify(statement, proof, received_at=received_at)
                value["policy_id"] = "changed-policy"
                return value
        with self.assertRaisesRegex(ValueError, "principal 不一致"):
            self.load(approval["decision_event_id"], verifier=ChangedPolicy())

    def test_same_name_other_authority_cannot_revoke(self):
        approval = self.record()
        other = TestHMACVerifier()
        other.principals["alice"]["authority_id"] = "other-authority"
        statement = self.statement("revoke", target_decision_event_id=approval["decision_event_id"])

        class TwoAuthorities:
            def verify(inner, value, proof, *, received_at):
                return (other if value["action"] == "revoke" else self.verifier).verify(
                    value, proof, received_at=received_at)
        self.assert_no_write(lambda: self.record(statement, proof=other.sign(statement), verifier=TwoAuthorities()))

    def test_revoke_load_reauthenticates_target_approval(self):
        approval = self.record()
        revoke = self.record(self.statement("revoke", target_decision_event_id=approval["decision_event_id"]))
        class TargetRefused:
            def verify(inner, statement, proof, *, received_at):
                if statement["action"] == "approve":
                    raise ValueError("工程目标认证失效")
                return self.verifier.verify(statement, proof, received_at=received_at)
        with self.assertRaisesRegex(ValueError, "认证失败"):
            self.load(revoke["decision_event_id"], verifier=TargetRefused())

    def test_actual_recording_time_controls_visibility_and_clock_rollback(self):
        approval = self.record()
        future = (datetime.fromisoformat(utc_now()) + timedelta(seconds=5)).isoformat(timespec="microseconds")
        event_id = self.forge(approval, lambda body: None, recorded_at=future)
        with self.assertRaises(ValueError):
            self.load(event_id)
        now = (datetime.fromisoformat(future) + timedelta(seconds=1)).isoformat(timespec="microseconds")
        with patch("ai_operator.human_review.utc_now", return_value=now):
            with self.assertRaises(ValueError):
                self.load(event_id, approval["recorded_available_at"])
            self.assertEqual(self.load(event_id, now)["recorded_available_at"], future)
        early = (datetime.fromisoformat(approval["event"]["payload"]["received_at"]) - timedelta(seconds=1)).isoformat()
        self.forge(approval, lambda body: None, recorded_at=early)
        with self.assertRaises(ValueError):
            self.load(event_id)

    def test_record_clock_anomaly_rolls_back_event_and_outbox(self):
        future = (datetime.fromisoformat(utc_now()) + timedelta(days=2)).isoformat()
        with patch("ai_operator.memory.utc_now", return_value=future):
            self.assert_no_write(lambda: self.record())

    def test_authorization_expired_after_issue_before_receive_zero_write(self):
        statement = self.statement()
        issued = datetime.fromisoformat(statement["issued_at"])
        received = (issued + timedelta(seconds=2)).isoformat(timespec="microseconds")
        self.verifier.authorizations["alice"]["valid_until"] = (
            issued + timedelta(seconds=1)).isoformat(timespec="microseconds")
        proof = self.verifier.sign(statement)
        with patch("ai_operator.human_review.utc_now", return_value=received), \
                patch("ai_operator.memory.utc_now", return_value=received):
            self.assert_no_write(lambda: self.record(statement, proof=proof))

    def test_authorization_revoked_after_issue_before_receive_zero_write(self):
        statement = self.statement()
        issued = datetime.fromisoformat(statement["issued_at"])
        received = (issued + timedelta(seconds=2)).isoformat(timespec="microseconds")
        self.verifier.authorizations["alice"]["revoked_at"] = (
            issued + timedelta(seconds=1)).isoformat(timespec="microseconds")
        proof = self.verifier.sign(statement)
        with patch("ai_operator.human_review.utc_now", return_value=received), \
                patch("ai_operator.memory.utc_now", return_value=received):
            self.assert_no_write(lambda: self.record(statement, proof=proof))

    def test_past_authorization_replays_after_current_revocation(self):
        statement = self.statement()
        issued = datetime.fromisoformat(statement["issued_at"])
        received = (issued + timedelta(seconds=1)).isoformat(timespec="microseconds")
        revoked = (issued + timedelta(seconds=2)).isoformat(timespec="microseconds")
        today = (issued + timedelta(days=2)).isoformat(timespec="microseconds")
        self.verifier.authorizations["alice"].update(valid_until=revoked, revoked_at=revoked)
        proof = self.verifier.sign(statement)
        with patch("ai_operator.human_review.utc_now", return_value=received), \
                patch("ai_operator.memory.utc_now", return_value=received):
            approval = self.record(statement, proof=proof)
        with patch("ai_operator.human_review.utc_now", return_value=today):
            loaded = self.load(approval["decision_event_id"], received)
            self.assertEqual(loaded["principal"], self.verifier.principals["alice"])
            self.assertEqual(loaded["event"]["payload"]["received_at"], received)


if __name__ == "__main__":
    unittest.main()
