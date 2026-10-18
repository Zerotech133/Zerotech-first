"""交易授权中心用例编排。

规则：
- 模拟交易开关开启 → 模拟盘可自动执行。
- 实盘默认必须人工授权；只有开启实盘自动跟单才可自动执行。
- 自动跟单按“市场 + 策略”配置。
"""
from __future__ import annotations

from dataclasses import dataclass

from zhice_os_v2.domain.approval.approval import ApprovalChannel, ApprovalRequest


@dataclass(frozen=True)
class StrategySwitch:
    market: str
    strategy_version_id: str
    simulated_enabled: bool = False
    live_enabled: bool = False
    live_auto_follow: bool = False


class ApprovalCenter:
    def __init__(self, switches: dict[tuple[str, str], StrategySwitch]) -> None:
        self._switches = switches

    def get_switch(self, market: str, strategy_version_id: str) -> StrategySwitch:
        return self._switches.get(
            (market, strategy_version_id),
            StrategySwitch(market=market, strategy_version_id=strategy_version_id),
        )

    def create_approval(
        self,
        market: str,
        strategy_version_id: str,
        order_intent_id: str,
        ts_code: str,
        direction: str,
        qty: float,
        price: float | None,
        reason: str,
    ) -> ApprovalRequest:
        switch = self.get_switch(market, strategy_version_id)
        auto = switch.live_auto_follow
        request = ApprovalRequest(
            request_id=f"apr-{order_intent_id}",
            order_intent_id=order_intent_id,
            strategy_version_id=strategy_version_id,
            market=market,
            ts_code=ts_code,
            direction=direction,
            qty=qty,
            price=price,
            reason=reason,
            channel=ApprovalChannel.AUTO if auto else ApprovalChannel.WEB_POPUP,
            auto_follow=auto,
        )
        if auto:
            request.approve(actor="auto")
        return request

    def approve(self, request: ApprovalRequest, actor: str) -> None:
        request.approve(actor)

    def reject(self, request: ApprovalRequest, actor: str, reason: str) -> None:
        request.reject(actor, reason)
