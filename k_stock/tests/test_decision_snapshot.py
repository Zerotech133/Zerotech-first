# -*- coding: utf-8 -*-
"""D-1 决策快照测试（K-PLAN-V1-0.1，tmp 隔离，复用 test_paper_engine 桩）。

覆盖契约：
  1. 落单必产快照：orders 非空 + 非 dry_run → decision_snapshots 一行，
     snapshot_id 回填 exec_run 返回；
  2. 快照内容完整：run 全文（决策输入 X）+ orders（决策输出 y 载体）+
     context（last_td/allow_stale/market）+ versions（X-6 版本化纪律）；
  3. combo_id 语义：现阶段 = account_id（组合注册表批 2 替换，字段预留）；
  4. dry_run / 零落单（幂等跳过）不产快照；
  5. 同 run_ref 唯一：exec_run 重放（幂等路径 orders 空）不产第二份；
  6. settle 闭环：按 run_ref 取快照 → 与 orders 关联 → 撮合后可配对后验。
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

from engine import PaperEngine, ENGINE_VERSION  # noqa: E402
import decision_snapshot  # noqa: E402
from market import FakeMarket  # noqa: E402

from test_paper_engine import _bar, _write_run, RUN_ASOF  # noqa: E402


def _mk_engine(tmp_path) -> PaperEngine:
    bars = {
        "600519.SH": [
            _bar("20261008", open_=1244.6, pre_close=1246.2, close=1247.0),
            _bar("20261009", open_=1250.0, pre_close=1247.0, close=1252.0),
        ]
    }
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    return e


def _buy_run(tmp_path, name="poc_snap.json", stock="600519.SH"):
    return _write_run(
        tmp_path,
        name,
        [
            {
                "stock": stock,
                "asof": RUN_ASOF,
                "outputs": {"D1": {"action": "BUY", "confidence": 0.75}},
            }
        ],
    )


def _count(con) -> int:
    decision_snapshot.ensure_table(con)
    return con.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]


def test_order_creates_snapshot_with_full_context(tmp_path):
    e = _mk_engine(tmp_path)
    run = _buy_run(tmp_path)
    r = e.exec_run(run, strategy="qingping", account_id="main")
    assert r["orders"] and r["orders"][0]["status"] == "pending"
    assert r.get("snapshot_id")
    snap = decision_snapshot.by_ref(e.con, "poc_snap.json")
    assert snap and snap["snapshot_id"] == r["snapshot_id"]
    # 决策输入 X：run 全文原样入快照
    assert json.loads(snap["run_content"])[0]["stock"] == "600519.SH"
    # 决策输出 y 载体：orders 含 order_id/plan_date/reason
    o = snap["orders_json"][0]
    assert o["order_id"] == r["orders"][0]["order_id"]
    assert o["plan_date"] == "20261009"
    # 守卫语境 + 版本（X-6）
    assert snap["context_json"]["last_td"] == "20261009"
    assert snap["context_json"]["allow_stale"] is False
    assert snap["versions_json"]["engine"] == ENGINE_VERSION
    assert snap["schema_version"] == decision_snapshot.SNAPSHOT_SCHEMA_VERSION


def test_combo_id_defaults_to_account(tmp_path):
    """combo_id 现阶段 = account_id；四元组注册表（批 2）上线后替换，字段预留。"""
    e = _mk_engine(tmp_path)
    r = e.exec_run(_buy_run(tmp_path), account_id="main")
    assert r["orders"]
    snap = decision_snapshot.by_ref(e.con, "poc_snap.json")
    assert snap["combo_id"] == "main"


def test_dry_run_and_no_orders_produce_no_snapshot(tmp_path):
    e = _mk_engine(tmp_path)
    # dry_run：不落单不落快照
    r0 = e.exec_run(_buy_run(tmp_path, "poc_dry.json"), dry_run=True)
    assert r0["orders"] and "snapshot_id" not in r0
    assert _count(e.con) == 0
    # 真落单一次
    e.exec_run(_buy_run(tmp_path, "poc_ok.json"))
    assert _count(e.con) == 1
    # 同 run 重放（幂等路径 orders 空）→ 不产第二份快照
    r2 = e.exec_run(_buy_run(tmp_path, "poc_ok.json"))
    assert r2["orders"] == [] and "snapshot_id" not in r2
    assert _count(e.con) == 1


def test_settle_pairs_snapshot_to_fills(tmp_path):
    """训练样本对闭环：快照(上下文-决策) + settle(结果) 按 run_ref 关联。"""
    e = _mk_engine(tmp_path)
    e.exec_run(_buy_run(tmp_path))
    snap = decision_snapshot.by_ref(e.con, "poc_snap.json")
    order_id = snap["orders_json"][0]["order_id"]
    s = e.settle()
    assert s["filled"][0]["order_id"] == order_id
    assert s["filled"][0]["price"] == 1250.0  # plan_date 当日开盘
    # 快照的决策 → 成交结果可追溯配对
    row = e.con.execute(
        "SELECT o.filled_price, s.run_ref FROM orders o "
        "JOIN decision_snapshots s ON o.ref LIKE s.run_ref || '#%' "
        "WHERE o.id=?",
        (order_id,),
    ).fetchone()
    assert row["run_ref"] == "poc_snap.json"
    assert row["filled_price"] == pytest.approx(1250.0)
    assert e.verify()["ok"]


def test_same_run_multi_account_two_snapshots(tmp_path):
    """同一 run 落多个子账本（ref 按 @账户分段）→ 每账户一份快照（联合唯一）。"""
    e = _mk_engine(tmp_path)
    run = _buy_run(tmp_path)
    r1 = e.exec_run(run, strategy="qingping", account_id="main")
    r2 = e.exec_run(run, strategy="qingping", account_id="taishan")
    assert r1["orders"] and r2["orders"]
    assert r1["snapshot_id"] != r2["snapshot_id"]
    assert _count(e.con) == 2
    assert (
        decision_snapshot.by_ref(e.con, "poc_snap.json", "main")["combo_id"] == "main"
    )
    assert (
        decision_snapshot.by_ref(e.con, "poc_snap.json", "taishan")["combo_id"]
        == "taishan"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
