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

from engine import PaperEngine, Reject   # noqa: E402
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


# ---------- v0.3 多交易员子账本 ----------

def test_multi_account_isolation(tmp_path):
    """子账本完全隔离：同库各户独立现金/持仓/订单，互不可见；档案风控独立生效。"""
    db = tmp_path / "paper.db"
    main = PaperEngine(db, _mk_market(), account="main")
    t = PaperEngine(db, _mk_market(), account="taishan",
                    config={"max_pos_pct": 0.30})   # 档案风控放宽（可买 200 股）
    r = t.buy("600519.SH", 200)
    assert r["account_id"] == "taishan"
    main.buy("600519.SH", 100)
    assert main.positions("main")[0]["qty"] == 100
    assert t.positions("taishan")[0]["qty"] == 200
    assert main.available_qty("600519.SH", "main") == 0   # 各自当日买入各自冻结
    assert t.available_qty("600519.SH", "taishan") == 0
    assert main.equity("main")["cash"] != t.equity("taishan")["cash"]
    assert main.verify("main")["ok"] and t.verify("taishan")["ok"]
    aids = {a["account_id"] for a in PaperEngine(db, _mk_market()).accounts()}
    assert aids == {"main", "taishan"}


def test_t1_frozen_per_account(tmp_path):
    """T+1 冻结按账户独立：各户按自己的买入日冻结，湖推进后各自解锁。"""
    db = tmp_path / "paper.db"
    mkt = _mk_market()
    main = PaperEngine(db, mkt, account="main")
    t = PaperEngine(db, mkt, account="taishan")
    main.buy("600519.SH", 100)          # 两户都在 1009 买入 → 各自冻结
    t.buy("600519.SH", 100)
    assert main.available_qty("600519.SH", "main") == 0
    assert t.available_qty("600519.SH", "taishan") == 0
    tail = _mk_market(extra_tail=True)  # 湖推进（1010 入库）→ 1009 买入解锁
    main.market = tail
    t.market = tail
    assert main.available_qty("600519.SH", "main") == 100
    assert t.available_qty("600519.SH", "taishan") == 100


def test_subaccount_ref_segmented_idempotent(tmp_path):
    """同一条 run 落 main 与子账本：ref 按 @账户 分段互不冲突，各自幂等。"""
    db = tmp_path / "paper.db"
    run = tmp_path / "poc_x.json"
    item = {"stock": "600519.SH", "asof": RUN_ASOF,
            "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}}
    run.write_text(json.dumps([item]), encoding="utf-8")
    mkt = _mk_market()
    main = PaperEngine(db, mkt, account="main")
    chaser = PaperEngine(db, mkt, account="chaser")
    r1 = main.exec_run(run, account_id="main")
    r2 = chaser.exec_run(run, account_id="chaser")
    assert len([o for o in r1["orders"] if o.get("status") == "pending"]) == 1
    assert len([o for o in r2["orders"] if o.get("status") == "pending"]) == 1
    refs = {row["ref"] for row in PaperEngine(db, mkt).history(10, account_id="chaser")}
    assert any(r.startswith("poc_x.json@chaser#") for r in refs), refs   # 子账本 ref 带 @段
    # 各自重跑 → 幂等跳过（互不误伤）
    r1b = main.exec_run(run, account_id="main")
    r2b = chaser.exec_run(run, account_id="chaser")
    assert all("幂等" in s["why"] for s in r1b["skipped"])
    assert all("幂等" in s["why"] for s in r2b["skipped"])


def test_min_conf_filters_buy_only(tmp_path):
    """min_conf（交易员 conf_floor）只拦 BUY；低于门槛 skip、达标 pending。"""
    run = tmp_path / "poc_y.json"
    items = [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.6}}},
        {"stock": "600001.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.9}}},
    ]
    run.write_text(json.dumps(items), encoding="utf-8")
    e = PaperEngine(tmp_path / "paper.db", _mk_market(), account="chaser")
    r = e.exec_run(run, account_id="chaser", min_conf=0.7)
    pend = [o for o in r["orders"] if o.get("status") == "pending"]
    assert len(pend) == 1 and pend[0]["stock"] == "600001.SH"
    assert any("交易员门槛" in s["why"] and s["stock"] == "600519.SH"
               for s in r["skipped"])


