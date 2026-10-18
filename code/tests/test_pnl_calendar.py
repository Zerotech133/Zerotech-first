"""盈亏日历测试：V1「每日权益盈亏」口径（持仓浮动盈亏逐日盯市，无交易日也有盈亏）。

纯函数测试不落库；集成测试走 TestClient + Postgres 真实数据（无库则跳过）。
"""
from __future__ import annotations

from datetime import UTC, date

import pytest

from zhice_os_v2.application.services.pnl_calendar_service import (
    build_calendar_payload,
    compute_daily_pnl,
)

_D1, _D2, _D3, _D4 = (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4))


def _lot(**kw):
    base = {
        "symbol": "600000.SH",
        "qty": 100.0,
        "open_price": 10.0,
        "open_date": _D1,
        "close_date": None,
        "close_price": None,
    }
    base.update(kw)
    return base


# ─── 纯函数：compute_daily_pnl ────────────────────────────────────────────────


def test_daily_pnl_holding_day():
    """开仓日基准=开仓价（当日盈亏 0）；次日按收盘盯市 (11-10)*100。"""
    lots = [_lot()]
    closes = {("600000.SH", _D1): 10.0, ("600000.SH", _D2): 11.0}
    res, _counts = compute_daily_pnl(lots, closes, [_D1, _D2], sell_cost=0.0)
    assert res[_D1] == 0.0
    assert res[_D2] == 100.0


def test_daily_pnl_close_day():
    """平仓当日价=平仓价×(1-卖出费率)；其后不再计该批次。"""
    lots = [_lot(close_date=_D3, close_price=12.0)]
    closes = {
        ("600000.SH", _D1): 10.0,
        ("600000.SH", _D2): 10.5,
        ("600000.SH", _D3): 11.0,
        ("600000.SH", _D4): 9.0,
    }
    res, _counts = compute_daily_pnl(lots, closes, [_D1, _D2, _D3, _D4], sell_cost=0.001)
    assert res[_D2] == 50.0  # (10.5-10)*100
    assert res[_D3] == round((12.0 * 0.999 - 10.5) * 100, 2)  # 平仓价扣费 11.988
    assert res[_D4] == 0.0  # 已平仓，不再计


def test_no_trade_day_still_counts():
    """核心诉求：无开仓无平仓的交易日，在仓批次照常按收盘算盈亏。"""
    lots = [_lot()]
    closes = {("600000.SH", _D1): 10.0, ("600000.SH", _D2): 10.5, ("600000.SH", _D3): 10.8}
    res, _counts = compute_daily_pnl(lots, closes, [_D1, _D2, _D3], sell_cost=0.0)
    assert res[_D2] == 50.0  # (10.5-10)*100
    assert res[_D3] == 30.0  # (10.8-10.5)*100


def test_gap_base_fallback():
    """前日缺K（停牌）：当日不计变动；次日基准向前找最近可用收盘。"""
    lots = [_lot()]
    closes = {("600000.SH", _D1): 10.0, ("600000.SH", _D3): 11.0}  # _D2 缺
    res, _counts = compute_daily_pnl(lots, closes, [_D1, _D2, _D3], sell_cost=0.0)
    assert res[_D2] == 0.0  # 当日无价 → 不计
    assert res[_D3] == 100.0  # 基准回退到 _D1 收盘 10，价 11


def test_daily_pnl_multi_lot_sum():
    """多批次多标的当日求和。"""
    lots = [
        _lot(symbol="600000.SH", qty=100.0, open_price=10.0),
        _lot(symbol="000001.SZ", qty=200.0, open_price=5.0, open_date=_D2),
    ]
    closes = {
        ("600000.SH", _D1): 10.0,
        ("600000.SH", _D2): 11.0,
        ("000001.SZ", _D2): 5.5,
    }
    res, counts = compute_daily_pnl(lots, closes, [_D1, _D2], sell_cost=0.0)
    assert res[_D2] == 200.0  # 100*1 + 200*0.5
    assert counts[_D2] == 2  # 当日在仓批次数


def test_closed_lot_anchored_to_pnl_amount():
    """统一扣费：平仓 lot 提供 pnl_amount → 该 lot 日次盯市合计精确 = pnl_amount
    （把费用/滑点结算在平仓日），使月度/累计与账户已实现净额分毫对齐。"""
    # 毛盯市：平仓日 (12-10.5)*100=150、次日 (10.5-10)*100=50 → 毛合计约 200；
    # 实际净额含双边费/滑点仅 185 → 差额 -15 应结算到平仓日，合计恰 185。
    lots = [_lot(close_date=_D3, close_price=12.0, pnl_amount=185.0)]
    closes = {("600000.SH", _D1): 10.0, ("600000.SH", _D2): 10.5, ("600000.SH", _D3): 11.0}
    res, _ = compute_daily_pnl(lots, closes, [_D1, _D2, _D3], sell_cost=0.0)
    assert res[_D2] == 50.0  # 平仓前日盯市不变
    assert round(sum(res.values()), 2) == 185.0  # 合计精确锚定净额


def test_open_lot_not_anchored():
    """未平仓 lot（无 pnl_amount）→ 保持毛盯市，不结算。"""
    lots = [_lot(pnl_amount=None)]
    closes = {("600000.SH", _D1): 10.0, ("600000.SH", _D2): 11.0}
    res, _ = compute_daily_pnl(lots, closes, [_D1, _D2], sell_cost=0.0)
    assert res[_D2] == 100.0  # (11-10)*100 毛额，不动


