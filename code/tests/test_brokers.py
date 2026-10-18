from decimal import Decimal

from zhice_os_v2.domain.execution.broker import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.brokers.live import LiveBroker
from zhice_os_v2.infrastructure.brokers.paper import PaperBroker
from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker
from zhice_os_v2.infrastructure.market_data.memory import MemoryMarketData, Quote


def _ctx():
    profile = get_profile("CN")
    return MarketContext(market="CN", profile=profile, currency=profile.currency, tz=profile.tz)


def _order():
    return OrderRequest(
        order_id="order-1",
        market="CN",
        ts_code="600519.SH",
        side=OrderSide.BUY,
        qty=Decimal("100"),
        limit_price=Decimal("100"),
    )


def _order_with(**overrides):
    """_order() 的可变版本：按关键字覆盖字段，便于撮合引擎测试构造订单。"""
    fields = dict(
        order_id="order-1",
        market="CN",
        ts_code="600519.SH",
        side=OrderSide.BUY,
        qty=Decimal("100"),
        limit_price=Decimal("100"),
    )
    fields.update(overrides)
    return OrderRequest(**fields)


def test_simulated_broker_fills():
    result = SimulatedBroker().place_order(_ctx(), _order())
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == Decimal("100")


def test_simulated_broker_idempotent():
    broker = SimulatedBroker()
    first = broker.place_order(_ctx(), _order())
    second = broker.place_order(_ctx(), _order())
    assert first == second


def test_paper_broker_uses_quote():
    data = MemoryMarketData()
    data.seed_quote(Quote(ts_code="600519.SH", price=1500, pre_close=1490))
    result = PaperBroker(data).place_order(_ctx(), _order())
    assert result.status == OrderStatus.FILLED
    assert result.filled_price == Decimal("1500")


def test_live_broker_rejects_when_disabled():
    result = LiveBroker().place_order(_ctx(), _order())
    assert result.status == OrderStatus.REJECTED


# ---------------------------------------------------------------------------
# 撮合引擎：市价单 / 限价单 / 分档滑点 / 部分成交 / 挂单撤单
# CN Profile: slippage_pct=0.001, commission_rate=0.0003, stamp_tax_rate=0.0005(仅卖)
# ---------------------------------------------------------------------------


def test_market_order_small_fills_fully():
    """小单（ratio<=0.01）：100% 成交，base 滑点。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="mkt-small",
            order_type=OrderType.MARKET,
            qty=Decimal("100"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("10_000_000"),
        ),
    )
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == Decimal("100")
    assert result.remaining_qty is None or result.remaining_qty == Decimal("0")
    assert result.avg_fill_ratio == 1.0
    # BUY 滑点向上：100 * (1 + 0.001) = 100.1
    assert result.filled_price == Decimal("100.100000")


def test_market_order_large_partial_fill():
    """大单（0.05<ratio<=0.2）：成交 60%，剩余量撤单（不挂单）。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="mkt-large",
            order_type=OrderType.MARKET,
            qty=Decimal("1000"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("500_000"),  # ratio = 100000/500000 = 0.2
        ),
    )
    assert result.status == OrderStatus.PARTIALLY_FILLED
    assert result.filled_qty == Decimal("600")
    assert result.remaining_qty == Decimal("400")
    assert result.avg_fill_ratio == 0.6
    # 滑点 8x base = 0.008：100 * 1.008
    assert result.filled_price == Decimal("100.800000")
    # 市价单剩余量不挂单：无未结订单
    assert broker.get_order_status(_ctx(), "mkt-large") == OrderStatus.PARTIALLY_FILLED


def test_market_order_exceeds_liquidity_rejected():
    """超大单（ratio>0.2）：拒单 exceeds_liquidity。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="mkt-reject",
            order_type=OrderType.MARKET,
            qty=Decimal("1000"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("400_000"),  # ratio = 0.25
        ),
    )
    assert result.status == OrderStatus.REJECTED
    assert result.message == "exceeds_liquidity"
    assert result.filled_qty == Decimal("0")


def test_limit_buy_pending_when_price_above():
    """限价买：市价高于限价 → 挂单 PENDING。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="lim-pending",
            order_type=OrderType.LIMIT,
            qty=Decimal("100"),
            limit_price=Decimal("100"),
            market_price=Decimal("105"),
        ),
    )
    assert result.status == OrderStatus.PENDING
    assert result.filled_qty == Decimal("0")
    assert broker.get_order_status(_ctx(), "lim-pending") == OrderStatus.PENDING


