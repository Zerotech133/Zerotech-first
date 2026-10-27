# -*- coding: utf-8 -*-
"""M3-P3 发布闸门 content_hash 绑定测试（批 A 发 B 拒绝）。

验收门 3：三类受门转移（gzh published / pitch delivered+approved /
skill approved）Approval 绑当次 content_hash；真源变更后原审批自动
失效（拒绝重放/拒绝在旧批准上交付新内容，要求新一轮）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解判例
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "agent_platform"))

gw = _load("gzh_wiring_p3", "agent_platform/domains/gzh_wiring.py")
pw = _load("pitch_wiring_p3", "agent_platform/domains/pitch_wiring.py")
sw = _load("skill_wiring_p3", "agent_platform/domains/skill_wiring.py")
engine = _load("approvals_engine_p3", "agent_platform/engine/approvals.py")
objects = _load("objects_p3", "agent_platform/contracts/objects.py")
ApprovalStore = engine.ApprovalStore
ArtifactStore = gw.ArtifactStore
Approval = objects.Approval
from contracts.objects import fact_hash_of


class ApprovalContentHashStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ApprovalStore(Path(self.tmp.name) / "a.db")

    def tearDown(self):
        self.store.close()

    def test_content_hash_roundtrip(self):
        ap, rp = self.store.record(Approval(
            target_type="workitem", target_id="gzh:d1",
            from_status="reviewed", to_status="published",
            decided_by="老田", idempotency_key="gzh:d1:reviewed>published",
            content_hash="h-abc"))
        self.assertFalse(rp)
        got = self.store.list(target_id="gzh:d1")[0]
        self.assertEqual(got.content_hash, "h-abc")

    def test_same_key_diff_hash_rejected(self):
        self.store.record(Approval(
            target_type="workitem", target_id="gzh:d1",
            from_status="reviewed", to_status="published",
            decided_by="老田", idempotency_key="k1", content_hash="h1"))
        with self.assertRaises(ValueError) as cm:
            self.store.record(Approval(
                target_type="workitem", target_id="gzh:d1",
                from_status="reviewed", to_status="published",
                decided_by="老田", idempotency_key="k1", content_hash="h2"))
        self.assertIn("content_hash", str(cm.exception))

    def test_legacy_rows_without_hash_replay_clean(self):
        # 旧凭据（无 hash）重放不受影响（存量兼容）
        self.store.record(Approval(
            target_type="task", target_id="dev:T1",
            from_status="doing", to_status="done",
            decided_by="A0", idempotency_key="dev:T1:done"))
        _, rp = self.store.record(Approval(
            target_type="task", target_id="dev:T1",
            from_status="doing", to_status="done",
            decided_by="A0", idempotency_key="dev:T1:done"))
        self.assertTrue(rp)


class GzhPublishGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        drafts = self.root / "gzh" / "drafts"
        drafts.mkdir(parents=True)
        self.md = drafts / "d.md"
        self.md.write_text("# v1", encoding="utf-8")
        (drafts / "index.json").write_text(json.dumps([{
            "id": "d1", "title": "t", "md": "d.md", "status": "draft",
        }], ensure_ascii=False), encoding="utf-8")
        self.apv = ApprovalStore(self.root / "a.db")
        self.art = ArtifactStore(self.root / "art.db")

    def tearDown(self):
        self.art.close()
        self.apv.close()

    def _approve(self, to):
        return gw.approve_transition(self.root, "d1", to, "老田",
                                     store=self.apv, artifact_store=self.art)

    def test_published_credential_binds_hash(self):
        self._approve("reviewed")
        _, _, replayed = self._approve("published")
        self.assertFalse(replayed)
        cred = [c for c in self.apv.list(target_id="gzh:d1")
                if c.to_status == "published"][0]
        self.assertEqual(cred.content_hash,
                         fact_hash_of(self.md.read_bytes()))

    def test_batch_a_deliver_b_rejected(self):
        self._approve("reviewed")
        self._approve("published")
        # 发布后正文被改（批 A 发 B）→ 再次走门必须拒绝
        self.md.write_text("# v2 篡改", encoding="utf-8")
        with self.assertRaises(gw.GzhWiringError) as cm:
            self._approve("published")
        self.assertIn("批 A 发 B", str(cm.exception))

    def test_rollback_reapprove_binds_new_hash(self):
        # 打回重批（新一轮）绑新 hash——正常演进路径不受影响
        self._approve("reviewed")
        self._approve("draft")                      # 打回
        self.md.write_text("# v2 修订", encoding="utf-8")
        _, _, replayed = self._approve("reviewed")  # 新一轮 reviewed
        self.assertFalse(replayed)
        creds = [c for c in self.apv.list(target_id="gzh:d1")
                 if c.to_status == "reviewed"]
        self.assertEqual(len(creds), 2)             # 两轮凭据
        self.assertEqual(creds[-1].content_hash, "")  # reviewed 不绑（published 专属）


class PitchDeliverGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.deck_file = self.root / "deck.pptx"
        self.deck_file.write_bytes(b"deck content v1")
        self._orig = pw.DECKS_FILE
        pw.DECKS_FILE = self.root / "decks.json"
        pw.DECKS_FILE.write_text("[]", encoding="utf-8")
        self.apv = ApprovalStore(self.root / "a.db")
        self.art = ArtifactStore(self.root / "art.db")
        pw.add_deck("deck-1", "d", artifact=str(self.deck_file))
        pw.move_stage("deck-1", "drafted", "COMM1", artifact_store=self.art)
        pw.set_claims("deck-1", [{"text": "t", "kind": "fact"}])
        pw.move_stage("deck-1", "fact_check", "COMM1")
        pw.move_stage("deck-1", "reviewed", "COMM1")

    def tearDown(self):
        pw.DECKS_FILE = self._orig
        self.art.close()
        self.apv.close()

    def test_approved_binds_hash_then_deliver_b_rejected(self):
        deck, _ = pw.approve_transition("deck-1", "approved", "老田",
                                        store=self.apv, artifact_store=self.art)
        cred = [c for c in self.apv.list(target_id="pitch:deck-1")
                if c.to_status == "approved"][0]
        self.assertEqual(cred.content_hash,
                         fact_hash_of(self.deck_file.read_bytes()))
        # 批准后 deck 被改（批 A 发 B）→ delivered 门拒绝
        self.deck_file.write_bytes(b"deck content v2 TAMPERED")
        with self.assertRaises(pw.PitchWiringError) as cm:
            pw.approve_transition("deck-1", "delivered", "老田",
                                  store=self.apv, artifact_store=self.art)
        self.assertIn("批 A 发 B", str(cm.exception))
        # 真源未越门：状态停在 approved
        self.assertEqual(pw.find_deck("deck-1")["status"], "approved")

    def test_clean_deliver_ok(self):
        pw.approve_transition("deck-1", "approved", "老田",
                              store=self.apv, artifact_store=self.art)
        deck, replayed = pw.approve_transition("deck-1", "delivered", "老田",
                                               store=self.apv,
                                               artifact_store=self.art)
        self.assertFalse(replayed)
        self.assertEqual(deck["status"], "delivered")
        cred = [c for c in self.apv.list(target_id="pitch:deck-1")
                if c.to_status == "delivered"][0]
        self.assertEqual(cred.content_hash,
                         fact_hash_of(self.deck_file.read_bytes()))


class SkillApproveGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.apv = ApprovalStore(self.root / "a.db")
        self.elements = dict(
            inputs="i", outputs="o", applies_to="a", not_for="n",
            acceptor="ac", samples="s", version="v1", rollback="r")
        sw.add_skill(self.root, "g1", " gate 测试", "老田", **self.elements)
        sw.move_stage(self.root, "g1", "reviewed", "老田")

    def tearDown(self):
        self.apv.close()

    def test_approve_binds_definition_hash_and_rejects_tamper(self):
        # 批准时点=reviewed 态定义（决策所基于的内容）
        reviewed_hash = sw._skill_content_hash(
            sw._find(sw._load(self.root), "g1"))
        apv, s, replayed = sw.approve(self.root, "g1", "approved", "老田",
                                      store=self.apv)
        self.assertFalse(replayed)
        self.assertEqual(apv.content_hash, reviewed_hash)
        self.assertTrue(apv.content_hash)
        # 批准后 Skill 定义被改动 → 重复批准（重放路径）拒绝
        skills = sw._load(self.root)
        g = sw._find(skills, "g1")
        g["applies_to"] = "被篡改的范围"
        sw._save(self.root, skills)
        with self.assertRaises(sw.SkillWiringError) as cm:
            sw.approve(self.root, "g1", "approved", "老田", store=self.apv)
        self.assertIn("批 A 发 B", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
