# -*- coding: utf-8 -*-
"""G1 基线工具：时区单例（Asia/Shanghai / UTC+8）。

基因 G1 = 裸本地时间（naive ``datetime.now()`` / ``date.today()`` / ``fromtimestamp`` 无时区声明）。
本模块提供全仓统一时区锚，消除跨时区 / 容器 UTC / 夏令时下的「生成时刻」与「日界」漂移。

判例：``portal/api_daily_data.py:39`` 已先行落地 ``CST = timezone(timedelta(hours=8))``，
本模块将其固化为可复用单例，并补齐 utc/cst 互转与格式化助手。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 中国自 1991 起不再实行夏令时，固定 +8 与 Asia/Shanghai 在可预见未来等价；
# 使用固定偏移以保证无 tzdata 依赖环境下的确定性（避免 Windows ZoneInfo 缺数据崩溃）。
CST = timezone(timedelta(hours=8))
UTC = timezone.utc


def now_cst() -> datetime:
    """返回带时区（Asia/Shanghai, +8）的当前时刻。"""
    return datetime.now(CST)


def utcnow() -> datetime:
    """返回带时区（UTC）的当前时刻。"""
    return datetime.now(UTC)


def to_cst(dt: datetime) -> datetime:
    """把 naive 或 UTC 时间归一为 CST 时区感知时间。

    - naive（无 tzinfo）：按「本地即 CST」解释（与旧代码 ``datetime.now()`` 语义兼容）；
    - 已带时区：转换到 CST。
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CST)
    return dt.astimezone(CST)


def to_utc(dt: datetime) -> datetime:
    """归一为 UTC 时区感知时间。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CST).astimezone(UTC)
    return dt.astimezone(UTC)


def format_cst(dt: datetime, timespec: str = "seconds") -> str:
    """输出带 +08:00 偏移的 ISO 字符串（替代 naive ``isoformat``）。"""
    return to_cst(dt).isoformat(timespec=timespec)


def cst_date_str(dt: datetime | None = None) -> str:
    """返回 CST 日界下的 ``YYYY-MM-DD``（替代 ``date.today()``）。"""
    d = to_cst(dt) if dt is not None else now_cst()
    return d.strftime("%Y-%m-%d")


def cst_month_str(dt: datetime | None = None) -> str:
    """返回 CST 日界下的 ``YYYY-MM``（替代 ``str(created_at)[:7]`` 的裸本地月）。"""
    d = to_cst(dt) if dt is not None else now_cst()
    return d.strftime("%Y-%m")
