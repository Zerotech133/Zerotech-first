# -*- coding: utf-8 -*-
"""M1 Academy 域接线测试（离线 fixture）。

覆盖：投影 / 自由推进 + history / 打回 / 受门转移凭据 / 幂等重放 +
崩溃窗口 catch-up / 跳级拒绝 / 受门阶段不可 move / 终态冻结 /
未知阶段 / 真源缺失与损坏显式报错 / 越门审计。
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解解析需要模块可寻址
    spec.loader.exec_module(module)
    return module


aw = _load("academy_wiring_under_test", "agent_platform/domains/academy_wiring.py")
apv = _load("approvals_under_test", "agent_platform/engine/approvals.py")


def _make_root(tmp: Path, topics):
    d = tmp / "agent_platform" / "data" / "academy"
    d.mkdir(parents=True, exist_ok=True)
    (d / "research_topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8"
    )


TOPIC = {
    "topic_id": "AC-20261017-LCS1",
    "title": "LCS-1 图形先行策略评估",
    "direction": "strategy_lab",
    "owner": "STRAT1",
    "sponsor": "D1",
    "status": "intake",
    "decision": "",
    "created": "2026-10-17T05:20:00",
    "updated": "2026-10-17T06:10:00",
    "history": [
        {
            "at": "2026-10-17T05:20:00",
            "stage": "intake",
            "by": "ACAD1",
            "note": "首个研究院示范课题登记",
        },
    ],
    "worker_plan": {
        "enabled": True,
        "mode": "controlled_shadow",
        "first_stage": "intake",
        "stages": [],
    },
}


class AcademyWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_root(self.root, [json.loads(json.dumps(TOPIC))])
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _read_topic(self):
        return json.loads(
            (self.root / "agent_platform/data/academy/research_topics.json").read_text(
                encoding="utf-8"
            )
        )[0]

    def test_move_by_non_owner_rejected_zero_write(self):
        """2026-10-29 复盘整改：阶段推进 by 必须等于 worker_plan 责任席。"""
        raw = self._read_topic()
        raw["worker_plan"]["stages"] = [
            {"stage": "hypothesis", "agent": "STRAT1"},
            {"stage": "evidence", "agent": "DATA1"},
        ]
        path = self.root / "agent_platform/data/academy/research_topics.json"
        path.write_text(json.dumps([raw], ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "hypothesis", by="ACAD1")
        self.assertEqual(self._read_topic()["status"], "intake")

    def test_move_by_owner_allowed(self):
        raw = self._read_topic()
        raw["worker_plan"]["stages"] = [{"stage": "hypothesis", "agent": "STRAT1"}]
        path = self.root / "agent_platform/data/academy/research_topics.json"
        path.write_text(json.dumps([raw], ensure_ascii=False, indent=2), encoding="utf-8")
        topic = aw.move_stage(
            self.root, "AC-20261017-LCS1", "hypothesis", by="STRAT1", note="spec draft"
        )
        self.assertEqual(topic["status"], "hypothesis")
        self.assertEqual(topic["history"][-1]["by"], "STRAT1")

    def test_move_without_stage_plan_keeps_legacy(self):
        """无 worker_plan（旧数据）保持兼容，不误伤历史课题。"""
        aw.move_stage(self.root, "AC-20261017-LCS1", "hypothesis", by="ACAD1")
        self.assertEqual(self._read_topic()["status"], "hypothesis")

    # ------------------------------------------------------------------
    def test_projection_stage_native_and_index(self):
        items = aw.project_workitems(self.root)
        self.assertEqual(len(items), 1)
        wi = items[0]
        self.assertEqual(wi.workitem_id, "academy:AC-20261017-LCS1")
        self.assertEqual(wi.status, "intake")  # 阶段机原生值
        self.assertEqual(wi.payload["stage_index"], 0)  # 阶段机索引
        self.assertEqual(wi.owner, "STRAT1")
        self.assertEqual(wi.payload["worker_mode"], "controlled_shadow")

    def test_free_move_forward_appends_history(self):
        topic = aw.move_stage(
            self.root,
            "AC-20261017-LCS1",
            "hypothesis",
            by="STRAT1",
            note="形成可证伪假设",
        )
        self.assertEqual(topic["status"], "hypothesis")
        self.assertEqual(len(topic["history"]), 2)  # 域内轨迹追加
        self.assertEqual(topic["history"][-1]["by"], "STRAT1")
        raw = self._read_topic()
        self.assertEqual(raw["status"], "hypothesis")  # 真源已更新

    def test_move_backward_allowed(self):
        aw.move_stage(self.root, "AC-20261017-LCS1", "hypothesis", by="STRAT1")
        topic = aw.move_stage(
            self.root, "AC-20261017-LCS1", "intake", by="ACAD1", note="打回重立项"
        )
        self.assertEqual(topic["status"], "intake")
        self.assertEqual(len(topic["history"]), 3)

    def test_move_skip_rejected_zero_write(self):
        before = (
            self.root / "agent_platform/data/academy/research_topics.json"
        ).read_text(encoding="utf-8")
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "validation", by="BACK1")
        self.assertEqual(
            (self.root / "agent_platform/data/academy/research_topics.json").read_text(
                encoding="utf-8"
            ),
            before,
        )

    def test_gated_stage_cannot_move(self):
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "decision", by="laotian")

    def test_gated_transition_requires_approval_flow(self):
        # 逐步推进到 joint_review（自由段），再走审批门 →decision
        for stage in ("hypothesis", "evidence", "validation", "review", "joint_review"):
            aw.move_stage(self.root, "AC-20261017-LCS1", stage, by="ACAD1")
        approval, topic, replayed = aw.approve_transition(
            self.root,
            "AC-20261017-LCS1",
            "decision",
            by="laotian",
            reason="Go：样本外达标",
            store=self.store,
        )
        self.assertFalse(replayed)
        self.assertEqual(topic["status"], "decision")
        self.assertEqual(
            approval.idempotency_key, "academy:AC-20261017-LCS1:joint_review>decision"
        )
        keys = [a.idempotency_key for a in self.store.list(target_type="workitem")]
        self.assertEqual(keys, ["academy:AC-20261017-LCS1:joint_review>decision"])

    def test_gated_replay_idempotent_and_catchup(self):
        for stage in ("hypothesis", "evidence", "validation", "review", "joint_review"):
            aw.move_stage(self.root, "AC-20261017-LCS1", stage, by="ACAD1")
        a1, _, r1 = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="laotian", store=self.store
        )
        # 重放：同状态同凭据，零变更
        a2, topic, r2 = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="laotian", store=self.store
        )
        self.assertTrue(r2)
        self.assertEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(topic["status"], "decision")
        self.assertEqual(len(topic["history"]), 7)  # 6 推进 + 1 首批批准
        self.assertEqual(len(self.store.list(target_type="workitem")), 1)
        # 崩溃窗口 catch-up：凭据已在账本但真源写入未发生（status 与
        # history 都没有 decision 痕迹——_apply_stage 原子性未达成）
        raw = self._read_topic()
        raw["status"] = "joint_review"  # 模拟真源滞后
        raw["history"] = [h for h in raw["history"] if h.get("stage") != "decision"]
        path = self.root / "agent_platform/data/academy/research_topics.json"
        path.write_text(
            json.dumps([raw], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _, topic3, r3 = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="laotian", store=self.store
        )
        self.assertTrue(r3)
        self.assertEqual(topic3["status"], "decision")  # 已补齐
        self.assertEqual(len(self.store.list(target_type="workitem")), 1)

    def test_terminal_closed_frozen(self):
        for stage in ("hypothesis", "evidence", "validation", "review", "joint_review"):
            aw.move_stage(self.root, "AC-20261017-LCS1", stage, by="ACAD1")
        aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="laotian", store=self.store
        )
        aw.approve_transition(
            self.root, "AC-20261017-LCS1", "closed", by="laotian", store=self.store
        )
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "review", by="ACAD1")
        with self.assertRaises(aw.AcademyWiringError):
            aw.approve_transition(
                self.root,
                "AC-20261017-LCS1",
                "decision",
                by="laotian",
                store=self.store,
            )

    def test_non_gated_target_rejected_in_approve(self):
        with self.assertRaises(aw.AcademyWiringError):
            aw.approve_transition(
                self.root,
                "AC-20261017-LCS1",
                "hypothesis",
                by="laotian",
                store=self.store,
            )

    def test_unknown_stage_and_topic_rejected(self):
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "nonstage", by="ACAD1")
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-9999", "hypothesis", by="ACAD1")

    def test_missing_by_rejected(self):
        with self.assertRaises(aw.AcademyWiringError):
            aw.move_stage(self.root, "AC-20261017-LCS1", "hypothesis", by="")

    def test_missing_source_explicit(self):
        (self.root / "agent_platform/data/academy/research_topics.json").unlink()
        with self.assertRaises(aw.AcademyWiringError) as ctx:
            aw.project_workitems(self.root)
        self.assertIn("missing_source", str(ctx.exception))

    def test_corrupt_source_explicit(self):
        (self.root / "agent_platform/data/academy/research_topics.json").write_text(
            "{broken", encoding="utf-8"
        )
        with self.assertRaises(aw.AcademyWiringError):
            aw.project_workitems(self.root)

    def test_audit_detects_bypassed_gate(self):
        aw.move_stage(self.root, "AC-20261017-LCS1", "hypothesis", by="ACAD1")
        raw = self._read_topic()
        raw["status"] = "closed"  # 模拟绕门
        path = self.root / "agent_platform/data/academy/research_topics.json"
        path.write_text(
            json.dumps([raw], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report = aw.audit_report(self.root, store=self.store)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["violations"][0]["topic_id"], "AC-20261017-LCS1")

    def test_audit_pass_on_clean_flow(self):
        report = aw.audit_report(self.root, store=self.store)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["by_status"], {"intake": 1})


# ── M1 轮次化幂等键回灌（Invest 判例对齐，2026-10-17）──────────────
class AcademyRoundKeyTests(unittest.TestCase):
    """decision 打回重批 = 新一轮凭据；崩溃窗口不新增轮次。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        _make_root(self.root, [json.loads(json.dumps(TOPIC))])
        self.store = apv.ApprovalStore(self.root / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _advance_to_joint_review(self):
        for stage in ("hypothesis", "evidence", "validation", "review", "joint_review"):
            aw.move_stage(self.root, "AC-20261017-LCS1", stage, by="ACAD1")

    def _keys(self):
        return [a.idempotency_key for a in self.store.list(target_type="workitem")]

    def test_decision_kickback_then_reapprove_new_round(self):
        self._advance_to_joint_review()
        a1, _, r1 = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="审查员A", store=self.store
        )
        self.assertFalse(r1)
        # 打回：decision → joint_review（自由 move）
        aw.move_stage(
            self.root,
            "AC-20261017-LCS1",
            "joint_review",
            by="老田",
            note="证据口径打回重审",
        )
        # 二轮重批：joint_review → decision（旧实现会重放 a1——判例禁止）
        a2, topic, r2 = aw.approve_transition(
            self.root,
            "AC-20261017-LCS1",
            "decision",
            by="审查员C",
            reason="二轮结题通过",
            store=self.store,
        )
        self.assertFalse(r2)  # 新凭据，非重放
        self.assertNotEqual(a2.approval_id, a1.approval_id)
        self.assertEqual(topic["status"], "decision")
        keys = self._keys()
        self.assertEqual(
            keys.count("academy:AC-20261017-LCS1:joint_review>decision"), 1
        )
        self.assertIn("academy:AC-20261017-LCS1:joint_review>decision#r2", keys)
        latest = [
            a
            for a in self.store.list(target_type="workitem")
            if a.to_status == "decision"
        ][-1]
        self.assertEqual(latest.decided_by, "审查员C")

    def test_crash_window_catchup_no_new_round(self):
        self._advance_to_joint_review()
        a1, _, _ = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="A", store=self.store
        )
        # 模拟崩溃窗口：凭据已落库、真源写入未发生（status 与 history
        # 都无 decision 痕迹）
        path = self.root / "agent_platform/data/academy/research_topics.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw[0]["status"] = "joint_review"
        raw[0]["history"] = [
            h for h in raw[0]["history"] if h.get("stage") != "decision"
        ]
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        a2, topic, r2 = aw.approve_transition(
            self.root, "AC-20261017-LCS1", "decision", by="A", store=self.store
        )
        self.assertTrue(r2)
        self.assertEqual(a2.approval_id, a1.approval_id)  # 复用最新凭据
        self.assertEqual(topic["status"], "decision")  # 真源已补齐
        self.assertEqual(len(self._keys()), 1)  # 不新增轮次凭据


