# -*- coding: utf-8 -*-
"""M3-P4 审计回放扩展测试（tmp 独立仓，全封闭不碰真库）。

覆盖：
- replay_query artifacts 段：登记入时间线 + artifact_chain 回放；
- evidence_collect artifact 域：登记观测入证据仓（幂等重放）；
- idempotency_audit trace 痕迹派聚合：真实仓 PASS 时零违规、
  tmp 隔离仓 not_applicable 声明。
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
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rq = _load("replay_query_under_test_p4",
           "agent_platform/adapters/replay_query.py")
apm = _load("artifacts_engine_p4", "agent_platform/engine/artifacts.py")
from contracts.objects import Artifact  # noqa: E402


class ReplayArtifactsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ap = self.root / "agent_platform"
        (self.ap / "data" / "artifacts").mkdir(parents=True)
        self.db = self.ap / "data" / "artifacts" / "artifacts.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_artifact_in_timeline_and_chain(self):
        store = apm.ArtifactStore(self.db)
        try:
            art = Artifact(kind="md", path="gzh/drafts/x.md",
                           fact_hash="a" * 64, content_hash="a" * 64,
                           source_system="gzh.drafts", source_ref="x",
                           produced_by="老田")
            store.record(art)
        finally:
            store.close()
        rep = rq.replay(self.root, target_id="gzh:x")
        self.assertEqual(rep["verdict"], "OK")
        self.assertEqual(rep["sources"]["artifacts"], "ok")
        kinds = [t["kind"] for t in rep["timeline"]]
        self.assertTrue(any(k.startswith("artifact:") for k in kinds))
        self.assertEqual(len(rep["artifact_chain"]), 1)
        chain = rep["artifact_chain"][0]
        self.assertEqual(chain["artifact_id"], art.artifact_id)
        self.assertEqual(chain["revision"], "r1")
        self.assertEqual(chain["produced_by"], "老田")

    def test_supersede_chain_replay(self):
        store = apm.ArtifactStore(self.db)
        try:
            v1, _ = store.record(Artifact(
                kind="md", path="gzh/drafts/y.md", fact_hash="b" * 64,
                content_hash="b" * 64, source_system="gzh.drafts",
                source_ref="y", produced_by="老田"))
            v2, _ = store.supersede(
                v1.artifact_id,
                Artifact(kind="md", path="gzh/drafts/y.md",
                         fact_hash="c" * 64, content_hash="c" * 64,
                         source_system="gzh.drafts", source_ref="y",
                         produced_by="老田"))
        finally:
            store.close()
        rep = rq.replay(self.root, target_id="gzh:y")
        chain = rep["artifact_chain"]
        self.assertEqual([c["revision"] for c in chain], ["r1", "r2"])
        self.assertEqual(chain[1]["supersedes"], v1.artifact_id)
        self.assertEqual(chain[0]["artifact_id"], v1.artifact_id)

    def test_no_artifacts_db_empty_source(self):
        rep = rq.replay(self.root, target_id="gzh:none")
        self.assertEqual(rep["sources"]["artifacts"], "empty")
        self.assertEqual(rep["artifact_chain"], [])


class EvidenceArtifactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ap = self.root / "agent_platform"
        (self.ap / "data" / "artifacts").mkdir(parents=True)
        self.db = self.ap / "data" / "artifacts" / "artifacts.db"
        store = apm.ArtifactStore(self.db)
        try:
            store.record(Artifact(
                kind="md", path="gzh/drafts/z.md", fact_hash="d" * 64,
                content_hash="d" * 64, source_system="gzh.drafts",
                source_ref="z", produced_by="老田"))
        finally:
            store.close()

    def tearDown(self):
        self._tmp.cleanup()

    def test_collect_artifact_domain(self):
        ec = _load("evidence_collect_under_test_p4",
                   "agent_platform/adapters/evidence_collect.py")
        ev_store_path = self.ap / "data" / "evidence" / "evidence.db"
        from engine.evidence import EvidenceStore
        estore = EvidenceStore(ev_store_path)
        try:
            rep = ec.collect(self.root, domains=["artifact"], store=estore)
            self.assertEqual(rep["domains"]["artifact"]["collected"], 1)
            self.assertEqual(rep["verdict"], "PASS")
            # 重放零新增
            rep2 = ec.collect(self.root, domains=["artifact"], store=estore)
            self.assertEqual(rep2["new"], 0)
            self.assertEqual(rep2["replayed"], 1)
        finally:
            estore.close()

    def test_collect_artifact_missing_db_fail_visible(self):
        ec = _load("evidence_collect_under_test_p4b",
                   "agent_platform/adapters/evidence_collect.py")
        from engine.evidence import EvidenceStore
        estore = EvidenceStore(self.ap / "data" / "evidence" / "evidence.db")
        try:
            rep = ec.collect(self.root / "nonexistent", domains=["artifact"],
                             store=estore)
            self.assertEqual(rep["domains"]["artifact"]["collected"], 0)
            self.assertTrue(rep["findings"])   # fail-visible
        finally:
            estore.close()


class TraceAggregationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tmp_root_not_applicable(self):
        ida = _load("idempotency_audit_under_test_p4",
                    "agent_platform/adapters/idempotency_audit.py")
        tr = ida._audit_credential_trace(self.root)
        self.assertEqual(tr["trace_style_domains"], "not_applicable")
        self.assertEqual(tr["pending_domains"], [])
        self.assertEqual(tr["status"], "ok")


if __name__ == "__main__":
    unittest.main()