def test_stops_per_account(tmp_path):
    """止损按账户独立：main 触发、子账本观察不触发；main 触发单 ref 保持旧格式。"""
    db = tmp_path / "paper.db"
    mkt = _mk_market()
    main = PaperEngine(db, mkt, account="main")
    t = PaperEngine(db, mkt, account="taishan")
    main.buy("600519.SH", 100)                  # fill 1009
    t.buy("600519.SH", 100)
    main.set_stop("600519.SH", 1270.0)          # main：1010 收 1258 < 1270 → 触发
    t.set_stop("600519.SH", 1200.0)             # taishan：1258 ≥ 1200 → 观察
    tail = _mk_market(extra_tail=True)          # 湖推进 → 1009 买入解锁（可卖 100）
    main.market = tail
    t.market = tail
    rm = main.check_stops(account_id="main")
    rt = t.check_stops(account_id="taishan")
    assert len([x for x in rm["triggered"] if x.get("status") == "pending"]) == 1
    assert len([x for x in rt["triggered"] if x.get("status") == "pending"]) == 0
    assert len(rt["watch"]) == 1
    mref = next(o["ref"] for o in main.history(5, account_id="main")
                if o["order_kind"] == "stop")
    assert mref == "stop#600519.SH#20261010"    # main 保持旧格式（历史兼容）


def test_snapshot_pk_multi_account(tmp_path):
    """同日同票两账户权益/持仓快照共存（PK 含 account_id），门户投影仍只回 main。"""
    import portal_status
    db = tmp_path / "paper.db"
    mkt = _mk_market()
    main = PaperEngine(db, mkt, account="main")
    t = PaperEngine(db, mkt, account="taishan")
    main.buy("600519.SH", 100)
    t.buy("600519.SH", 100)
    main.mark("2026-10-09", account_id="main")
    t.mark("2026-10-09", account_id="taishan")   # 同日同票，不撞主键
    n = main.con.execute("SELECT COUNT(*) FROM positions_snapshot").fetchone()[0]
    assert n == 2
    monkey = pytest.MonkeyPatch()
    monkey.setattr(portal_status, "DB", db)
    try:
        s = portal_status.status()
        assert s["ok"] and len(s["positions"]) == 1          # 只回 main 的持仓
        assert s["positions"][0]["qty"] == 100
        assert s["positions"][0]["account_id"] == "main"
    finally:
        monkey.undo()


def test_exec_run_skips_bad_stock(tmp_path):
    """exec_run 单票异常隔离：湖外股票 skip（不炸整个 run），正常票照常落单。"""
    run = tmp_path / "poc_z.json"
    items = [
        {"stock": "000999.SZ", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}},   # 湖外票
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}},
    ]
    run.write_text(json.dumps(items), encoding="utf-8")
    e = PaperEngine(tmp_path / "paper.db", _mk_market())
    r = e.exec_run(run)
    pend = [o for o in r["orders"] if o.get("status") == "pending"]
    assert len(pend) == 1 and pend[0]["stock"] == "600519.SH"
    assert len(r["skipped"]) == 1 and "落单失败跳过" in r["skipped"][0]["why"]
    assert r["skipped"][0]["stock"] == "000999.SZ"


# ---------- v0.4 AsOfMarket 时点视图 + replay 历史重放预筛 ----------

