# -*- coding: utf-8 -*-
"""A 股休市日历（本地轻量版）——只回答一个问题：某日是否交易日。

权威来源（双源，快照优先）
    1. **主源：本地 Tushare `trade_cal` 快照**（`data/tushare/trade_cal/part_*.parquet`，
       每日 23:30 由既有的 Tushare 同步任务刷新）。实测覆盖 1991-01-01 →
       2027-12-31，字段 exchange/cal_date/is_open/pretrade_date，本机只有 SSE
       （沪深休市一致，可代用）。这是**机器可取**的路径，不需要等公告、不需要手改。
    2. **回退：内置 HOLIDAYS 表**（仅 2026，依上证公告〔2025〕45 号核实）+ FIXED。
       快照缺失/pandas 不可用时才用，保证产线不中断。
    交叉验证：2026 全年逐日比对，快照与内置表 **0 差异**（242 开市日全对）。

判定口径
    * 交易日 = 周一至周五 **且** 当日 is_open=1（快照）或不在休市区间内（内置表）；
    * A 股周末从不交易——调休上班的周六日也不开市（上证公告明确 9/20(周日)、
      10/10(周六) 为「周末休市」），因此不需要「补班日」白名单；
    * 元旦 1-1 收进 FIXED，**任意年份**都判休市——快照不可用时的最后兜底。

**两个必须知道的陷阱**
    1. 快照对未来年份给的是**推算值**。实测 2027：Tushare 与新华社口径在 3 处
       不一致（春节 2/12、劳动 4/30 与 5/4、中秋 9/16-17），方向还相反。不过
       写入端收不到未来日期（`valid_day` 拒绝未来年份），这层风险只影响「判定
       展示」，不影响采集；进入该年后官方安排已发布、快照亦已修正。
    2. **真正会影响采集的是快照陈旧**：同步任务挂了却仍拿旧日历拒写，会把真实
       交易日拒掉。故 snapshot_stale() 在 mtime 超过 7 天时让写入端降级为
       「只告警、照写」。无快照**不算**陈旧——那是内置表接管，人工核实过，
       仍可拒写；两种情况处置不同，不能用同一个标志。

盲区自检
    快照 + 内置表都判不到时只会漏判节假日（把休市日当交易日），不会误杀。
    这类错误不报错、只静默失真，故提供：
      * confidence(d) —— 'exact' / 'weekday_only' / 'invalid'
      * source_of(d) —— 该判定来自快照/内置表/固定日/周末
      * needs_update(today) —— 该补登哪几年（快照覆盖时为空）
      * gap_deadline(today) —— 首个降级判定的交易日 + 剩余天数
    审计侧据此对「px_d 落在未覆盖年份」的存量数据报出来。

为什么需要它
    weak 产线只校验 px_d「是不是一个真实存在的日期」，拦不住「真实存在但
    休市」的值。真实事故：2026 国庆 10-01~10-07 休市，行情源却把 px_d 写成
    2026-10-02，下游 weak_track_close.nd 按 px_d 去重计「跟踪 N 日」，虚增
    天数；同时下一行回落 2026-09-30（真·节前最后交易日），审计侧表现为
    「px_d 倒退」，把正确的 09-30 和错误的 10-02 混为一谈。
"""

from __future__ import annotations

import os
from datetime import date, datetime

# Tushare trade_cal 快照目录（既有同步任务的产物，每日 23:30 刷新）
SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "tushare", "trade_cal"
)

# 年份 → 休市区间（起月, 起日, 止月, 止日），闭区间。**回退用**：仅收录已核实年份。
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


# --------------------------------------------------------------------------
# Tushare trade_cal 快照（主数据源，机器可取，无需等公告）
# --------------------------------------------------------------------------
_SNAP = {"key": None, "map": None, "info": None}


def _snapshot_file():
    """快照目录里 mtime 最新的 part_*.parquet → (mtime, path)；无则 None。

    **不能按文件名排序**：序号会回绕。实测 part_0026 的 mtime 是 10-22，
    比 part_0015 的 11-04 还旧；每个 part 都是全量快照而非分片，按名字取
    「最大」会拿到过期数据。只能按 mtime。
    """
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    try:
        names = os.listdir(SNAPSHOT_DIR)
    except OSError:
        return None
    best = None
    for name in names:
        if not (name.startswith("part_") and name.endswith(".parquet")):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if best is None or mt > best[0]:
            best = (mt, path)
    return best


def load_snapshot():
    """读最新快照 → (map, info)；不可用返回 ({}, None)。

    map: {date: 1/0}（1=开市）。info: path/mtime/start/end/rows/open_days。
    按 (path, mtime) 进程内缓存——每日刷新后 mtime 变化即自动重载。
    pandas/pyarrow 缺失、文件损坏、目录不存在一律静默降级（返回空），不抛，
    保证产线在依赖缺失时退回内置表继续跑。
    """
    best = _snapshot_file()
    if best is None:
        return {}, None
    mt, path = best
    if _SNAP["key"] == (path, mt):
        return _SNAP["map"], _SNAP["info"]
    try:
        import pandas as pd  # 延迟导入：无 pandas 时直接退回内置表
    except ImportError:
        return {}, None
    try:
        df = pd.read_parquet(path, columns=["exchange", "cal_date", "is_open"])
        if "exchange" in df.columns:
            df = df[df["exchange"] == "SSE"]
        out = {}
        for cal, op in zip(df["cal_date"].tolist(), df["is_open"].tolist()):
            s = str(cal)[:10]
            if len(s) == 8 and s.isdigit():  # 20270104 → 2027-01-04
                s = "%s-%s-%s" % (s[:4], s[4:6], s[6:])
            try:
                out[date.fromisoformat(s)] = 1 if int(op) == 1 else 0
            except (TypeError, ValueError):
                continue
        if not out:
            return {}, None
        info = {
            "path": path,
            "mtime": datetime.fromtimestamp(mt),
            "start": min(out),
            "end": max(out),
            "rows": len(out),
            "open_days": sum(out.values()),
        }
    except Exception:  # noqa: BLE001  读取/解析任何异常都降级，不中断采集
        return {}, None
    _SNAP.update(key=(path, mt), map=out, info=info)
    return out, info


