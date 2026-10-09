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


def test_positions_snapshot(tmp_path):
    e = _mk_engine(tmp_path)
    e.buy("600519.SH", 100)
    e.mark("2026-10-09")
    con = e.con
    rows = con.execute(
        "SELECT * FROM positions_snapshot WHERE date='2026-10-09'").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["stock"] == "600519.SH" and r["qty"] == 100
    assert r["available"] == 0                    # T+1 冻结
    assert r["market_value"] == pytest.approx(100 * r["last"])
    # 同日重跑覆盖（先加仓再 mark → 该日仍 1 行且数量更新；加仓超单票限用 force）
    e.market = _mk_market(extra_tail=True)
    e.buy("600519.SH", 100, force=True)
    e.mark("2026-10-09")
    n = con.execute("SELECT COUNT(*) FROM positions_snapshot WHERE date='2026-10-09'").fetchone()[0]
    assert n == 1
    q = con.execute("SELECT qty FROM positions_snapshot WHERE date='2026-10-09'").fetchone()[0]
    assert q == 200
    # 部分卖出：第二手买在 last_td(20261010) 被 T+1 冻结，可卖仅首手 100 股
    e.sell("600519.SH")
    e.mark("2026-10-10")
    r10 = con.execute(
        "SELECT qty FROM positions_snapshot WHERE date='2026-10-10'").fetchone()
    assert r10 is not None and r10["qty"] == 100   # 剩一手
    # 湖再进一日（T+1 解锁）→ 清仓 → 该日明细为空
    mkt = _mk_market(extra_tail=True)
    mkt.bars["600519.SH"].append(
        _bar("20261013", open_=1255.0, pre_close=1258.0, close=1255.0))
    e.market = mkt
    e.sell("600519.SH")
    e.mark("2026-10-13")
    n2 = con.execute("SELECT COUNT(*) FROM positions_snapshot WHERE date='2026-10-13'").fetchone()[0]
    assert n2 == 0
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


# ---------- v0.2: strategy 策略来源标记 ----------

