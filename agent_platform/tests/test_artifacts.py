# -*- coding: utf-8 -*-
"""ArtifactStore 契约测试（M3-P1：统一产物登记仓）。

验收门 1：去重 / append-only / supersede 链 / 崩溃重放 / 只读模式
/ 无源 ID 不伪造。判例「登记不设门」锁定——record() 无状态校验。
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

engine = _load("artifacts_engine", "agent_platform/engine/artifacts.py")
objects = _load("artifacts_objects", "agent_platform/contracts/objects.py")
Artifact = objects.Artifact
ArtifactStore = engine.ArtifactStore


def _art(
    content_hash="h-aaa",
    path="gzh/drafts/a.md",
    kind="md",
    revision="r1",
    supersedes="",
    source="gzh.drafts",
    produced_by="writer-a",
):
    return Artifact(
        artifact_id="art-" + content_hash,
        kind=kind,
        path=path,
        fact_hash=content_hash,
        content_hash=content_hash,
        revision=revision,
        supersedes=supersedes,
        source_system=source,
        source_ref="2026-10-17-22",
        produced_by=produced_by,
    )


class ArtifactStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "artifacts.db"
        self.store = ArtifactStore(self.db)  # keeper 连接判例

    def tearDown(self):
        self.store.close()

    # ---- 验收门 1：去重 ------------------------------------------------
    def test_same_hash_dedup_reuses_artifact_id(self):
        a1, rp1 = self.store.record(_art())
        a2, rp2 = self.store.record(_art())
        self.assertFalse(rp1)
        self.assertTrue(rp2)
        self.assertEqual(a2.artifact_id, a1.artifact_id)
        self.assertEqual(self.store.stats()["total"], 1)

    def test_hash_required(self):
        with self.assertRaises(ValueError):
            self.store.record(Artifact(kind="md"))  # 无任何指纹

    def test_fact_hash_fallback(self):
        a = Artifact(kind="md", fact_hash="h-fallback")
        got, replayed = self.store.record(a)
        self.assertFalse(replayed)
        self.assertEqual(self.store.by_hash("h-fallback").artifact_id, got.artifact_id)

    # ---- 验收门 1：append-only ----------------------------------------
    def test_append_only_no_update_no_delete(self):
        self.store.record(_art())
        # 重复登记同 hash 不产生新行；不同 hash 才新增
        self.store.record(_art(content_hash="h-bbb"))
        self.assertEqual(self.store.stats()["total"], 2)

    # ---- 验收门 1：supersede 链 ---------------------------------------
    def test_supersede_chain_and_rollback_history(self):
        a1, _ = self.store.record(_art(content_hash="h-v1"))
        a2, _ = self.store.supersede(a1.artifact_id, _art(content_hash="h-v2"))
        a3, _ = self.store.supersede(
            a2.artifact_id, _art(content_hash="h-v3", produced_by="writer-b")
        )
        # 代次递增、指回旧条目
        self.assertEqual((a2.revision, a2.supersedes), ("r2", a1.artifact_id))
        self.assertEqual((a3.revision, a3.supersedes), ("r3", a2.artifact_id))
        # 继承来源与负责人回填
        self.assertEqual(a3.source_system, "gzh.drafts")
        self.assertEqual(a3.produced_by, "writer-b")
        # 旧条目仍在（append-only 可回滚）
        self.assertIsNotNone(self.store.get(a1.artifact_id))
        self.assertEqual(self.store.stats()["superseded_count"], 2)
        # 链回放：旧→新完整
        chain = self.store.chain(a3.artifact_id)
        self.assertEqual(
            [x.artifact_id for x in chain],
            [a1.artifact_id, a2.artifact_id, a3.artifact_id],
        )
        self.assertEqual([x.revision for x in chain], ["r1", "r2", "r3"])

    def test_supersede_unknown_target_refused(self):
        with self.assertRaises(ValueError) as cm:
            self.store.supersede("art-nothing", _art(content_hash="h-x"))
        self.assertIn("无源 ID 不伪造", str(cm.exception))

    def test_supersede_same_content_dedups(self):
        a1, _ = self.store.record(_art(content_hash="h-v1"))
        a2, replayed = self.store.supersede(a1.artifact_id, _art(content_hash="h-v1"))
        self.assertTrue(replayed)  # 内容没变=重放，不产生 r2
        self.assertEqual(a2.artifact_id, a1.artifact_id)
        self.assertEqual(a2.revision, "r1")

    # ---- 崩溃重放 ------------------------------------------------------
    def test_crash_replay_same_result(self):
        a1, rp1 = self.store.record(_art(content_hash="h-crash"))
        # 模拟崩溃后重开库重放同一条
        self.store.close()
        self.store = ArtifactStore(self.db)
        a2, rp2 = self.store.record(_art(content_hash="h-crash"))
        self.assertFalse(rp1)
        self.assertTrue(rp2)
        self.assertEqual(a2.artifact_id, a1.artifact_id)
        self.assertEqual(a2.revision, "r1")

    # ---- 只读模式 ------------------------------------------------------
    def test_read_only_mode_no_side_effects(self):
        self.store.record(_art(content_hash="h-ro"))
        self.store.close()
        ro = ArtifactStore(self.db, read_only=True)
        try:
            self.assertEqual(len(ro.list()), 1)
            chain = ro.chain("art-h-ro")
            self.assertEqual(len(chain), 1)
        finally:
            ro.close()
        # 不存在的库：只读模式必须报错不建库
        with self.assertRaises(FileNotFoundError):
            ArtifactStore(self.db.parent / "nope.db", read_only=True)

    # ---- stats 结构性断言 ----------------------------------------------
    def test_stats_structure(self):
        self.store.record(_art(content_hash="h-s1"))
        self.store.record(_art(content_hash="h-s2", source="pitch.decks"))
        st = self.store.stats()
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["by_source"]["gzh.drafts"], 1)
        self.assertEqual(st["by_source"]["pitch.decks"], 1)
        self.assertEqual(st["by_kind"]["md"], 2)


if __name__ == "__main__":
    unittest.main()
