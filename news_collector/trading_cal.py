# -*- coding: utf-8 -*-
"""A 股交易日历（只读、纯函数、无网络）：AlertWatch 周末/非交易日静默判据。

数据来源
    运行时文件 ``<仓库根>/portal/data/trade_cal_open_days.json``（gitignored，
    不入库）。由平台从 Tushare SSE ``trade_cal``（is_open=1）导出精简版，schema
    ``trade_cal_open_days/v1``；字段：schema / generated_at / source /
    source_sha256 / exchange / range / count / open_days（"YYYY-MM-DD" 升序）。
    exchange=SSE（上交所），以文件内 range 为准（生成时为 2020-01-02 ~
    2027-12-31）。导出脚本落盘时用原子写，读取方只读不改。

失败语义（fail-open，绝不抛异常）
    文件缺失 / 读失败 / JSON 损坏 / schema 不合法 / 无有效日期 → 回退
    ``weekday() < 5``（周一至周五视为开市），并打印一行
    ``TRADING_CAL_WARN ...`` 供告警链吐司/日志观测。
    这里刻意不抛异常：调用方是告警链，日历不可用不能让告警链整体崩掉。
    代价是法定节假日会被误判为交易日（只是"多跑一轮静默比对"，不会误报），
    而"直接崩掉"会让看门狗分不清故障与休息日——两害相权取可观测。

缓存
    进程内按绝对路径缓存首次加载结果（含失败回退空集）。AlertWatch 由计划任务
    每轮起独立进程，因此不存在"长驻进程看不到新生成日历"的问题。
"""

import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
# 默认路径：仓库根/portal/data/trade_cal_open_days.json
OPEN_DAYS_PATH = os.path.join(STATION, "portal", "data", "trade_cal_open_days.json")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CACHE = {}


def _warn(msg):
    """fail-open 告警：只打印，不抛异常（调用方是告警链，不能反过来弄死它）。"""
    print("TRADING_CAL_WARN %s" % msg)


def _parse_open_days(payload):
    """校验 payload 并返回日期集合；结构不合法返回空集（由调用方回退 weekday）。"""
    if not isinstance(payload, dict):
        return set()
    days = payload.get("open_days")
    if not isinstance(days, list):
        return set()
    valid = set()
    for item in days:
        if not isinstance(item, str) or not _DATE_RE.match(item):
            continue
        try:
            datetime.date.fromisoformat(item)
        except ValueError:
            continue
        valid.add(item)
    return valid


def load_open_days(path=None):
    """加载开市日集合；失败返回空集（=走 weekday 回退），绝不抛异常。

    path 缺省用 OPEN_DAYS_PATH；返回值是副本，调用方修改不会污染模块缓存。
    """
    p = os.path.abspath(path or OPEN_DAYS_PATH)
    if p in _CACHE:
        return set(_CACHE[p])
    try:
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # noqa: BLE001  fail-open：日历缺失/损坏不能弄死告警链
        _warn("load %s failed: %s -> fallback weekday<5" % (p, str(exc)[:120]))
        _CACHE[p] = set()
        return set()
    days = _parse_open_days(payload)
    if not days:
        _warn("invalid/empty open_days in %s -> fallback weekday<5" % p)
    _CACHE[p] = days
    return set(days)


def _as_date(day):
    """接受 date/datetime/ISO 字符串；其它类型是调用方编程错误，直接 TypeError。"""
    if isinstance(day, datetime.datetime):
        return day.date()
    if isinstance(day, datetime.date):
        return day
    if isinstance(day, str) and _DATE_RE.match(day):
        try:
            return datetime.date.fromisoformat(day)
        except ValueError:
            pass
    raise TypeError("expect datetime.date, got %s" % type(day).__name__)


def is_trading_day(day, path=None):
    """day 是否开市日；日历不可用（空集）时回退 weekday<5。"""
    d = _as_date(day)
    days = load_open_days(path)
    if not days:
        return d.weekday() < 5
    return d.isoformat() in days


def last_trading_day(day, path=None):
    """返回 <= day 的最近开市日；日历不可用时回退"最近的周一至周五"。

    向前最多回看 40 个自然日（覆盖春节等最长休市段）；仍找不到（如日期落在
    日历 range 之外）则退回工作日常识，保证调用方总能拿到一个 <= day 的日期。
    """
    d = _as_date(day)
    days = load_open_days(path)
    probe = d
    for _ in range(40):
        if days:
            if probe.isoformat() in days:
                return probe
        elif probe.weekday() < 5:
            return probe
        probe -= datetime.timedelta(days=1)
    probe = d
    while probe.weekday() >= 5:
        probe -= datetime.timedelta(days=1)
    return probe
