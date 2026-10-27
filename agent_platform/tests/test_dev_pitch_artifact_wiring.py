# -*- coding: utf-8 -*-
"""M3-P2 dev/pitch 产线产物挂接测试（离线 fixture）。

dev：close_done 登记 closure_note 产物（kind=closure_note，hash 与
note 一致；catch-up 补登记；重放零重复；TaskStore 零改动）。
pitch：drafted/delivered 登记 deck 产物文件（存在才登记不伪造；
未指定 artifact 跳过；同内容重放零重复）。
"""

from __future__ import annotations

import importlib.util
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

dw = _load("dev_wiring_p2", "agent_platform/domains/dev_wiring.py")
pw = _load("pitch_wiring_p2", "agent_platform/domains/pitch_wiring.py")
ApprovalStore = dw.ApprovalStore
ArtifactStore = dw.ArtifactStore
TaskStore = dw.TaskStore


class DevArtifactWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks_base = self.root / "tasks"
        self.apv = ApprovalStore(self.root / "approvals.db")  # keeper 判例
        self.art = ArtifactStore(self.root / "artifacts.db")
        ts = TaskStore(self.tasks_base)
        self.task_id = ts.open(
            "dev", "演示任务", "body", severity="P2", opened_by="A0"
        )  # 返回 task_id 字符串
        ts.move(self.task_id, "assigned", "A0")
        ts.move(self.task_id, "doing", "A0")

    def tearDown(self):
        self.art.close()
        self.apv.close()

    def test_done_registers_closure_artifact(self):
        note = "验收通过：E2E 全绿"
        task, replayed = dw.close_done(
            self.task_id,
            "dept/dev/laotian",
            note,
            store=self.apv,
            tasks_base=self.tasks_base,
            artifact_store=self.art,
        )
        self.assertFalse(replayed)
        self.assertEqual(task["status"], "done")
        arts = self.art.list()
        self.assertEqual(len(arts), 1)
        art = arts[0]
        from contracts.objects import fact_hash_of

        self.assertEqual(art.effective_hash(), fact_hash_of(note.encode("utf-8")))
        self.assertEqual(art.kind, "closure_note")
        self.assertEqual(art.source_system, "dev.tasks")
        self.assertEqual(art.source_ref, self.task_id)
        self.assertEqual(art.produced_by, "dept/dev/laotian")

    def test_done_replay_no_duplicate(self):
        dw.close_done(
            self.task_id,
            "dept/dev/laotian",
            "note-v1",
            store=self.apv,
            tasks_base=self.tasks_base,
            artifact_store=self.art,
        )
        task, replayed = dw.close_done(
            self.task_id,
            "dept/dev/laotian",
            "note-v1",
            store=self.apv,
            tasks_base=self.tasks_base,
            artifact_store=self.art,
        )
        self.assertTrue(replayed)
        self.assertEqual(len(self.art.list()), 1)

    def test_taskstore_untouched_shape(self):
        # TaskStore 零改动判例：账本仍由 TaskStore 独占写入（append-only）
        dw.close_done(
            self.task_id,
            "dept/dev/laotian",
            "n",
            store=self.apv,
            tasks_base=self.tasks_base,
            artifact_store=self.art,
        )
        files = list(self.tasks_base.glob("*.jsonl"))
        self.assertTrue(files)


class PitchArtifactWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.deck_file = self.root / "decks.json"
        self.deck_file.write_text("[]", encoding="utf-8")
        self._orig = pw.DECKS_FILE
        pw.DECKS_FILE = self.deck_file  # 模块级变量即注入点
        self.deck_path = self.root / "demo-deck.pptx"
        self.deck_path.write_bytes(b"PK\x03\x04 fake pptx content v1")
        self.art = ArtifactStore(self.root / "artifacts.db")
        self.apv = ApprovalStore(self.root / "approvals.db")
        # artifact 用绝对路径（相对路径按仓库根解析，tmp fixture 不可达）
        pw.add_deck("deck-1", "演示 deck", artifact=str(self.deck_path))

    def tearDown(self):
        pw.DECKS_FILE = self._orig
        self.art.close()
        self.apv.close()

    def test_drafted_registers_deck_artifact(self):
        pw.move_stage("deck-1", "drafted", "COMM1", artifact_store=self.art)
        arts = self.art.list()
        self.assertEqual(len(arts), 1)
        from contracts.objects import fact_hash_of

        art = arts[0]
        self.assertEqual(
            art.effective_hash(), fact_hash_of(self.deck_path.read_bytes())
        )
        self.assertEqual(art.kind, "pptx")
        self.assertEqual(art.source_system, "pitch.decks")
        self.assertEqual(art.source_ref, "deck-1")

    def test_delivered_registers_no_duplicate(self):
        pw.move_stage("deck-1", "drafted", "COMM1", artifact_store=self.art)
        pw.set_claims("deck-1", [{"text": "目标客户 3 家", "kind": "fact"}])
        pw.move_stage("deck-1", "fact_check", "COMM1")
        pw.move_stage("deck-1", "reviewed", "COMM1")
        pw.approve_transition(
            "deck-1", "approved", "老田", store=self.apv, artifact_store=self.art
        )
        deck, replayed = pw.approve_transition(
            "deck-1", "delivered", "老田", store=self.apv, artifact_store=self.art
        )
        self.assertFalse(replayed)
        self.assertEqual(deck["status"], "delivered")
        self.assertEqual(len(self.art.list()), 1)  # drafted+delivered 同内容=去重
        # delivered 终态冻结：重复批准=终态拒绝（非重放），产物零重复
        with self.assertRaises(pw.PitchWiringError):
            pw.approve_transition(
                "deck-1", "delivered", "老田", store=self.apv, artifact_store=self.art
            )
        self.assertEqual(len(self.art.list()), 1)

    def test_no_artifact_file_skips_registration(self):
        pw.add_deck("deck-2", "无产物 deck")
        pw.move_stage("deck-2", "drafted", "COMM1", artifact_store=self.art)
        self.assertEqual(len(self.art.list()), 0)  # 不伪造

    def test_empty_ledger_fail_visible(self):
        # 绿地域空集：无 deck 时 register 显式报错（不静默）
        with self.assertRaises(pw.PitchWiringError):
            pw.register_deck_artifact("deck-ghost", artifact_store=self.art)


if __name__ == "__main__":
    unittest.main()
