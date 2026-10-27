# -*- coding: utf-8 -*-
"""新闻事件时间与实际入库时间；旧短日期只使用记录自己的年份参考。"""

import re
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
_SHORT = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}$")


def parse_full_time(value):
    """解析带年份的日期或 ISO 时间，无时区的来源按北京时间解释。"""
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=CST) if value.tzinfo is None else value.astimezone(CST)
        )
    value = str(value or "").strip()
    if len(value) < 10 or value[4:5] != "-" or value[7:8] != "-":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=CST)
            if parsed.tzinfo is None
            else parsed.astimezone(CST)
        )
    except ValueError:
        return None


def parse_news_time(value, reference=None, source=""):
    """完整时间保留原年份；短日期选择参考前后年中唯一的近邻。"""
    full = parse_full_time(value)
    if full is not None:
        return full
    value = str(value or "").strip()
    ref = parse_full_time(reference)
    if ref is None or not _SHORT.fullmatch(value):
        return None
    candidates = []
    for year in (ref.year - 1, ref.year, ref.year + 1):
        try:
            candidate = datetime.strptime(
                "%04d-%s" % (year, value), "%Y-%m-%d %H:%M"
            ).replace(tzinfo=CST)
        except ValueError:
            continue
        if abs(candidate - ref) > timedelta(days=183):
            continue
        # 公告和调研披露日可以晚于入库时间，普通快讯最多容许五分钟钟差。
        if source not in ("announcement", "survey") and candidate > ref + timedelta(
            minutes=5
        ):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: abs(candidate - ref))
    if len(candidates) > 1 and abs(candidates[0] - ref) == abs(candidates[1] - ref):
        return None
    return candidates[0]
