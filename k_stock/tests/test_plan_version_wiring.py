# -*- coding: utf-8 -*-
"""WO-QT-20261020-012 验收测试：exec_run→capture 的 plan_version 透传接线。

背景：006 落了 decision_snapshot 的 plan 印记能力（v3 加法），
但 engine.exec_run 未透传 → "快照关联投研预案"生产路径断链。本单补接线。

验收口径：
  1. 带 plan_version 落单 → 快照含 plan 印记（四键齐备）；
  2. 不带 plan_version → 快照零变化（无 plan 字段）；
  3. plan_version 不在账 → 落单失败（fail-visible）且事务回滚
     （不留半截 pending 单、不留脏事务）；
  4. 跟随分支端到端：信号→as_of_plan→落单→快照含 plan_version
     （禁回写防线在链路生效：会后生效版本被拒）。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import decision_snapshot                                  # noqa: E402
import plan_meeting_ledger as pml                         # noqa: E402
import strategy_branch as sb                              # noqa: E402
from engine import PaperEngine                            # noqa: E402
from market import FakeMarket                             # noqa: E402

from test_paper_engine import _bar, _write_run, RUN_ASOF  # noqa: E402
from test_strategy_branch import _signal, COMBO, STOCK    # noqa: E402

PLAN_VERSION = "pv-20261008-001"


def _mk_engine(tmp_path) -> PaperEngine:
    bars = {STOCK: [
        _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
        _bar("20261009", open_=102.0, pre_close=100.0, close=101.0),
    ]}
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    return e


def _plan_stamp(effective_at="2026-10-08T18:00:00") -> dict:
    return {"combo_id": COMBO, "stock": STOCK,
            "plan_version": PLAN_VERSION, "effective_at": effective_at}


def _seed_plan(engine: PaperEngine, effective_at="2026-10-08T18:00:00"):
    """在同一库登记一条预案（plan 表与快照表同库，-006 口径）。"""
    pml.ensure_table(engine.con)
    return pml.add_plan_version(
        engine.con, combo_id=COMBO, stock=STOCK, plan_version=PLAN_VERSION,
        plan={"action": "BUY", "note": "测试预案"},
        risk_profile="aggressive", effective_at=effective_at, source="test")


def _buy_run(tmp_path, name="poc_plan.json"):
    return _write_run(tmp_path, name, [
        {"stock": STOCK, "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.80}}}])


# ---------- 验收①：带 plan_version → 快照含 plan 印记 ----------

def test_snapshot_carries_plan_stamp(tmp_path):
    e = _mk_engine(tmp_path)
    _seed_plan(e)
    out = e.exec_run(_buy_run(tmp_path), account_id="acct_plan",
                     plan_version=_plan_stamp())
    assert out["orders"] and out.get("snapshot_id")
    snap = decision_snapshot.by_ref(e.con, "poc_plan.json", "acct_plan")
    pv = snap["context_json"]["plan_version"]
    assert pv["plan_version"] == PLAN_VERSION
    assert pv["combo_id"] == COMBO and pv["stock"] == STOCK
    assert pv["effective_at"] == "2026-10-08T18:00:00"


# ---------- 验收②：不带 → 零变化 ----------

def test_no_plan_stamp_zero_change(tmp_path):
    e = _mk_engine(tmp_path)
    out = e.exec_run(_buy_run(tmp_path, "poc_noplan.json"), account_id="acct_noplan")
    assert out["orders"]
    snap = decision_snapshot.by_ref(e.con, "poc_noplan.json", "acct_noplan")
    assert "plan_version" not in snap["context_json"]


# ---------- 验收③：不在账 → 落单失败 + 事务回滚 ----------

def test_unknown_plan_version_fails_visible_and_rolls_back(tmp_path):
    e = _mk_engine(tmp_path)
    pml.ensure_table(e.con)          # 建表但不登记该版本
    with pytest.raises(pml.PlanLedgerError):
        e.exec_run(_buy_run(tmp_path, "poc_badplan.json"), account_id="acct_bad",
                   plan_version=_plan_stamp())
    # fail-visible：无落单、无快照
    assert e.history(account_id="acct_bad") == []
    # 事务已回滚：随后一次正常落单不受脏事务影响
    out = e.exec_run(_buy_run(tmp_path, "poc_after.json"), account_id="acct_after")
    assert out["orders"] and out.get("snapshot_id")


def test_backdated_plan_rejected_at_write_time(tmp_path):
    """会后生效的版本（生效时点晚于快照时刻）→ 写入即拒（禁回写）。"""
    e = _mk_engine(tmp_path)
    _seed_plan(e, effective_at="2099-01-01T00:00:00")
    with pytest.raises(pml.PlanBackdateError):
        e.exec_run(_buy_run(tmp_path, "poc_backdate.json"), account_id="acct_bd",
                   plan_version=_plan_stamp(effective_at="2099-01-01T00:00:00"))
    assert e.history(account_id="acct_bd") == []


# ---------- 验收④：跟随分支端到端（快照含 plan_version） ----------

def test_follow_branch_end_to_end_plan_stamp_in_snapshot(tmp_path):
    e = _mk_engine(tmp_path)
    # 接线约束：plan 账与快照账同库（engine 的 paper.db）——快照侧禁回写
    # 校验查的就是这张库，plan 账在别处则 plan_not_found（fail-visible）。
    pml.ensure_table(e.con)
    pml.add_plan_version(
        e.con, combo_id=COMBO, stock=STOCK, plan_version=PLAN_VERSION,
        plan={"action": "BUY", "note": "跟随分支预案"},
        risk_profile="aggressive", effective_at="2026-10-08T18:00:00", source="test")
    runner = sb.BranchRunner(e, receipt_log=tmp_path / "receipts.jsonl",
                              rejection_log=tmp_path / "rejections.jsonl")
    out = runner.feed_signal(_signal(), COMBO, plan_con=e.con)
    res = out["branches"]["follow_research"]
    assert res["status"] == "placed", res
    snap = decision_snapshot.by_ref(e.con, f"signal_{_signal()['signal_id']}.json",
                                    f"{COMBO}@follow_research")
    pv = snap["context_json"]["plan_version"]
    assert pv["plan_version"] == PLAN_VERSION
    assert pv["effective_at"] == "2026-10-08T18:00:00"
