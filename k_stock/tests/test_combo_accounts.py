# -*- coding: utf-8 -*-
"""[WO-QT-20261021-109] combo_accounts 开户工具测试。

承重语义：
  · 默认 dry-run **不写库**
  · 幂等：已存在则 skip，**不重复注资**（重复注资＝凭空造钱）
  · 只新增 combo@branch 账户与其入金行，**不碰既有账户**
  · 事务：失败整体回滚

纪律：全部用临时 sqlite，不读不写真实 paper.db。
运行：仓根 pytest k_stock/tests/test_combo_accounts.py -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 仓库根
sys.path.insert(0, os.path.join(ROOT, "k_stock", "paper"))

import combo_accounts as ca                            # noqa: E402

SCHEMA = """
CREATE TABLE account (
  account_id TEXT PRIMARY KEY, cash REAL, initial_cash REAL, created_at TEXT);
CREATE TABLE ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, stock TEXT,
  qty REAL, amount REAL, cash_after REAL, order_id TEXT, account_id TEXT,
  trace_id TEXT, fact_hash TEXT, decision_id TEXT, idempotency_key TEXT);
"""


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "paper.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def accounts(self):
        return {r[0] for r in self.conn.execute("SELECT account_id FROM account")}

    def ledger_n(self):
        return self.conn.execute("SELECT count(*) FROM ledger").fetchone()[0]


class TestPlan(_Base):
    def test_plan_covers_combo_branch_product(self):
        plan = ca.build_plan(self.conn, combo_ids=["c1", "c2"],
                             branch_ids=["b1", "b2"])
        self.assertEqual(len(plan), 4)
        self.assertEqual({r["account_id"] for r in plan},
                         {"c1@b1", "c1@b2", "c2@b1", "c2@b2"})
        self.assertTrue(all(r["action"] == "create" for r in plan))

    def test_plan_marks_existing_as_skip(self):
        self.conn.execute("INSERT INTO account VALUES('c1@b1', 5.0, 5.0, 'x')")
        self.conn.commit()
        plan = ca.build_plan(self.conn, combo_ids=["c1"],
                             branch_ids=["b1", "b2"])
        by = {r["account_id"]: r for r in plan}
        self.assertEqual(by["c1@b1"]["action"], "skip")
        self.assertEqual(by["c1@b2"]["action"], "create")

    def test_real_registry_produces_expected_shape(self):
        """真实注册表：3 组合 × 2 分支 = 6 个 combo@branch 账户。"""
        plan = ca.build_plan(self.conn)
        self.assertGreaterEqual(len(plan), 6)
        self.assertTrue(all("@" in r["account_id"] for r in plan))


class TestNoWriteByDefault(_Base):
    def test_main_without_apply_does_not_write(self):
        rc = ca.main(["--db", str(self.db)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.accounts(), set(), "dry-run 不得建账户")
        self.assertEqual(self.ledger_n(), 0, "dry-run 不得写入金流水")


class TestApply(_Base):
    def test_apply_creates_accounts_and_deposits(self):
        plan = ca.build_plan(self.conn, combo_ids=["c1"],
                             branch_ids=["b1", "b2"], initial_cash=1000.0)
        res = ca.apply_plan(self.conn, plan, ts="2026-10-21T20:00:00")
        self.assertEqual(sorted(res["created"]), ["c1@b1", "c1@b2"])
        self.assertEqual(self.accounts(), {"c1@b1", "c1@b2"})
        rows = list(self.conn.execute(
            "SELECT type, amount, cash_after FROM ledger ORDER BY account_id"))
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["type"], "deposit")
            self.assertEqual(float(r["amount"]), 1000.0)
            self.assertEqual(float(r["cash_after"]), 1000.0)

    def test_apply_is_idempotent_no_double_funding(self):
        """核心：重复执行不得重复注资（否则凭空造钱）。"""
        plan = ca.build_plan(self.conn, combo_ids=["c1"], branch_ids=["b1"],
                             initial_cash=1000.0)
        ca.apply_plan(self.conn, plan)
        cash1 = self.conn.execute(
            "SELECT cash FROM account WHERE account_id='c1@b1'").fetchone()[0]
        plan2 = ca.build_plan(self.conn, combo_ids=["c1"], branch_ids=["b1"],
                              initial_cash=1000.0)
        self.assertEqual(plan2[0]["action"], "skip")
        res2 = ca.apply_plan(self.conn, plan2)
        self.assertEqual(res2["created"], [])
        cash2 = self.conn.execute(
            "SELECT cash FROM account WHERE account_id='c1@b1'").fetchone()[0]
        self.assertEqual(float(cash1), float(cash2), "不得重复注资")
        self.assertEqual(self.ledger_n(), 1, "不得重复写入金行")

    def test_existing_accounts_untouched(self):
        self.conn.execute("INSERT INTO account VALUES('main', 140457.1, 1000000.0, 'x')")
        self.conn.commit()
        plan = ca.build_plan(self.conn, combo_ids=["c1"], branch_ids=["b1"])
        ca.apply_plan(self.conn, plan)
        cash = self.conn.execute(
            "SELECT cash FROM account WHERE account_id='main'").fetchone()[0]
        self.assertAlmostEqual(float(cash), 140457.1, "既有账户不得被改动")

    def test_rollback_on_failure(self):
        """中途失败 → 整体回滚，不留半截账户。"""
        plan = ca.build_plan(self.conn, combo_ids=["c1"], branch_ids=["b1"],
                             initial_cash=1000.0)
        self.conn.execute("DROP TABLE ledger")       # 制造失败：入金行写不进
        self.conn.commit()
        with self.assertRaises(Exception):
            ca.apply_plan(self.conn, plan)
        self.assertEqual(self.accounts(), set(), "失败必须回滚，不得留半截")


if __name__ == "__main__":
    unittest.main()
