# -*- coding: utf-8 -*-
"""K-STOCK-245 Top-K 优选回测机制（老田 10-13 01:51 指令）
需求原话：策略每天选出的候选股（如三买 ~50 只）里只选最优的 10 只，统计这 10 只的胜率——建立这样的回测机制。

机制三层（全部严格 point-in-time，复用 241 截断双 pass）：
  ①候选层：pit 截断选股（与实时同一逻辑）→ 当天新确认信号 + 当天可见特征
  ②优选层：SCORE_V1 打分（20日动量降序 + 流动性下限 1亿/日），取 Top-K      ← 可替换（未来量子双刀接入即换打分器）
  ③评价层：只用 D 之后数据算前瞻收益（d5/d10/d20/d60 复权），对比候选池均值 → 优选增益(lift)

用法：
  python chanlun_topk_backtest.py --dates 20251201,20260105,... --sig 3买 --topk 10
  python chanlun_topk_backtest.py --start 20251201 --end 20260227 --step 10   # 等步长采样
"""
import json
import os
import sys
import argparse
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
OUT_DIR = os.path.join(BASE, "quant_backtest")

sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze   # noqa: E402

HORIZONS = (5, 10, 20, 30, 40, 50, 60, 120, 180, 360)   # 老田 10-13 钉死口径，十档
AMT_FLOOR = 1e5   # 20日均成交额下限（千元）= 1亿元/日，过滤无流动性票


def to_bars(df):
    return [{"dt": r.trade_date, "o": float(r.open), "h": float(r.high),
             "l": float(r.low), "c": float(r.close)} for r in df.itertuples()]


def sigset(bars):
    sig = (chan_analyze(bars) or {}).get("signals") or []
    return {(str(s["dt"]), s["type"]) for s in sig}


def forward_returns(df_full, D):
    after = df_full[df_full.trade_date > D].reset_index(drop=True)
    out = {}
    for h in HORIZONS:
        seg = after.iloc[:h]
        out[f"d{h}"] = round(float(seg["pct_chg"].fillna(0).sum()) / 100.0, 4) if len(seg) >= h else None
    return out


def day_candidates(D, codes, warmup=250):
    """①候选层：截断双 pass，返回当天新确认信号 + PIT 特征（mom20/mom60/amt20）。"""
    rows = []
    for code in codes:
        f = os.path.join(DATA, code.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            continue
        try:
            df_full = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
            dates = df_full.trade_date.tolist()
            if D not in dates:
                continue
            i = dates.index(D)
            if i < 60:
                continue
            prev = dates[i - 1]
            win_now = df_full.iloc[max(0, i + 1 - warmup): i + 1]
            sig_now = sigset(to_bars(win_now))
            sig_prev = sigset(to_bars(df_full.iloc[max(0, i - warmup): i]))
            new = sig_now - sig_prev
            if not new:
                continue
            chg = win_now["pct_chg"].fillna(0.0)
            fr = forward_returns(df_full, D)
            for (dt, typ) in sorted(new):
                rows.append({"stock": code, "type": typ, "signal_dt": dt,
                             "mom20": round(float(chg.tail(20).sum()) / 100.0, 4),
                             "mom60": round(float(chg.tail(60).sum()) / 100.0, 4),
                             "amt20": round(float(win_now["amount"].tail(20).mean()), 0),
                             **fr})
        except Exception:
            continue
    return rows


def score_v1(r):
    """②优选层 SCORE_V1：20日动量降序（三买=突破延续，动量确认）。可替换点。"""
    return r["mom20"]


def run_topk(dates, codes, sig_types, topk):
    days_out = []
    all_top, all_pool = [], []
    t0 = time.time()
    for d in dates:
        cands = day_candidates(d, codes)
        pool = [r for r in cands if r["type"] in sig_types and r["amt20"] >= AMT_FLOOR]
        ranked = sorted(pool, key=score_v1, reverse=True)
        top = ranked[:topk]
        for rank, r in enumerate(top, 1):
            all_top.append({**r, "day": d, "rank": rank})
        all_pool.extend(pool)
        days_out.append({"day": d, "candidates": len(pool), "topk": len(top),
                         "top_list": [{"rank": k, "stock": r["stock"], "type": r["type"],
                                       "mom20": r["mom20"], **{f"d{h}": r[f"d{h}"] for h in HORIZONS}}
                                      for k, r in enumerate(top, 1)]})
        print(f"  {d}: 候选 {len(pool)} → Top{len(top)}（累计 {time.time()-t0:.0f}s）", flush=True)

    def agg(rows, tag):
        out = {"n": len(rows)}
        dfw = pd.DataFrame(rows)
        for h in HORIZONS:
            seg = dfw[f"d{h}"].dropna()
            if len(seg):
                out[f"d{h}"] = {"mean": round(float(seg.mean()), 4),
                                "median": round(float(seg.median()), 4),
                                "win_rate": round(float((seg > 0).mean()), 4)}
        return {tag: out}

    result = {"meta": {"dates": dates, "sig_types": sig_types, "topk": topk,
                       "score": "SCORE_V1=mom20 降序（可替换点）",
                       "amt_floor": "20日均成交额≥1亿元",
                       "pit": "241 截断双pass，特征与信号均只用 ≤D 数据"},
              "days": days_out,
              "topk_stats": agg(all_top, "topk"),
              "pool_stats": agg(all_pool, "pool")}
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--universe", default="all", choices=["all", "pool"])
    ap.add_argument("--sig", default="3买", help="逗号分隔，如 3买 或 3买,2买")
    ap.add_argument("--topk", type=int, default=10)
    args = ap.parse_args()

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",")]
    elif args.start:
        df0 = pd.read_parquet(os.path.join(DATA, "000001_SZ.parquet"))
        days = df0.trade_date.tolist()
        seq = [d for d in days if args.start <= d <= (args.end or days[-1])]
        dates = seq[::max(1, args.step)]
    else:
        raise SystemExit("需要 --dates 或 --start")

    from chanlun_pit_select import load_universe
    codes = load_universe(args.universe)
    sig_types = [s.strip() for s in args.sig.split(",")]

    result = run_topk(dates, codes, sig_types, args.topk)
    out = os.path.join(OUT_DIR, f"chanlun_topk_{args.sig.replace(',', '_')}_{dates[0]}_{dates[-1]}.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n===== Top-K 优选回测汇总 =====")
    print("打分:", result["meta"]["score"], "| 信号:", sig_types, "| TopK:", args.topk, "| 天数:", len(dates))
    print("TopK:", json.dumps(result["topk_stats"], ensure_ascii=False))
    print("候选池:", json.dumps(result["pool_stats"], ensure_ascii=False))
    print("JSON:", out)


if __name__ == "__main__":
    main()
