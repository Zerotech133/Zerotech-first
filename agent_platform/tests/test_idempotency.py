# -*- coding: utf-8 -*-
"""统一 IdempotencyRecord 与只读审计器测试（M0 P3）。

覆盖 docs/audit/2026-10-17_统一幂等语义盘点.md 第五节验收用例中
可在离线环境验证的核心语义：
- 同键同 payload → 返回首次结果；
- 同键不同 payload → conflict；
- failed → retry → 新 attempt（不覆盖历史）；
- completed → 不重试；
- 执行中 → 稳定 in_progress（并发不抛唯一约束异常）；
- 终态 attempt 拒绝覆盖；
- payload NaN 拒绝；
- 只读审计器：Paper 重复 ref / 空 ref / 跨账户冲突、Shadow 重复 done、
  parse_ok=false done、缺失数据源显式报告。
"""

import concurrent.futures
import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


idy = _load("idempotency_under_test", "agent_platform/engine/idempotency.py")
audit = _load(
    "idempotency_audit_under_test", "agent_platform/adapters/idempotency_audit.py"
)

IdempotencyStore = idy.IdempotencyStore


class IdempotencyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = IdempotencyStore(Path(self.tmp.name) / "idy.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_same_key_same_payload_returns_first_result(self):
        first = self.store.claim("paper", "order", "K1", payload={"q": 100})
        self.assertEqual(first["outcome"], "claimed")
        self.assertEqual(first["attempt"], 1)
        done = self.store.complete(first["attempt_id"], result_ref="order-1")
        self.assertTrue(done["ok"])

        again = self.store.claim("paper", "order", "K1", payload={"q": 100})
        self.assertEqual(again["outcome"], "completed")
        self.assertEqual(again["result_ref"], "order-1")
        self.assertEqual(again["attempt"], 1)
        self.assertEqual(len(self.store.get("paper", "order", "K1")["attempts"]), 1)

    def test_same_key_different_payload_is_conflict_and_sticky(self):
        self.store.claim("paper", "order", "K2", payload={"q": 1})
        conflict = self.store.claim("paper", "order", "K2", payload={"q": 2})
        self.assertEqual(conflict["outcome"], "conflict")
        self.assertIn("payload_hash mismatch", conflict["conflict_reason"])
        # 粘性：即使 payload 又换回其它值，仍是 conflict
        sticky = self.store.claim("paper", "order", "K2", payload={"q": 3})
        self.assertEqual(sticky["outcome"], "conflict")
        rec = self.store.get("paper", "order", "K2")
        self.assertEqual(rec["status"], "conflict")
        self.assertGreaterEqual(len(rec["conflicts"]), 1)

    def test_failed_then_retry_creates_new_attempt_without_overwrite(self):
        first = self.store.claim("shadow", "decide", "S1", payload={"d": 1})
        self.store.complete(first["attempt_id"], ok=False, error="boom")
        retry = self.store.claim("shadow", "decide", "S1", payload={"d": 1})
        self.assertEqual(retry["outcome"], "claimed")
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(retry["retry_of"], first["attempt_id"])
        rec = self.store.get("shadow", "decide", "S1")
        self.assertEqual([a["status"] for a in rec["attempts"]], ["failed", "claimed"])
        # 第二次成功后不再重试
        self.store.complete(retry["attempt_id"], result_ref="dec-1")
        final = self.store.claim("shadow", "decide", "S1", payload={"d": 1})
        self.assertEqual(final["outcome"], "completed")
        self.assertEqual(final["result_ref"], "dec-1")

    def test_in_progress_is_stable_skip(self):
        self.store.claim("paper", "order", "K3", payload={"q": 5})
        second = self.store.claim("paper", "order", "K3", payload={"q": 5})
        self.assertEqual(second["outcome"], "in_progress")
        self.assertEqual(second["attempt"], 1)

    def test_concurrent_claims_no_exception_single_winner(self):
        db_path = self.store.db_path
        barrier = threading.Barrier(4)
        results = []

        def worker(i):
            # 每线程独立连接（模拟多进程/多连接真实竞争）
            store = IdempotencyStore(db_path)
            try:
                barrier.wait()
                return store.claim("paper", "order", "KR", payload={"w": 1})
            finally:
                store.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(4)]
            for fut in futures:
                results.append(fut.result())

        outcomes = [r["outcome"] for r in results]
        # 不抛异常；恰好一个 claimed，其余为稳定 in_progress
        self.assertEqual(outcomes.count("claimed"), 1)
        self.assertEqual(outcomes.count("in_progress"), 3)

    def test_complete_refuses_terminal_overwrite(self):
        att = self.store.claim("paper", "order", "K4", payload={"q": 9})
        self.store.complete(att["attempt_id"], result_ref="r1")
        again = self.store.complete(att["attempt_id"], result_ref="r2")
        self.assertFalse(again["ok"])
        self.assertIn("refuse overwrite", again["error"])

    def test_nan_payload_rejected(self):
        with self.assertRaises(ValueError):
            self.store.claim("paper", "order", "K5", payload={"q": float("nan")})

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            self.store.claim("paper", "order", "")


