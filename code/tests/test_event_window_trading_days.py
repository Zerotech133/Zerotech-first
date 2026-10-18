"""事件窗自然日→交易日回归测试（P2-J）。

缺陷：event_strategies._win 用自然日窗（as_of - timedelta(days=3)）+ 下界严格 >，
     周一扫描时下界=周五、`ann_date > 周五` 把周五公告系统性排除（周末后漏采）。

修复：窗口下界改「最近 3 个开市交易日中的最早者」（查 v2_market_calendar），
     且下界由严格 > 改为包含 >=。三事件策略（EARN_SURPRISE/HOLDER_BUY/TOP_NET_BUY）
     一致，其中 TOP_NET_BUY 过滤字段为 trade_date。

用 sqlite 真库（StaticPool 跨 session 共享）+ 真实 ORM 播种，直连被测查询逻辑，不打桩。
BigInteger 主键在 sqlite 不自增 → 显式给 id。
"""
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime.now(UTC)


def _shared_sessionmaker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _cal(_id, market, d, is_open):
    return orm.MarketCalendarModel(
        id=_id, market=market, trade_date=d, is_open=is_open, created_at=_NOW)


def _earn(_id, ts_code, ann_date, p_change_min=80.0, forecast_type="预增",
          end_date=date(2026, 6, 30)):
    return orm.EarningsForecastModel(
        id=_id, ts_code=ts_code, ann_date=ann_date, end_date=end_date,
        forecast_type=forecast_type, p_change_min=p_change_min, p_change_max=p_change_min + 20,
        created_at=_NOW)


def _holder(_id, ts_code, ann_date, change_ratio=1.5):
    return orm.HolderTradeModel(
        id=_id, ts_code=ts_code, ann_date=ann_date, holder_name="张三", holder_type="P",
        in_de="IN", change_vol=10000, change_ratio=change_ratio, after_ratio=5.0,
        created_at=_NOW)


def _top(_id, ts_code, trade_date, net_amount=1.0e8):
    return orm.TopListModel(
        id=_id, ts_code=ts_code, trade_date=trade_date, close=10.0, pct_change=9.9,
        l_buy=2.0e8, l_sell=1.0e8, net_amount=net_amount, reason="日涨幅偏离7%",
        created_at=_NOW)


def _patch_session(monkeypatch, Session):
    import zhice_os_v2.infrastructure.db.session as sess_mod
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())


# 具体一周：周三 8/19 ~ 周三 8/26，周六/日休市。
WED0 = date(2026, 8, 19)
THU0 = date(2026, 8, 20)
FRI = date(2026, 8, 21)
SAT = date(2026, 8, 22)
SUN = date(2026, 8, 23)
MON = date(2026, 8, 24)
TUE = date(2026, 8, 25)
WED = date(2026, 8, 26)


def _seed_week_calendar(seed):
    """播种含周末休市的日历：8/17(周一)~8/26(周三) 工作日开市、周末休市。"""
    seed.add_all([
        _cal(101, "CN", date(2026, 8, 17), True),
        _cal(102, "CN", date(2026, 8, 18), True),
        _cal(103, "CN", WED0, True),
        _cal(104, "CN", THU0, True),
        _cal(105, "CN", FRI, True),
        _cal(106, "CN", SAT, False),
        _cal(107, "CN", SUN, False),
        _cal(108, "CN", MON, True),
        _cal(109, "CN", TUE, True),
        _cal(110, "CN", WED, True),
    ])


def test_friday_announcement_captured_on_monday_scan(monkeypatch):
    """核心：周五公告，周一扫描必须命中（自然日窗+严格> 会漏掉，交易日窗+>= 命中）。"""
    Session = _shared_sessionmaker()
    seed = Session()
    _seed_week_calendar(seed)
    seed.add(_earn(1, "000001.SZ", FRI))  # 周五公告
    seed.commit()
    seed.close()

    _patch_session(monkeypatch, Session)
    from zhice_os_v2.domain.strategy.strategies import event_strategies
    picks = event_strategies.select_earnings_surprise(MON, market="CN")

    codes = {p["ts_code"] for p in picks}
    assert "000001.SZ" in codes, f"周五公告应被周一扫描命中，实得 {codes}"


