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
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"E:/老田的AI助手/quant_backtest"
QBT = BASE
DATA = os.path.join(QBT, "data", "ashare")
NAMES_FP = os.path.join(QBT, "data", "stock_names.json")
OUT_DIR = os.path.join(QBT, "data", "bt2_state")
WARMUP = 250

sys.path.insert(0, QBT)


def market_stocks():
    """全市场可交易股票（剔 ST·北交所·指数）：数据文件 ∩ code2name 非 ST。"""
    names = json.load(open(NAMES_FP, encoding="utf-8"))["code2name"]
    out = []
    for fn in os.listdir(DATA):
        if not fn.endswith(".parquet"):
            continue
        code = fn[:-8].replace("_", ".")      # 000001_SZ -> 000001.SZ
        if code.endswith(".BJ"):              # 北交所（老田口径：不分析）
            continue
        nm = names.get(code)
        if nm is None:                        # 指数/北交所/名单外
            continue
        if "ST" in str(nm).upper():           # ST（按 2026-10-08 现名，历史 ST 未回溯）
            continue
        out.append(code)
    return sorted(out)


def scan_stock(args):
    """单股票 seen-ever 扫描（worker 端）。返回 [{stock,type,day}, ...]"""
    code, d0, d1, init_day, win = args
    import pandas as pd
    from chanlun_signal import analyze as chan_analyze

    fp = os.path.join(DATA, code.replace(".", "_") + ".parquet")
    if not os.path.exists(fp):
        return []
    b = pd.read_parquet(fp).sort_values("trade_date").reset_index(drop=True)
    if len(b) < WARMUP // 2:
        return []
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    bars_all = [{"dt": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
                for t, o, h, l, c in zip(b["trade_date"], b["open"], b["high"],
                                         b["low"], b["close"] * kfac)]
    date2i = {int(t): i for i, t in enumerate(b["trade_date"])}

    def run_day(day):
        i = date2i.get(int(day))
        if i is None or i + 1 < WARMUP // 2:
            return {}
        seg = bars_all[:i + 1][-WARMUP:]
        out = {}
        for sg in ((chan_analyze(seg) or {}).get("signals") or []):
            out[(code, sg["type"])] = int(sg["dt"])
        return out

    events = []
    seen = set(run_day(init_day))
    for day in win:
        cur = run_day(day)
        fresh = [k for k in cur if k not in seen]
        seen |= set(cur)
        events += [{"stock": s, "type": tp, "day": day} for (s, tp) in fresh]
    return events


def scan_market(d0, d1, workers=None, progress=None):
    """全市场扫描 → 事件缓存。返回 (events, meta)。"""
    from multiprocessing import get_context
    import pandas as pd

    stocks = market_stocks()
    if progress:
        progress("宇宙", 3, f"全市场 {len(stocks)} 只（剔 ST/北交所/指数）")
    cal = None
    # 日历：用上证综指日历（数据文件里有 000001_SH 指数残片，正好当日历用）
    idx_fp = os.path.join(DATA, "000001_SH.parquet")
    cal_all = pd.read_parquet(idx_fp, columns=["trade_date"])["trade_date"].astype(str).tolist()
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
                progress("扫描", 5 + int(90 * (i + 1) / len(tasks)),
                         f"全市场扫描 {i+1}/{len(tasks)} 只（{round(time.time()-t0)}s）")
    cnt = Counter(e["type"] for e in events)
    meta = {"d0": d0, "d1": d1, "universe": len(stocks), "n_events": len(events),
            "type_dist": dict(cnt), "workers": n, "seconds": round(time.time() - t0, 1),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(OUT_DIR, exist_ok=True)
    fp = os.path.join(OUT_DIR, "sig_events_market_%s_%s.json" % (d0, d1))
    json.dump({"meta": meta, "events": events}, open(fp, "w", encoding="utf-8"),
              ensure_ascii=False)
    if progress:
        progress("扫描", 100, f"完成：{len(stocks)} 只 · {len(events)} 事件 · {meta['seconds']}s")
    return events, meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K-STOCK-289 全市场缠论信号扫描")
    ap.add_argument("--d0", default="20251013")
    ap.add_argument("--d1", default="20261012")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    ev, mt = scan_market(a.d0, a.d1, workers=a.workers,
                         progress=lambda st, pc, note: print(f"[{st} {pc}%] {note}", flush=True))
    print("DONE", json.dumps(mt, ensure_ascii=False), flush=True)