# ── ACL-101：ACAD1 池 SLA 与 30 卡证据年龄口径（只读契约断言，不改实现）──
# 口径来源：compositions/acad1-legion/manifest.yaml `spec.data.sla`；
# 26h = data_date_lag_days(1) × 24h + 2h 日管线余量（与 ops/health_check.py
# `_STALE_MAX_HOURS=26`「每日任务错过一个完整调度日 + 2h 余量」同判例）。
MANIFEST_PATH = ROOT / "compositions" / "acad1-legion" / "manifest.yaml"
POOL_CONTRACT = "acad1-pool/v1"
POOL_MAX_30 = 30
DAILY_CLOSE_HOUR = 15  # 日线收盘时点：证据年龄从数据日 15:00 收盘起算
SLA_REQUIRED = ("freshness_days", "pool_max", "evidence_max_age_h", "data_date_lag_days")
SLA_EXPECTED = {
    "freshness_days": 1,
    "pool_max": 30,
    "evidence_max_age_h": 26,
    "data_date_lag_days": 1,
}


def _load_manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _manifest_sla():
    return _load_manifest()["spec"]["data"]["sla"]


def _evidence_age_hours(generated_at, data_date):
    """证据年龄（小时）= 复核/生成时点 − 数据日收盘时点（15:00）。

    data_date 缺失 → None（unknown，不伪造成 fresh）。
    """
    if data_date is None:
        return None
    close_at = datetime.combine(data_date, time(hour=DAILY_CLOSE_HOUR))
    return (generated_at - close_at).total_seconds() / 3600.0


