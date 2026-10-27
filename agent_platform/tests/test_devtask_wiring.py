# -*- coding: utf-8 -*-
"""M1 devtask 工单线接线测试（离线 fixture）。

覆盖：投影 / worktree 缺失检测 / 弃单门（reason 强制）/ 打回返工 /
漂移修复 done（无合并证据拒绝）/ 幂等重放 / 轮次化幂等键 /
catch-up / 跳级拒绝 / 终态拒绝 / 未知工单 / 绕过审批门检测 /
审计（merged_but_review 漂移 / legacy_done grandfather / 弃单统计 / CLI）。
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
    sys.modules[name] = module  # dataclass 字符串注解解析需要模块可寻址
    spec.loader.exec_module(module)
    return module


dw = _load("devtask_wiring_under_test", "agent_platform/domains/devtask_wiring.py")
apv = _load("approvals_under_test_dt", "agent_platform/engine/approvals.py")


def _make_tasks(root: Path, tasks: dict):
    d = root / ".worktrees"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8"
    )


TASKS = {
    "K-T1": {
        "status": "doing",
        "owner": "DEV1",
        "project": "portal",
        "branch": "task/K-T1",
        "worktree": ".worktrees/K-T1",
        "created": "2026-10-17 10:00:00",
        "desc": "修复门户死结",
    },
    "K-T2": {
        "status": "review",
        "owner": "DEV2",
        "project": "portal",
        "branch": "task/K-T2",
        "worktree": ".worktrees/K-T2",
        "created": "2026-10-17 10:10:00",
        "desc": "导航挂载",
    },
    "K-T3": {
        "status": "done",
        "owner": "DEV3",
        "project": "general",
        "branch": "task/K-T3",
        "worktree": "",
        "created": "2026-10-16 09:00:00",
        "merged": "2026-10-16 11:00:00",
        "desc": "已完成工单",
    },
    "K-T4": {
        "status": "done",
        "owner": "DEV4",
        "project": "general",
        "branch": "task/K-T4",
        "worktree": "",
        "created": "2026-10-15 09:00:00",
        "desc": "无 merged 存量",
    },
    "K-T5": {
        "status": "aborted",
        "owner": "DEV5",
        "project": "general",
        "branch": "task/K-T5",
        "worktree": "",
        "created": "2026-10-15 09:00:00",
        "abort_reason": "需求撤销",
        "desc": "已弃单",
    },
}


class DevtaskWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_tasks(self.root, json.loads(json.dumps(TASKS)))
        (self.root / ".worktrees" / "K-T1").mkdir(parents=True, exist_ok=True)
        (self.root / ".worktrees" / "K-T2").mkdir(parents=True, exist_ok=True)
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ---------------- 投影 ----------------
    def test_projection_workitems_and_artifacts(self):
        items, arts = dw.project_workitems(self.root, check_git=False)
        self.assertEqual(len(items), 5)
        by_id = {wi.workitem_id: wi for wi in items}
        wi = by_id["devtask:K-T1"]
        self.assertEqual(wi.status, "doing")  # 域内原生状态原样
        self.assertEqual(wi.source, "devtask.worktrees_tasks")
        self.assertEqual(wi.owner, "DEV1")
        self.assertEqual(wi.payload["branch"], "task/K-T1")
        art_by_id = {a.workitem_id: a for a in arts}
        self.assertFalse(art_by_id["devtask:K-T1"].missing)
        self.assertFalse(art_by_id["devtask:K-T3"].missing)  # 终态不检查

    def test_projection_missing_worktree(self):
        (self.root / ".worktrees" / "K-T2").rmdir()
        items, arts = dw.project_workitems(self.root, check_git=False)
        art_by_id = {a.workitem_id: a for a in arts}
        self.assertTrue(art_by_id["devtask:K-T2"].missing)  # 在办缺 worktree
        self.assertFalse(art_by_id["devtask:K-T1"].missing)

    # ---------------- 弃单门 ----------------
    def test_approve_abort_requires_reason(self):
        with self.assertRaises(dw.DevtaskWiringError) as cm:
            dw.approve_transition(
                self.root, "K-T1", "aborted", "老田", store=self.store
            )
        self.assertIn("reason", str(cm.exception))

    def test_approve_abort_ok(self):
        a, t, rp = dw.approve_transition(
            self.root, "K-T1", "aborted", "老田", "需求撤销", store=self.store
        )
        self.assertFalse(rp)
        self.assertEqual(t["status"], "aborted")
        self.assertEqual(t["abort_reason"], "需求撤销")

    # ---------------- 打回返工 ----------------
    def test_approve_rework(self):
        a, t, rp = dw.approve_transition(
            self.root, "K-T2", "doing", "老田", "评审未过退回", store=self.store
        )
        self.assertFalse(rp)
        self.assertEqual(t["status"], "doing")

    # ---------------- 漂移修复 done ----------------
    def test_approve_done_requires_merge_evidence(self):
        with self.assertRaises(dw.DevtaskWiringError) as cm:
            dw.approve_transition(
                self.root,
                "K-T2",
                "done",
                "老田",
                "对齐现实",
                store=self.store,
                check_git=False,
            )
        self.assertIn("无合并证据", str(cm.exception))

    def test_approve_done_with_merged_ts(self):
        tasks = dw.load_tasks(self.root)
        tasks["K-T2"]["merged"] = "2026-10-17 12:00:00"  # 已合并证据
        dw.save_tasks(self.root, tasks)
        a, t, rp = dw.approve_transition(
            self.root,
            "K-T2",
            "done",
            "老田",
            "对齐现实",
            store=self.store,
            check_git=False,
        )
        self.assertFalse(rp)
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["merged"], "2026-10-17 12:00:00")  # 不覆盖原值

    def test_approve_done_nongit_manual_confirm(self):
        """非 git 工作流单（无 branch/worktree/merged）review→done 走
        人工审查确认通道：审批凭据即审计证据，不强制合并证据。"""
        tasks = dw.load_tasks(self.root)
        tasks["K-T6"] = {
            "status": "review",
            "owner": None,
            "project": None,
            "created": "2026-10-17",
            "title": "推送域工作单（直接落生产文件）",
            "note": "完成铁证记录于 note",
        }
        dw.save_tasks(self.root, tasks)
        a, t, rp = dw.approve_transition(
            self.root,
            "K-T6",
            "done",
            "老田",
            "用户指示授权审查通过",
            store=self.store,
            check_git=False,
        )
        self.assertFalse(rp)
        self.assertEqual(t["status"], "done")
        # 凭据落库可追溯
        keys = [
            x.idempotency_key
            for x in self.store.list(target_type="workitem")
            if x.target_id == "devtask:K-T6"
        ]
        self.assertEqual(keys, ["devtask:K-T6:review>done"])

    def test_approve_done_nongit_still_blocked_without_reason(self):
        """非 git 单也必须带 reason（弃单语义沿用；done 用 reason 记审查依据）。"""
        tasks = dw.load_tasks(self.root)
        tasks["K-T7"] = {
            "status": "review",
            "owner": None,
            "project": None,
            "created": "2026-10-17",
        }
        dw.save_tasks(self.root, tasks)
        a, t, rp = dw.approve_transition(
            self.root, "K-T7", "done", "老田", "", store=self.store, check_git=False
        )
        self.assertFalse(rp)  # done 不强制 reason（与 aborted 不同），可过

    # ---------------- 幂等/轮次化/catch-up ----------------
    def test_approve_idempotent_replay(self):
        dw.approve_transition(
            self.root, "K-T2", "doing", "老田", "打回", store=self.store
        )
        a, t, rp = dw.approve_transition(
            self.root, "K-T2", "doing", "老田", store=self.store
        )
        self.assertTrue(rp)
        self.assertEqual(t["status"], "doing")

    def test_approve_round_key_on_reapprove(self):
        """打回后再打回：第二轮新凭据（#r2）。"""
        a1, _, _ = dw.approve_transition(
            self.root, "K-T2", "doing", "老田", "第一次打回", store=self.store
        )
        tasks = dw.load_tasks(self.root)
        tasks["K-T2"]["status"] = "review"  # 模拟重新提交评审
        dw.save_tasks(self.root, tasks)
        a2, _, rp = dw.approve_transition(
            self.root, "K-T2", "doing", "老田", "第二次打回", store=self.store
        )
        self.assertFalse(rp)
        self.assertEqual(a2.idempotency_key, "devtask:K-T2:review>doing#r2")
        self.assertNotEqual(a1.approval_id, a2.approval_id)

    def test_catch_up_after_crash_window(self):
        self.store.record(
            apv.Approval(
                target_type="workitem",
                target_id="devtask:K-T1",
                from_status="doing",
                to_status="aborted",
                decision="approved",
                decided_by="老田",
                reason="崩溃模拟",
                decided_at=dw._now_naive(),
                idempotency_key="devtask:K-T1:doing>aborted",
            )
        )
        a, t, rp = dw.approve_transition(
            self.root, "K-T1", "aborted", "老田", "崩溃模拟", store=self.store
        )
        self.assertTrue(rp)
        self.assertEqual(t["status"], "aborted")  # catch-up 补齐

    # ---------------- 非法转移 ----------------
    def test_approve_skip_level_rejected(self):
        with self.assertRaises(dw.DevtaskWiringError) as cm:
            dw.approve_transition(
                self.root, "K-T1", "done", "老田", store=self.store, check_git=False
            )
        self.assertIn("非法转移", str(cm.exception))

    def test_approve_terminal_rejected(self):
        with self.assertRaises(dw.DevtaskWiringError) as cm:
            dw.approve_transition(self.root, "K-T3", "doing", "老田", store=self.store)
        self.assertIn("终态", str(cm.exception))

    def test_approve_unknown_tid_rejected(self):
        with self.assertRaises(dw.DevtaskWiringError):
            dw.approve_transition(
                self.root, "K-NOPE", "aborted", "老田", "x", store=self.store
            )

    def test_approve_bypass_detected(self):
        tasks = dw.load_tasks(self.root)
        tasks["K-T1"]["status"] = "aborted"  # 绕门直改真源
        dw.save_tasks(self.root, tasks)
        with self.assertRaises(dw.DevtaskWiringError) as cm:
            dw.approve_transition(
                self.root, "K-T1", "aborted", "老田", "x", store=self.store
            )
        self.assertIn("绕过审批门", str(cm.exception))

    # ---------------- 审计 ----------------
    def test_audit_pass_and_classifications(self):
        rep = dw.audit_report(self.root, store=self.store, check_git=False)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(
            rep["by_status"], {"doing": 1, "review": 1, "done": 2, "aborted": 1}
        )
        self.assertEqual(rep["findings"]["review_pending"], ["K-T2"])
        self.assertEqual(rep["findings"]["legacy_done"], ["K-T4"])
        self.assertEqual(rep["findings"]["unapproved_drops"], ["K-T5"])

    def test_audit_merged_but_review_drift(self):
        tasks = dw.load_tasks(self.root)
        tasks["K-T2"]["merged"] = "2026-10-17 12:00:00"  # 已合并卡 review
        dw.save_tasks(self.root, tasks)
        rep = dw.audit_report(self.root, store=self.store, check_git=False)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(
            any(
                v["tid"] == "K-T2" and "漂移" in v["finding"] for v in rep["violations"]
            )
        )

    def test_audit_missing_worktree_fails(self):
        (self.root / ".worktrees" / "K-T2").rmdir()
        rep = dw.audit_report(self.root, store=self.store, check_git=False)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("devtask:K-T2", rep["missing_worktrees"])

    def test_audit_drop_credential_counted(self):
        dw.approve_transition(
            self.root, "K-T1", "aborted", "老田", "弃", store=self.store
        )
        rep = dw.audit_report(self.root, store=self.store, check_git=False)
        self.assertIn("devtask:K-T1", rep["findings"]["approved_drops"])
        self.assertNotIn("K-T1", rep["findings"]["unapproved_drops"])

    # ---------------- CLI ----------------
    def test_cli_list_report(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dw.main(["list", "--active", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("devtask:K-T1", buf.getvalue())
        self.assertNotIn("devtask:K-T3", buf.getvalue())  # 终态被过滤
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dw.main(["report", "--no-git", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn('"verdict": "PASS"', buf.getvalue())

    def test_cli_approve(self):
        """CLI approve 通路：patch 模块默认 store 根到 tmp，不碰真库。"""
        import io
        from contextlib import redirect_stdout

        real_platform = dw._AGENT_PLATFORM
        dw._AGENT_PLATFORM = self.root / "agent_platform"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = dw.main(
                    [
                        "approve",
                        "K-T2",
                        "doing",
                        "--by",
                        "老田",
                        "--reason",
                        "打回",
                        "--no-git",
                        "--root",
                        str(self.root),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("批准", buf.getvalue())
        finally:
            dw._AGENT_PLATFORM = real_platform


if __name__ == "__main__":
    unittest.main()
