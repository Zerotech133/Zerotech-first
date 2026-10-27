# -*- coding: utf-8 -*-
"""SIM_MARKET 独立只读策略适配器。

本模块只把已关闭、截至 ``as_of`` 可见的模拟市场快照送入策略注册表，
不接执行器、凭据或账本路径，也不写任何文件。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from types import MappingProxyType
from typing import Any

try:  # 先复用当前运行时的顶层模块身份
    from contracts.sim_market import MODE, SimMarketSnapshot
    from strategy_registry import Verdict
except ImportError:  # 仓库根目录包导入
    from agent_platform.contracts.sim_market import MODE, SimMarketSnapshot
    from agent_platform.engine.strategy_registry import Verdict


class SimMarketReadOnlyError(ValueError):
    """SIM_MARKET 只读适配器的输入或运行边界错误。"""


def _parse_aware_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SimMarketReadOnlyError("%s 必须是非空 ISO8601 字符串" % field_name)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SimMarketReadOnlyError("%s 不是有效的 ISO8601 时间" % field_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SimMarketReadOnlyError("%s 必须包含合法时区" % field_name)
    return parsed


def _readonly(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_readonly(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_readonly(item) for item in value)
    return value


def _context(snapshot: SimMarketSnapshot) -> Mapping:
    return _readonly(snapshot.to_dict())


def _context_bytes(snapshot: SimMarketSnapshot) -> int:
    try:
        return len(snapshot.to_json().encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SimMarketReadOnlyError("snapshot context 必须可序列化为 JSON") from exc


def validate_snapshot(snapshot: SimMarketSnapshot, now: datetime | None = None) -> None:
    """校验 SIM_MARKET 快照的模式、时间和 bar 可见性。"""
    if not isinstance(snapshot, SimMarketSnapshot):
        raise SimMarketReadOnlyError("snapshot 必须是 SimMarketSnapshot")
    if snapshot.mode != MODE:
        raise SimMarketReadOnlyError("snapshot.mode 必须为 %s" % MODE)

    as_of = _parse_aware_time(snapshot.as_of, "snapshot.as_of")
    current = datetime.now(timezone.utc) if now is None else now
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise SimMarketReadOnlyError("now 必须包含合法时区")
    if as_of > current:
        raise SimMarketReadOnlyError("snapshot.as_of 不可晚于当前时间")
    if snapshot.visible is not True or snapshot.closed is not True:
        raise SimMarketReadOnlyError("snapshot 只允许 visible=True 且 closed=True")

    for index, bar in enumerate(snapshot.bars):
        if bar.mode != MODE:
            raise SimMarketReadOnlyError("bars[%d].mode 必须为 %s" % (index, MODE))
        if bar.closed is not True:
            raise SimMarketReadOnlyError("bars[%d] closed 必须已关闭" % index)
        if bar.visible is not True:
            raise SimMarketReadOnlyError("bars[%d] 必须截至 as_of 可见" % index)
        bar_time = _parse_aware_time(bar.timestamp, "bars[%d].timestamp" % index)
        if bar_time > as_of:
            raise SimMarketReadOnlyError(
                "bars[%d].timestamp 晚于 snapshot.as_of" % index
            )
        visible_at = _parse_aware_time(bar.visible_at, "bars[%d].visible_at" % index)
        if visible_at > as_of:
            raise SimMarketReadOnlyError("bars[%d] 在 as_of 时尚不可见" % index)

    for view_name, view in (("portfolio", snapshot.portfolio), ("risk", snapshot.risk)):
        if view is None:
            continue
        view_as_of = _parse_aware_time(view.as_of, "%s.as_of" % view_name)
        if view_as_of > as_of:
            raise SimMarketReadOnlyError("%s.as_of 晚于 snapshot.as_of" % view_name)
        visible_at = _parse_aware_time(view.visible_at, "%s.visible_at" % view_name)
        if visible_at < view_as_of:
            raise SimMarketReadOnlyError(
                "%s.visible_at 不能早于 %s.as_of" % (view_name, view_name)
            )
        if visible_at > as_of:
            raise SimMarketReadOnlyError("%s 在 snapshot.as_of 时尚不可见" % view_name)


def run_sim_market_readonly(
    strategy_id: str,
    snapshot: SimMarketSnapshot,
    config,
    registry,
    now: datetime | None = None,
):
    """通过配置门禁和 ``registry.run`` 运行一次 SIM_MARKET 只读策略。

    返回值是不可变映射，仅包含 ``Verdict`` 和只读 ``metadata``。
    函数签名不接受执行器、凭据或账本路径。
    """
    validate_snapshot(snapshot, now=now)
    full_context = snapshot.to_dict()
    # 先用完整快照计算上限，绑定后再按 allowed_data 构造最小上下文。
    context_bytes = len(
        json.dumps(
            full_context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    try:
        authorization = config.bind_for_run(registry, strategy_id, MODE, context_bytes)
    except Exception as exc:
        raise SimMarketReadOnlyError(
            "SIM_MARKET 配置校验/Registry 绑定失败：%s" % exc
        ) from exc

    allowed_data = set(authorization.allowed_data)
    full_context = snapshot.to_dict()
    context_payload = {"mode": MODE}
    for key in ("bars", "portfolio", "risk"):
        if key in allowed_data:
            context_payload[key] = full_context[key]
    context = _readonly(context_payload)

    try:
        verdict = registry.run(strategy_id, context)
    except Exception as exc:
        raise SimMarketReadOnlyError("SIM_MARKET 策略运行失败：%s" % exc) from exc
    if not isinstance(verdict, Verdict):
        raise SimMarketReadOnlyError("registry.run 必须返回 Verdict")

    metadata = {
        "mode": MODE,
        "as_of": snapshot.as_of,
        "bar_count": len(snapshot.bars),
        "symbols": tuple(sorted({bar.symbol for bar in snapshot.bars})),
        "adjustment": snapshot.adjustment,
        "authorization": authorization.as_dict(),
    }
    return MappingProxyType(
        {
            "verdict": verdict,
            "metadata": _readonly(metadata),
        }
    )


__all__ = [
    "SimMarketReadOnlyError",
    "run_sim_market_readonly",
    "validate_snapshot",
]