def test_midweek_within_window_and_boundary_exclusion(monkeypatch):
    """中周回归：as_of=周五，窗口=[周三,周五]；周三命中、更早的周二越界排除。"""
    Session = _shared_sessionmaker()
    seed = Session()
    _seed_week_calendar(seed)
    seed.add_all([
        _earn(1, "600000.SH", WED0),                 # 周三 = 窗口最早交易日 → 命中
        _earn(2, "600001.SH", date(2026, 8, 18)),    # 周二 = 第 4 交易日 → 越界排除
    ])
    seed.commit()
    seed.close()

    _patch_session(monkeypatch, Session)
    from zhice_os_v2.domain.strategy.strategies import event_strategies
    codes = {p["ts_code"] for p in event_strategies.select_earnings_surprise(FRI, market="CN")}

    assert "600000.SH" in codes, f"窗口内(周三)应命中，实得 {codes}"
    assert "600001.SH" not in codes, f"窗口外(周二,第4交易日)应排除，实得 {codes}"


def test_post_holiday_retraces_three_trading_days(monkeypatch):
    """节假日：长假后首日回溯到假前第 3 交易日；更早的公告排除。"""
    Session = _shared_sessionmaker()
    seed = Session()
    # 假前 9/28~9/30 开市，10/1~10/7 长假休市，10/8 复市。
    seed.add_all([
        _cal(201, "CN", date(2026, 9, 25), True),
        _cal(202, "CN", date(2026, 9, 28), True),
        _cal(203, "CN", date(2026, 9, 29), True),
        _cal(204, "CN", date(2026, 9, 30), True),
        _cal(205, "CN", date(2026, 10, 1), False),
        _cal(206, "CN", date(2026, 10, 2), False),
        _cal(207, "CN", date(2026, 10, 5), False),
        _cal(208, "CN", date(2026, 10, 6), False),
        _cal(209, "CN", date(2026, 10, 7), False),
        _cal(210, "CN", date(2026, 10, 8), True),
    ])
    # 复市 10/8 扫描 → 窗口=[9/29(第3), 9/30, 10/8]，9/29 命中、9/28 越界。
    seed.add_all([
        _earn(1, "000002.SZ", date(2026, 9, 29)),   # 假前第 3 交易日 → 命中
        _earn(2, "000003.SZ", date(2026, 9, 28)),   # 假前第 4 交易日 → 排除
    ])
    seed.commit()
    seed.close()

    _patch_session(monkeypatch, Session)
    from zhice_os_v2.domain.strategy.strategies import event_strategies
    codes = {p["ts_code"] for p in event_strategies.select_earnings_surprise(
        date(2026, 10, 8), market="CN")}

    assert "000002.SZ" in codes, f"假前第3交易日应命中，实得 {codes}"
    assert "000003.SZ" not in codes, f"假前第4交易日应排除，实得 {codes}"


@pytest.mark.parametrize("strategy_attr,seeder,field_desc", [
    ("select_earnings_surprise",
     lambda seed: seed.add(_earn(1, "000001.SZ", FRI)), "ann_date"),
    ("select_holder_buy",
     lambda seed: seed.add(_holder(1, "000001.SZ", FRI)), "ann_date"),
    ("select_top_net_buy",
     lambda seed: seed.add(_top(1, "000001.SZ", FRI)), "trade_date"),
])
def test_all_three_strategies_capture_friday_on_monday(
        monkeypatch, strategy_attr, seeder, field_desc):
    """三策略各覆盖：周五事件（TOP_NET_BUY 按 trade_date）周一扫描均命中。"""
    Session = _shared_sessionmaker()
    seed = Session()
    _seed_week_calendar(seed)
    seeder(seed)
    seed.commit()
    seed.close()

    _patch_session(monkeypatch, Session)
    from zhice_os_v2.domain.strategy.strategies import event_strategies
    fn = getattr(event_strategies, strategy_attr)
    codes = {p["ts_code"] for p in fn(MON, market="CN")}

    assert "000001.SZ" in codes, f"{strategy_attr}({field_desc}) 周五事件应被周一命中，实得 {codes}"