def test_limit_buy_fills_when_price_drops():
    """限价买挂单后价格回落 → process_pending 成交，按 min(market, limit)±滑点。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="lim-fill",
            order_type=OrderType.LIMIT,
            qty=Decimal("100"),
            limit_price=Decimal("100"),
            market_price=Decimal("102"),
        ),
    )
    assert result.status == OrderStatus.PENDING
    prices = {"600519.SH": Decimal("99")}
    fills = broker.process_pending(_ctx(), lambda code: prices[code])
    assert len(fills) == 1
    assert fills[0].order_id == "lim-fill"
    assert fills[0].status == OrderStatus.FILLED
    assert fills[0].filled_qty == Decimal("100")
    # market(99) < limit(100) → 基准价取 min = 99，BUY 滑点向上：99 * 1.001
    assert fills[0].filled_price == Decimal("99.099000")
    assert broker.get_order_status(_ctx(), "lim-fill") == OrderStatus.FILLED


def test_cancel_pending_order():
    """撤单：PENDING → CANCELLED，订单移出挂单簿。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(
            order_id="lim-cancel",
            order_type=OrderType.LIMIT,
            qty=Decimal("100"),
            limit_price=Decimal("100"),
            market_price=Decimal("105"),
        ),
    )
    assert result.status == OrderStatus.PENDING
    broker.cancel_order(_ctx(), "lim-cancel")
    assert broker.get_order_status(_ctx(), "lim-cancel") == OrderStatus.CANCELLED
    # 撤单后价格回落也不应成交
    fills = broker.process_pending(_ctx(), lambda code: Decimal("90"))
    assert fills == []


def test_tiered_slippage():
    """分档滑点：ratio<=0.01 → 1x；<=0.05 → 3x；<=0.2 → 8x。"""
    broker = SimulatedBroker()
    r1 = broker.place_order(
        _ctx(),
        _order_with(
            order_id="tier-1x",
            qty=Decimal("100"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("10_000_000"),  # ratio=0.001 → 1x=0.001
        ),
    )
    assert r1.filled_price == Decimal("100.100000")
    assert r1.status == OrderStatus.FILLED
    r2 = broker.place_order(
        _ctx(),
        _order_with(
            order_id="tier-3x",
            qty=Decimal("300"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("2_000_000"),  # ratio=0.015 → 3x=0.003，成交 90%
        ),
    )
    assert r2.filled_price == Decimal("100.300000")
    assert r2.filled_qty == Decimal("270")
    assert r2.status == OrderStatus.PARTIALLY_FILLED
    r3 = broker.place_order(
        _ctx(),
        _order_with(
            order_id="tier-8x",
            qty=Decimal("1000"),
            limit_price=Decimal("100"),
            avg_daily_turnover=Decimal("1_000_000"),  # ratio=0.1 → 8x=0.008，成交 60%
        ),
    )
    assert r3.filled_price == Decimal("100.800000")
    assert r3.filled_qty == Decimal("600")
    assert r3.status == OrderStatus.PARTIALLY_FILLED


def test_no_turnover_data_uses_base_slippage():
    """无 turnover 数据：base 滑点全量成交（保持向后兼容）。"""
    broker = SimulatedBroker()
    result = broker.place_order(
        _ctx(),
        _order_with(order_id="no-turnover", qty=Decimal("100"), limit_price=Decimal("100")),
    )
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == Decimal("100")
    assert result.filled_price == Decimal("100.100000")


def test_idempotency_preserved():
    """幂等：同一 order_id 重复下单返回同一结果（含部分成交与挂单场景）。"""
    broker = SimulatedBroker()
    req = _order_with(
        order_id="idem",
        order_type=OrderType.MARKET,
        qty=Decimal("1000"),
        limit_price=Decimal("100"),
        avg_daily_turnover=Decimal("1_000_000"),  # ratio=0.1 → 部分成交
    )
    first = broker.place_order(_ctx(), req)
    second = broker.place_order(_ctx(), req)
    assert first == second
    assert first.status == OrderStatus.PARTIALLY_FILLED
    # 挂单场景幂等：PENDING 重复下单返回同一挂单结果
    lim = _order_with(
        order_id="idem-pending",
        order_type=OrderType.LIMIT,
        qty=Decimal("100"),
        limit_price=Decimal("100"),
        market_price=Decimal("110"),
    )
    p1 = broker.place_order(_ctx(), lim)
    p2 = broker.place_order(_ctx(), lim)
    assert p1 == p2
    assert p1.status == OrderStatus.PENDING
    # 拒单场景幂等：同一 order_id 重复提交返回同一拒单结果
    rej = _order_with(
        order_id="idem-reject",
        order_type=OrderType.MARKET,
        qty=Decimal("1000"),
        limit_price=Decimal("100"),
        avg_daily_turnover=Decimal("400_000"),  # ratio=0.25 → 拒单
    )
    j1 = broker.place_order(_ctx(), rej)
    j2 = broker.place_order(_ctx(), rej)
    assert j1 == j2
    assert j1.status == OrderStatus.REJECTED
