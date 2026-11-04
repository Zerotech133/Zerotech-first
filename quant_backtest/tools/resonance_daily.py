# -*- coding: utf-8 -*-
"""P4 共振 v4 日更闭环 —— resonance_daily：让 resonance_events.db 每天自动长数据。

背景：v4 三表库（resonance_store.py）此前只能由 v3 JSON 手动 rebuild 灌数据；
本入口补上「日更闭环」——每天增量 compute → update_day / update_event / backfill_fwd。

模块复用（不重写入库/口径逻辑）：
  - resonance_store：update_day / update_event / backfill_fwd / stats / _load_index
  - resonance_v3   ：load_jev_daily / load_jev_daily 的 SQL 口径（雪球 bull 率 / JEV bull 率）
    —— 通过把 v3 的 WIN_START/WIN_END 钉到单日复用（v3 只读 mode=ro，安全）；
    v3 import 时会包装 sys.stdout（v3 L49），导入后立即还原；
    若 v3 不可 import，则退回本文件内的最小 SQL 复刻（口径抄自 v3 L114-138 / L146-168）。

日更口径（与 v3 一致的处所以行号注明）：
  - xq：xq_status_classified(sentiment) JOIN xq_status(created_at)，created_at 前 10 位=当日，
    n=bullish+bearish+neutral，bull_rate=bullish/n（v3 L146-168）；
  - jev：news.hit=1 且 jev 非空且 bias∈{bull,bear,neutral}，ts 前 10 位=当日，
    bull_rate=bull/n（v3 L114-138）；
  - gap = xq_bull − jev_bull（v3 L190）；噪声 = xq_n<5（v3 L63）；
    某源当日零数据 → 该率/gap 记 None（诚实，v3 L185-189 同语义），noise 标 True（工单口径）；
  - z_roll：读库内截至前一日的最近 ROLL_WINDOW=20 个有效 gap 滚动算（不全量重算），
    窗口不足 ROLL_MIN=5 → None；σ≈0 退化记 0.0（与 v3 zscore_series L233-235 同款）；
  - 收敛事件（口径同 v3 find_convergence L241-258，z 源改为库内 z_roll）：
    昨日 |z_roll|>1 且当日 gap 与昨日 gap 跨越库内全量 gap 均值 → update_event。

CLI：
  python resonance_daily.py                # 增量：库内最大日期之后 → 今天，逐日补齐
  python resonance_daily.py --date 2026-11-03   # 单日重算（幂等覆盖）
  python resonance_daily.py --dry          # 只算不写，看今日计划

不 git 提交。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

BASE = "C:/项目"
DB_PATH = BASE + "/老田的AI助手/quant_backtest/data/resonance/resonance_events.db"
XQ_DB = BASE + "/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db"
NEWS_DB = BASE + "/老田的AI助手/news_collector/news.db"
IDX_PARQUET = BASE + "/老田的AI助手/quant_backtest/data/ashare/000905_SZ.parquet"

# ---- 复用 v4 入库器（入库逻辑单一来源）----
from resonance_store import _load_index, backfill_fwd, stats, update_day, update_event

# ---- 复用 v3 计算口径（import 成功则用其 loaders；失败退回本地 SQL 复刻）----
_v3 = None
try:
    _stdout_keep = sys.stdout
    try:
        import resonance_v3 as _v3
    finally:
        _wrapped = sys.stdout
        sys.stdout = _stdout_keep          # 还原 v3 L49 的 stdout 包装
        try:
            _wrapped.detach()              # 防 GC 关闭共享底层 buffer
        except Exception:
            pass
except Exception:
    _v3 = None

XQ_NOISE_N = getattr(_v3, "XQ_NOISE_N", 5) if _v3 else 5    # 雪球 n<5 → 噪声（v3 L63）
Z_BAND = getattr(_v3, "Z_BAND", 1.0) if _v3 else 1.0        # 极端带 |z|>1（v3 L61）
ROLL_WINDOW = 20        # z_roll 滚动窗：库内截至前一日的最近 20 个有效 gap
ROLL_MIN = 5            # 窗口不足 5 个有效 gap → z_roll=None（诚实）
EVENT_SCAN_DAYS = 7     # 收敛事件扫描：最近 N 日


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


# ================================================================
# 单日两源 bull 率（口径与 v3 完全一致）
# ================================================================
def _day_rates_via_v3(date_str: str, xq_db: str, news_db: str):
    """把 v3 的窗钉到单日，直接复用其 loaders（SQL/过滤口径零漂移）。"""
    old = (_v3.WIN_START, _v3.WIN_END, _v3.XQ_DB, _v3.NEWS_DB)
    _v3.WIN_START = _v3.WIN_END = date_str
    _v3.XQ_DB, _v3.NEWS_DB = xq_db, news_db
    try:
        jev = _v3.load_jev_daily().get(date_str)
        xq = _v3.load_xq_daily().get(date_str)
    finally:
        (_v3.WIN_START, _v3.WIN_END, _v3.XQ_DB, _v3.NEWS_DB) = old
    return xq, jev


def _day_rates_local(date_str: str, xq_db: str, news_db: str):
    """v3 不可 import 时的最小口径复刻（口径抄自 v3 L114-138 JEV / L146-168 雪球）。"""
    jev = None
    con = _ro(news_db)
    try:
        rows = con.execute(
            "SELECT jev FROM news WHERE hit=1 AND ts >= ? AND ts < ? "
            "AND jev IS NOT NULL AND jev != ''",
            (date_str, date_str + "Z")).fetchall()
    finally:
        con.close()
    c = {"bull": 0, "bear": 0, "neutral": 0}
    for (raw,) in rows:
        try:
            bias = (json.loads(raw) or {}).get("bias")
        except Exception:
            continue
        if bias in c:
            c[bias] += 1
    n = sum(c.values())
    if n:
        jev = {"n": n, "bull_rate": c["bull"] / n}

    xq = None
    con = _ro(xq_db)
    try:
        rows = con.execute(
            "SELECT c.sentiment FROM xq_status_classified c "
            "JOIN xq_status x ON c.status_id = x.status_id "
            "WHERE x.created_at >= ? AND x.created_at < ? AND c.sentiment IS NOT NULL",
            (date_str, date_str + "Z")).fetchall()
    finally:
        con.close()
    c = {"bullish": 0, "bearish": 0, "neutral": 0}
    for (s,) in rows:
        if s in c:
            c[s] += 1
    n = sum(c.values())
    if n:
        xq = {"n": n, "bull_rate": c["bullish"] / n}
    return xq, jev


def _z_roll(date_str: str, gap, db_path: str,
            window: int = ROLL_WINDOW, min_n: int = ROLL_MIN):
    """滚动 z：读库内 date 之前最近 window 个有效 gap（不全量重算）；不足 min_n → None。"""
    if gap is None or not os.path.exists(db_path):
        return None
    con = _ro(db_path)
    try:
        rows = [r[0] for r in con.execute(
            "SELECT gap FROM gap_daily WHERE date < ? AND gap IS NOT NULL "
            "ORDER BY date DESC LIMIT ?", (date_str, window))]
    finally:
        con.close()
    if len(rows) < min_n:
        return None
    sd = statistics.stdev(rows)
    if sd <= 1e-12:
        return 0.0   # σ≈0 退化记 0.0，与 v3 zscore_series L233-235 同款
    return (gap - statistics.fmean(rows)) / sd


def compute_day(date_str: str, *, db_path: str = None, xq_db: str = None,
                news_db: str = None, index_df=None) -> dict:
    """单日口径计算（两源只读）。零数据日：该率/gap 记 None、noise=True（诚实）。"""
    db_path = db_path or DB_PATH
    if _v3 is not None:
        xq, jev = _day_rates_via_v3(date_str, xq_db or XQ_DB, news_db or NEWS_DB)
    else:
        xq, jev = _day_rates_local(date_str, xq_db or XQ_DB, news_db or NEWS_DB)
    xq_n = xq["n"] if xq else 0
    jev_n = jev["n"] if jev else 0
    xq_bull = xq["bull_rate"] if xq else None
    jev_bull = jev["bull_rate"] if jev else None
    gap = (xq_bull - jev_bull) if (xq_bull is not None and jev_bull is not None) else None
    noise = (xq_n < XQ_NOISE_N) or (jev_n == 0)   # 某源零数据 → True（工单口径）
    z_roll = _z_roll(date_str, gap, db_path)
    is_trade_day = pct_chg = None
    if index_df is not None:
        dates = set(index_df["date"])
        if dates and date_str <= max(dates):      # 超出 parquet 最新日 → 未知，记 None
            is_trade_day = date_str in dates
            if is_trade_day:
                pct_chg = float(index_df.loc[index_df["date"] == date_str, "pct_chg"].iloc[0])
    return {"date": date_str, "xq_n": xq_n, "xq_bull": xq_bull, "jev_n": jev_n,
            "jev_bull": jev_bull, "gap": gap, "noise": noise, "z_roll": z_roll,
            "is_trade_day": is_trade_day, "pct_chg": pct_chg}


# ================================================================
# 增量日期枚举 + 收敛事件扫描
# ================================================================
def _pending_dates(db_path: str, end_date: str):
    """库内 gap_daily 最大日期之后 → end_date 的所有自然日（含端点）。"""
    if not os.path.exists(db_path):
        return []
    con = _ro(db_path)
    try:
        mx = con.execute("SELECT MAX(date) FROM gap_daily").fetchone()[0]
    finally:
        con.close()
    if mx is None or mx >= end_date:
        return []
    d = datetime.strptime(mx, "%Y-%m-%d").date() + timedelta(days=1)
    d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    out = []
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _detect_events(db_path: str, extra_rows=None, scan_days: int = EVENT_SCAN_DAYS):
    """收敛事件（口径同 v3 find_convergence L241-258，z 源=库内 z_roll）：
    昨日 |z_roll|>Z_BAND 且 (prev.gap−m)·(cur.gap−m)≤0（m=库内全量 gap 均值）。
    extra_rows：dry 模式下未入库的虚拟行（date/gap/z_roll/noise/xq_n/jev_n）。"""
    extra_rows = extra_rows or []
    con = _ro(db_path) if os.path.exists(db_path) else None
    try:
        lib_max, all_gaps = None, []
        if con is not None:
            lib_max = con.execute("SELECT MAX(date) FROM gap_daily").fetchone()[0]
            all_gaps = [r[0] for r in con.execute(
                "SELECT gap FROM gap_daily WHERE gap IS NOT NULL")]
        last = max([d for d in [lib_max] + [r["date"] for r in extra_rows] if d], default=None)
        if last is None:
            return [], None
        rows = {}
        if con is not None:
            for r in con.execute(
                "SELECT date, gap, z_roll, noise, xq_n, jev_n FROM gap_daily "
                "WHERE date <= ? ORDER BY date DESC LIMIT ?",
                (last, scan_days + 1)).fetchall():
                rows[r[0]] = {"date": r[0], "gap": r[1], "z_roll": r[2],
                              "noise": r[3], "xq_n": r[4], "jev_n": r[5]}
        for r in extra_rows:
            rows[r["date"]] = {"date": r["date"], "gap": r["gap"], "z_roll": r["z_roll"],
                               "noise": 1 if r["noise"] else 0,
                               "xq_n": r["xq_n"], "jev_n": r["jev_n"]}
        seq = sorted(rows.values(), key=lambda x: x["date"])
        gaps = all_gaps + [r["gap"] for r in extra_rows if r["gap"] is not None]
        m = statistics.fmean(gaps) if gaps else None
        events = []
        for i in range(1, len(seq)):
            prev, cur = seq[i - 1], seq[i]
            if (m is None or prev["gap"] is None or cur["gap"] is None
                    or prev["z_roll"] is None):
                continue
            if abs(prev["z_roll"]) > Z_BAND and (prev["gap"] - m) * (cur["gap"] - m) <= 0:
                events.append({
                    "event_date": cur["date"], "prev_date": prev["date"],
                    "prev_gap": prev["gap"], "prev_z": prev["z_roll"],
                    "gap": cur["gap"], "z": cur["z_roll"],
                    "pullback": abs(prev["gap"] - cur["gap"]),
                    "noise": bool(cur["noise"]) or bool(prev["noise"]),
                    "xq_n": cur["xq_n"], "jev_n": cur["jev_n"]})
        return events, m
    finally:
        if con is not None:
            con.close()


# ================================================================
# 日更主流程
# ================================================================
def daily_update(dates=None, *, dry=False, db_path: str = None, xq_db: str = None,
                 news_db: str = None, index_path: str = None, end_date: str = None,
                 scan_days: int = EVENT_SCAN_DAYS) -> dict:
    """缺省=库内最大日期之后到今天的所有日期，逐日 compute_day→update_day；
    然后事件扫描（最近 scan_days 日）→ update_event；再 backfill_fwd（新到指数自动补 fwd）。
    返回 {updated_days, new_events, backfilled, stats}。"""
    db_path = db_path or DB_PATH
    ipp = index_path or IDX_PARQUET
    end = end_date or datetime.now().date().isoformat()
    if dates is None:
        dates = _pending_dates(db_path, end)
    dates = sorted(set(dates))

    index_df = None
    if os.path.exists(ipp):
        try:
            index_df = _load_index(ipp)
        except Exception:
            index_df = None

    computed = [compute_day(d, db_path=db_path, xq_db=xq_db, news_db=news_db,
                            index_df=index_df) for d in dates]
    if not dry:
        for r in computed:
            update_day(
                r["date"], is_trade_day=r["is_trade_day"], gap=r["gap"],
                z_roll=r["z_roll"],
                extreme=(r["z_roll"] is not None and abs(r["z_roll"]) > Z_BAND),
                noise=r["noise"], xq_n=r["xq_n"], jev_n=r["jev_n"],
                xq_bull=r["xq_bull"], jev_bull=r["jev_bull"],
                pct_chg=r["pct_chg"], db_path=db_path)

    # 事件扫描：dry 用虚拟行（未入库的当日行），实跑直接读库（已写入）
    detected, _m = _detect_events(db_path, extra_rows=computed if dry else None,
                                  scan_days=scan_days)
    existing = set()
    if os.path.exists(db_path):
        con = _ro(db_path)
        try:
            existing = {r[0] for r in con.execute("SELECT event_date FROM events")}
        finally:
            con.close()
    new_events = [e for e in detected if e["event_date"] not in existing]
    if not dry:
        for e in detected:
            update_event(e["event_date"], prev_date=e["prev_date"], prev_gap=e["prev_gap"],
                         prev_z=e["prev_z"], gap=e["gap"], z=e["z"],
                         pullback=e["pullback"], noise=e["noise"],
                         xq_n=e["xq_n"], jev_n=e["jev_n"], db_path=db_path)
        bf = backfill_fwd(ipp, db_path)
        backfilled = len(bf["filled"])
        index_last = bf["index_last_date"]
        st = stats(db_path)
    else:
        backfilled = None
        index_last = index_df["date"].iloc[-1] if index_df is not None else None
        st = stats(db_path) if os.path.exists(db_path) else None

    return {"updated_days": [r["date"] for r in computed], "days_detail": computed,
            "new_events": [e["event_date"] for e in new_events],
            "events_detail": detected, "backfilled": backfilled,
            "index_last_date": index_last, "stats": st, "dry": dry}


# ================================================================
# CLI
# ================================================================
def _f(x, nd=4):
    return ("{:." + str(nd) + "f}").format(x) if x is not None else "None"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P4 共振 v4 日更：resonance_events.db 增量长数据（复用 store 入库 + v3 口径）")
    ap.add_argument("--date", help="单日重算 YYYY-MM-DD；缺省=库内最大日期之后到今天")
    ap.add_argument("--dry", action="store_true", help="只算不写，看今日计划")
    a = ap.parse_args(argv)
    r = daily_update(dates=[a.date] if a.date else None, dry=a.dry)
    tag = "DRY" if a.dry else "实跑"
    v3_used = _v3 is not None
    print(f"[{tag}] v3 口径来源: {'import resonance_v3（loaders 复用）' if v3_used else '本地 SQL 复刻（v3 不可 import）'}")
    print(f"[{tag}] 待更新日: {r['updated_days'] or '（无，库已最新）'}")
    for d in r["days_detail"]:
        print(f"  {d['date']}  xq n={d['xq_n']} bull={_f(d['xq_bull'])} | "
              f"jev n={d['jev_n']} bull={_f(d['jev_bull'])} | gap={_f(d['gap'])} "
              f"z_roll={_f(d['z_roll'])} noise={d['noise']} "
              f"trade={_f(d['is_trade_day'], 0)} pct={_f(d['pct_chg'], 2)}")
    print(f"[{tag}] 新收敛事件: {r['new_events'] or '无'}")
    for e in r["events_detail"]:
        print(f"  {e['event_date']}  prev {e['prev_date']} "
              f"{e['prev_gap']:+.3f}(z={e['prev_z']:+.2f}) -> {e['gap']:+.3f}  "
              f"pullback={e['pullback']:.3f}{' [含噪声]' if e['noise'] else ''}")
    bf = "（dry 未执行）" if r["backfilled"] is None else r["backfilled"]
    print(f"[{tag}] backfill 回填事件数: {bf}  指数 parquet 最新日: {r['index_last_date']}")
    print(f"[{tag}] stats: {json.dumps(r['stats'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
