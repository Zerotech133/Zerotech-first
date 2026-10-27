# -*- coding: utf-8 -*-
"""M1 路演 PPT 域接线测试（decks 受控状态机）。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解判例⑤
    spec.loader.exec_module(module)
    return module


class PitchWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pw = _load("pitch_wiring", "agent_platform/domains/pitch_wiring.py")
        self.decks = Path(self.tmp.name) / "decks.json"
        self.db = Path(self.tmp.name) / "approvals.db"
        self.pw.DECKS_FILE = self.decks
        self.pw._db_path = lambda: self.db
        self.store = self.pw.ApprovalStore(self.db)

    def tearDown(self):
        self.store.close()

    def _mk(self, deck_id="deck-1", status="reviewed", claims=True):
        self.decks.parent.mkdir(parents=True, exist_ok=True)
        deck = {
            "deck_id": deck_id,
            "title": "涪陵 OPC 试点汇报",
            "audience": "政府协调会",
            "goal": "产业落地口径",
            "status": status,
            "claims": (
                [
                    {"text": "年产 500 台", "kind": "forecast"},
                    {"text": "北理工共建实验室", "kind": "fact"},
                ]
                if claims
                else []
            ),
            "artifact": "",
            "created": "2026-10-17 13:50",
            "history": [],
        }
        self.decks.write_text(json.dumps([deck], ensure_ascii=False), encoding="utf-8")

    # ---- add / projection ----

    def test_add_and_duplicate_rejected(self):
        d = self.pw.add_deck("d1", "标题", audience="政府")
        self.assertEqual(d["status"], "outline")
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.add_deck("d1", "重复")

    def test_projection_claims_counts(self):
        self._mk()
        rep = self.pw.projection()
        self.assertEqual(rep["count"], 1)
        it = rep["decks"][0]
        self.assertEqual(it["status"], "reviewed")
        self.assertEqual(it["claims_by_kind"]["forecast"], 1)
        self.assertEqual(it["claims_by_kind"]["fact"], 1)

    # ---- claims 纪律（架构红线）----

    def test_set_claims_kind_whitelist(self):
        self._mk(status="drafted", claims=False)
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.set_claims("deck-1", [{"text": "x", "kind": "rumor"}])
        d = self.pw.set_claims(
            "deck-1", [{"text": "税收 500-1000 万", "kind": "forecast"}]
        )
        self.assertEqual(len(d["claims"]), 1)

    def test_move_to_fact_check_requires_claims(self):
        self._mk(status="drafted", claims=False)
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.move_stage("deck-1", "fact_check", by="PM1")
        # 零写入
        deck = self.pw.find_deck("deck-1")
        self.assertEqual(deck["status"], "drafted")

    # ---- move_stage ----

    def test_move_forward_and_back(self):
        self._mk()
        d = self.pw.move_stage("deck-1", "fact_check", by="PM1")
        self.assertEqual(d["status"], "fact_check")
        d = self.pw.move_stage("deck-1", "drafted", by="PM1", note="打回")
        self.assertEqual(d["status"], "drafted")
        hist = self.pw.find_deck("deck-1")["history"]
        self.assertEqual(
            [h["action"] for h in hist], ["move:fact_check", "move:drafted"]
        )

    def test_move_skip_rejected(self):
        self._mk()
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.move_stage("deck-1", "approved", by="PM1")

    def test_move_to_gated_rejected(self):
        self._mk()
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.move_stage("deck-1", "approved", by="PM1")

    def test_move_delivered_terminal(self):
        self._mk(status="delivered")
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.move_stage("deck-1", "reviewed", by="PM1")

    # ---- approve_transition（受门）----

    def test_approve_to_approved(self):
        self._mk()
        deck, replayed = self.pw.approve_transition(
            "deck-1", "approved", by="laotian", reason="批准", store=self.store
        )
        self.assertFalse(replayed)
        self.assertEqual(deck["status"], "approved")
        creds = self.store.list()
        self.assertEqual(creds[0].idempotency_key, "pitch:deck-1:reviewed>approved")

    def test_approve_rollback_new_round(self):
        """打回重批 = 新轮次（已消费凭据不复活，#r{n+1}）。

        语义对齐并行线 14:43 轮次化重构（history 痕迹派判例）：
        catch-up 仅限崩溃窗口（凭据落库、history 无 approve 痕迹）；
        打回走自由 move 后再批 = 正常新轮次，replayed=False。
        """
        self._mk()
        deck, replayed = self.pw.approve_transition(
            "deck-1", "approved", by="laotian", store=self.store
        )
        self.assertFalse(replayed)
        self.assertEqual(deck["status"], "approved")
        # 打回（自由 move，不落凭据）后重批 → 新轮次
        self.pw.move_stage("deck-1", "reviewed", by="PM1", note="打回")
        deck, replayed = self.pw.approve_transition(
            "deck-1", "approved", by="laotian", store=self.store
        )
        self.assertFalse(replayed)
        self.assertEqual(deck["status"], "approved")

    def test_approve_skip_rejected(self):
        self._mk(status="drafted")
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.approve_transition(
                "deck-1", "approved", by="laotian", store=self.store
            )

    def test_approve_requires_by(self):
        self._mk()
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.approve_transition("deck-1", "approved", by="", store=self.store)

    # ---- audit_report ----

    def test_audit_consistent(self):
        self._mk()
        self.pw.approve_transition("deck-1", "approved", by="laotian", store=self.store)
        rep = self.pw.audit_report()
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["credentials"][0]["state"], "consistent")

    def test_audit_status_mismatch(self):
        self._mk()
        self.pw.approve_transition("deck-1", "approved", by="laotian", store=self.store)
        # 绕门改动真源状态 → status_mismatch → FAIL
        rows = json.loads(self.decks.read_text(encoding="utf-8"))
        rows[0]["status"] = "reviewed"
        self.decks.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        rep = self.pw.audit_report()
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["violations"][0]["kind"], "status_mismatch")


# ── M1 轮次化幂等键回灌（pitch：打回走自由 move，academy 同款痕迹守卫）──
class PitchRoundKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pw = _load("pitch_wiring_ru", "agent_platform/domains/pitch_wiring.py")
        self.decks = Path(self.tmp.name) / "decks.json"
        self.db = Path(self.tmp.name) / "approvals.db"
        self.pw.DECKS_FILE = self.decks
        self.pw._db_path = lambda: self.db
        self.store = self.pw.ApprovalStore(self.db)
        self._mk()

    def tearDown(self):
        self.store.close()

    def _mk(self, deck_id="deck-1", status="reviewed"):
        self.decks.parent.mkdir(parents=True, exist_ok=True)
        deck = {
            "deck_id": deck_id,
            "title": "复盘",
            "audience": "政府",
            "goal": "口径",
            "status": status,
            "claims": [{"text": "x", "kind": "fact"}],
            "artifact": "",
            "created": "2026-10-17 14:00",
            "history": [],
        }
        self.decks.write_text(json.dumps([deck], ensure_ascii=False), encoding="utf-8")

    def _rewrite(self, deck):
        self.decks.write_text(json.dumps([deck], ensure_ascii=False), encoding="utf-8")

    def _keys(self):
        return [a.idempotency_key for a in self.store.list(target_type="workitem")]

    def test_kickback_then_reapprove_new_round_credential(self):
        d1, r1 = self.pw.approve_transition(
            "deck-1", "approved", "审查A", store=self.store
        )
        self.assertFalse(r1)
        self.assertEqual(d1["status"], "approved")
        # 打回：approved → reviewed（自由 move，不落凭据）
        self.pw.move_stage("deck-1", "reviewed", "老田", note="口径打回")
        # 二轮重批：旧实现会重放第一轮凭据——判例禁止
        d2, r2 = self.pw.approve_transition(
            "deck-1", "approved", "审查C", reason="二轮通过", store=self.store
        )
        self.assertFalse(r2)  # 新凭据，非重放
        self.assertEqual(d2["status"], "approved")
        base = "pitch:deck-1:reviewed>approved"
        keys = self._keys()
        self.assertEqual(keys.count(base), 1)
        self.assertIn("%s#r2" % base, keys)
        self.assertEqual(len(keys), 2)

    def test_reapprove_same_status_rejected_zero_write(self):
        # pitch 域语义：无幂等快路径，已 approved 再批 = 非法转移拒绝
        d1, _ = self.pw.approve_transition("deck-1", "approved", "A", store=self.store)
        with self.assertRaises(self.pw.PitchWiringError):
            self.pw.approve_transition("deck-1", "approved", "A", store=self.store)
        self.assertEqual(len(self._keys()), 1)  # 零新增凭据

    def test_crash_window_catchup_no_new_round(self):
        d1, _ = self.pw.approve_transition("deck-1", "approved", "A", store=self.store)
        # 模拟崩溃窗口：凭据已落库、真源写入未发生（status 回拨 +
        # approve:approved 痕迹移除——_apply 原子性未达成）
        deck = json.loads(self.decks.read_text(encoding="utf-8"))[0]
        deck["status"] = "reviewed"
        deck["history"] = [
            h for h in deck["history"] if h.get("action") != "approve:approved"
        ]
        self._rewrite(deck)
        d2, r2 = self.pw.approve_transition("deck-1", "approved", "A", store=self.store)
        self.assertTrue(r2)
        self.assertEqual(d2["status"], "approved")  # 真源已补齐
        self.assertEqual(len(self._keys()), 1)  # 不新增轮次凭据


if __name__ == "__main__":
    unittest.main()