# ─── 纯函数：build_calendar_payload（月切片 + 汇总） ──────────────────────────


def test_month_slice_and_summary():
    daily = {
        date(2026, 7, 31): 100.0,
        date(2026, 8, 3): -50.0,
        date(2026, 8, 4): 200.0,
    }
    counts = {date(2026, 7, 31): 3, date(2026, 8, 3): 2, date(2026, 8, 4): 2}
    p = build_calendar_payload(2026, 8, daily, 1_000_000.0, daily_counts=counts)
    assert [d["day"] for d in p["days"]] == [3, 4]  # 仅当月切片
    d3 = p["days"][0]
    assert d3["pnl"] == -50.0
    # 盈亏比例基数 = 初始资金 + 截至前日累计盈亏（含 7/31 的 +100）
    assert d3["pnl_pct"] == round(-50.0 / 1_000_100.0 * 100, 4)
    assert d3["equity"] == round(1_000_100.0 - 50.0, 2)
    assert d3["count"] == 2
    assert p["month_pnl"] == 150.0
    assert p["win_days"] == 1
    assert p["loss_days"] == 1
    assert p["trading_days"] == 2
    assert p["month_pnl_pct"] == round(150.0 / 1_000_100.0 * 100, 4)


def test_month_slice_empty_month():
    p = build_calendar_payload(2026, 8, {date(2026, 7, 31): 100.0}, 1_000_000.0)
    assert p["days"] == []
    assert p["month_pnl"] == 0.0
    assert p["win_days"] == 0 and p["loss_days"] == 0


# ─── 集成：TestClient + 真实库 ────────────────────────────────────────────────


def _pg_available() -> bool:
    try:
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session
        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.main import app
    return TestClient(app)


def _expected_aug_days() -> set[int]:
    """8 月应有盈亏的日集合 = 持仓标的日K在 [月初, 今日] 的全部交易日（与 service 口径一致）。"""
    from datetime import datetime

    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db.session import get_session
    today = datetime.now(UTC).date()
    s = get_session()
    try:
        inception = s.execute(text(
            "select min(opened_at::date) from v2_position where market = 'CN' "
            "and opened_at is not null"
        )).scalar()
        rows = s.execute(text(
            "select distinct b.trade_date from v2_bar_daily b "
            "where b.market = 'CN' and b.close is not null "
            "and b.symbol in (select distinct ts_code from v2_position where market = 'CN') "
            "and b.trade_date >= greatest(:lo, :incep) and b.trade_date <= least(:hi, :today)"
        ), {"lo": date(2026, 8, 1), "hi": date(2026, 8, 31), "incep": inception, "today": today}).all()
        return {r[0].day for r in rows}
    finally:
        s.close()


@pytest.mark.real_pg
def test_pnl_calendar_covers_all_trading_days(client, auth_headers):
    """用户核心诉求：8 月返回全部交易日（含无平仓日 8/4-8/7），而非仅平仓日。"""
    if not _pg_available():
        pytest.skip("Postgres 不可用，跳过集成验证")
    resp = client.get("/api/v1/reports/pnl-calendar?market=CN&year=2026&month=8", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    days = data["days"]
    expected = _expected_aug_days()
    assert expected, "库内应有 8 月日K数据"
    assert len(days) >= 10  # 远超旧口径的 9 个平仓日
    assert {d["day"] for d in days} == expected
    # 旧口径缺失的无平仓日（8/4、8/5、8/6、8/7）必须出现
    assert {4, 5, 6, 7} <= {d["day"] for d in days}
    for d in days:
        assert set(d.keys()) == {"day", "pnl", "count", "wins", "losses"}
        assert d["count"] >= 0  # 当日在仓批次数（策略停用/清仓后某交易日可能为 0）
    s = data["summary"]
    assert set(s.keys()) == {"total_pnl", "total_pnl_pct", "win_days", "loss_days", "win_rate"}
    assert s["win_days"] + s["loss_days"] <= len(days)
    assert abs(s["total_pnl"] - round(sum(d["pnl"] for d in days), 2)) < 0.05


@pytest.mark.real_pg
def test_pnl_calendar_strategy_filter(client, auth_headers):
    if not _pg_available():
        pytest.skip("Postgres 不可用，跳过集成验证")
    resp = client.get("/api/v1/reports/pnl-calendar?market=CN&year=2026&month=8&strategy=v1-cn-sv-4", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) >= 10
    for d in data["days"]:
        assert 1 <= d["day"] <= 31
    # 与合并口径一致：覆盖同一批交易日
    merged = client.get("/api/v1/reports/pnl-calendar?market=CN&year=2026&month=8", headers=auth_headers).json()
    assert {d["day"] for d in data["days"]} <= {d["day"] for d in merged["days"]}


@pytest.mark.real_pg
def test_pnl_calendar_default_month(client, auth_headers):
    """不传年月 → 最近有数据的月份。"""
    if not _pg_available():
        pytest.skip("Postgres 不可用，跳过集成验证")
    resp = client.get("/api/v1/reports/pnl-calendar?market=CN", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] is not None and data["month"] is not None
    assert len(data["days"]) >= 1
