# -*- coding: utf-8 -*-
"""Agent ``Verdict`` 到 quant_backtest 模拟订单的独立适配器。

本模块只做两件事：校验 Agent 平台的 ``Verdict``，以及把可执行的四档建议
转换为 quant_backtest 的 ``OrderIntent``。它不接入 portfolio、账本或公共策略
主链；``simulate_verdict`` 仅在调用方明确要求时使用内存 ``BarStore`` 执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from agent_platform.contracts.sim_market import SimMarketBar
from agent_platform.engine.strategy_registry import Verdict
from execution_engine import (
    Bar,
    BarStore,
    ExecutionConfig,
    OrderIntent,
    ensure_utc,
    run_from_store,
)


_RECOMMENDATION_TO_SIDE = {
    "strong_buy": "BUY",
    "buy": "BUY",
    "sell": "SELL",
    "strong_sell": "SELL",
}


class AgentSimMarketError(ValueError):
    """Verdict 适配或模拟执行输入非法。"""


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentSimMarketError(f"{field_name} 必须为非空字符串")
    return value.strip()


def _validate_verdict(verdict: Any) -> Verdict:
    if not isinstance(verdict, Verdict):
        raise TypeError(
            "verdict 必须是 agent_platform.engine.strategy_registry.Verdict"
        )
    try:
        verdict.validate()
    except Exception as exc:
        raise AgentSimMarketError(f"Verdict 非法：{exc}") from exc
    if verdict.recommendation not in {
        "strong_buy",
        "buy",
        "hold",
        "sell",
        "strong_sell",
    }:
        raise AgentSimMarketError("Verdict.recommendation 不在合法五档枚举内")
    return verdict


def sim_market_bar_to_bar(
    bar: SimMarketBar,
    *,
    market: str,
    timeframe: str,
    session_date: date,
    source: str,
    source_version: str,
    calendar_ref: str,
    calendar_version: str,
    close_at: datetime,
    as_of: datetime,
) -> Bar:
    """显式映射已收盘、已可见的原始行情；不生成订单或推导成交价格。"""
    if not isinstance(bar, SimMarketBar):
        raise TypeError("bar 必须是 SimMarketBar")
    if bar.adjustment != "none":
        raise AgentSimMarketError("adjustment 必须为 none")
    if not bar.closed or not bar.visible:
        raise AgentSimMarketError("bar 必须已收盘且可见")
    if bar.visible_at is None:
        raise AgentSimMarketError("visible_at 不可缺失")
    observed_at = ensure_utc(as_of, "as_of")
    timestamp = ensure_utc(
        datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00")), "timestamp"
    )
    visible_at = ensure_utc(
        datetime.fromisoformat(bar.visible_at.replace("Z", "+00:00")), "visible_at"
    )
    closed_at = ensure_utc(close_at, "close_at")
    if (
        observed_at > datetime.now(timezone.utc)
        or timestamp > observed_at
        or closed_at > observed_at
        or visible_at > observed_at
    ):
        raise AgentSimMarketError("bar 尚未在 as_of 可见或 as_of 位于未来")
    if closed_at < timestamp or visible_at < closed_at:
        raise AgentSimMarketError("close_at 与 visible_at 时间顺序非法")
    if not isinstance(session_date, date) or isinstance(session_date, datetime):
        raise AgentSimMarketError("session_date 必须是 date")
    if abs((session_date - timestamp.date()).days) > 1:
        raise AgentSimMarketError("session_date 与 bar 时间不一致")
    source = _require_text(source, "source")
    return Bar(
        symbol=bar.symbol,
        market=_require_text(market, "market"),
        timeframe=_require_text(timeframe, "timeframe"),
        dt=timestamp,
        session_date=session_date,
        o=bar.open,
        h=bar.high,
        l=bar.low,
        c=bar.close,
        v=bar.volume,
        amount=None,
        close_at=closed_at,
        available_at=visible_at,
        closed=True,
        source=source,
        source_version=_require_text(source_version, "source_version"),
        adjustment="none",
        quality="observed",
        source_trace=(source,),
        calendar_ref=_require_text(calendar_ref, "calendar_ref"),
        calendar_version=_require_text(calendar_version, "calendar_version"),
    )


def verdict_to_order_intent(
    verdict: Verdict,
    *,
    quantity: float,
    submitted_at: datetime,
    market: str,
    timeframe: str,
    order_id: str = "",
    signal_at: Optional[datetime] = None,
    exchange: Optional[str] = None,
    asset_class: str = "equity",
) -> Optional[OrderIntent]:
    """将合法 Agent ``Verdict`` 转为 quant_backtest ``OrderIntent``。

    ``hold`` 明确返回 ``None``；其余四档建议返回一个显式带有
    ``quantity/submitted_at/market/timeframe`` 的订单意图。
    """
    checked = _validate_verdict(verdict)
    market = _require_text(market, "market")
    timeframe = _require_text(timeframe, "timeframe")
    if checked.recommendation == "hold":
        return None

    return OrderIntent(
        symbol=checked.symbol,
        side=_RECOMMENDATION_TO_SIDE[checked.recommendation],
        quantity=quantity,
        submitted_at=submitted_at,
        order_id=order_id,
        market=market,
        timeframe=timeframe,
        signal_at=signal_at,
        exchange=exchange,
        asset_class=asset_class,
    )


def static_trading_status_fixture(symbol: str, dt: datetime, bar: Bar) -> str:
    """仅用于显式演示的固定开市状态，绝不能作为市场状态证据。"""
    return "TRADING"


@dataclass(frozen=True)
class SimMarketStatusEvidence:
    """调用方提供的离线状态快照；source 仅是标签，不证明来源可信。"""

    symbol: str
    market: str
    session_date: date
    status: str
    observed_at: datetime
    effective_until: datetime
    source: str
    source_version: str

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.market, "market")
        _require_text(self.source, "source")
        _require_text(self.source_version, "source_version")
        if not isinstance(self.session_date, date) or isinstance(
            self.session_date, datetime
        ):
            raise AgentSimMarketError("session_date 必须是 date")
        if self.status not in {
            "TRADING",
            "HALTED",
            "SUSPENDED",
            "UNLISTED",
            "DELISTED",
        }:
            raise AgentSimMarketError("status 必须是明确的交易状态")
        observed = ensure_utc(self.observed_at, "observed_at")
        effective_until = ensure_utc(self.effective_until, "effective_until")
        if effective_until <= observed:
            raise AgentSimMarketError("effective_until 必须晚于 observed_at")


def _evidence_status_provider(
    evidence: Mapping[tuple[str, date], SimMarketStatusEvidence],
    *,
    symbol: str,
    market: str,
    as_of: datetime,
):
    """只读取冻结快照；缺失或超出有效区间一律返回 UNKNOWN。"""
    snapshot = MappingProxyType(dict(evidence))
    observed_at = ensure_utc(as_of, "as_of")
    if observed_at > datetime.now(timezone.utc):
        raise AgentSimMarketError("as_of 不得位于未来")
    for key, item in snapshot.items():
        if not isinstance(item, SimMarketStatusEvidence) or key != (
            item.symbol,
            item.session_date,
        ):
            raise AgentSimMarketError("市场状态证据键或类型非法")
        if item.market.upper() != market.upper():
            raise AgentSimMarketError("市场状态证据与订单市场不一致")
        if item.symbol != symbol:
            raise AgentSimMarketError("市场状态证据与订单标的不一致")
        if ensure_utc(item.observed_at, "observed_at") > observed_at:
            raise AgentSimMarketError("市场状态证据晚于 as_of")

    def provider(symbol: str, dt: datetime, bar: Bar) -> str:
        if (
            not isinstance(bar, Bar)
            or bar.symbol != symbol
            or bar.market.upper() != market.upper()
        ):
            return "UNKNOWN"
        item = snapshot.get((symbol, bar.session_date))
        if item is None:
            return "UNKNOWN"
        execution_at = ensure_utc(dt, "dt")
        if (
            execution_at > observed_at
            or abs((bar.session_date - bar.dt.date()).days) > 1
        ):
            return "UNKNOWN"
        if (
            bar.close_at is None
            or bar.available_at is None
            or bar.close_at > execution_at
            or bar.available_at > execution_at
        ):
            return "UNKNOWN"
        if not (
            ensure_utc(item.observed_at, "observed_at")
            <= execution_at
            < ensure_utc(item.effective_until, "effective_until")
        ):
            return "UNKNOWN"
        return item.status

    return provider


def simulate_verdict(
    verdict: Verdict,
    bars: BarStore | Iterable[Any],
    *,
    quantity: float,
    submitted_at: datetime,
    market: str,
    timeframe: str,
    config: Optional[ExecutionConfig] = None,
    order_id: str = "",
    signal_at: Optional[datetime] = None,
    exchange: Optional[str] = None,
    asset_class: str = "equity",
    status_provider=None,
    limit_provider=None,
    calendar_provider=None,
    market_rule_provider=None,
    status_evidence: Optional[
        Mapping[tuple[str, date], SimMarketStatusEvidence]
    ] = None,
    as_of: Optional[datetime] = None,
    allow_demo_fixture: bool = False,
):
    """把一个 Verdict 在内存 BarStore 上交给 ``run_from_store`` 执行。

    ``bars`` 可直接是已有 ``BarStore``，也可是不落盘的 ``Bar`` 可迭代对象。
    hold 不生成订单，但仍返回 ``run_from_store`` 对空订单的结果。
    """
    if type(allow_demo_fixture) is not bool:
        raise AgentSimMarketError("allow_demo_fixture 必须是布尔值")
    if status_evidence is not None and allow_demo_fixture:
        raise AgentSimMarketError("状态证据不得混用演示模式")
    if status_evidence is not None and status_provider is not None:
        raise AgentSimMarketError("状态证据必须独立使用，不得混用 status_provider")
    if (
        status_provider is not None
        and status_provider is not static_trading_status_fixture
    ):
        raise AgentSimMarketError("status_provider 不允许外部回调")
    if status_provider is static_trading_status_fixture and not allow_demo_fixture:
        raise AgentSimMarketError("静态状态夹具必须显式启用演示模式")
    if status_evidence is None and not (
        allow_demo_fixture and status_provider is static_trading_status_fixture
    ):
        raise AgentSimMarketError("缺少状态证据；演示夹具必须显式启用")
    evidence_provider = None
    if status_evidence is not None:
        if as_of is None:
            raise AgentSimMarketError("状态证据必须提供 as_of")
        if not isinstance(status_evidence, Mapping):
            raise AgentSimMarketError("status_evidence 必须是映射")
        checked_verdict = _validate_verdict(verdict)
        evidence_provider = _evidence_status_provider(
            status_evidence,
            symbol=checked_verdict.symbol,
            market=_require_text(market, "market"),
            as_of=as_of,
        )
        status_provider = evidence_provider
    for name, provider in (
        ("status_provider", status_provider),
        ("limit_provider", limit_provider),
        ("calendar_provider", calendar_provider),
        ("market_rule_provider", market_rule_provider),
    ):
        if provider is not None and not (
            name == "status_provider"
            and (
                provider is static_trading_status_fixture
                or provider is evidence_provider
            )
        ):
            raise AgentSimMarketError(
                f"{name} 不允许外部回调；仅可使用静态夹具或离线状态证据"
            )
    if config is not None and not isinstance(config, ExecutionConfig):
        raise TypeError("config 必须是 ExecutionConfig")
    if config is not None and any(
        getattr(config, name) is not None
        for name in ("fill_model", "fee_calculator", "fee_provider")
    ):
        raise AgentSimMarketError(
            "config 不允许 fill_model、fee_calculator 或 fee_provider"
        )
    if config is not None and (
        config.calendar_required or config.market_rules_required
    ):
        raise AgentSimMarketError("config 不允许依赖外部日历或市场规则 provider")
    if evidence_provider is not None:
        cutoff = ensure_utc(as_of, "as_of")
        if ensure_utc(submitted_at, "submitted_at") > cutoff:
            raise AgentSimMarketError("submitted_at 晚于 as_of")
        if signal_at is not None and ensure_utc(signal_at, "signal_at") > cutoff:
            raise AgentSimMarketError("signal_at 晚于 as_of")
    intent = verdict_to_order_intent(
        verdict,
        quantity=quantity,
        submitted_at=submitted_at,
        market=market,
        timeframe=timeframe,
        order_id=order_id,
        signal_at=signal_at,
        exchange=exchange,
        asset_class=asset_class,
    )
    store = bars if isinstance(bars, BarStore) else BarStore(bars)
    if evidence_provider is not None:
        for bar in store.all():
            if (
                bar.market.upper() != market.upper()
                or bar.timeframe != timeframe
                or bar.adjustment != "none"
                or not bar.closed
                or bar.close_at is None
                or bar.available_at is None
                or bar.dt > cutoff
                or bar.close_at > cutoff
                or bar.available_at > cutoff
                or bar.close_at < bar.dt
                or bar.available_at < bar.close_at
                or abs((bar.session_date - bar.dt.date()).days) > 1
            ):
                raise AgentSimMarketError(
                    "状态证据模式下 Bar 市场、复权、收盘或可见性不符合 as_of"
                )
    policy = config
    if policy is None:
        policy_refs = {
            "CN": "registry:market-cn",
            "US": "registry:market-us",
            "HK": "registry:market-hk",
            "CRYPTO": "registry:market-crypto",
        }
        policy = ExecutionConfig(
            market=market,
            timeframe=timeframe,
            policy_ref=policy_refs.get(market.upper()),
        )
    return run_from_store(
        store,
        () if intent is None else (intent,),
        policy,
        status_provider=status_provider,
        limit_provider=limit_provider,
        calendar_provider=calendar_provider,
        market_rule_provider=market_rule_provider,
    )


@dataclass(frozen=True)
class AgentSimMarketAdapter:
    """绑定订单字段的不可变 Verdict → 模拟市场适配器。"""

    quantity: float
    submitted_at: datetime
    market: str
    timeframe: str
    order_id: str = ""
    signal_at: Optional[datetime] = None
    exchange: Optional[str] = None
    asset_class: str = "equity"

    def to_order_intent(self, verdict: Verdict) -> Optional[OrderIntent]:
        return verdict_to_order_intent(
            verdict,
            quantity=self.quantity,
            submitted_at=self.submitted_at,
            market=self.market,
            timeframe=self.timeframe,
            order_id=self.order_id,
            signal_at=self.signal_at,
            exchange=self.exchange,
            asset_class=self.asset_class,
        )

    def simulate(
        self,
        verdict: Verdict,
        bars: BarStore | Iterable[Any],
        config: Optional[ExecutionConfig] = None,
        *,
        status_provider=None,
        limit_provider=None,
        calendar_provider=None,
        market_rule_provider=None,
        status_evidence=None,
        as_of=None,
        allow_demo_fixture=False,
    ):
        return simulate_verdict(
            verdict,
            bars,
            quantity=self.quantity,
            submitted_at=self.submitted_at,
            market=self.market,
            timeframe=self.timeframe,
            config=config,
            order_id=self.order_id,
            signal_at=self.signal_at,
            exchange=self.exchange,
            asset_class=self.asset_class,
            status_provider=status_provider,
            limit_provider=limit_provider,
            calendar_provider=calendar_provider,
            market_rule_provider=market_rule_provider,
            status_evidence=status_evidence,
            as_of=as_of,
            allow_demo_fixture=allow_demo_fixture,
        )


__all__ = [
    "AgentSimMarketAdapter",
    "AgentSimMarketError",
    "SimMarketStatusEvidence",
    "sim_market_bar_to_bar",
    "static_trading_status_fixture",
    "simulate_verdict",
    "verdict_to_order_intent",
]