def test_asof_market_view():
    """AsOfMarket：last_bar/trading_days 停在视角日、end 窗口不预支未来、
    wall clock 超前视角时按「视角日盘后」取严格后继。"""
    from market import AsOfMarket
    m = _mk_market(extra_tail=True)
    v = AsOfMarket(m, view="20261009", end="20261010")
    assert v.last_bar("600519.SH")["trade_date"] == "20261009"
    assert v.last_bar("600519.SH")["close"] == 1252.0
    assert v.trading_days() == ["20261008", "20261009"]
    assert v.next_trading_day("2027-01-01T10:00:00") == "20261010"     # wall clock 超前→视角日严格后继
    v.view = "20261010"                       # 视角逐日推进
    assert v.last_bar("600519.SH")["trade_date"] == "20261010"
    assert v.next_trading_day("2027-01-01T10:00:00") is None           # 视角日盘后+窗口尾→无后继
    v2 = AsOfMarket(m, view="20261009", end="20261009")
    assert v2.first_bar_on_or_after("600519.SH", "20261010") is None   # 窗外不预支
    assert v2.next_trading_day("2026-10-09T15:00:00") is None          # 窗外无后继


def test_replay_prefilter_tmpdb(tmp_path):
    """trainee.replay：临时库重放不碰真账本，逐日 settle→止损→mark 全语义，
    重放 2 天 < 毕业线 → provisional 判定。"""
    import trainee as T
    bars = {"600519.SH": [
        _bar("20261008", open_=1244.6, pre_close=1246.2, close=1247.0),
        _bar("20261009", open_=1250.0, pre_close=1247.0, close=1252.0),
        _bar("20261010", open_=1260.0, pre_close=1252.0, close=1258.0),
        _bar("20261013", open_=1258.0, pre_close=1258.0, close=1265.0),
    ]}
    m = FakeMarket(bars)
    run = _write_run(tmp_path, "poc_rp.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}}])
    prof = {"id": "rp_t", "name": "重放测试员", "status": "training",
            "initial_cash": 1_000_000.0,
            "params": {"max_pos_pct": 0.3, "max_total_pct": 0.9,
                       "stop_dist_pct": 0.04, "conf_floor": 0.60},
            "graduation": {"min_days": 20, "max_drawdown_pct": 15}}
    r = T.replay(run, start="20261009", end="20261013", market=m, profiles=[prof])
    assert r["ok"] and r["window"]["days"] == 3
    assert r["window"]["start"] == "20261009" and r["window"]["end"] == "20261013"
    s = r["rows"][0]
    assert s["id"] == "rp_t" and s["placed"] == 1
    assert s["days"] == 3 and s["cum_pct"] > 0        # 1250 买入 → 1013 收 1265 浮盈
    assert s["verify_ok"] is True                     # 对账过
    assert s["stop_hits"] == 0                        # 未触发止损
    assert s["sharpe"] is not None                    # 3 天快照 → 日收益 2 个可算
    assert s["verdict"].startswith("provisional")     # 3 天 < 毕业线 20 天


def test_replay_prefilter_stop_hit(tmp_path):
    """replay 止损语义：跌破止损价 → check_stops 触发 → 次日开盘卖出，stop_hits 计数。"""
    import trainee as T
    bars = {"600519.SH": [
        _bar("20261008", open_=1244.6, pre_close=1246.2, close=1247.0),
        _bar("20261009", open_=1250.0, pre_close=1247.0, close=1252.0),
        _bar("20261010", open_=1150.0, pre_close=1252.0, close=1148.0),   # 崩 -8.3%
        _bar("20261013", open_=1140.0, pre_close=1148.0, close=1135.0),
    ]}
    m = FakeMarket(bars)
    run = _write_run(tmp_path, "poc_rp2.json", [
        {"stock": "600519.SH", "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}}])
    prof = {"id": "rp_s", "name": "止损测试员", "status": "training",
            "initial_cash": 1_000_000.0,
            "params": {"max_pos_pct": 0.3, "max_total_pct": 0.9,
                       "stop_dist_pct": 0.04, "conf_floor": 0.60},
            "graduation": {"min_days": 20, "max_drawdown_pct": 15}}
    r = T.replay(run, start="20261009", end="20261013", market=m, profiles=[prof])
    s = r["rows"][0]
    # 1009 买 1250（stop≈1200）→ 1010 收 1148 < 1200 触发 → SELL pending plan 1013
    # → 1013 开 1140 撮合卖出
    assert s["stop_hits"] == 1
    assert s["cum_pct"] < 0                           # 止损离场实亏
    assert s["verify_ok"] is True                     # 卖出后现金对账仍平
