# -*- coding: utf-8 -*-
"""K-STOCK-289 全市场缠论信号扫描器（老田 10-13 08:30 口径）。

口径：每日全网 5000+ 只（剔 ST、北交所、指数），缠论一买/二买/三买全部入池；
满足三买的进行交易（交易过滤在引擎 buy_filter，本扫描器产出全部买点类型事件）。

- 信号口径与 285b 引擎完全一致：hfq K 线（263 铁律）+ WARMUP=250 尾窗 +
  seen-ever fresh 去重（同 (stock,type,dt) 只在首次出现日记事件）。
- 多进程（spawn，按股分任务，32 核），结果落
  data/bt2_state/sig_events_market_<d0>_<d1>.json（引擎直接吃）。
- CLI: python market_scan.py --d0 20251013 --d1 20261012 [--workers 16]
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

BASE = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in dir()
    else r"C:/项目/老田的AI助手/quant_backtest"
)
QBT = BASE
DATA = os.path.join(QBT, "data", "ashare")
NAMES_FP = os.path.join(QBT, "data", "stock_names.json")
OUT_DIR = os.path.join(
    QBT, "data", "sig_events"
)  # K-STOCK-358：事件缓存独立目录（原混在 bt2_state）
NC_FP = os.path.join(
    QBT, "data", "tdx", "ts_namechange.parquet"
)  # K-STOCK-311 历史曾用名
WARMUP = 250

sys.path.insert(0, QBT)


def market_stocks():
    """全市场可交易股票（剔 ST·北交所·指数）：数据文件 ∩ code2name 非 ST。"""
    names = json.load(open(NAMES_FP, encoding="utf-8"))["code2name"]
    out = []
    for fn in os.listdir(DATA):
        if not fn.endswith(".parquet"):
            continue
        code = fn[:-8].replace("_", ".")  # 000001_SZ -> 000001.SZ
        if code.endswith(".BJ"):  # 北交所（老田口径：不分析）
            continue
        # 指数混入剔除（K-STOCK-303·273 体检实锤 000001_SH/399001/399006 混入）：
        # SH 指数=000xxx/880xxx/999xxx 段（股票是 60/68 段，不会误伤 000688.SZ 国城矿业这类同名码）；
        # SZ 指数=399 段（深市个股无 399 开头）。
        if code.endswith(".SH") and code[:3] in ("000", "880", "999"):
            continue
        if code.startswith("399"):
            continue
        nm = names.get(code)
        if nm is None:  # 指数/北交所/名单外
            continue
        if "ST" in str(nm).upper():  # ST（按 2026-10-08 现名，历史 ST 未回溯）
            continue
        out.append(code)
    return sorted(out)


def hist_st_set(d0, d1):
    """K-STOCK-311 历史 ST 回溯：窗口 [d0,d1] 内任一时点名称含 ST 的股票集合。

    现名剔 ST 有幸存者偏差（历史被 ST 后摘帽的票一直留在宇宙）。
    数据源 ts_namechange（股票曾用名，2020 起，4044 行），区间
    [start_date, end_date] 与窗口有交集即命中（end_date 空=现行名称）。
    覆盖边界：2020-01 前已戴帽且窗口内仍未摘帽的区间整段缺失——此类票
    现名大概率仍为 ST（现名过滤已覆盖），漏网风险极小。
    """
    import pandas as pd

    if not os.path.exists(NC_FP):
        return set()
    nc = pd.read_parquet(NC_FP)
    nc = nc[nc["name"].astype(str).str.upper().str.contains("ST")]
    sd = nc["start_date"].astype(str)
    ed = nc["end_date"].fillna("99999999").astype(str)
    hit = nc[(sd <= str(d1)) & (ed >= str(d0))]
    return set(hit["ts_code"])


# ---- N3 ST 公告 PIT 化（K-STOCK 门② 子项，2026-10-21 老田拍板 P1-P5 后实施）----
# 新增为「增量、零副作用」函数：不改变 hist_st_set / market_stocks 既有行为；
# 生产默认仍走 legacy hist_st_set，直至 BACK1/STRAT1 签字采纳后才在引擎侧启用。
_ST_PIT_INTERVALS_CACHE = None


def _load_st_pit_intervals():
    """PIT ST 区间（P1：public_start = max(start_date, ann_date)），进程内缓存。

    返回 {code: [(public_start, end), ...]}，public_start 为「市场可见即剔」起点。
    数据源同 hist_st_set：data/tdx/ts_namechange.parquet。文件缺失返回空 dict。
    """
    global _ST_PIT_INTERVALS_CACHE
    if _ST_PIT_INTERVALS_CACHE is not None:
        return _ST_PIT_INTERVALS_CACHE
    import pandas as pd

    if not os.path.exists(NC_FP):
        _ST_PIT_INTERVALS_CACHE = {}
        return _ST_PIT_INTERVALS_CACHE
    nc = pd.read_parquet(NC_FP)
    nc = nc[nc["name"].astype(str).str.upper().str.contains("ST")]
    intervals = defaultdict(list)
    for code, start, end, ann in zip(
        nc["ts_code"], nc["start_date"], nc["end_date"], nc["ann_date"]
    ):
        s = str(start)
        e = None if pd.isna(end) else str(end)
        a = None if pd.isna(ann) else str(ann)
        # P1：公开起点取 max(start, ann)；ann 缺失时退化为 start（等价原探针）
        pub = s if (a is None or s >= a) else a
        intervals[str(code)].append((pub, e))
    _ST_PIT_INTERVALS_CACHE = intervals
    return intervals


def is_st_pit_blocked(code, day, intervals=None):
    """N3 PIT 阻塞判定（逐事件交易日级）：事件交易日 day 当日或之前 ST 已公开 → 阻塞。

    - P1 口径：public_start = max(start_date, ann_date)（可见即剔，绝不把未来
      ST 状态回写删除过去事件）。
    - 纯函数、零副作用、不触网/库；供引擎/评分侧「逐事件」调用，替代
      hist_st_set 的整窗丢弃（后者是前视根因，见 a3_st_pit_diff_report.json）。
    - intervals 可注入（测试用，绕过缓存与 parquet 依赖）；为 None 时懒加载缓存。
    - 注意：本函数仅供研究副本 / 显式开启路径调用；生产默认仍走 legacy，
      采纳门（BACK1/STRAT1 签字）后才在 backtest_engine_v2.calc_chanlun_signals_market
      接入。引擎接入步因 backtest_engine_v2.py 当前受敏感审批门限制，列为 N3 后置项。
    """
    if intervals is None:
        intervals = _load_st_pit_intervals()
    day_s = str(day)
    for pub, end in intervals.get(code, ()):
        if pub <= day_s and (end is None or end > day_s):
            return True
    return False


def score_3buy(close, zg, vr, bei, bei2):
    """三买评分（0-100，老田"评分取前十"规律）：位置贴近 40 + 量能 30 + 背驰 30。

    - 位置：收盘距中枢上沿 zg 的回踩贴近度（贴近=洗盘充分·远离=追高风险）
    - 量能：信号日成交额 / 20 日均额（量比·放量过中枢优）
    - 背驰：bei=回踩笔盘整背驰 18 分 · bei2=回踩vs离开力度背驰 12 分
    """
    if not zg or zg <= 0:
        return None
    t = close / zg - 1.0
    if t <= 0.02:
        sp = 40
    elif t <= 0.05:
        sp = 32
    elif t <= 0.10:
        sp = 24
    elif t <= 0.20:
        sp = 14
    else:
        sp = 8
    if vr is None:
        sv = 15
    elif vr >= 1.5:
        sv = 30
    elif vr >= 1.0:
        sv = 24
    elif vr >= 0.7:
        sv = 16
    else:
        sv = 8
    sb = (18 if bei else 0) + (12 if bei2 else 0)
    return round(sp + sv + sb)


def scan_stock(args):
    """单股票 seen-ever 扫描（worker 端）。返回 [{stock,type,day,...}, ...]"""
    code, d0, d1, init_day, win = args
    import pandas as pd
    from chanlun_signal import analyze as chan_analyze

    fp = os.path.join(DATA, code.replace(".", "_") + ".parquet")
    if not os.path.exists(fp):
        return []
    b = pd.read_parquet(fp).sort_values("trade_date").reset_index(drop=True)
    if len(b) < WARMUP // 2:
        return []
    # 2026-11-10 数据刷新后 210/5816 只只剩 OHLC+pre_close（无 pct_chg），
    # 扫描器原样取列直接 KeyError 崩。与 backtest_engine_v2._load_px 同一推导式兜底，
    # 保证扫描侧与引擎侧复权因子同源（否则同一只票两边 kfac 不同）。
    if "pct_chg" not in b.columns:
        if "close" not in b.columns or "pre_close" not in b.columns:
            return []
        b["pct_chg"] = (b["close"] / b["pre_close"] - 1.0) * 100.0
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    amt = b["amount"].astype(float).tolist() if "amount" in b.columns else None
    bars_all = [
        {"dt": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
        for t, o, h, l, c in zip(
            b["trade_date"], b["open"], b["high"], b["low"], b["close"] * kfac
        )
    ]
    date2i = {int(t): i for i, t in enumerate(b["trade_date"])}

    def run_day(day):
        i = date2i.get(int(day))
        if i is None or i + 1 < WARMUP // 2:
            return {}
        seg = bars_all[: i + 1][-WARMUP:]
        vr = None
        if amt is not None and i >= 19:
            w = amt[i - 19 : i + 1]
            m = sum(w) / 20.0
            vr = amt[i] / m if m > 0 else None
        close = bars_all[i]["c"]
        out = {}
        for sg in (chan_analyze(seg) or {}).get("signals") or []:
            sc = None
            if sg["type"] == "3买":
                sc = score_3buy(close, sg.get("zg"), vr, sg.get("bei"), sg.get("bei2"))
            out[(code, sg["type"])] = {
                "dt": int(sg["dt"]),
                "zg": sg.get("zg"),
                "zd": sg.get("zd"),
                "vr": vr,
                "bei": sg.get("bei"),
                "bei2": sg.get("bei2"),
                "score": sc,
            }
        return out

    events = []
    seen = set(run_day(init_day))
    for day in win:
        cur = run_day(day)
        fresh = [k for k in cur if k not in seen]
        seen |= set(cur)
        events += [
            {"stock": s, "type": tp, "day": day, **cur[k]}
            for (s, tp) in fresh
            for k in ((s, tp),)
            if k in cur
        ]
    return events


def scan_market(d0, d1, workers=None, progress=None):
    """全市场扫描 → 事件缓存。返回 (events, meta)。"""
    from multiprocessing import get_context
    import pandas as pd

    stocks = market_stocks()
    if progress:
        progress("宇宙", 3, f"全市场 {len(stocks)} 只（剔 ST/北交所/指数）")
    # 日历：用 000001_SZ（2023-04 起全量；000001_SH 指数文件是 300 行残片勿用）
    idx_fp = os.path.join(DATA, "000001_SZ.parquet")
    cal_all = (
        pd.read_parquet(idx_fp, columns=["trade_date"])["trade_date"]
        .astype(str)
        .tolist()
    )
    prior = [d for d in cal_all if d < d0]
    init_day = prior[-1] if prior else d0
    win = [d for d in cal_all if d0 <= d <= d1]

    tasks = [(s, d0, d1, init_day, win) for s in stocks]
    n = workers or min(24, os.cpu_count() or 8)
    events = []
    t0 = time.time()
    ctx = get_context("spawn")
    with ctx.Pool(n) as pool:
        for i, ev in enumerate(pool.imap_unordered(scan_stock, tasks, chunksize=8)):
            events += ev
            if progress and (i % 200 == 0 or i == len(tasks) - 1):
                progress(
                    "扫描",
                    5 + int(90 * (i + 1) / len(tasks)),
                    f"全市场扫描 {i + 1}/{len(tasks)} 只（{round(time.time() - t0)}s）",
                )
    cnt = Counter(e["type"] for e in events)
    meta = {
        "d0": d0,
        "d1": d1,
        "universe": len(stocks),
        "n_events": len(events),
        "type_dist": dict(cnt),
        "workers": n,
        "seconds": round(time.time() - t0, 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    fp = os.path.join(OUT_DIR, "sig_events_market_v3_%s_%s.json" % (d0, d1))
    json.dump(
        {"meta": meta, "events": events},
        open(fp, "w", encoding="utf-8"),
        ensure_ascii=False,
    )
    # W5 影子采集（gate-shadow-dev 20261014）：cross 三档虚拟对照
    # θ∈{40,45,50}，只记不改——读本产物 meta.d1 当日事件 + 宽度序列锚。
    # 注记式 try/except，采集失败不影响扫描产线；collect_run 自身亦不抛。
    try:
        import gate_shadow_collect as _gsc

        _gsc.collect_run(strategies=("cross",))
    except Exception as _ex:
        print("GATE_SHADOW fail: %s" % str(_ex)[:100], flush=True)
    if progress:
        progress(
            "扫描",
            100,
            f"完成：{len(stocks)} 只 · {len(events)} 事件 · {meta['seconds']}s",
        )
    return events, meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K-STOCK-289 全市场缠论信号扫描")
    ap.add_argument("--d0", default="20251013")
    ap.add_argument("--d1", default="20261012")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    ev, mt = scan_market(
        a.d0,
        a.d1,
        workers=a.workers,
        progress=lambda st, pc, note: print(f"[{st} {pc}%] {note}", flush=True),
    )
    print("DONE", json.dumps(mt, ensure_ascii=False), flush=True)
