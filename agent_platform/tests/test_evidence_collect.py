"""M2-P1 证据仓 + 六域收编测试。"""

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
    sys.modules[name] = module          # dataclass 字符串注解判例
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "agent_platform"))

ec = _load("evc", "agent_platform/adapters/evidence_collect.py")
ev_engine = _load("ev_engine", "agent_platform/engine/evidence.py")
from contracts import Evidence  # noqa: E402
from engine.tasks import TaskStore      # noqa: E402


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "evidence.db"
        self.store = ev_engine.EvidenceStore(self.db)  # keeper 连接判例

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _ev(self, statement="status=draft md_sha256=abc", ref="gzh:d1"):
        return Evidence(source="gzh.drafts", source_ref=ref,
                        statement=statement,
                        payload={"domain": "gzh",
                                 "kind": "artifact_fingerprint"})

    def test_record_and_replay(self):
        ev, replayed = self.store.record(self._ev())
        self.assertFalse(replayed)
        ev2, replayed2 = self.store.record(self._ev())
        self.assertTrue(replayed2)
        self.assertEqual(ev2.evidence_id, ev.evidence_id)  # 幂等返回原行
        self.assertEqual(self.store.stats()["total"], 1)

    def test_source_change_appends_new_row(self):
        self.store.record(self._ev("status=draft md_sha256=abc"))
        self.store.record(self._ev("status=reviewed md_sha256=abc"))
        self.assertEqual(self.store.stats()["total"], 2)  # append-only
        kinds = [e.statement for e in self.store.list()]
        self.assertEqual(kinds, ["status=draft md_sha256=abc",
                                 "status=reviewed md_sha256=abc"])

    def test_different_ref_same_statement_distinct(self):
        self.store.record(self._ev(ref="gzh:d1"))
        self.store.record(self._ev(ref="gzh:d2"))
        self.assertEqual(self.store.stats()["total"], 2)

    def test_payload_roundtrip(self):
        self.store.record(self._ev())
        ev = self.store.list()[0]
        self.assertEqual(ev.payload["domain"], "gzh")
        self.assertIn("fact_hash", ev.payload)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "ev.db"
        self.store = ev_engine.EvidenceStore(self.db)
        # mini 真源
        (self.root / "gzh" / "drafts").mkdir(parents=True)
        (self.root / "gzh" / "drafts" / "a.md").write_text("hello",
                                                           encoding="utf-8")
        (self.root / "gzh" / "drafts" / "index.json").write_text(
            json.dumps([{"id": "d1", "md": "a.md", "status": "draft",
                         "title": "测试"}]), encoding="utf-8")
        ac = self.root / "agent_platform" / "data" / "academy"
        ac.mkdir(parents=True)
        (ac / "research_topics.json").write_text(
            json.dumps([{"topic_id": "T1", "status": "intake",
                         "history": [{"at": "x"}]}]), encoding="utf-8")
        pe = self.root / "agent_platform" / "data"
        (pe / "schedules.json").write_text(
            json.dumps([{"id": "s1", "enabled": True, "title": "晨报"}]),
            encoding="utf-8")
        TaskStore(pe / "org" / "tasks").open("dev", "测试任务", "内容",
                                             severity="P2", opened_by="A0")
        iv = pe / "invest"
        iv.mkdir()
        (iv / "runs_index.json").write_text("[]", encoding="utf-8")
        sh = pe / "shadow"
        sh.mkdir()
        (sh / "shadow_ledger.jsonl").write_text(
            json.dumps({"date": "2026-10-14", "symbol": "600519",
                        "status": "done", "run_id": "r1",
                        "recommendation": "BUY", "confidence": 0.65,
                        "parse_ok": True, "degraded": False,
                        "price_at_decision": 1286.0}) + "\n" +
            json.dumps({"date": "2026-10-14", "symbol": "000858",
                        "status": "done", "run_id": "r2",
                        "recommendation": "HOLD", "confidence": 0.62,
                        "parse_ok": True, "degraded": False,
                        "price_at_decision": 70.82}) + "\n",
            encoding="utf-8")
        # pitch：绿地域不建账本 → fail-visible 空收编

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_collect_all_domains(self):
        rep = ec.collect(self.root, store=self.store)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["findings"], [])
        self.assertEqual(rep["domains"]["gzh"]["collected"], 1)
        self.assertEqual(rep["domains"]["academy"]["collected"], 1)
        self.assertEqual(rep["domains"]["personal"]["collected"], 1)
        self.assertEqual(rep["domains"]["dev"]["collected"], 1)
        self.assertEqual(rep["domains"]["invest"]["collected"], 0)
        self.assertEqual(rep["domains"]["pitch"]["collected"], 0)
        self.assertEqual(rep["domains"]["shadow"]["collected"], 2)
        self.assertEqual(rep["new"], 6)

    def test_collect_idempotent_rerun(self):
        ec.collect(self.root, store=self.store)
        rep2 = ec.collect(self.root, store=self.store)
        self.assertEqual(rep2["new"], 0)
        self.assertEqual(rep2["replayed"], 6)

    def test_collect_detects_change_append_only(self):
        ec.collect(self.root, store=self.store)
        idx = self.root / "gzh" / "drafts" / "index.json"
        data = json.loads(idx.read_text(encoding="utf-8"))
        data[0]["status"] = "reviewed"
        idx.write_text(json.dumps(data), encoding="utf-8")
        rep2 = ec.collect(self.root, store=self.store)
        self.assertEqual(rep2["domains"]["gzh"]["new"], 1)  # 新观测行
        self.assertEqual(self.store.stats()["by_domain"]["gzh"], 2)

    def test_missing_source_fail_visible(self):
        (self.root / "agent_platform" / "data" / "schedules.json").unlink()
        rep = ec.collect(self.root, domains=["personal"], store=self.store)
        self.assertEqual(rep["domains"]["personal"]["collected"], 0)
        self.assertTrue(any("schedules.json 缺失" in f
                            for f in rep["findings"]))  # 不伪造空证据

    def test_shadow_bad_line_fail_visible(self):
        """坏行 fail-visible 记 finding，不伪造、不静默吞。"""
        p = self.root / "agent_platform" / "data" / "shadow" / \
            "shadow_ledger.jsonl"
        p.write_text(p.read_text(encoding="utf-8") + "{broken\n",
                     encoding="utf-8")
        rep = ec.collect(self.root, domains=["shadow"], store=self.store)
        self.assertEqual(rep["domains"]["shadow"]["collected"], 2)
        self.assertTrue(any("第 3 行非法 JSON" in f
                            for f in rep["findings"]))

    def test_shadow_missing_fail_visible(self):
        """账本缺失如实报告，不伪造空轨迹。"""
        (self.root / "agent_platform" / "data" / "shadow"
         / "shadow_ledger.jsonl").unlink()
        rep = ec.collect(self.root, domains=["shadow"], store=self.store)
        self.assertEqual(rep["domains"]["shadow"]["collected"], 0)
        self.assertTrue(any("shadow_ledger.jsonl 缺失" in f
                            for f in rep["findings"]))

    def test_shadow_trajectory_kind_and_ref(self):
        rep = ec.collect(self.root, domains=["shadow"], store=self.store)
        self.assertEqual(rep["new"], 2)
        rows = {e.source_ref: e for e in self.store.list()}
        ev = rows["shadow:2026-10-14:600519"]
        self.assertEqual(ev.payload["kind"], "shadow_trajectory")
        self.assertEqual(ev.payload["run_id"], "r1")
        self.assertIn("rec=BUY", ev.statement)
        self.assertIn("parse_ok=True", ev.statement)

    def test_real_repo_read_only(self):
        """真实仓库收编 dry-run（真源零写入，写入仅内存 store）。"""
        before = {}
        for rel in ["gzh/drafts/index.json",
                    "agent_platform/data/schedules.json"]:
            p = ROOT / rel
            before[rel] = p.stat().st_mtime_ns
        rep = ec.collect(ROOT, store=self.store)  # 注入临时 store
        self.assertEqual(rep["verdict"], "PASS")
        for rel, mtime in before.items():
            self.assertEqual((ROOT / rel).stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
