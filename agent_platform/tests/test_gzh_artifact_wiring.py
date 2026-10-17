# -*- coding: utf-8 -*-
"""M3-P2 GZH 产线产物挂接测试（离线 fixture）。

验收门 2（gzh 段）：draft→reviewed 达成时自动登记 md 为 Artifact，
hash 与真源一致；幂等重放/catch-up 零重复；published/打回路径不登记；
md 缺失不伪造空 hash 条目；TaskStore/真源零破坏。
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

gw = _load("gzh_wiring_p2", "agent_platform/domains/gzh_wiring.py")
ApprovalStore = gw.ApprovalStore
ArtifactStore = gw.ArtifactStore


class GzhArtifactWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        drafts = self.root / "gzh" / "drafts"
        drafts.mkdir(parents=True)
        self.md_path = drafts / "2026-10-17-demo.md"
        self.md_path.write_text("# demo\n\n正文内容 v1\n", encoding="utf-8")
        (drafts / "index.json").write_text(json.dumps([{
            "id": "2026-10-17-demo", "title": "演示稿", "md": self.md_path.name,
            "status": "draft", "col": "", "date": "10-17", "ts": "",
        }], ensure_ascii=False), encoding="utf-8")
        self.apv_db = self.root / "approvals.db"
        self.art_db = self.root / "artifacts.db"
        self.apv = ApprovalStore(self.apv_db)      # keeper 连接判例
        self.art = ArtifactStore(self.art_db)

    def tearDown(self):
        self.art.close()
        self.apv.close()

    def _approve(self, to="reviewed", draft_id="2026-10-17-demo"):
        return gw.approve_transition(self.root, draft_id, to, "老田",
                                     reason="测试", store=self.apv,
                                     artifact_store=self.art)

    # ---- 首次批准登记，hash 与真源一致 ---------------------------------
    def test_reviewed_registers_artifact_with_true_hash(self):
        _, _, replayed = self._approve()
        self.assertFalse(replayed)
        arts = self.art.list()
        self.assertEqual(len(arts), 1)
        art = arts[0]
        from contracts.objects import fact_hash_of
        true_hash = fact_hash_of(self.md_path.read_bytes())
        self.assertEqual(art.effective_hash(), true_hash)
        self.assertEqual(art.source_system, "gzh.drafts")
        self.assertEqual(art.source_ref, "2026-10-17-demo")
        self.assertEqual(art.path, "gzh/drafts/2026-10-17-demo.md")
        self.assertEqual(art.revision, "r1")
        # 真源零破坏：index 仍是唯一状态真源
        idx = json.loads((self.root / "gzh" / "drafts" / "index.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(idx[0]["status"], "reviewed")

    # ---- 幂等：重复批准/重放不重复登记 ----------------------------------
    def test_replay_does_not_duplicate(self):
        self._approve()
        _, _, replayed = self._approve()          # 同转移重复批准=重放
        self.assertTrue(replayed)
        self.assertEqual(len(self.art.list()), 1)

    # ---- published 转移不登记（登记点只在 reviewed）--------------------
    def test_published_path_no_new_artifact(self):
        self._approve()
        self._approve(to="published")
        self.assertEqual(len(self.art.list()), 1)  # 仅 reviewed 产物

    # ---- 打回→重批：md 未变=重放；md 变了=新 hash 新行 -----------------
    def test_reject_then_reapprove_with_edited_content(self):
        self._approve()
        # 打回 draft 并修改正文
        self._approve(to="draft")
        self.md_path.write_text("# demo\n\n正文内容 v2（修改后）\n",
                                encoding="utf-8")
        _, _, replayed = self._approve()          # 第二轮 reviewed
        self.assertFalse(replayed)
        arts = self.art.list()
        self.assertEqual(len(arts), 2)            # v1 + v2 两代产物
        chain_root = arts[0]
        self.assertEqual(chain_root.revision, "r1")
        # 两代 hash 互异，且 v2 与真源一致
        from contracts.objects import fact_hash_of
        true_v2 = fact_hash_of(self.md_path.read_bytes())
        hashes = {a.effective_hash() for a in arts}
        self.assertIn(true_v2, hashes)
        self.assertNotEqual(arts[0].effective_hash(), true_v2)

    # ---- md 缺失：跳过登记不伪造 ---------------------------------------
    def test_missing_md_skips_registration(self):
        self.md_path.unlink()
        _, _, replayed = self._approve()
        self.assertFalse(replayed)                # 转移本身照常
        self.assertEqual(len(self.art.list()), 0)  # 不伪造空 hash 条目

    # ---- catch-up 窗口：凭据在真源滞后时补登记 --------------------------
    def test_catchup_registers_artifact(self):
        # 模拟崩溃窗口：凭据已落、真源未消费（无 last_wiring_key 且状态仍 draft）
        self.apv.record(__import__("contracts.objects", fromlist=["Approval"])
                        .Approval(target_type="workitem",
                                  target_id="gzh:2026-10-17-demo",
                                  from_status="draft", to_status="reviewed",
                                  decision="approved", decided_by="老田",
                                  reason="崩溃窗口预置",
                                  idempotency_key="gzh:2026-10-17-demo:draft>reviewed"))
        _, _, replayed = self._approve()
        self.assertTrue(replayed)                 # catch-up
        self.assertEqual(len(self.art.list()), 1)  # 补登记恰好一次


if __name__ == "__main__":
    unittest.main()