def _evidence_status(age_hours, max_age_h):
    """证据年龄三态口径：fresh / stale / unknown（缺时点不得判 fresh）。"""
    if age_hours is None:
        return "unknown"
    return "fresh" if age_hours <= max_age_h else "stale"


class AcademyPoolSlaTests(unittest.TestCase):
    """ACL-101：manifest SLA 字段 + 30 卡证据年龄口径只读回归。"""

    def test_manifest_sla_fields_declared(self):
        manifest = _load_manifest()
        self.assertEqual(manifest["apiVersion"], "zerobound/v1")
        self.assertEqual(manifest["kind"], "Composition")
        data = manifest["spec"]["data"]
        self.assertEqual(data["contract"], POOL_CONTRACT)
        sla = data["sla"]
        for key in SLA_REQUIRED:
            self.assertIn(key, sla, "manifest SLA 缺字段 %s" % key)
        for key, expected in SLA_EXPECTED.items():
            value = sla[key]
            self.assertIsInstance(value, int, "%s 必须是整数（单位显式）" % key)
            self.assertNotIsInstance(value, bool)
            self.assertGreater(value, 0)
            self.assertEqual(value, expected, "manifest SLA %s 漂移" % key)
        # 接线声明：池 API/页面必须挂在 manifest 上（声明层真源，不靠页面硬编码）
        exposes = manifest["spec"]["interfaces"]["exposes"]
        self.assertIn({"http": "/api/acad1/pool"}, exposes)
        page_refs = [p.get("ref") for p in manifest["spec"]["pages"]]
        self.assertIn("page/acad1/pool", page_refs)

    def test_manifest_sla_evidence_age_derivation(self):
        sla = _manifest_sla()
        lag = sla["data_date_lag_days"]
        max_age = sla["evidence_max_age_h"]
        # 26h = 1 天数据滞后（24h）+ 2h 日管线余量：必须容纳最大允许滞后
        self.assertEqual(max_age, lag * 24 + 2)
        self.assertGreater(max_age, lag * 24)  # 纯日历滞后之外还要留管线运行时间
        self.assertEqual(sla["freshness_days"], lag)  # 池新鲜度与数据日滞后同口径
        self.assertEqual(sla["pool_max"], POOL_MAX_30)  # 30 卡池上限
        # 26h 严于黄忠验收 48h 认证门（运行门严于认证门），不得反向放宽
        self.assertLessEqual(max_age, 48)

    def test_30_cards_evidence_age_caliber(self):
        sla = _manifest_sla()
        max_age = sla["evidence_max_age_h"]
        reviewed_at = datetime(2026, 11, 4, 17, 0)  # 交易日 D 17:00 日管线复核
        cards = []
        # 28 张当日数据：D 收盘 15:00 → D 17:00 复核 = 2h（正常节奏）
        for idx in range(28):
            cards.append({"candidate_id": "C%02d" % idx, "data_date": date(2026, 11, 4)})
        # 1 张滞后 1 天：D-1 收盘 → D 17:00 = 26h（SLA 边界，仍 fresh）
        cards.append({"candidate_id": "LAG1", "data_date": date(2026, 11, 3)})
        # 1 张滞后 2 天：D-2 收盘 → D 17:00 = 50h（超龄 stale）
        cards.append({"candidate_id": "LAG2", "data_date": date(2026, 11, 2)})
        self.assertEqual(len(cards), POOL_MAX_30)  # 恰为 30 卡池上限
        ages = [_evidence_age_hours(reviewed_at, c["data_date"]) for c in cards]
        statuses = [_evidence_status(age, max_age) for age in ages]
        self.assertEqual(statuses.count("fresh"), 29)
        self.assertEqual(statuses.count("stale"), 1)
        self.assertEqual(statuses.count("unknown"), 0)
        by_id = dict(zip([c["candidate_id"] for c in cards], statuses))
        self.assertEqual(by_id["LAG1"], "fresh")  # 26h 边界不误杀
        self.assertEqual(by_id["LAG2"], "stale")  # 超龄显式失败，不假绿
        # 年龄换算口径：lag0=2h / lag1=26h / lag2=50h
        self.assertEqual(_evidence_age_hours(reviewed_at, date(2026, 11, 4)), 2.0)
        self.assertEqual(_evidence_age_hours(reviewed_at, date(2026, 11, 3)), 26.0)
        self.assertEqual(_evidence_age_hours(reviewed_at, date(2026, 11, 2)), 50.0)

    def test_30_cards_evidence_age_boundary_and_unknown(self):
        sla = _manifest_sla()
        max_age = sla["evidence_max_age_h"]
        reviewed_at = datetime(2026, 11, 4, 17, 0)
        boundary = _evidence_age_hours(reviewed_at, date(2026, 11, 3))  # 26h
        self.assertEqual(_evidence_status(boundary, max_age), "fresh")
        over = boundary + 1.0 / 3600.0  # 26h + 1s
        self.assertEqual(_evidence_status(over, max_age), "stale")
        # 缺数据日/证据时点 → unknown（fail-visible，不按 fresh 计）
        self.assertEqual(
            _evidence_status(_evidence_age_hours(reviewed_at, None), max_age), "unknown"
        )
        # 卡数上限：31 卡超过 pool_max → 溢出显式可判（不静默截断）
        self.assertGreater(POOL_MAX_30 + 1, sla["pool_max"])


if __name__ == "__main__":
    unittest.main()
