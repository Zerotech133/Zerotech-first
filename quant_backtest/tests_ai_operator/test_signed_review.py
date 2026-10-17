"""以临时 Ed25519 密钥和真实工程事件验证离线审核策略。"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # importlib 收集模式下兄弟模块不可隐式发现

import builtins
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_operator.contracts import canonical_json, utc_now
from ai_operator.human_review import record_human_decision, load_human_decision
from ai_operator.memory import open_store
import test_human_review as fixtures


def sha(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class SignedReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.signed_review"),
                             "缺少第七批 Ed25519 策略适配器")
        self.module = importlib.import_module("ai_operator.signed_review")
        self.fixture = fixtures.HumanReviewTests("test_approve_roundtrip_and_exact_contract")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.base = datetime.fromisoformat(utc_now())
        self.keys = {identity: Ed25519PrivateKey.generate() for identity in ("alice", "bob", "admin")}
        principals = []
        for identity, key in self.keys.items():
            public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            (self.fixture.directory / (identity + "-temporary.pem")).write_bytes(private)
            principals.append({"principal_id": identity, "key_id": hashlib.sha256(public).hexdigest(),
                               "public_key_hex": public.hex(), "role": "source_admin" if identity == "admin"
                               else "source_reviewer", "valid_from": self.at(-86400),
                               "valid_until": self.at(86400 * 30), "revoked_at": None,
                               "enrollment_reference": "工程夹具登记引用，不证明真人身份"})
        self.bundle = {"schema_version": 1, "authority_id": "fixture-authority", "policies": [
            {"policy_id": "policy-v1", "effective_at": self.at(-86400), "principals": principals}]}
        self.verifier = self.make_verifier()

    def at(self, seconds):
        return (self.base + timedelta(seconds=seconds)).isoformat(timespec="microseconds")

    def statement(self, action="approve", **changes):
        return self.fixture.statement(action, issued_at=self.at(0),
                                      **({"expires_at": self.at(1000)} if action == "approve" else {}), **changes)

    def make_verifier(self, bundle=None):
        value = self.bundle if bundle is None else bundle
        return self.module.SignedReviewVerifier(value, sha(value))

    def sign(self, statement, identity="alice", policy_id="policy-v1", authority_id="fixture-authority", message=None):
        principal = next(value for value in self.bundle["policies"][0]["principals"]
                         if value["principal_id"] == identity)
        message = self.module.signing_message(statement, authority_id, policy_id) if message is None else message
        return {"schema_version": 1, "algorithm": "Ed25519", "authority_id": authority_id,
                "policy_id": policy_id, "key_id": principal["key_id"],
                "signature_hex": self.keys[identity].sign(message).hex()}

    def record(self, statement=None, *, identity="alice", policy_id="policy-v1", verifier=None, proof=None):
        statement = self.statement() if statement is None else statement
        proof = self.sign(statement, identity, policy_id) if proof is None else proof
        with open_store(self.db) as store:
            return record_human_decision(store, statement, proof,
                                         verifier=self.verifier if verifier is None else verifier)

    def verify_at(self, statement, proof, seconds=0, verifier=None):
        with patch("ai_operator.signed_review.utc_now", return_value=self.at(100)):
            return (self.verifier if verifier is None else verifier).verify(
                statement, proof, received_at=self.at(seconds))

    def test_fixed_message_domain_and_normalized_statement(self):
        statement = self.statement()
        local = copy.deepcopy(statement)
        local["issued_at"] = datetime.fromisoformat(local["issued_at"]).astimezone(
            timezone(timedelta(hours=8))).isoformat()
        expected = canonical_json({"domain": "ai_operator.human_review.ed25519.v1",
                                   "authority_id": "fixture-authority", "policy_id": "policy-v1",
                                   "statement": statement}).encode("utf-8")
        self.assertEqual(self.module.signing_message(local, "fixture-authority", "policy-v1"), expected)
        self.assertIs(type(expected), bytes)
        for authority, policy in (("", "policy-v1"), ("fixture-authority", None)):
            with self.assertRaises(ValueError):
                self.module.signing_message(statement, authority, policy)

    def test_signature_returns_only_registered_principal(self):
        statement = self.statement()
        for identity in self.keys:
            principal = self.verify_at(statement, self.sign(statement, identity))
            self.assertEqual(set(principal), {"principal_id", "authority_id", "policy_id", "role"})
            self.assertEqual(principal["principal_id"], identity)
            self.assertEqual(principal["role"], "source_admin" if identity == "admin" else "source_reviewer")

    def test_message_domain_statement_authority_policy_key_signature_tampering(self):
        statement = self.statement()
        proof = self.sign(statement)
        changed = copy.deepcopy(statement)
        changed["reason"] = "签名后改审核原因"
        with self.assertRaises(ValueError):
            self.verify_at(changed, proof)
        message = canonical_json({"domain": "other.domain", "authority_id": proof["authority_id"],
                                  "policy_id": proof["policy_id"], "statement": statement}).encode("utf-8")
        with self.assertRaises(ValueError):
            self.verify_at(statement, self.sign(statement, message=message))
        bob = self.bundle["policies"][0]["principals"][1]
        for change in ({"authority_id": "other-authority"}, {"policy_id": "other-policy"},
                       {"key_id": bob["key_id"]}, {"key_id": "a" * 64}, {"signature_hex": "0" * 128}):
            with self.subTest(field=next(iter(change))), self.assertRaises(ValueError):
                self.verify_at(statement, {**proof, **change})

    def test_proof_strict_schema_private_key_extra_and_types(self):
        statement = self.statement()
        proof = self.sign(statement)
        changes = [{"schema_version": True}, {"algorithm": "ed25519"}, {"signature_hex": "A" * 128},
                   {"signature_hex": "1" * 127}, {"signature_hex": None}, {"key_id": 1},
                   {"private_key": (self.fixture.directory / "alice-temporary.pem").read_text()},
                   {"public_key_hex": self.bundle["policies"][0]["principals"][0]["public_key_hex"]},
                   {"principal_id": "alice"}, {"policy": self.bundle["policies"][0]}]
        for change in changes:
            with self.subTest(field=next(iter(change))), self.assertRaises(ValueError):
                self.verify_at(statement, {**proof, **change})
        for name in proof:
            with self.subTest(missing=name), self.assertRaises(ValueError):
                self.verify_at(statement, {key: value for key, value in proof.items() if key != name})
        for value in (None, [], "{\"schema_version\":1,\"schema_version\":2}", {"x": object()}):
            with self.assertRaises(ValueError):
                self.verify_at(statement, value)

    def test_bundle_digest_external_pin_and_strict_json(self):
        for digest in ("0" * 64, "A" * 64, None, True):
            with self.subTest(digest_type=type(digest).__name__), self.assertRaises(ValueError):
                self.module.SignedReviewVerifier(self.bundle, digest)
        cycle = {}
        cycle["loop"] = cycle
        for value in (None, [], "{\"schema_version\":1,\"schema_version\":2}", {"x": object()},
                      {"x": float("nan")}, {"x": "\ud800"}, cycle):
            with self.assertRaises(ValueError):
                self.module.SignedReviewVerifier(value, "a" * 64)

    def test_bundle_policy_principal_fields_and_key_constraints(self):
        cases = []
        for field, value in (("schema_version", True), ("authority_id", " "), ("private_key", "forbidden")):
            bundle = copy.deepcopy(self.bundle)
            bundle[field] = value
            cases.append(bundle)
        for field, value in (("policy_id", ""), ("effective_at", "2026-01-01T00:00:00"), ("extra", 1)):
            bundle = copy.deepcopy(self.bundle)
            bundle["policies"][0][field] = value
            cases.append(bundle)
        for field, value in (("principal_id", ""), ("key_id", "0" * 64), ("public_key_hex", "A" * 64),
                             ("public_key_hex", "1" * 62), ("role", "operator"), ("enrollment_reference", None),
                             ("valid_from", "2026-01-01T08:00:00+08:00"), ("valid_until", self.at(-86400)),
                             ("revoked_at", "2026-01-01T00:00:00"), ("private_key", "forbidden")):
            bundle = copy.deepcopy(self.bundle)
            bundle["policies"][0]["principals"][0][field] = value
            cases.append(bundle)
        for bundle in cases:
            with self.subTest(case=cases.index(bundle)), self.assertRaises(ValueError):
                self.make_verifier(bundle)
        for layer in ("bundle", "policy", "principal"):
            bundle = copy.deepcopy(self.bundle)
            target = bundle if layer == "bundle" else bundle["policies"][0] if layer == "policy" \
                else bundle["policies"][0]["principals"][0]
            del target[next(iter(target))]
            with self.subTest(missing=layer), self.assertRaises(ValueError):
                self.make_verifier(bundle)

    def test_duplicate_keys_policy_ids_and_nonincreasing_effective_time(self):
        duplicate_key = copy.deepcopy(self.bundle)
        duplicate_key["policies"][0]["principals"].append(copy.deepcopy(duplicate_key["policies"][0]["principals"][0]))
        with self.assertRaises(ValueError):
            self.make_verifier(duplicate_key)
        for policy_id, effective in (("policy-v1", self.at(10)), ("policy-v2", self.at(-86400)),
                                     ("policy-v2", self.at(-86401))):
            bundle = copy.deepcopy(self.bundle)
            bundle["policies"].append({**copy.deepcopy(bundle["policies"][0]),
                                       "policy_id": policy_id, "effective_at": effective})
            with self.assertRaises(ValueError):
                self.make_verifier(bundle)

    def test_bundle_policy_principal_count_and_utf8_size_limit(self):
        for target in ("policies", "principals"):
            for count in (0, 65):
                bundle = copy.deepcopy(self.bundle)
                owner = bundle if target == "policies" else bundle["policies"][0]
                owner[target] = [copy.deepcopy(owner[target][0]) for _ in range(count)]
                with self.assertRaises(ValueError):
                    self.make_verifier(bundle)
        bundle = copy.deepcopy(self.bundle)
        principal = bundle["policies"][0]["principals"][0]
        principal["enrollment_reference"] = "x"
        principal["enrollment_reference"] += "x" * (262144 - len(canonical_json(bundle).encode("utf-8")))
        self.assertEqual(len(canonical_json(bundle).encode("utf-8")), 262144)
        self.make_verifier(bundle)
        principal["enrollment_reference"] += "中"
        with self.assertRaises(ValueError):
            self.make_verifier(bundle)

    def test_received_at_policy_and_principal_boundaries(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["policies"][0]["effective_at"] = self.at(0)
        principal = bundle["policies"][0]["principals"][0]
        principal.update(valid_from=self.at(1), valid_until=self.at(10), revoked_at=self.at(8))
        verifier = self.make_verifier(bundle)
        statement = self.statement()
        proof = self.sign(statement)
        for seconds in (-1, 0, 8, 10, 20):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.verify_at(statement, proof, seconds, verifier)
        for seconds in (1, 7):
            self.assertEqual(self.verify_at(statement, proof, seconds, verifier)["principal_id"], "alice")
        for received in (None, datetime.now(timezone.utc), "2026-01-01T00:00:00", self.at(200)):
            with self.assertRaises(ValueError), patch("ai_operator.signed_review.utc_now", return_value=self.at(100)):
                verifier.verify(statement, proof, received_at=received)

    def test_latest_policy_disables_old_policy_without_fallback(self):
        bundle = copy.deepcopy(self.bundle)
        second = copy.deepcopy(bundle["policies"][0])
        second.update(policy_id="policy-v2", effective_at=self.at(10))
        second["principals"][0]["revoked_at"] = self.at(10)
        bundle["policies"].append(second)
        verifier = self.make_verifier(bundle)
        statement = self.statement()
        old_proof, new_proof = self.sign(statement), self.sign(statement, policy_id="policy-v2")
        self.assertEqual(self.verify_at(statement, old_proof, 1, verifier)["policy_id"], "policy-v1")
        for proof in (old_proof, new_proof):
            with self.assertRaises(ValueError):
                self.verify_at(statement, proof, 10, verifier)
        self.assertEqual(self.verify_at(statement, self.sign(statement, "bob", "policy-v2"), 10,
                                        verifier)["policy_id"], "policy-v2")

    def test_new_policy_removed_key_does_not_fallback_to_old_key(self):
        bundle = copy.deepcopy(self.bundle)
        second = copy.deepcopy(bundle["policies"][0])
        second.update(policy_id="policy-v2", effective_at=self.at(10))
        second["principals"] = second["principals"][1:]
        bundle["policies"].append(second)
        verifier = self.make_verifier(bundle)
        statement = self.statement()
        for policy in ("policy-v1", "policy-v2"):
            with self.assertRaises(ValueError):
                self.verify_at(statement, self.sign(statement, policy_id=policy), 10, verifier)

    def test_registered_authority_and_policy_are_cryptographically_bound(self):
        statement = self.statement()
        original = self.sign(statement)
        for field, replacement in (("authority_id", "second-authority"), ("policy_id", "second-policy")):
            bundle = copy.deepcopy(self.bundle)
            if field == "authority_id":
                bundle[field] = replacement
            else:
                bundle["policies"][0][field] = replacement
            verifier = self.make_verifier(bundle)
            altered = {**original, field: replacement}
            with self.assertRaisesRegex(ValueError, "签名无效"):
                self.verify_at(statement, altered, verifier=verifier)
            authority = bundle["authority_id"]
            policy = bundle["policies"][0]["policy_id"]
            valid = self.sign(statement, authority_id=authority, policy_id=policy)
            self.assertEqual(self.verify_at(statement, valid, verifier=verifier)[field], replacement)

    def test_superseded_policy_submission_rejects_without_writes(self):
        bundle = copy.deepcopy(self.bundle)
        second = copy.deepcopy(bundle["policies"][0])
        second.update(policy_id="policy-v2", effective_at=self.at(10))
        second["principals"][0]["revoked_at"] = self.at(10)
        bundle["policies"].append(second)
        verifier = self.make_verifier(bundle)
        statement = self.statement()
        proof = self.sign(statement)
        with patch("ai_operator.human_review.utc_now", return_value=self.at(20)), \
                patch("ai_operator.memory.utc_now", return_value=self.at(20)), \
                patch("ai_operator.signed_review.utc_now", return_value=self.at(20)):
            self.fixture.assert_no_write(lambda: self.record(statement, verifier=verifier, proof=proof))

    def test_sixty_four_policy_versions_are_supported(self):
        bundle = copy.deepcopy(self.bundle)
        base_policy = bundle["policies"][0]
        bundle["policies"] = [{**copy.deepcopy(base_policy), "policy_id": "version-" + str(index),
                               "effective_at": self.at(-86400 + index)} for index in range(64)]
        verifier = self.make_verifier(bundle)
        statement = self.statement()
        proof = self.sign(statement, policy_id="version-63")
        self.assertEqual(self.verify_at(statement, proof, verifier=verifier)["policy_id"], "version-63")

    def test_caller_mutation_cannot_change_frozen_authority_roles_or_key(self):
        statement = self.statement()
        proof = self.sign(statement)
        self.bundle["authority_id"] = "changed"
        self.bundle["policies"][0]["principals"][0].update(role="source_admin", revoked_at=self.at(-1),
                                                         public_key_hex="0" * 64)
        self.bundle["policies"].clear()
        principal = self.verify_at(statement, proof)
        self.assertEqual(principal["authority_id"], "fixture-authority")
        self.assertEqual(principal["role"], "source_reviewer")
        principal["role"] = "source_admin"
        self.assertEqual(self.verify_at(statement, proof)["role"], "source_reviewer")

    def test_missing_crypto_fails_closed_but_message_has_no_dependency(self):
        original = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ModuleNotFoundError("模拟缺少 cryptography")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=unavailable):
            with self.assertRaisesRegex(ValueError, "cryptography"):
                self.make_verifier()
            self.assertIs(type(self.module.signing_message(self.statement(), "fixture-authority", "policy-v1")), bytes)

    def test_real_capture_review_record_load_and_roles_revoke(self):
        approval = self.record()
        self.assertFalse(approval["eligible"])
        self.assertFalse(approval["production_eligible"])
        revoke_statement = self.statement("revoke", target_decision_event_id=approval["decision_event_id"])
        self.fixture.assert_no_write(lambda: self.record(revoke_statement, identity="bob"))
        for identity in ("alice", "admin"):
            revoke = self.record(revoke_statement, identity=identity)
            with open_store(self.db, readonly=True) as store:
                loaded = load_human_decision(store, revoke["decision_event_id"], utc_now(), verifier=self.verifier)
                self.assertEqual(loaded["principal"]["principal_id"], identity)

    def test_late_expired_or_revoked_authorization_has_zero_writes(self):
        statement = self.statement()
        for field in ("valid_until", "revoked_at"):
            bundle = copy.deepcopy(self.bundle)
            bundle["policies"][0]["principals"][0][field] = self.at(1)
            verifier = self.make_verifier(bundle)
            with patch("ai_operator.human_review.utc_now", return_value=self.at(2)), \
                    patch("ai_operator.memory.utc_now", return_value=self.at(2)), \
                    patch("ai_operator.signed_review.utc_now", return_value=self.at(2)):
                self.fixture.assert_no_write(lambda: self.record(statement, verifier=verifier))

    def test_multiversion_history_replays_original_received_after_today_revocation(self):
        bundle = copy.deepcopy(self.bundle)
        second = copy.deepcopy(bundle["policies"][0])
        second.update(policy_id="policy-v2", effective_at=self.at(10))
        second["principals"][0]["revoked_at"] = self.at(10)
        bundle["policies"].append(second)
        verifier = self.make_verifier(bundle)
        with patch("ai_operator.human_review.utc_now", return_value=self.at(1)), \
                patch("ai_operator.memory.utc_now", return_value=self.at(1)), \
                patch("ai_operator.signed_review.utc_now", return_value=self.at(1)):
            approval = self.record(verifier=verifier)
        with patch("ai_operator.human_review.utc_now", return_value=self.at(50)), \
                patch("ai_operator.signed_review.utc_now", return_value=self.at(50)):
            with open_store(self.db, readonly=True) as store:
                loaded = load_human_decision(store, approval["decision_event_id"], self.at(1), verifier=verifier)
                self.assertEqual(loaded["principal"]["policy_id"], "policy-v1")
            statement = self.statement("revoke", target_decision_event_id=approval["decision_event_id"])
            with patch("ai_operator.memory.utc_now", return_value=self.at(50)):
                revoke = self.record(statement, identity="admin", policy_id="policy-v2", verifier=verifier)
            with open_store(self.db, readonly=True) as store:
                self.assertEqual(load_human_decision(store, revoke["decision_event_id"], self.at(50),
                                                    verifier=verifier)["principal"]["policy_id"], "policy-v2")

    def test_shared_verifier_concurrent_readonly_load_has_no_writes(self):
        approval = self.record()
        with open_store(self.db, readonly=True) as store:
            before = store.snapshot()

        def load(_):
            with open_store(self.db, readonly=True) as store:
                return load_human_decision(store, approval["decision_event_id"], utc_now(), verifier=self.verifier)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(load, range(12)))
        self.assertTrue(all(result == approval for result in results))
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
