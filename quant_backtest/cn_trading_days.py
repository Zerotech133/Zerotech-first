# -*- coding: utf-8 -*-
"""A 股休市日历（本地轻量版）——只回答一个问题：某日是否交易日。

权威来源
    上海证券交易所《关于上海证券交易所 2026 年部分节假日休市安排的通知》
    上证公告〔2025〕45 号（2025-12-22）
    https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml

判定口径
    * 交易日 = 周一至周五 **且** 不在休市区间内；
    * A 股周末从不交易——调休上班的周六日也不开市（上述公告明确
      9/20(周日)、10/10(周六) 为「周末休市」），因此不需要「补班日」白名单；
    * **只登记已核实的年份**。未登记年份退回「周一至周五」判断，
      调用方可用 covered() 判断 holiday 判定是否可信；
    * 例外：元旦 1-1 收进 FIXED，**任意年份**都判休市。依据是《全国年节及
      纪念日放假办法》法定假日、A 股历年 1-1 均休市，无需等年度公告；
      春节/清明/端午/中秋/国庆涉及调休与节气，必须等公告，此处**不猜**。

盲区自检
    未登记年份只会漏判节假日（把休市日当交易日），不会误杀交易日。
    这类错误不报错、只静默失真，故提供：
      * confidence(d) —— 'exact' / 'weekday_only' / 'invalid'
      * needs_update(today) —— 该补登哪几年的休市安排
    审计侧据此对「px_d 落在未覆盖年份」的存量数据报出来。

为什么需要它
    weak 产线只校验 px_d「是不是一个真实存在的日期」，拦不住「真实存在但
    休市」的值。真实事故：2026 国庆 10-01~10-07 休市，行情源却把 px_d 写成
    2026-10-02，下游 weak_track_close.nd 按 px_d 去重计「跟踪 N 日」，虚增
    天数；同时下一行回落 2026-09-30（真·节前最后交易日），审计侧表现为
    「px_d 倒退」，把正确的 09-30 和错误的 10-02 混为一谈。
"""

from __future__ import annotations

from datetime import date, datetime

# 年份 → 休市区间（起月, 起日, 止月, 止日），闭区间。仅收录已核实年份。
HOLIDAYS = {
    2026: (
        (1, 1, 1, 3),      # 元旦
        (2, 15, 2, 23),    # 春节
        (4, 4, 4, 6),      # 清明
        (5, 1, 5, 5),      # 劳动节
        (6, 19, 6, 21),    # 端午
        (9, 25, 9, 27),    # 中秋
        (10, 1, 10, 7),    # 国庆
    ),
}

COVERED_YEARS = tuple(sorted(HOLIDAYS))

# 固定休市日（月, 日）——不依赖年度公告，任意年份生效。
# 只收「法定且历年必然休市」的：元旦 1-1。
# 春节（农历）、清明（节气）、端午、中秋、国庆（均含调休）必须等公告，不在此列。
FIXED = ((1, 1),)


def _as_date(v):
    """str/date/datetime → date；非法返回 None（不抛，调用方自行降级）。"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def covered(year):
    """该年份是否有已核实的休市安排。"""
    return int(year) in HOLIDAYS


def in_fixed(d):
    """是否落在固定休市日（元旦 1-1，任意年份生效）。"""
    dt = _as_date(d)
    return dt is not None and (dt.month, dt.day) in FIXED


def confidence(d):
    """判定可信度：exact=年份已登记或固定日；weekday_only=只看了周末。"""
    dt = _as_date(d)
    if dt is None:
        return "invalid"
    if covered(dt.year) or (dt.month, dt.day) in FIXED:
        return "exact"
    return "weekday_only"


def needs_update(today=None, remind_from_month=11):
    """该补登哪几年的休市安排 → 年份列表（空=无缺口）。

    今年未登记 → 立即要补（危害已发生）。
    下一年未登记且已到 remind_from_month 月 → 提前提醒（公告一般 12 月发布）。
    """
    t = _as_date(today) or date.today()
    out = []
    if not covered(t.year):
        out.append(t.year)
    nxt = t.year + 1
    if not covered(nxt) and t.month >= remind_from_month:
        out.append(nxt)
    return out


def in_holiday(d):
    """是否落在休市区间内（含固定休市日；其余未覆盖年份一律 False）。"""
    dt = _as_date(d)
    if dt is None:
        return False
    if (dt.month, dt.day) in FIXED:
        return True
    if not covered(dt.year):
        return False
    for m1, d1, m2, d2 in HOLIDAYS[dt.year]:
        start = date(dt.year, m1, d1)
        end = date(dt.year, m2, d2)
        if start <= dt <= end:
            return True
    return False


def is_trading_day(d):
    """是否交易日。未覆盖年份只在周末判定上可信（见 covered()）。"""
    dt = _as_date(d)
    if dt is None:
        return False
    return dt.weekday() < 5 and not in_holiday(dt)


def why_not(d):
    """非交易日原因（'周末' / '休市' / 空串），交易日返回空串。"""
    dt = _as_date(d)
    if dt is None:
        return ""
    if dt.weekday() >= 5:
        return "周末"
    return "休市" if in_holiday(dt) else ""


def last_trading_day(d):
    """≤ d 的最近交易日（最多回退 30 天；找不到返回 None）。"""
    dt = _as_date(d)
    if dt is None:
        return None
    for i in range(0, 31):
        cand = date.fromordinal(dt.toordinal() - i)
        if is_trading_day(cand):
            return cand.isoformat()
    return None


def prev_trading_day(d):
    """严格早于 d 的最近交易日（用于「上一交易日」语义）。"""
    dt = _as_date(d)
    if dt is None:
        return None
    for i in range(1, 31):
        cand = date.fromordinal(dt.toordinal() - i)
        if is_trading_day(cand):
            return cand.isoformat()
    return None