def snapshot_info():
    """快照状态 dict（path/mtime/start/end/rows/open_days）；不可用返回 None。"""
    return load_snapshot()[1]


def _snap_flag(d):
    """快照里该日是否开市 → 1/0；快照无此日返回 None（交由回退逻辑判）。"""
    m, _ = load_snapshot()
    return m.get(d) if m else None


def covered(year):
    """该年份是否有可靠休市判定（快照覆盖该年，或内置表已登记）。"""
    y = int(year)
    if y in HOLIDAYS:
        return True
    info = snapshot_info()
    return bool(info and info["start"].year <= y <= info["end"].year)


def snapshot_stale(max_age_days=7, today=None):
    """快照存在但已过期（mtime 早于 max_age_days 天）→ True。

    用途：快照每日 23:30 刷新，一旦同步任务挂了，手里的就是旧日历。此时
    **不该拿它做拒写**（会把真实交易日拒掉、破坏采集），只告警照写。

    无快照返回 False——那不是「陈旧」，而是「没有」，由内置表接管，内置表
    是人工核实过的，仍可拒写。两种情况的处置不同，故不能用同一个标志。
    """
    info = snapshot_info()
    if info is None:
        return False
    t = _as_date(today) or date.today()
    return (t - info["mtime"].date()).days > max_age_days


class snapshot_off(object):
    """临时禁用快照——测降级路径、或离线只按内置表核对时用。

        with ctd.snapshot_off():
            ...  # 此块内 covered()/is_trading_day() 只走内置表 + FIXED

    快照目录被指到不存在的路径，退出时恢复原目录与缓存。产线不使用，
    但降级路径必须可测：否则「快照挂了还能不能跑」永远是未验证的假设。
    """

    def __enter__(self):
        global SNAPSHOT_DIR
        self._dir, self._cache = SNAPSHOT_DIR, dict(_SNAP)
        SNAPSHOT_DIR = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_snapshot_off_"
        )
        _SNAP.update(key=None, map=None, info=None)
        return self

    def __exit__(self, *exc):
        global SNAPSHOT_DIR
        SNAPSHOT_DIR = self._dir
        _SNAP.update(self._cache)
        return False


def source_of(d):
    """判定来源：snapshot / fixed / holiday_table / weekday / invalid。"""
    dt = _as_date(d)
    if dt is None:
        return "invalid"
    if _snap_flag(dt) is not None:
        return "snapshot"
    if (dt.month, dt.day) in FIXED:
        return "fixed"
    if dt.year in HOLIDAYS:
        return "holiday_table"
    return "weekday"


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


def gap_deadline(today=None):
    """缺口「第一个会判错的交易日」→ dict(year, first_gap_day, days_left)。

    无缺口返回 None。标的是**首个降级判定的交易日**：从该日起 confidence()
    只剩 weekday_only，节假日会被当成交易日（静默失真）。元旦由 FIXED 兜住
    属 exact，故不会被算进来——真实例子：2027-01-01 判对，但 01-04 起降级。

    days_left 为负表示已经进入缺口期（危害已发生，不是将要发生）。
    """
    t = _as_date(today) or date.today()
    ys = needs_update(t)
    if not ys:
        return None
    year = ys[0]
    start = max(t, date(year, 1, 1))
    end = date(year, 12, 31)
    d = start
    while d <= end:
        if d.weekday() < 5 and confidence(d) == "weekday_only":
            return {
                "year": year,
                "first_gap_day": d.isoformat(),
                "days_left": (d - t).days,
            }
        d = date.fromordinal(d.toordinal() + 1)
    # 整年都判不到缺口（该年余下全是周末/固定日），仍报年份但无具体日子
    return {"year": year, "first_gap_day": None, "days_left": None}


def in_holiday(d):
    """是否非交易日里的「休市」（快照优先；兜底 FIXED + 内置区间）。"""
    dt = _as_date(d)
    if dt is None:
        return False
    v = _snap_flag(dt)
    if v is not None:  # 快照权威：is_open=0 即休市
        return v == 0
    if (dt.month, dt.day) in FIXED:
        return True
    if dt.year not in HOLIDAYS:
        return False  # 无快照且无内置表 → 无法判定，不误杀
    for m1, d1, m2, d2 in HOLIDAYS[dt.year]:
        start = date(dt.year, m1, d1)
        end = date(dt.year, m2, d2)
        if start <= dt <= end:
            return True
    return False


def is_trading_day(d):
    """是否交易日（快照优先；无快照退回周末 + 内置休市表）。"""
    dt = _as_date(d)
    if dt is None:
        return False
    if dt.weekday() >= 5:  # 周末永不交易，先短路，省一次快照查询
        return False
    v = _snap_flag(dt)
    if v is not None:
        return v == 1
    return not in_holiday(dt)


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
