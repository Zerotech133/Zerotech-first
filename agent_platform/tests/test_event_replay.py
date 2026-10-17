# -*- coding: utf-8 -*-
"""M1-③ 事件回放查询层测试（离线 fixture + 真实仓库只读冒烟）。

覆盖：排序（asc/desc、ts 缺失沉底）/ 白名单过滤 / limit 截断 /
since-until 边界（纯日期 until 含全天）/ 非法参数显式报错 /
fixture 仓（假 portal.db + JSONL 双源）/ 真实仓只读冒烟（存在时）。
"""

import importlib.util
import json
import sqlite3
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


er = _load("event_replay_under_test",
           "agent_platform/adapters/event_replay.py")


def _make_fixture(root: Path) -> None:
    """假仓：portal.db events 表 + agent_platform/data/llm_tool_audit.jsonl。

    ts 一律带时区（event_adapter 规范化为 UTC 存储）；
    naive ts 会被判 None（M0 判例：naive 不补 UTC，宁丢不伪造），
    由 test_naive_ts_sinks_and_excluded 单独固化该行为。
    """
    data_dir = root / "portal" / "data"
    data_dir.mkdir(parents=True)
    con = sqlite3.connect(data_dir / "portal.db")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, "
                "ts TEXT, kind TEXT, actor TEXT, object_id TEXT, source TEXT)")
    con.executemany(
        "INSERT INTO events (ts, kind, actor, object_id, source) VALUES (?,?,?,?,?)",
        [
            # +08:00 本地 → UTC：01:00 / 02:30 / 10:00(16日)
            ("2026-10-17T09:00:00+08:00", "gzh.approve", "m1-probe", "d1", "portal"),
            ("2026-10-17T10:30:00+08:00", "task.claim", "worker-a", "t1", "portal"),
            ("2026-10-16T18:00:00+08:00", "gzh.approve", "m1-probe", "d0", "portal"),
            (None, "unknown.kind", "ghost", "x", "portal"),
        ])
    con.commit()
    con.close()
    audit_dir = root / "agent_platform" / "data"
    audit_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"caller": "worker-b", "action": "tool.call", "ts": "2026-10-17T11:00:00+08:00"},  # UTC 03:00
        {"caller": "worker-b", "action": "tool.call", "ts": "2026-10-17T08:00:00+08:00"},  # UTC 00:00
    ]
    (audit_dir / "llm_tool_audit.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


class EventReplayTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_fixture(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_desc_order_and_ts_missing_sinks(self):
        r = er.build_replay(self.root, limit=100)
        self.assertEqual(r["matched_total"], 6)
        ts_list = [e["ts"] for e in r["events"]]
        # ts=None 沉底；其余降序
        self.assertIsNone(ts_list[-1])
        present = [t for t in ts_list if t]
        self.assertEqual(present, sorted(present, reverse=True))

    def test_asc_order(self):
        r = er.build_replay(self.root, order="asc", limit=100)
        present = [e["ts"] for e in r["events"] if e["ts"]]
        self.assertEqual(present, sorted(present))

    def test_filter_kind(self):
        r = er.build_replay(self.root, kind="gzh.approve", limit=100)
        self.assertEqual(r["matched_total"], 2)
        self.assertTrue(all(e["kind"] == "gzh.approve" for e in r["events"]))
        self.assertIn("portal::gzh.approve", r["timeline"])

    def test_since_until_bounds(self):
        # 纯日期 until 含全天（UTC 语义：事件 ts 规范化为 UTC 存储）：
        # 17 日 UTC 内事件 4 条（01:00/02:30/03:00/00:00）；
        # 16 日 10:00 UTC 与 naive(None) 均被排除。
        r = er.build_replay(self.root, since="2026-10-17",
                            until="2026-10-17", limit=100)
        self.assertEqual(r["matched_total"], 4)

    def test_naive_ts_sinks_and_excluded(self):
        # naive ts → envelope None（M0 判例：不补 UTC）：
        # 无过滤查询时沉底末位；带 since/until 时被时间过滤排除
        r_all = er.build_replay(self.root, limit=100)
        self.assertIsNone(r_all["events"][-1]["ts"])
        r_since = er.build_replay(self.root, since="2026-10-16", limit=100)
        self.assertNotIn("unknown.kind", r_since["timeline"])
        self.assertTrue(all(e["ts"] for e in r_since["events"]))

    def test_limit_and_truncated(self):
        r = er.build_replay(self.root, limit=2)
        self.assertEqual(r["returned"], 2)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["matched_total"], 6)

    def test_invalid_params_visible(self):
        for kwargs in ({"order": "sideways"}, {"limit": 0}, {"limit": 99999},
                       {"since": "not-a-date"}):
            with self.assertRaises(ValueError):
                er.build_replay(self.root, **kwargs)

    def test_empty_repo_fail_visible(self):
        with tempfile.TemporaryDirectory() as empty:
            r = er.build_replay(empty, limit=10)
            self.assertEqual(r["matched_total"], 0)
            missing = [s for s, m in (r["sources"] or {}).items()
                       if not (m or {}).get("exists", False)]
            self.assertTrue(missing)  # 源缺失显式可见
            self.assertFalse(r["events"])

    def test_readonly_fixture_untouched(self):
        before = {p: p.stat().st_mtime for p in self.root.rglob("*") if p.is_file()}
        er.build_replay(self.root, limit=10)
        after = {p: p.stat().st_mtime for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    @unittest.skipUnless((ROOT / "portal" / "data" / "portal.db").exists(),
                         "真实仓不存在时跳过")
    def test_real_repo_smoke(self):
        r = er.build_replay(ROOT, limit=20)
        self.assertGreater(r["matched_total"], 0)
        self.assertFalse(r["source_errors"])  # 真仓 canonical 校验应干净
        sample = r["events"][0]
        for field in ("source", "kind", "event_hash", "provenance"):
            self.assertIn(field, sample)


if __name__ == "__main__":
    unittest.main()
