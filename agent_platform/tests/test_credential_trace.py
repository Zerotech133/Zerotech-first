# -*- coding: utf-8 -*-
"""adapters/credential_trace 交叉校验器单元测试（K-M1 审计补强）。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.credential_trace import cross_check, verdict_for  # noqa: E402


class Cred:
    """Approval 轻量替身（duck-type，只暴露校验所需字段）。"""

    def __init__(self, target_id, to_status, key, decision="approved",
                 approval_id="A1", from_status="draft"):
        self.target_id = target_id
        self.to_status = to_status
        self.from_status = from_status
        self.decision = decision
        self.idempotency_key = key
        self.approval_id = approval_id
        self.decided_by = "tester"


def _cred(target, to, key, **kw):
    return Cred(target, to, key, **kw)


class EffectiveTests(unittest.TestCase):
    """生效判定三途径 + 死凭据/崩溃窗口分级。"""

    def test_effective_via_trace_status(self):
        creds = [_cred("d:t1", "reviewed", "k1")]
        v = cross_check("x", creds, {"d:t1": {"statuses": ["draft",
                                                              "reviewed"]}})
        self.assertEqual(v, [])

    def test_effective_via_current_status(self):
        creds = [_cred("d:t1", "reviewed", "k1")]
        v = cross_check("x", creds, {"d:t1": {"statuses": []}},
                        {"d:t1": "reviewed"})
        self.assertEqual(v, [])

    def test_effective_via_consumed_key(self):
        creds = [_cred("d:t1", "reviewed", "k1")]
        v = cross_check("x", creds,
                        {"d:t1": {"statuses": [],
                                  "consumed_keys": {"k1"}}})
        self.assertEqual(v, [])

    def test_dead_credential_not_latest_fail(self):
        """非最新凭据且无任何生效痕迹 = 死凭据（FAIL）。"""
        creds = [_cred("d:t1", "reviewed", "k1", approval_id="A1"),
                 _cred("d:t1", "approved", "k2", approval_id="A2")]
        v = cross_check("x", creds, {"d:t1": {"statuses": []}},
                        {"d:t1": "draft"})
        kinds = {(x["kind"], x["severity"]) for x in v}
        self.assertIn(("credential_never_effective", "FAIL"), kinds)
        # k2 是最新 → 崩溃窗口候选 WARN，不是死凭据
        self.assertIn(("pending_execution", "WARN"), kinds)

    def test_latest_unconsumed_warn_pending(self):
        creds = [_cred("d:t1", "reviewed", "k1")]
        v = cross_check("x", creds, {"d:t1": {"statuses": []}},
                        {"d:t1": "draft"})
        self.assertEqual([(x["kind"], x["severity"]) for x in v],
                         [("pending_execution", "WARN")])


class RoundTests(unittest.TestCase):
    """轮次 vs 痕迹次数。"""

    def test_round_exceeds_trace_warn(self):
        """两轮凭据只有一次痕迹 → round_exceeds_trace（WARN）。"""
        creds = [_cred("d:t1", "reviewed", "k1", approval_id="A1"),
                 _cred("d:t1", "reviewed", "k1#r2", approval_id="A2")]
        v = cross_check("x", creds, {"d:t1": {"statuses": ["reviewed"]}},
                        {"d:t1": "reviewed"})
        self.assertEqual([x["kind"] for x in v], ["round_exceeds_trace"])
        self.assertEqual(v[0]["severity"], "WARN")

    def test_round_match_no_violation(self):
        creds = [_cred("d:t1", "reviewed", "k1"),
                 _cred("d:t1", "reviewed", "k1#r2")]
        v = cross_check("x", creds,
                        {"d:t1": {"statuses": ["reviewed", "reviewed"]}},
                        {"d:t1": "reviewed"})
        self.assertEqual(v, [])

    def test_round_check_skipped_when_no_statuses(self):
        """消费标记域无 statuses 痕迹 → 轮次检查跳过。"""
        creds = [_cred("d:t1", "reviewed", "k1"),
                 _cred("d:t1", "reviewed", "k1#r2")]
        v = cross_check("x", creds, {"d:t1": {"statuses": [],
                                              "consumed_keys": {"k1#r2"}}},
                        {"d:t1": "reviewed"}, latest_only=True)
        self.assertEqual(v, [])


class MarkerTests(unittest.TestCase):
    """消费标记域（latest_only）语义。"""

    def test_marker_without_credential_fail(self):
        """标记引用的键不在账本 = 账本被清/篡改（FAIL）。"""
        v = cross_check("x", [],
                        {"d:t1": {"statuses": [],
                                  "consumed_keys": {"ghost-key"}}})
        self.assertEqual([(x["kind"], x["severity"]) for x in v],
                         [("marker_without_credential", "FAIL")])

    def test_latest_only_skips_old_rounds(self):
        """latest_only：旧轮次凭据标记被覆盖不可判定 → 跳过。"""
        creds = [_cred("d:t1", "reviewed", "k1", approval_id="A1"),
                 _cred("d:t1", "reviewed", "k1#r2", approval_id="A2")]
        v = cross_check("x", creds,
                        {"d:t1": {"statuses": [],
                                  "consumed_keys": {"k1#r2"}}},
                        {"d:t1": "reviewed"}, latest_only=True)
        self.assertEqual(v, [])

    def test_latest_only_unconsumed_pending(self):
        creds = [_cred("d:t1", "reviewed", "k1"),
                 _cred("d:t1", "reviewed", "k1#r2")]
        v = cross_check("x", creds, {"d:t1": {"statuses": []}},
                        {"d:t1": "draft"}, latest_only=True)
        self.assertEqual([(x["kind"], x["severity"]) for x in v],
                         [("pending_execution", "WARN")])


class DecisionTests(unittest.TestCase):
    """rejected 凭据与 verdict 分级。"""

    def test_rejected_credential_skipped(self):
        creds = [_cred("d:t1", "rejected", "k1", decision="rejected")]
        v = cross_check("x", creds, {"d:t1": {"statuses": []}},
                        {"d:t1": "draft"})
        self.assertEqual(v, [])

    def test_verdict_for_severity(self):
        self.assertEqual(verdict_for([]), "PASS")
        self.assertEqual(verdict_for([{"severity": "WARN"}]), "WARN")
        self.assertEqual(verdict_for([{"severity": "WARN"},
                                      {"severity": "FAIL"}]), "FAIL")
        # gate 违规无 severity 字段 → 视为 FAIL
        self.assertEqual(verdict_for([{"kind": "gate"}]), "FAIL")


if __name__ == "__main__":
    unittest.main()
