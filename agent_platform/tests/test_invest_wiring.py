# -*- coding: utf-8 -*-
"""M1 Invest（股票投研）域接线测试（离线 fixture）。

覆盖：登记幂等 / 无源拒绝 / 未完成拒绝 / 无决策拒绝 / 指纹漂移拒绝 /
投影 / 合法转移 / 打回 / 幂等重放 / 跳级拒绝 / 终态拒绝 / 未知 run /
绕过审批门检测 / 崩溃窗口 catch-up / 三方审计（伪造/越门/指纹漂移）。
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解解析需要模块可寻址
    spec.loader.exec_module(module)
    return module


iw = _load("invest_wiring_under_test", "agent_platform/domains/invest_wiring.py")
apv = _load("approvals_under_test_inv", "agent_platform/engine/approvals.py")

DECISION_A = "[最终决策] 600519 买入，仓位 5%，止损 1680"
DECISION_B = "[最终决策] 300750 观望，等待量能确认"


def _make_checkpoints(root: Path, runs):
    """构造离线 checkpoints.db：runs=[(run_id, stock, status)]，
    每个 done run 自动带 d1_decision 快照（output 取 DECISIONS）。"""
    db = root / "agent_platform" / "data" / "checkpoints.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs(
            run_id TEXT PRIMARY KEY, stock TEXT,
            started_at REAL, finished_at REAL, status TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(
            run_id TEXT, node_key TEXT, agent_id TEXT,
            state_json TEXT, ts REAL,
            PRIMARY KEY(run_id, node_key));
        """
    )
    now = time.time()
    for run_id, stock, status, decision in runs:
        conn.execute(
            "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
            (run_id, stock, now - 60, now, status),
        )
        if decision:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?)",
                (
                    run_id,
                    "d1_decision",
                    "D1",
                    json.dumps({"output": decision}, ensure_ascii=False),
                    now,
                ),
            )
    conn.commit()
    conn.close()


RUNS = [
    ("run_aaa111", "600519", "done", DECISION_A),
    ("run_bbb222", "300750", "done", DECISION_B),
    ("run_ccc333", "601127", "running", ""),
]


class InvestWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_checkpoints(self.root, json.loads(json.dumps(RUNS)))
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ---------------- 登记 ----------------
    def test_register_drafted_and_idempotent(self):
        entry, replayed = iw.register_run(self.root, "run_aaa111")
        self.assertFalse(replayed)
        self.assertEqual(entry["status"], "drafted")
        self.assertEqual(entry["stock"], "600519")
        import hashlib

        self.assertEqual(
            entry["decision_hash"], hashlib.sha256(DECISION_A.encode()).hexdigest()
        )
        entry2, replayed2 = iw.register_run(self.root, "run_aaa111")
        self.assertTrue(replayed2)
        self.assertEqual(entry2["decision_hash"], entry["decision_hash"])

    def test_register_unknown_run_rejected(self):
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.register_run(self.root, "run_nope")
        self.assertIn("无源", str(cm.exception))

    def test_register_running_rejected(self):
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.register_run(self.root, "run_ccc333")
        self.assertIn("running", str(cm.exception))

    def test_register_no_decision_rejected(self):
        _make_checkpoints(self.root, [("run_nod1", "600030", "done", "")])
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.register_run(self.root, "run_nod1")
        self.assertIn("d1_decision", str(cm.exception))

    def test_register_replay_drift_rejected(self):
        iw.register_run(self.root, "run_aaa111")
        # 重跑覆盖：D1 决策被改写
        _make_checkpoints(self.root, [("run_aaa111", "600519", "done", "被篡改的决策")])
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.register_run(self.root, "run_aaa111")
        self.assertIn("指纹漂移", str(cm.exception))

    # ---------------- 投影 ----------------
    def test_projection_workitems_and_artifacts(self):
        iw.register_run(self.root, "run_aaa111")
        iw.register_run(self.root, "run_bbb222")
        items, arts = iw.project_workitems(self.root)
        self.assertEqual(len(items), 2)
        wi = items[0]
        self.assertEqual(wi.workitem_id, "invest:run_aaa111")
        self.assertEqual(wi.status, "drafted")  # 域内原生状态原样
        self.assertEqual(wi.source, "invest.runs_index")
        self.assertEqual(wi.owner, "D1")
        self.assertEqual(wi.payload["stock"], "600519")
        self.assertFalse(arts[0].missing)
        self.assertEqual(arts[0].fact_hash, wi.payload["decision_hash"])

    # ---------------- 审批门 ----------------
    def test_approve_full_chain(self):
        iw.register_run(self.root, "run_aaa111")
        a1, e1, rp1 = iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", "复核通过", store=self.store
        )
        self.assertFalse(rp1)
        self.assertEqual(e1["status"], "reviewed")
        a2, e2, rp2 = iw.approve_transition(
            self.root,
            "run_aaa111",
            "adopted",
            "老田",
            "采纳进实盘参考",
            store=self.store,
        )
        self.assertFalse(rp2)
        self.assertEqual(e2["status"], "adopted")

    def test_approve_idempotent_replay(self):
        iw.register_run(self.root, "run_aaa111")
        iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        a, e, rp = iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        self.assertTrue(rp)
        self.assertEqual(e["status"], "reviewed")

    def test_approve_skip_level_rejected(self):
        iw.register_run(self.root, "run_aaa111")
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.approve_transition(
                self.root, "run_aaa111", "adopted", "老田", store=self.store
            )
        self.assertIn("非法转移", str(cm.exception))

    def test_approve_terminal_rejected(self):
        iw.register_run(self.root, "run_aaa111")
        iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        iw.approve_transition(
            self.root, "run_aaa111", "adopted", "老田", store=self.store
        )
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.approve_transition(
                self.root, "run_aaa111", "drafted", "老田", store=self.store
            )
        self.assertIn("终态", str(cm.exception))

    def test_approve_reject_then_reapprove(self):
        """打回 drafted 后重新走 reviewed（重新复核）：
        第二轮批准必须留新凭据（轮次化幂等键），不得重放第一轮。"""
        iw.register_run(self.root, "run_aaa111")
        a1, _, _ = iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        iw.approve_transition(
            self.root, "run_aaa111", "drafted", "老田", "证据不足打回", store=self.store
        )
        a2, e, rp = iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", "补证后复审", store=self.store
        )
        self.assertFalse(rp)
        self.assertEqual(e["status"], "reviewed")
        self.assertNotEqual(a1.approval_id, a2.approval_id)  # 第二轮新凭据
        self.assertEqual(a2.reason, "补证后复审")
        self.assertEqual(a2.idempotency_key, "invest:run_aaa111:drafted>reviewed#r2")
        self.assertEqual(len(self.store.list(target_type="workitem")), 3)

    def test_approve_unknown_run_rejected(self):
        with self.assertRaises(iw.InvestWiringError):
            iw.approve_transition(
                self.root, "run_nope", "reviewed", "老田", store=self.store
            )

    def test_approve_bypass_detected(self):
        """绕过审批门直接改真源 → 同态重批显式拒绝。"""
        iw.register_run(self.root, "run_aaa111")
        entries = iw.load_index(self.root)
        entries[0]["status"] = "reviewed"
        iw.save_index(self.root, entries)
        with self.assertRaises(iw.InvestWiringError) as cm:
            iw.approve_transition(
                self.root, "run_aaa111", "reviewed", "老田", store=self.store
            )
        self.assertIn("绕过审批门", str(cm.exception))

    def test_catch_up_after_crash_window(self):
        """凭据落库后真源写入前崩溃 → 重放补齐真源。"""
        iw.register_run(self.root, "run_aaa111")
        # 模拟：先手工落凭据（绕过 approve 的真源写入步骤）
        self.store.record(
            apv.Approval(
                target_type="workitem",
                target_id="invest:run_aaa111",
                from_status="drafted",
                to_status="reviewed",
                decision="approved",
                decided_by="老田",
                reason="崩溃模拟",
                decided_at=iw._now_iso(),
                idempotency_key="invest:run_aaa111:drafted>reviewed",
            )
        )
        a, e, rp = iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        self.assertTrue(rp)
        self.assertEqual(e["status"], "reviewed")  # catch-up 补齐

    def test_second_reject_gets_new_credential(self):
        """同转移反复（二次打回）：消费标记判定第二轮批准，落 #r2 新凭据，
        不重放第一轮（devtask 判例回灌：last_wiring_key）。"""
        iw.register_run(self.root, "run_aaa111")
        iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        a1, _, _ = iw.approve_transition(
            self.root, "run_aaa111", "drafted", "老田", "第一次打回", store=self.store
        )
        # 返工后重新复核（真源回 reviewed）
        entries = iw.load_index(self.root)
        entries[0]["status"] = "reviewed"
        iw.save_index(self.root, entries)
        a2, e, rp = iw.approve_transition(
            self.root, "run_aaa111", "drafted", "老田", "第二次打回", store=self.store
        )
        self.assertFalse(rp)  # 新轮次，非重放
        self.assertEqual(e["status"], "drafted")
        self.assertEqual(a2.idempotency_key, "invest:run_aaa111:reviewed>drafted#r2")
        self.assertNotEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(a2.reason, "第二次打回")

    # ---------------- 三方审计 ----------------
    def test_audit_pass_on_compliant_state(self):
        iw.register_run(self.root, "run_aaa111")
        iw.register_run(self.root, "run_bbb222")
        iw.approve_transition(
            self.root, "run_aaa111", "reviewed", "老田", store=self.store
        )
        rep = iw.audit_report(self.root, store=self.store)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["by_status"], {"drafted": 1, "reviewed": 1})

    def test_audit_gate_violation(self):
        """越门：status=reviewed 无凭据 → FAIL。"""
        iw.register_run(self.root, "run_aaa111")
        entries = iw.load_index(self.root)
        entries[0]["status"] = "reviewed"
        iw.save_index(self.root, entries)
        rep = iw.audit_report(self.root, store=self.store)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(
            any(v["finding"].startswith("状态=") for v in rep["violations"])
        )

    def test_audit_decision_drift(self):
        """指纹漂移：登记后 D1 决策被重跑覆盖 → FAIL。"""
        iw.register_run(self.root, "run_aaa111")
        _make_checkpoints(
            self.root, [("run_aaa111", "600519", "done", "事后篡改的决策")]
        )
        rep = iw.audit_report(self.root, store=self.store)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(any("指纹漂移" in v["finding"] for v in rep["violations"]))

    def test_audit_decision_missing(self):
        """执行底账决策快照被删 → FAIL + missing_artifacts。"""
        iw.register_run(self.root, "run_aaa111")
        db = self.root / "agent_platform" / "data" / "checkpoints.db"
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM checkpoints WHERE run_id=?", ("run_aaa111",))
        conn.commit()
        conn.close()
        rep = iw.audit_report(self.root, store=self.store)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(rep["missing_artifacts"])

    # ---------------- CLI ----------------
    def test_cli_register_list_report(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = iw.main(["register", "run_aaa111", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("登记", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = iw.main(["list", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("invest:run_aaa111", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = iw.main(["report", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn('"verdict": "PASS"', buf.getvalue())

    def test_cli_approve(self):
        """CLI approve 通路测试：patch 模块默认 store 根到 tmp，不碰真库。"""
        import io
        from contextlib import redirect_stdout

        real_platform = iw._AGENT_PLATFORM
        iw._AGENT_PLATFORM = self.root / "agent_platform"
        try:
            iw.register_run(self.root, "run_aaa111")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = iw.main(
                    [
                        "approve",
                        "run_aaa111",
                        "reviewed",
                        "--by",
                        "老田",
                        "--reason",
                        "复核通过",
                        "--root",
                        str(self.root),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("批准", buf.getvalue())
        finally:
            iw._AGENT_PLATFORM = real_platform


if __name__ == "__main__":
    unittest.main()
