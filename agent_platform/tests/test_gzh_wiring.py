# -*- coding: utf-8 -*-
"""M1 GZH 域接线测试（离线 fixture）。

覆盖：投影 / 合法转移 / 幂等重放 / 跳级拒绝 / 终态拒绝 / 未知草稿 /
真源缺失显式报错 / 指纹校验 / 审批账本并发幂等 / 审计比对。
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解解析需要模块可寻址
    spec.loader.exec_module(module)
    return module


gw = _load("gzh_wiring_under_test", "agent_platform/domains/gzh_wiring.py")
apv = _load("approvals_under_test", "agent_platform/engine/approvals.py")
objects = _load("contracts_objects_under_test", "agent_platform/contracts/objects.py")


def _make_root(tmp: Path, drafts):
    """构造离线 GZH fixture：index.json + md 正文。"""
    d = tmp / "gzh" / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    for item in drafts:
        (d / item["md"]).write_text("# %s\n\n正文" % item["title"],
                                    encoding="utf-8")
    (d / "index.json").write_text(
        json.dumps(drafts, ensure_ascii=False, indent=1), encoding="utf-8")


DRAFTS = [
    {"id": "2026-10-17-22", "date": "2026-10-17", "title": "芯片焦虑",
     "col": "量化观察", "status": "draft",
     "md": "2026-10-17-芯片焦虑.md", "ts": "10-17 09:30"},
    {"id": "2026-10-16-21", "date": "2026-10-16", "title": "算力资金",
     "col": "AI 工作流", "status": "draft",
     "md": "2026-10-16-算力资金.md", "ts": "10-16 11:25"},
]


class GzhWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_root(self.root, json.loads(json.dumps(DRAFTS)))
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    def test_projection_workitems_and_artifacts(self):
        items, arts = gw.project_workitems(self.root)
        self.assertEqual(len(items), 2)
        wi = items[0]
        self.assertEqual(wi.workitem_id, "gzh:2026-10-17-22")
        self.assertEqual(wi.status, "draft")          # 域内原生状态原样
        self.assertEqual(wi.source, "gzh.drafts_index")
        art = arts[0]
        self.assertFalse(art.missing)
        import hashlib
        content = (self.root / "gzh/drafts" / DRAFTS[0]["md"]).read_bytes()
        self.assertEqual(art.fact_hash, hashlib.sha256(content).hexdigest())

    def test_missing_md_marked_not_faked(self):
        (self.root / "gzh/drafts" / DRAFTS[0]["md"]).unlink()
        _, arts = gw.project_workitems(self.root)
        self.assertTrue(arts[0].missing)
        self.assertEqual(arts[0].fact_hash, "")       # 无内容不伪造指纹

    def test_legal_transition_writes_approval_and_index(self):
        approval, after, replayed = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="laotian",
            reason="审查通过", store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(after["status"], "reviewed")
        self.assertEqual(approval.to_status, "reviewed")
        self.assertEqual(approval.decided_by, "laotian")
        # index 唯一变更：status/ts
        idx = json.loads((self.root / "gzh/drafts/index.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(idx[0]["status"], "reviewed")
        # 账本一条凭据
        self.assertEqual(len(self.store.list(target_type="workitem")), 1)

    def test_idempotent_replay_no_double_write(self):
        a1, _, r1 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="laotian",
            store=self.store)
        idx_before = (self.root / "gzh/drafts/index.json").read_text(
            encoding="utf-8")
        a2, _, r2 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="laotian",
            store=self.store)
        self.assertFalse(r1)
        self.assertTrue(r2)
        self.assertEqual(a1.approval_id, a2.approval_id)  # 同凭据
        self.assertEqual(
            (self.root / "gzh/drafts/index.json").read_text(encoding="utf-8"),
            idx_before)                                    # 真源零重复变更

    def test_skip_transition_rejected_zero_write(self):
        before = (self.root / "gzh/drafts/index.json").read_text(
            encoding="utf-8")
        with self.assertRaises(gw.GzhWiringError):
            gw.approve_transition(self.root, "2026-10-17-22", "published",
                                  by="laotian", store=self.store)
        self.assertEqual(
            (self.root / "gzh/drafts/index.json").read_text(encoding="utf-8"),
            before)                                        # 零写入
        self.assertEqual(len(self.store.list(target_type="workitem")), 0)

    def test_terminal_status_frozen(self):
        gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                              by="laotian", store=self.store)
        gw.approve_transition(self.root, "2026-10-17-22", "published",
                              by="laotian", store=self.store)
        with self.assertRaises(gw.GzhWiringError):
            gw.approve_transition(self.root, "2026-10-17-22", "draft",
                                  by="laotian", store=self.store)

    def test_unknown_draft_rejected(self):
        with self.assertRaises(gw.GzhWiringError):
            gw.approve_transition(self.root, "9999-99-99", "reviewed",
                                  by="laotian", store=self.store)

    def test_missing_by_rejected(self):
        with self.assertRaises(gw.GzhWiringError):
            gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                                  by="", store=self.store)

    def test_missing_index_explicit_error(self):
        (self.root / "gzh/drafts/index.json").unlink()
        with self.assertRaises(gw.GzhWiringError) as ctx:
            gw.project_workitems(self.root)
        self.assertIn("missing_source", str(ctx.exception))

    def test_corrupt_index_explicit_error(self):
        (self.root / "gzh/drafts/index.json").write_text("{broken",
                                                         encoding="utf-8")
        with self.assertRaises(gw.GzhWiringError):
            gw.project_workitems(self.root)

    def test_publish_requires_review_first(self):
        # draft 只能到 reviewed；published 必须两步走
        gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                              by="laotian", store=self.store)
        approval, after, _ = gw.approve_transition(
            self.root, "2026-10-17-22", "published", by="laotian",
            reason="用户批准发布", store=self.store)
        self.assertEqual(after["status"], "published")
        keys = [a.idempotency_key for a in
                self.store.list(target_type="workitem")]
        self.assertEqual(keys, ["gzh:2026-10-17-22:draft>reviewed",
                                "gzh:2026-10-17-22:reviewed>published"])

    def test_audit_report_detects_bypassed_gate(self):
        # 绕过审批门手工把 status 改成 published → 审计必须 FAIL
        gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                              by="laotian", store=self.store)
        idx = json.loads((self.root / "gzh/drafts/index.json")
                         .read_text(encoding="utf-8"))
        idx[0]["status"] = "published"                 # 模拟绕门
        (self.root / "gzh/drafts/index.json").write_text(
            json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        report = gw.audit_report(self.root, store=self.store)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["violations"][0]["draft_id"], "2026-10-17-22")

    def test_audit_report_pass_on_clean_flow(self):
        gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                              by="laotian", store=self.store)
        report = gw.audit_report(self.root, store=self.store)
        self.assertEqual(report["verdict"], "PASS")


class ApprovalStoreConcurrencyTests(unittest.TestCase):
    def test_same_key_concurrent_single_row(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            db = Path(tmp.name) / "approvals.db"
            # M0 判例：并发期间保持一个常驻连接（keeper）——Windows/SQLite 3.49
            # 下 WAL 库全部连接关闭后 shm 被删除，多连接同时重开重建 shm 有竞争，
            # 会出现假性 SQLITE_READONLY（2026-10-17 实测定案）
            keeper = apv.ApprovalStore(db)
            results, errors = [], []
            barrier = threading.Barrier(4)

            def worker(i):
                # M0 判例：每线程独立连接同一 DB 文件，才触发真实竞争
                store = apv.ApprovalStore(db)
                try:
                    barrier.wait()
                    ap, replayed = store.record(objects.Approval(
                        target_type="workitem", target_id="gzh:x",
                        from_status="draft", to_status="reviewed",
                        decided_by="laotian",
                        idempotency_key="gzh:x:draft>reviewed"))
                    results.append((ap.approval_id, replayed))
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)
                finally:
                    store.close()

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(results), 4)          # 零异常、零丢失
            self.assertEqual(errors, [])
            keeper.close()
            ids = {r[0] for r in results}
            self.assertEqual(len(ids), 1)              # 同键同凭据
            n = sqlite3.connect(db).execute(
                "SELECT COUNT(*) FROM approvals").fetchone()[0]
            self.assertEqual(n, 1)                     # 单行
        finally:
            tmp.cleanup()


# ── M1 轮次化幂等键回灌（Invest 判例对齐，2026-10-17）──────────────
class GzhRoundKeyTests(unittest.TestCase):
    """打回后重批 = 新一轮凭据；同轮重放幂等；崩溃窗口不新增轮次。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_root(self.root, json.loads(json.dumps(DRAFTS)))
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _keys(self):
        return [a.idempotency_key for a
                in self.store.list(target_type="workitem")]

    def test_reject_then_reapprove_new_round_credential(self):
        a1, _, r1 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="审查员A",
            store=self.store)
        self.assertFalse(r1)
        # 打回：reviewed → draft
        a2, draft, r2 = gw.approve_transition(
            self.root, "2026-10-17-22", "draft", by="老田",
            reason="数据口径需修改", store=self.store)
        self.assertFalse(r2)
        self.assertEqual(draft["status"], "draft")
        # 二轮重批：draft → reviewed（旧实现会重放 a1——判例禁止）
        a3, draft3, r3 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="审查员C",
            reason="二轮复核通过", store=self.store)
        self.assertFalse(r3)                       # 新凭据，非重放
        self.assertNotEqual(a3.approval_id, a1.approval_id)
        self.assertEqual(draft3["status"], "reviewed")
        keys = self._keys()
        self.assertEqual(len(keys), 3)             # 三张凭据全留痕
        self.assertEqual(
            keys.count("gzh:2026-10-17-22:draft>reviewed"), 1)
        self.assertIn("gzh:2026-10-17-22:draft>reviewed#r2", keys)
        latest = [a for a in self.store.list(target_type="workitem")
                  if a.to_status == "reviewed"][-1]
        self.assertEqual(latest.decided_by, "审查员C")   # 最新凭据可追溯

    def test_same_round_replay_still_idempotent(self):
        a1, _, _ = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="A", store=self.store)
        a2, draft, r2 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="A", store=self.store)
        self.assertTrue(r2)
        self.assertEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(len(self._keys()), 1)     # 不新增凭据

    def test_crash_window_catchup_no_new_round(self):
        a1, _, _ = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="A", store=self.store)
        # 模拟崩溃窗口：凭据已落库、真源被回拨。
        # 注意（原子写语义）：status 与 last_wiring_key 是同一次
        # save_index 原子写的，崩溃时两者都不落——回拨必须同时
        # 清除 last_wiring_key，否则外观等同「打回后重批」会被
        # 消费标记判据判为新轮次（那是正确的，不是 catch-up）。
        path = self.root / "gzh/drafts/index.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        for it in raw:
            if it["id"] == "2026-10-17-22":
                it["status"] = "draft"
                it.pop("last_wiring_key", None)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        a2, draft, r2 = gw.approve_transition(
            self.root, "2026-10-17-22", "reviewed", by="A", store=self.store)
        self.assertTrue(r2)
        self.assertEqual(a2.approval_id, a1.approval_id)  # 复用最新凭据
        self.assertEqual(draft["status"], "reviewed")     # 真源已补齐
        self.assertEqual(len(self._keys()), 1)            # 不新增轮次凭据