def test_strategy_marked_on_orders_and_positions(tmp_path):
    """AI 单 strategy=ai_team / 手动单默认 manual / 自定义来源贯通到持仓。"""
    e = _mk_engine(tmp_path)
    run = _write_run(tmp_path, "poc_st.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.75}}}])
    e.exec_run(run)
    od = e.con.execute("SELECT strategy, order_kind FROM orders WHERE ref LIKE 'poc_st.json%'").fetchone()
    assert od["strategy"] == "ai_team" and od["order_kind"] == "market"
    e.settle()
    assert e.con.execute("SELECT strategy FROM positions WHERE stock='600519.SH'").fetchone()["strategy"] == "ai_team"
    # 手动单：默认 manual（force：AI 单已占 20% 单票限额，测试场景非测风控）
    e.buy("600519.SH", 100, force=True)
    od2 = e.con.execute(
        "SELECT strategy FROM orders WHERE source='manual' ORDER BY id DESC LIMIT 1").fetchone()
    assert od2["strategy"] == "manual"
    # 自定义策略来源（双刀 adapter 将用此路径）
    e.buy("600519.SH", 100, force=True, strategy="double_knife")
    od3 = e.con.execute(
        "SELECT strategy FROM orders WHERE source='manual' ORDER BY id DESC LIMIT 1").fetchone()
    assert od3["strategy"] == "double_knife"
    assert e.verify()["ok"]


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """旧库（无 v0.2 列）打开时自动 ALTER 升级，数据无损。"""
    import sqlite3 as sq
    legacy = tmp_path / "legacy.db"
    con = sq.connect(str(legacy))
    con.executescript("""
      CREATE TABLE account(id INTEGER PRIMARY KEY CHECK(id=1), cash REAL, initial_cash REAL, created_at TEXT);
      CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, ref TEXT DEFAULT '',
        stock TEXT, side TEXT, qty INTEGER, plan_date TEXT, price_base TEXT, manual_price REAL,
        status TEXT, fill_date TEXT, filled_price REAL, filled_qty INTEGER, fee REAL,
        reason TEXT, conf REAL, created_at TEXT);
      CREATE TABLE positions(stock TEXT PRIMARY KEY, qty INTEGER, avg_cost REAL, updated_at TEXT);
      CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, stock TEXT,
        qty INTEGER, amount REAL, cash_after REAL, order_id INTEGER);
      CREATE TABLE equity_marks(date TEXT PRIMARY KEY, total REAL, cash REAL, market_value REAL,
        pos_pct REAL, pending INTEGER, verify_ok INTEGER, ts TEXT);
      CREATE TABLE positions_snapshot(date TEXT, stock TEXT, qty INTEGER, avg_cost REAL, last REAL,
        market_value REAL, pnl REAL, available INTEGER, PRIMARY KEY(date, stock));
      INSERT INTO account VALUES(1, 1000000.0, 1000000.0, '2026-10-08');
      INSERT INTO ledger VALUES(1, '2026-10-08', 'deposit', NULL, 1000000.0, 1000000.0, NULL, NULL);
    """)
    con.commit()
    con.close()
    e = PaperEngine(legacy, _mk_engine(tmp_path).market)
    cols = {r["name"] for r in e.con.execute("PRAGMA table_info(orders)").fetchall()}
    assert {"strategy", "account_id", "order_kind"} <= cols
    pcols = {r["name"] for r in e.con.execute("PRAGMA table_info(positions)").fetchall()}
    assert "stop_price" in pcols
    assert e.verify()["ok"]  # 旧数据对账不受迁移影响


# ---------- v0.2: 手动止损 ----------

def test_stop_set_clear_and_validation(tmp_path):
    e = _mk_engine(tmp_path)
    with pytest.raises(Reject):  # 无持仓不能设止损
        e.set_stop("600519.SH", 1200.0)
    e.buy("600519.SH", 100)
    r = e.set_stop("600519.SH", 1200.0)
    assert r["stop_price"] == 1200.0
    with pytest.raises(Reject):
        e.set_stop("600519.SH", -1)  # 非法价格
    rows = e.stops()
    assert len(rows) == 1 and rows[0]["stock"] == "600519.SH"
    e.clear_stop("600519.SH")
    assert e.stops() == []


def test_check_stops_triggers_pending_sell(tmp_path):
    """收盘 < 止损价 → 生成 SELL pending（stop_loss/stop），次日开盘撮合清仓。"""
    e = _mk_engine(tmp_path)                    # 湖尾=1009：buy 成交于 1009
    e.buy("600519.SH", 100)                     # fill_date=1009
    # 把湖推进到 1010：换带尾 bar 的 market（1010 收 1258 跌破止损）
    e.market = _mk_engine(tmp_path, extra_tail=True).market
    e.set_stop("600519.SH", 1270.0)             # 1010 收 1258 < 1270 → 触发
    r = e.check_stops()
    assert len(r["triggered"]) == 1 and r["triggered"][0].get("status") == "pending"
    od = e.con.execute(
        "SELECT * FROM orders WHERE ref LIKE 'stop#%'").fetchone()
    assert od["side"] == "SELL" and od["strategy"] == "stop_loss"
    assert od["order_kind"] == "stop" and od["status"] == "pending"
    # 幂等：再查不重复
    r2 = e.check_stops()
    assert all("幂等" in t["why"] for t in r2["triggered"])
    assert e.verify()["ok"]


def test_check_stops_no_trigger_when_above(tmp_path):
    e = _mk_engine(tmp_path)
    e.buy("600519.SH", 100)
    e.set_stop("600519.SH", 1000.0)             # 现价 1252 远高于止损
    r = e.check_stops()
    assert r["triggered"] == [] and len(r["watch"]) == 1
    assert e.con.execute("SELECT COUNT(*) FROM orders WHERE order_kind='stop'").fetchone()[0] == 0


def test_portal_status_projection(tmp_path, monkeypatch):
    """投影层契约：strategy 贯通 / stops 清单 / 历史回看 / 坏日期 fail-soft。"""
    import portal_status  # noqa: E402
    db = tmp_path / "paper.db"
    monkeypatch.setattr(portal_status, "DB", db)
    e = PaperEngine(db, _mk_market())
    e.buy("600519.SH", 100, force=True)
    e.set_stop("600519.SH", 1000.0)
    e.mark("2026-10-09")
    s = portal_status.status()
    assert s["ok"] and s["as_of"] == "2026-10-09"
    p = s["positions"][0]
    assert p["strategy"] == "manual" and p["account_id"] == "main"
    st = s["stops"][0]
    assert st["stock"] == "600519.SH" and st["stop_price"] == 1000.0
    assert st["distance_pct"] is not None       # 快照有现价 → 触发距离可算
    assert portal_status.snapshot_days() == ["2026-10-09"]
    h = portal_status.history("2026-10-09")
    assert h["ok"] and h["mark"]["total"] == s["latest"]["total"]
    assert h["positions"][0]["strategy"] == "manual"
    bad = portal_status.history("1999-01-01")
    assert not bad["ok"] and bad["available_days"] == ["2026-10-09"]
