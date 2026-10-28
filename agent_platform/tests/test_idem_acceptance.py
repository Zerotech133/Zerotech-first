# -*- coding: utf-8 -*-
"""M0 P4 自动化验收：双轨接入 + 死信 + 人工恢复 + 崩溃窗口。

覆盖 docs/audit/2026-10-17_统一幂等语义盘点.md 第五/七节验收用例：
- 死信：claimed 超过 stale_seconds 未回写 → 死信化 + 新 attempt；
- 人工恢复：执行中 attempt 由运维 fail 标记后可重试；
- 崩溃窗口（bridge 语义）：claim 后进程死亡 → 死信 → 重试 → 完成 → 幂等命中；
- Hook fail-open：账本不可用时降级 unavailable，绝不抛异常阻断旧链；
- Hook disabled：IDEMPOTENCY_DISABLED=1 / enabled=False 完全旁路；
- Shadow 语义：failed → retry → done 生命周期经 Hook 完整走通。
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


idy = _load("idempotency_under_test", "agent_platform/engine/idempotency.py")
hook_mod = _load("idem_hook_under_test", "agent_platform/engine/idem_hook.py")

IdempotencyStore = idy.IdempotencyStore
DualTrackHook = hook_mod.DualTrackHook

import time  # noqa: E402


class DeadLetterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "idy.db"
        self.store = IdempotencyStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_stale_claim_dead_lettered_and_new_attempt(self):
        first = self.store.claim(
            "shadow",
            "daily_decision",
            "D1",
            payload={"date": "2026-10-17", "symbol": "600519"},
        )
        self.assertEqual(first["outcome"], "claimed")
        time.sleep(0.02)  # 让 updated_at 产生可测时差
        again = self.store.claim(
            "shadow",
            "daily_decision",
            "D1",
            payload={"date": "2026-10-17", "symbol": "600519"},
            stale_seconds=0.01,
        )
        self.assertEqual(again["outcome"], "claimed")
        self.assertEqual(again["attempt"], 2)
        self.assertEqual(again["dead_lettered"], first["attempt_id"])
        rec = self.store.get("shadow", "daily_decision", "D1")
        self.assertEqual(rec["attempts"][0]["status"], "failed")
        self.assertTrue(
            rec["attempts"][0]["error"].startswith("dead-letter: stale lease")
        )
        self.assertEqual(rec["attempts"][1]["status"], "claimed")
        self.assertEqual(self.store.stats()["dead_letters"], 1)

    def test_fresh_claim_not_dead_lettered(self):
        self.store.claim("shadow", "daily_decision", "D2", payload={"x": 1})
        again = self.store.claim(
            "shadow", "daily_decision", "D2", payload={"x": 1}, stale_seconds=3600
        )
        self.assertEqual(again["outcome"], "in_progress")

    def test_manual_recovery_via_fail(self):
        """人工恢复：执行中 attempt 被运维 fail 标记 → 允许重试。"""
        att = self.store.claim("paper", "ingest_order", "M1", payload={"q": 1})
        manual = self.store.complete(
            att["attempt_id"], ok=False, error="manual: operator recovery"
        )
        self.assertTrue(manual["ok"])
        retry = self.store.claim("paper", "ingest_order", "M1", payload={"q": 1})
        self.assertEqual(retry["outcome"], "claimed")
        self.assertEqual(retry["attempt"], 2)

    def test_bridge_crash_window_recovery(self):
        """崩溃窗口（bridge 语义）：claim 后进程死亡 → 死信重试 → 完成 → 幂等命中。"""
        # 第一次：claim 后"进程崩溃"（不 complete）
        self.store.claim(
            "paper_bridge",
            "ingest_order",
            "pick-600337-2026-10-17",
            payload={"code": "600337", "qty": 200},
        )
        time.sleep(0.02)
        # 第二次运行：死信 → 新 attempt
        second = self.store.claim(
            "paper_bridge",
            "ingest_order",
            "pick-600337-2026-10-17",
            payload={"code": "600337", "qty": 200},
            stale_seconds=0.01,
        )
        self.assertEqual(second["outcome"], "claimed")
        self.assertEqual(second["attempt"], 2)
        # 第二次正常完成
        self.assertTrue(
            self.store.complete(
                second["attempt_id"], ok=True, result_ref="pick-600337-2026-10-17"
            )["ok"]
        )
        # 第三次运行：幂等命中，不再下单
        third = self.store.claim(
            "paper_bridge",
            "ingest_order",
            "pick-600337-2026-10-17",
            payload={"code": "600337", "qty": 200},
            stale_seconds=0.01,
        )
        self.assertEqual(third["outcome"], "completed")
        self.assertEqual(third["result_ref"], "pick-600337-2026-10-17")

    def test_shadow_failed_retry_done_lifecycle(self):
        """Shadow failed → retry → done 生命周期经统一账本完整走通。"""
        k, p = "2026-10-17|000858", {"date": "2026-10-17", "symbol": "000858"}
        a1 = self.store.claim("shadow", "daily_decision", k, payload=p)
        self.store.complete(a1["attempt_id"], ok=False, error="ollama timeout")
        a2 = self.store.claim("shadow", "daily_decision", k, payload=p)
        self.assertEqual(a2["outcome"], "claimed")
        self.assertEqual(a2["attempt"], 2)
        self.store.complete(a2["attempt_id"], ok=True, result_ref="run-abc")
        a3 = self.store.claim("shadow", "daily_decision", k, payload=p)
        self.assertEqual(a3["outcome"], "completed")
        self.assertEqual(a3["result_ref"], "run-abc")


class DualTrackHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fail_open_on_broken_db(self):
        """账本不可用（路径是目录）→ unavailable，不抛异常。"""
        hook = DualTrackHook("t", db_path=self.tmp.name, stale_seconds=3600)
        try:
            claim = hook.claim("op", "k", {"x": 1})
            self.assertEqual(claim["outcome"], "unavailable")
            self.assertIn("error", claim)
            self.assertIsNotNone(hook.last_error)
            # complete 同样 fail-open
            self.assertEqual(
                hook.complete("whatever", ok=True)["outcome"], "unavailable"
            )
        finally:
            hook.close()

    def test_disabled_bypasses_entirely(self):
        db = Path(self.tmp.name) / "idy.db"
        hook = DualTrackHook("t", db_path=db, enabled=False)
        claim = hook.claim("op", "k", {"x": 1})
        self.assertEqual(claim["outcome"], "disabled")
        self.assertIsNone(hook.complete("x", ok=True))
        self.assertFalse(db.exists())  # 完全旁路，不建库

    def test_happy_path_claim_complete(self):
        db = Path(self.tmp.name) / "idy.db"
        hook = DualTrackHook("paper_bridge", db_path=db, stale_seconds=3600)
        try:
            c1 = hook.claim("ingest_order", "K1", {"code": "600337"})
            self.assertEqual(c1["outcome"], "claimed")
            self.assertTrue(
                hook.complete(c1["attempt_id"], ok=True, result_ref="ref-1")["ok"]
            )
            c2 = hook.claim("ingest_order", "K1", {"code": "600337"})
            self.assertEqual(c2["outcome"], "completed")
            self.assertEqual(c2["result_ref"], "ref-1")
        finally:
            hook.close()


class PaperBridgeDualTrackTests(unittest.TestCase):
    """paper_bridge 双轨接入全离线验证：不碰真库、不碰真幂等账本。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp.name)
        self._old_env = {}

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _env(self, **kv):
        for k, v in kv.items():
            self._old_env[k] = os.environ.get(k)
            os.environ[k] = v

    def test_ingest_dual_track_claim_complete_and_replay_skip(self):
        import json

        import sqlite3

        self._env(IDEMPOTENCY_DB=str(self.tmp_path / "idy.db"))
        pb = _load("paper_bridge_under_test", "tools/paper_bridge.py")

        # fixture：1 张 T1 票
        pv = {
            "summary": {"asof": "20261017"},
            "per_code": {
                "600337": {
                    "tier": "T1",
                    "date": "20261017",
                    "name": "美克",
                    "fact_hash": "fh-1",
                    "decision_id": "dc-1",
                    "strategies": ["s1"],
                }
            },
        }
        pv_path = self.tmp_path / "pv.json"
        pv_path.write_text(json.dumps(pv, ensure_ascii=False), encoding="utf-8")
        pb.PV = str(pv_path)
        # 临时 events 库
        events_db = self.tmp_path / "events.db"
        with sqlite3.connect(str(events_db)) as con:
            con.executescript(pb.pdb.SCHEMA)
        pb.pdb.DB = str(events_db)
        # 假 paper CLI：rc=0
        paper_calls = []
        pb._paper = lambda *a, **kw: (paper_calls.append(a), (0, "ok"))[1]
        pb._lookup_close = lambda code: 10.0

        # 第一次 ingest：claim → 落单 → complete
        r1 = pb.ingest()
        self.assertEqual(r1["opened"], ["600337"])
        self.assertEqual(r1["skipped"], [])
        self.assertEqual(len(paper_calls), 1)
        # 幂等账本应有 completed 记录，result_ref=trace_id
        store = IdempotencyStore(self.tmp_path / "idy.db")
        try:
            rec = store.get("paper_bridge", "ingest_order", "pick-600337-20261017")
            self.assertTrue(rec["exists"])
            self.assertEqual(rec["attempts"][-1]["status"], "completed")
            self.assertEqual(rec["attempts"][-1]["result_ref"], "pick-600337-20261017")
        finally:
            store.close()

        # 第二次 ingest：旧链 events 查重权威 → skip；账本无新 attempt
        r2 = pb.ingest()
        self.assertEqual(r2["skipped"], ["600337"])
        self.assertEqual(len(paper_calls), 1)  # 未重复落单

    def test_ingest_conflict_skips_without_order(self):
        import json

        import sqlite3

        self._env(IDEMPOTENCY_DB=str(self.tmp_path / "idy2.db"))
        pb = _load("paper_bridge_under_test2", "tools/paper_bridge.py")
        pv = {
            "summary": {"asof": "20261017"},
            "per_code": {
                "600337": {
                    "tier": "T1",
                    "date": "20261017",
                    "name": "美克",
                    "fact_hash": "fh-1",
                    "decision_id": "dc-1",
                }
            },
        }
        pv_path = self.tmp_path / "pv2.json"
        pv_path.write_text(json.dumps(pv, ensure_ascii=False), encoding="utf-8")
        pb.PV = str(pv_path)
        events_db = self.tmp_path / "events2.db"
        with sqlite3.connect(str(events_db)) as con:
            con.executescript(pb.pdb.SCHEMA)
        pb.pdb.DB = str(events_db)
        pb._paper = lambda *a, **kw: (0, "ok")
        pb._lookup_close = lambda code: 10.0

        # 预置同键不同 payload 的冲突记录（qty 不同 → payload_hash 不一致）
        store = IdempotencyStore(self.tmp_path / "idy2.db")
        try:
            store.claim(
                "paper_bridge",
                "ingest_order",
                "pick-600337-20261017",
                payload={
                    "date": "20261017",
                    "code": "600337",
                    "qty": 999,
                    "fact_hash": "fh-1",
                    "decision_id": "dc-1",
                },
            )
            store.complete(
                _last_attempt(
                    store, "paper_bridge", "ingest_order", "pick-600337-20261017"
                ),
                ok=True,
            )
        finally:
            store.close()

        r = pb.ingest()
        # 冲突 → 跳过且不落单（qty=100 与预置 qty=999 payload 不一致）
        self.assertEqual(r["skipped"], ["600337"])
        self.assertEqual(r["opened"], [])


def _last_attempt(store, scope, operation, key):
    rec = store.get(scope, operation, key)
    return rec["attempts"][-1]["attempt_id"]


if __name__ == "__main__":
    unittest.main()
