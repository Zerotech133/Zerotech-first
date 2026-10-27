# -*- coding: utf-8 -*-
"""M1 回放查询层测试（离线 fixture + 真实只读冒烟）。

覆盖：参数校验 / 按 target 串 approvals 时间线 + 域现状快照 /
幂等键查询 / 零命中 EMPTY / idempotency 缺库 missing_source /
timeline 排序 / 真实仓库只读冒烟。
"""

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


rq = _load("replay_query_under_test", "agent_platform/adapters/replay_query.py")
apv = _load("replay_approvals_under_test", "agent_platform/engine/approvals.py")
objects = _load("replay_objects_under_test", "agent_platform/contracts/objects.py")


class ReplayQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        # 路径注入：approvals → fixture 库；idempotency → 不存在的路径
        self._orig_approvals_db = rq._APPROVALS_DB
        self._orig_idem_db = rq._IDEM_DB
        rq._APPROVALS_DB = self.root / "approvals.db"
        rq._IDEM_DB = self.root / "idempotency" / "idempotency.db"
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        rq._APPROVALS_DB = self._orig_approvals_db
        rq._IDEM_DB = self._orig_idem_db
        self.tmp.cleanup()

    def _mk_approval(self, key, target, frm, to, trace=""):
        ap, _ = self.store.record(
            objects.Approval(
                target_type="workitem",
                target_id=target,
                from_status=frm,
                to_status=to,
                decided_by="laotian",
                reason="测试凭据",
                idempotency_key=key,
                trace_id=trace,
            )
        )
        return ap

    def _mk_gzh_fixture(self, status="reviewed"):
        d = self.root / "gzh" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "a.md").write_text("# t", encoding="utf-8")
        (d / "index.json").write_text(
            json.dumps(
                [
                    {
                        "id": "d1",
                        "date": "2026-10-17",
                        "title": "测试篇",
                        "col": "c",
                        "status": status,
                        "md": "a.md",
                        "ts": "10-17 09:00",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_requires_query_param(self):
        with self.assertRaises(ValueError):
            rq.replay(self.root)

    def test_replay_by_target_hits_approvals_and_state(self):
        self._mk_approval(
            "gzh:d1:draft>reviewed", "gzh:d1", "draft", "reviewed", trace="TR-1"
        )
        self._mk_gzh_fixture(status="reviewed")
        rep = rq.replay(self.root, target_id="gzh:d1")
        self.assertEqual(rep["verdict"], "OK")
        self.assertEqual(len(rep["timeline"]), 1)
        entry = rep["timeline"][0]
        self.assertEqual(entry["source"], "approvals")
        self.assertEqual(entry["kind"], "approval:reviewed")
        self.assertEqual(entry["trace_id"], "TR-1")
        self.assertIsNotNone(rep["current_state"])
        self.assertEqual(rep["current_state"]["status"], "reviewed")
        self.assertFalse(rep["current_state"]["artifact_missing"])

    def test_replay_by_idempotency_key(self):
        self._mk_approval(
            "gzh:d1:reviewed>published", "gzh:d1", "reviewed", "published"
        )
        rep = rq.replay(self.root, idempotency_key="gzh:d1:reviewed>published")
        self.assertEqual(rep["verdict"], "OK")
        self.assertEqual(len(rep["timeline"]), 1)
        self.assertEqual(
            rep["timeline"][0]["idempotency_key"], "gzh:d1:reviewed>published"
        )

    def test_replay_by_trace(self):
        self._mk_approval(
            "gzh:d1:draft>reviewed", "gzh:d1", "draft", "reviewed", trace="TR-9"
        )
        rep = rq.replay(self.root, trace_id="TR-9")
        self.assertEqual(rep["verdict"], "OK")
        self.assertEqual(len(rep["timeline"]), 1)

    def test_zero_hit_is_empty_not_error(self):
        self._mk_approval("gzh:d1:draft>reviewed", "gzh:d1", "draft", "reviewed")
        rep = rq.replay(self.root, trace_id="TR-NOTHING")
        self.assertEqual(rep["verdict"], "EMPTY")
        self.assertEqual(rep["sources"].get("approvals"), "ok")
        self.assertEqual(rep["timeline"], [])

    def test_idem_db_missing_is_not_error(self):
        rep = rq.replay(self.root, idempotency_key="gzh:x:draft>reviewed")
        self.assertEqual(
            rep["sources"].get("idempotency"), "missing_source（双轨起点，账本未建库）"
        )
        self.assertNotIn("error", rep["sources"].values())

    def test_timeline_sorted_by_ts(self):
        rows = [
            {"ts": "2026-10-17T05:00:00", "source": "approvals"},
            {"ts": "2026-10-17T04:00:00", "source": "events:x"},
        ]
        out = sorted(rows, key=rq._sort_key)
        self.assertEqual(out[0]["ts"], "2026-10-17T04:00:00")

    def test_real_repo_read_only_smoke(self):
        if not (ROOT / "gzh" / "drafts" / "index.json").exists():
            self.skipTest("真实仓库数据不在位")
        rep = rq.replay(ROOT, target_id="gzh:2026-10-17-22")
        self.assertTrue(rep["read_only"])
        self.assertIn(rep["verdict"], ("OK", "EMPTY"))
        if rep["verdict"] == "OK":
            self.assertGreaterEqual(len(rep["timeline"]), 1)
            self.assertIsNotNone(rep["current_state"])


if __name__ == "__main__":
    unittest.main()
