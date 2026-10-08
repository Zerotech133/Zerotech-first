# -*- coding: utf-8 -*-
"""模拟仓引擎测试网 v0.1（FakeMarket 注入，不碰 pandas / 不碰真实行情湖）。

覆盖：建账/重置、手动即时成交与费率、整手与价格区间、T+1 冻结、
涨跌停撮合拦截、风控三条+force、现金不足、exec_run（BUY/HOLD/SELL/error/
幂等/dry-run/目标仓位算术）、settle 开盘价撮合、资金链对账。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

from engine import PaperEngine, Reject, LOT   # noqa: E402
from market import FakeMarket                 # noqa: E402


def _bar(td: str, open_: float, pre_close: float, close: float | None = None,
         high: float | None = None, low: float | None = None) -> dict:
    c = close if close is not None else open_
    return {"trade_date": td, "open": open_,
            "high": high if high is not None else max(open_, c) * 1.01,
            "low": low if low is not None else min(open_, c) * 0.99,
            "close": c, "pre_close": pre_close}


def _mk_market(extra_tail: bool = False) -> FakeMarket:
    """600519: D1=1008 收 1247 / D2=1009 开 1250 / (D3=1010 开 1260)。"""
    bars = {"600519.SH": [
        _bar("20261008", open_=1244.6, pre_close=1246.2, close=1247.0),
        _bar("20261009", open_=1250.0, pre_close=1247.0, close=1252.0),
    ]}
    if extra_tail:
        bars["600519.SH"].append(_bar("20261010", open_=1260.0, pre_close=1252.0, close=1258.0))
    # 一字涨停票（pre_close 100 → 涨停 110，D2 开盘即涨停）
    bars["000002.SZ"] = [
        _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
        _bar("20261009", open_=110.0, pre_close=100.0, close=110.0, high=110.0, low=110.0),
    ]
    # 高价票用于风控（1500 元/股）
    bars["600001.SH"] = [
        _bar("20261008", open_=1500.0, pre_close=1500.0, close=1500.0),
        _bar("20261009", open_=1500.0, pre_close=1500.0, close=1500.0),
    ]
    return FakeMarket(bars)


def _mk_engine(tmp_path, extra_tail: bool = False) -> PaperEngine:
    return PaperEngine(tmp_path / "paper.db", _mk_market(extra_tail))


RUN_ASOF = "2026-10-09T07:05:00"  # 盘前决策 → plan_date 应为当日 20261009


def _write_run(tmp_path, name: str, items: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- 建账 / 重置 ----------

def test_account_bootstrap_and_reset_guard(tmp_path):
    e = _mk_engine(tmp_path)
    acc = e.ensure_account()
    assert acc["cash"] == 1_000_000.0
    with pytest.raises(Reject):  # reset 必须显式确认
        e.reset()
    r = e.reset(initial_cash=500_000.0, yes=True)
    assert r["cash"] == 500_000.0
    assert e.verify()["ok"]


# ---------- 手动即时成交 ----------

def test_manual_buy_fill_and_fees(tmp_path):
    e = _mk_engine(tmp_path)
    r = e.buy("600519.SH", 100)  # 按 D2 收盘 1252 成交
    fee = round(max(5, 100 * 1252.0 * 0.00025), 2)  # 31.3
    assert r["qty"] == 100 and r["price"] == 1252.0
    assert r["fee"] == fee
    pos = e.positions()[0]
    assert pos["qty"] == 100
    assert pos["avg_cost"] == pytest.approx((125200 + fee) / 100)
    assert e.verify()["ok"]


def test_lot_and_manual_price_bounds(tmp_path):
    e = _mk_engine(tmp_path)
    with pytest.raises(Reject):  # 非整手
        e.buy("600519.SH", 50)
    with pytest.raises(Reject):  # 指定价超出最近 bar 区间
        e.buy("600519.SH", 100, price=999.0)
    r = e.buy("600519.SH", 100, price=1250.0)  # 区间内指定价 OK
    assert r["price"] == 1250.0


# ---------- T+1 ----------

def test_t1_freeze_then_unlock(tmp_path):
    e = _mk_engine(tmp_path, extra_tail=False)  # last_td = 20261009
    e.buy("600519.SH", 100)                     # fill_date = 20261009 = last_td
    assert e.available_qty("600519.SH") == 0
    with pytest.raises(Reject):                 # 当日买入不可卖
        e.sell("600519.SH")
    e.market = _mk_market(extra_tail=True)      # 湖更新出现 20261010
    assert e.available_qty("600519.SH") == 100
    r = e.sell("600519.SH")                     # 解锁后可卖
    assert r["qty"] == 100
    assert e.verify()["ok"]


# ---------- 涨跌停撮合拦截 ----------

def test_settle_limit_up_rejects_buy(tmp_path):
    e = _mk_engine(tmp_path)
    run = _write_run(tmp_path, "poc_t1.json", [
        {"stock": "000002.SZ", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}},
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}},
    ])
    r = e.exec_run(run)
    pend = [o for o in r["orders"] if o.get("status") == "pending"]
    assert len(pend) == 2
    s = e.settle()
    by_stock = {x["stock"]: x for x in s["rejected"]}
    assert "000002.SZ" in by_stock and "涨停" in by_stock["000002.SZ"]["why"]
    filled = {x["stock"]: x for x in s["filled"]}
    assert filled["600519.SH"]["price"] == 1250.0
    assert e.verify()["ok"]


# ---------- 风控三条 ----------

def test_risk_rules_and_force(tmp_path):
    e = _mk_engine(tmp_path)
    e.buy("600001.SH", 100)              # 一手 15 万（15%）：单票合规 → 正常成交
    with pytest.raises(Reject):          # 加仓致单票超限：600001 再一手 → 30 万 > 20 万限
        e.buy("600001.SH", 100)
    # 总仓 >95%：force 打到 90%，再正常买触发总仓限（单票合规）
    e2 = _mk_engine(tmp_path / "sub")
    e2.buy("600001.SH", 200, force=True)  # 30 万
    e2.buy("600001.SH", 200, force=True)  # 累计 60 万（单票限被 force 跳过）
    e2.buy("600001.SH", 200, force=True)  # 累计 90 万 → 总仓 90%
    with pytest.raises(Reject):           # 9 手 000002 = 9.9 万：单票合规，总仓 99.9% > 95%
        e2.buy("000002.SZ", 900)
    assert e2.verify()["ok"]


def test_cash_guard_survives_force(tmp_path):
    e = _mk_engine(tmp_path)
    with pytest.raises(Reject):  # 现金不足不可被 force 绕过
        e.buy("600001.SH", 1000, force=True)  # 150 万 > 现金 100 万


# ---------- exec_run ----------

def test_exec_run_plan_and_idempotency(tmp_path):
    e = _mk_engine(tmp_path)
    run = _write_run(tmp_path, "poc_t2.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}},
        {"stock": "000002.SZ", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "HOLD", "confidence": 0.5}}},
        {"stock": "600001.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "SELL", "confidence": 0.6}}},
        {"stock": "000003.SZ", "asof": RUN_ASOF, "error": "boom"},
    ])
    r = e.exec_run(run)
    buy = next(o for o in r["orders"] if o["stock"] == "600519.SH")
    assert buy["status"] == "pending" and buy["plan_date"] == "20261009"  # 盘前 → 当日
    assert buy["qty"] == 100  # 20% 目标 = 20 万 / (1247*1.01*100) ≈ 1.58 → 1 手
    sell = next(o for o in r["orders"] if o["stock"] == "600001.SH")
    assert sell["status"] == "cancelled" and "无可卖" in sell["reason"]
    assert all(s["stock"] in ("000002.SZ", "000003.SZ") for s in r["skipped"])
    # 幂等：同 run 重跑，已落单的两股跳过原因=幂等，且零新订单
    n_before = len(e.history(100))
    r2 = e.exec_run(run)
    by_stock = {s["stock"]: s["why"] for s in r2["skipped"]}
    assert "幂等" in by_stock["600519.SH"] and "幂等" in by_stock["600001.SH"]
    assert len(e.history(100)) == n_before
    # dry-run：不写库
    run3 = _write_run(tmp_path, "poc_t3.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.5}}}])
    n_before = len(e.history(100))
    r3 = e.exec_run(run3, dry_run=True)
    assert r3["orders"][0]["qty"] == 100
    assert len(e.history(100)) == n_before


def test_exec_target_math_low_conf(tmp_path):
    e = _mk_engine(tmp_path)
    run = _write_run(tmp_path, "poc_t4.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.375}}},  # 目标 10% = 10 万 → 不足一手
    ])
    r = e.exec_run(run)
    assert r["orders"][0]["status"] == "cancelled" and "不足一手" in r["orders"][0]["reason"]


# ---------- 全链路：exec → settle → T+1 → SELL 清仓 ----------

def test_full_cycle_buy_hold_sell(tmp_path):
    e = _mk_engine(tmp_path, extra_tail=False)
    run = _write_run(tmp_path, "poc_full.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}}])
    e.exec_run(run)
    s = e.settle()
    assert s["filled"][0]["price"] == 1250.0
    fee = round(max(5, 125000 * 0.00025), 2)  # 31.25
    assert s["filled"][0]["fee"] == fee
    # 当日（20261009=last_td）exec SELL → T+1 冻结 → cancelled
    run_sell = _write_run(tmp_path, "poc_sell0.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "SELL", "confidence": 0.7}}}])
    r0 = e.exec_run(run_sell)
    assert r0["orders"][0]["status"] == "cancelled"
    # 次日（湖更新）→ 新 run 文件（真实场景每日新决策产物）exec SELL → settle 清仓
    e.market = _mk_market(extra_tail=True)
    run_sell2 = _write_run(tmp_path, "poc_sell1.json", [
        {"stock": "600519.SH", "asof": "2026-10-10T07:05:00",
         "outputs": {"D1": {"action": "SELL", "confidence": 0.7}}}])
    r1 = e.exec_run(run_sell2)
    assert r1["orders"][0]["status"] == "pending"
    assert r1["orders"][0]["qty"] == 100
    s2 = e.settle()
    assert s2["filled"][0]["price"] == 1260.0
    eq = e.equity()
    assert eq["positions"] == [] and eq["cash"] == eq["total"]
    v = e.verify()
    assert v["ok"] and v["positions_consistent"]


# ---------- 湖滞后：plan_date 预测 + settle 纠偏 ----------

def test_plan_date_prediction_and_settle_realign(tmp_path):
    """07:05 决策时湖最新=昨日（滞后一天）：plan_date 按工作日预测当日，
    settle 时湖已补上当日 bar → 按当日开盘成交。"""
    # 湖只到 20261008（决策日 20261009 尚未入库，模拟 07:05 真实状态）
    bars = {"600519.SH": [_bar("20261008", open_=1244.6, pre_close=1246.2, close=1247.0)]}
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    run = _write_run(tmp_path, "poc_lag.json", [
        {"stock": "600519.SH", "asof": "2026-10-09T07:05:00",
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}}])
    r = e.exec_run(run)
    assert r["orders"][0]["status"] == "pending"
    assert r["orders"][0]["plan_date"] == "20261009"  # 工作日预测（盘前→当日）
    # settle 早了（湖未更新）→ 保持 pending
    s0 = e.settle()
    assert s0["filled"] == [] and len(e.pending()) == 1
    # 湖补上 20261009 → settle 纠偏成交
    e.market = _mk_market(extra_tail=False)
    s1 = e.settle()
    assert s1["filled"][0]["price"] == 1250.0
    assert s1["filled"][0]["fill_date"] == "20261009"
    assert e.verify()["ok"]


# ---------- 权益日快照 ----------

def test_mark_and_curve(tmp_path):
    e = _mk_engine(tmp_path)
    e.buy("600519.SH", 100)                       # ≈12.52 万
    m1 = e.mark("2026-10-09")
    assert m1["verify_ok"] and m1["pending"] == 0
    # 同日重跑覆盖（幂等）
    e.mark("2026-10-09")
    # 次日快照
    e.market = _mk_market(extra_tail=True)
    e.sell("600519.SH")                           # 清仓，现金回升
    m2 = e.mark("2026-10-10")
    assert m2["pos_pct"] == 0.0
    curve = e.curve()
    assert len(curve) == 2                        # 同日覆盖后仍是 2 行
    assert curve[0]["date"] == "2026-10-09" and curve[1]["date"] == "2026-10-10"
    # 清仓后 total == 现金（空仓），且不等于首日 total（中间有真实买卖盈亏与费用）
    assert curve[1]["total"] == pytest.approx(curve[1]["cash"])
    assert abs(curve[1]["total"] - curve[0]["total"]) > 1.0
    assert e.verify()["ok"]


# ---------- settle 正常路径不误伤 ----------

def test_settle_normal_path_not_blocked(tmp_path):
    """settle 风控复核对正常 AI 单（目标 ≤20%）不拦截。"""
    e = _mk_engine(tmp_path)
    run = _write_run(tmp_path, "poc_rv.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}}])
    e.exec_run(run)
    s = e.settle()
    assert len(s["filled"]) == 1
    assert e.verify()["ok"]