class IdempotencyAuditTests(unittest.TestCase):
    def test_missing_sources_reported_read_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            report = audit.build_report(root)
            self.assertTrue(report["read_only"])
            self.assertEqual(report["sources"]["paper_db"]["status"], "missing_source")
            self.assertEqual(
                report["sources"]["shadow_ledger"]["status"], "missing_source"
            )
            self.assertFalse((root / "k_stock" / "data" / "paper.db").exists())
            self.assertEqual(report["blockers"], [])

    def test_paper_dup_and_empty_refs_detected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "paper.db"
            con = sqlite3.connect(str(db))
            con.executescript(
                """
                CREATE TABLE orders (id INTEGER PRIMARY KEY, ref TEXT, account_id TEXT);
                INSERT INTO orders (ref, account_id) VALUES ('', 'main');
                INSERT INTO orders (ref, account_id) VALUES ('', 'main');
                INSERT INTO orders (ref, account_id) VALUES ('run1#600000', 'main');
                INSERT INTO orders (ref, account_id) VALUES ('run1#600000', 'taishan');
                """
            )
            con.commit()
            con.close()
            report = audit.build_report(Path(tmp), paper_db=db)
            paper = report["sources"]["paper_db"]
            self.assertEqual(paper["status"], "ok")
            self.assertEqual(paper["orders_total"], 4)
            self.assertEqual(paper["empty_ref_count"], 2)
            self.assertEqual(paper["duplicate_ref_count"], 1)
            self.assertEqual(paper["cross_account_ref_count"], 1)
            self.assertEqual(len(report["blockers"]), 2)

    def test_shadow_ledger_dup_done_and_parse_fail(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            ledger = root / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            rows = [
                {
                    "date": "2026-10-17",
                    "symbol": "600337",
                    "status": "done",
                    "parse_ok": True,
                },
                {
                    "date": "2026-10-17",
                    "symbol": "600337",
                    "status": "done",
                    "parse_ok": True,
                },
                {
                    "date": "2026-10-17",
                    "symbol": "000001",
                    "status": "done",
                    "parse_ok": False,
                },
                {"date": "2026-10-17", "symbol": "000002", "status": "failed"},
                "not-json",
            ]
            ledger.write_text(
                "\n".join(
                    r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
                    for r in rows
                )
                + "\n",
                encoding="utf-8",
            )
            report = audit.build_report(root)
            shadow = report["sources"]["shadow_ledger"]
            self.assertEqual(shadow["status"], "ok")
            self.assertEqual(shadow["total"], 4)
            self.assertEqual(shadow["bad_lines"], 1)
            self.assertEqual(shadow["duplicate_done_pair_count"], 1)
            self.assertEqual(shadow["parse_fail_done_count"], 1)
            self.assertEqual(len(report["blockers"]), 2)


if __name__ == "__main__":
    unittest.main()