class GzhSecondRejectTests(unittest.TestCase):
    """同转移反复（二次打回）：last_wiring_key 消费标记判据
    （devtask/invest 判例统一回灌）——二轮打回落 #r2 新凭据，
    不误判 catch-up 重放第一轮。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_root(self.root, json.loads(json.dumps(DRAFTS)))
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_second_reject_gets_new_credential(self):
        gw.approve_transition(self.root, "2026-10-17-22", "reviewed",
                              by="审查员A", store=self.store)
        a1, _, r1 = gw.approve_transition(
            self.root, "2026-10-17-22", "draft", by="老田",
            reason="第一次打回", store=self.store)
        self.assertFalse(r1)
        # 修改后重新送审（真源回 reviewed）
        items = gw.load_index(self.root)
        for it in items:
            if it["id"] == "2026-10-17-22":
                it["status"] = "reviewed"
        gw.save_index(self.root, items)
        # 第二次打回：同转移反复，必须新凭据 #r2（不得重放 a1）
        a2, draft, r2 = gw.approve_transition(
            self.root, "2026-10-17-22", "draft", by="老田",
            reason="第二次打回", store=self.store)
        self.assertFalse(r2)
        self.assertEqual(draft["status"], "draft")
        self.assertNotEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(a2.idempotency_key,
                         "gzh:2026-10-17-22:reviewed>draft#r2")
        self.assertEqual(a2.reason, "第二次打回")


if __name__ == "__main__":
    unittest.main()
