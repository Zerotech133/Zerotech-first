# -*- coding: utf-8 -*-
"""M1-阶段二 Approval 注册表只读数据面测试（离线 fixture + 真实仓冒烟）。

覆盖：ro 直读（不触发建表）/ 过滤 / limit 截断 / 排序 / 库缺失
fail-visible / 只读 mtime 断言 / 真仓冒烟。
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DDL = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id     TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    decision        TEXT NOT NULL,
    decided_by      TEXT,
    reason          TEXT,
    decided_at      TEXT,
    schema_version  INTEGER,
    created_at      TEXT,
    trace_id        TEXT NOT NULL DEFAULT ''
)
"""


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reg = _load("approvals_registry_under_test",
            "agent_platform/adapters/approvals_registry.py")


def _make_fixture(root: Path, rows=None) -> Path:
    db_dir = root / "agent_platform" / "data" / "approvals"
    db_dir.mkdir(parents=True)
    db = db_dir / "approvals.db"
    con = sqlite3.connect(db)
    con.execute(DDL)
    if rows is None:
        rows = [
            ("a1", "gzh:d1:reviewed", "workitem", "gzh:d1", "draft", "reviewed",
             "approved", "reviewer-a", "审查通过", "2026-10-17T04:53:34+00:00", 1, None, ""),
            ("a2", "gzh:d1:draft", "workitem", "gzh:d1", "reviewed", "draft",
             "approved", "reviewer-a", "打回原态", "2026-10-17T04:54:02+00:00", 1, None, ""),
            ("a3", "academy:c1:running", "workitem", "academy:c1", "planned", "running",
             "approved", "admin", "开课", "2026-10-16T08:00:00+00:00", 1, None, ""),
            ("a4", "gzh:d2:published", "workitem", "gzh:d2", "reviewed", "published",
             "rejected", "admin", "内容未达标", "2026-10-17T02:00:00+00:00", 1, None, "tr-x1"),
        ]
    con.executemany(
        "INSERT INTO approvals (approval_id, idempotency_key, target_type, target_id,"
        " from_status, to_status, decision, decided_by, reason, decided_at,"
        " schema_version, created_at, trace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    con.commit()
    con.close()
    return db


class ApprovalsRegistryTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = _make_fixture(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_desc_and_fields(self):
        r = reg.build_registry(self.root, limit=100)
        self.assertEqual(r["total"], 4)
        self.assertEqual([x["approval_id"] for x in r["records"]],
                         ["a2", "a1", "a4", "a3"])  # decided_at desc
        for field in ("approval_id", "idempotency_key", "from_status",
                      "to_status", "decision", "decided_by", "reason", "trace_id"):
            self.assertIn(field, r["records"][0])

    def test_filter_target_id(self):
        r = reg.build_registry(self.root, target_id="gzh:d1", limit=100)
        self.assertEqual(r["total"], 2)
        self.assertTrue(all(x["target_id"] == "gzh:d1" for x in r["records"]))

    def test_filter_decided_by(self):
        r = reg.build_registry(self.root, decided_by="admin", limit=100)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["by_decision"], {"rejected": 1, "approved": 1})

    def test_limit_and_truncated(self):
        r = reg.build_registry(self.root, limit=2)
        self.assertEqual(r["returned"], 2)
        self.assertTrue(r["truncated"])

    def test_asc_order(self):
        r = reg.build_registry(self.root, order="asc", limit=100)
        self.assertEqual([x["approval_id"] for x in r["records"]],
                         ["a3", "a4", "a1", "a2"])

    def test_invalid_params(self):
        for kwargs in ({"order": "sideways"}, {"limit": 0}, {"limit": 99999}):
            with self.assertRaises(ValueError):
                reg.build_registry(self.root, **kwargs)

    def test_missing_db_fail_visible(self):
        with tempfile.TemporaryDirectory() as empty:
            r = reg.build_registry(empty, limit=10)
            self.assertEqual(r["total"], 0)
            self.assertFalse(r["source"]["exists"])
            self.assertEqual(r["errors"][0]["kind"], "missing_source")

    def test_readonly_untouched(self):
        before = {p: p.stat().st_mtime for p in self.root.rglob("*") if p.is_file()}
        reg.build_registry(self.root, limit=10)
        after = {p: p.stat().st_mtime for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    @unittest.skipUnless((ROOT / "agent_platform" / "data" / "approvals"
                          / "approvals.db").exists(), "真实账本不存在时跳过")
    def test_real_repo_smoke(self):
        r = reg.build_registry(ROOT, limit=10)
        self.assertGreater(r["total"], 0)
        self.assertFalse(r["errors"])
        sample = r["records"][0]
        # 最新凭据带域前缀即可（域随真实批准动态变化，不硬编码）：
        # 前缀集 = 平台已接线域（gzh/academy/invest/dev/devtask/pitch/personal）
        # devtask_l2: —— tools/devtask.py:528 的 L2 豁免凭据（target_type=
        # devtask_l2_exemption）。实现新增了该域而白名单没跟上，测试会把
        # 「新登记的合法凭据」误报成未知域（2026-10-22 实测红）。
        prefixes = ("gzh:", "academy:", "invest:", "dev:", "devtask:", "devtask_l2:",
                    "pitch:", "personal:")
        self.assertTrue(sample["idempotency_key"].startswith(prefixes),
                        "最新凭据无已知域前缀: %r"
                        % sample["idempotency_key"])


if __name__ == "__main__":
    unittest.main()
