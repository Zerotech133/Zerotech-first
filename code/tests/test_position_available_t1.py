"""A股 T+1 可用数量回归：昨仓当日应可卖(available=open_qty),不应误判为当日开仓=0。

缺陷:positions.list_positions 的 available_qty 用 _get_latest_trade_date(最新日K trade_date)
作 T+1"当日开仓"基准。盘中今日日K未入库时该函数返回昨天→昨仓 opened_at.date()==ref_date
→ opened_today=True → available_qty=0(T+1不可卖)。每个交易日收盘前所有CN昨仓可用被误清零。

修复:可用性判断改用 _current_trading_date(交易日历"当前交易日"),与 _get_latest_trade_date
(盈亏/复权参照,保留)解耦。

用 sqlite 真库直连 list_positions 被测本体,不打桩逻辑。用真实 today/昨天播种,跨天稳定。
"""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime.now(UTC)
TODAY = date.today()
YEST = TODAY - timedelta(days=1)


def _shared_sessionmaker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(Session):
    s = Session()
    # 交易日历:昨天+今天均开市
    s.add(orm.MarketCalendarModel(id=1, market="CN", trade_date=YEST, is_open=True, created_at=_NOW))
    s.add(orm.MarketCalendarModel(id=2, market="CN", trade_date=TODAY, is_open=True, created_at=_NOW))
    # 日K最新=昨天(模拟今日盘中日K未入库)→ _get_latest_trade_date 返回 YEST
    s.add(orm.BarDailyModel(id=1, instrument_id="CN-000908.SZ", market="CN", symbol="000908.SZ",
                            trade_date=YEST, close=6.60, created_at=_NOW))
    # 昨天开的 CN 持仓
    s.add(orm.PositionModel(
        position_id="pos-yest-000908", strategy_version_id="v1-cn-sv-4", market="CN",
        ts_code="000908.SZ", direction="BUY", open_qty=8400, open_price=6.60,
        status="OPEN", opened_at=datetime(YEST.year, YEST.month, YEST.day, 9, 35, tzinfo=_SH)))
    s.commit()
    s.close()


def _patch_session(monkeypatch, Session):
    import zhice_os_v2.api.routers.positions as pos_mod
    monkeypatch.setattr(pos_mod, "get_session", lambda: Session())


def test_yesterday_cn_position_available_today(monkeypatch):
    """昨天开的 CN 仓,今天可用数量应=开仓量(不是0)。"""
    from zhice_os_v2.api.routers.positions import list_positions
    from zhice_os_v2.domain.security.user import Role, User

    Session = _shared_sessionmaker()
    _seed(Session)
    _patch_session(monkeypatch, Session)

    # 直调端点函数：显式传 ADMIN current_user（本用例测 available_qty 计算，非 owner
    # 收敛；ADMIN 不过滤，等价改造前语义）。
    result = list_positions(
        market="CN",
        current_user=User(user_id="t-admin", username="admin", role=Role.ADMIN),
    )
    rows = result if isinstance(result, list) else result.get("positions", result.get("items", []))
    mine = [r for r in rows if r.get("position_id") == "pos-yest-000908"]
    assert mine, f"未找到测试持仓,返回={result}"
    avail = mine[0]["available_qty"]
    assert str(avail) == "8400", f"昨仓今日应可用8400,实际={avail}(=0则T+1误判缺陷未修)"
