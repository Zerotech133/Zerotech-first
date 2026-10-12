# -*- coding: utf-8 -*-
"""K-STOCK-241 严格截断式 point-in-time 缠论选股重放 v2（老田 10-13 01:45 指令）
语义（与实时选股同一逻辑，结构性杜绝未来数据）：
  给定日期 D → 每只股票【只喂 <=D 的数据】→ 跑缠论 analyze。
  「D 当天新确认的信号」= run(≤D) 的信号集 − run(≤前一交易日) 的信号集。
  ⚠️ v1 教训（本脚本修订记录）：239 的「全历史喂入后筛窗口」依赖引擎确认滞后防前视，
  实测不成立——dt==D 的信号多数要靠 D 之后的数据才确认，属前视。以本脚本截断式为准。
用法：
  python chanlun_pit_select.py --date 20251201 --universe all   # 全市场实时选股重放
  python chanlun_pit_select.py --date 20260916 --universe pool --check-239
      # 对照检验：239 全历史式账本中 dt==D 的信号，有多少在 D 收盘时真正可见
"""
import json, os, sys, glob, argparse, time
import pandas as pd

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_FILE = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT_DIR = os.path.join(BASE, "quant_backtest")

sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze   # noqa: E402

HORIZONS = (5, 10, 20, 30, 40, 50, 60, 120, 180, 360)   # 老田 10-13 钉死口径，十档

def load_universe(universe):
    if universe == "pool":
        pool = json.load(open(POOL_FILE, encoding="utf-8"))
        return sorted({x["stock"] for x in pool})
    return sorted(os.path.basename(f)[:-8].replace("_", ".")
                  for f in glob.glob(os.path.join(DATA, "*.parquet")))

def to_bars(df):
    return [{"dt": r.trade_date, "o": float(r.open), "h": float(r.high),
             "l": float(r.low), "c": float(r.close)} for r in df.itertuples()]

def sigset(bars):
    sig = (chan_analyze(bars) or {}).get("signals") or []
    return {(str(s["dt"]), s["type"]) for s in sig}

def forward_returns(df_full, D):
    """D 之后 d5/d10/d20/d60 交易日累计 pct_chg（复权口径）。"""
    after = df_full[df_full.trade_date > D].reset_index(drop=True)
    out = {}
    for h in HORIZONS:
        seg = after.iloc[:h]
        out[f"d{h}"] = round(float(seg["pct_chg"].fillna(0).sum()) / 100.0, 4) if len(seg) >= h else None
    return out

def pit_select(D, universe="all", warmup=250):
    """截断式选股。返回 (rows, meta)。
    rows: 每只标的在 D 当天「新确认」的信号（含此前已确认但 D 前未出现的），附前瞻收益。"""
    codes = load_universe(universe)
    rows, errs, skip_short = [], 0, 0
    t0 = time.time()
    for k, code in enumerate(codes, 1):
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
                skip_short += 1
                continue
            prev = dates[i - 1]
            sig_now = sigset(to_bars(df_full.iloc[max(0, i + 1 - warmup): i + 1]))
            sig_prev = sigset(to_bars(df_full.iloc[max(0, i - warmup): i]))
            new = sig_now - sig_prev
            if new:
                fr = forward_returns(df_full, D)
                for (dt, typ) in sorted(new):
                    rows.append({"stock": code, "signal_dt": dt, "type": typ,
                                 "confirmed_at": D, "was_dt_eq_D": dt == D, **fr})
        except Exception:
            errs += 1
        if k % 500 == 0:
            print(f"  …{k}/{len(codes)} 已扫，D 当天新确认 {len(rows)} 信号（{time.time()-t0:.0f}s）", flush=True)
    meta = {"date": D, "universe": universe, "scanned": len(codes), "errors": errs,
            "skip_short": skip_short, "elapsed_s": round(time.time() - t0, 1),
            "pit": "严格截断双pass：run(≤D)−run(≤D-1)=当天新确认信号；每只只喂 ≤D 数据，结构性杜绝未来"}
    return rows, meta

def check_239(D):
    """对照检验：239 全历史式账本 dt==D 的信号，在 D 收盘时的真实可见性。
    可见性判定（对池内每只）：以截断 run(≤D) 为准——
      visible_before  = 信号在 run(≤D) 中且也在 run(≤D-1) 中（早就知道，239 把它记在 dt=D 属时点错位）
      new_at_D        = run(≤D)−run(≤D-1)（D 收盘才知道，合法）
      never_by_D      = 全历史式有 dt==D 信号但 run(≤D) 中根本不存在（纯前视，靠 D 后数据确认）"""
    codes = load_universe("pool")
    out = {"new_at_D": [], "visible_before": [], "never_by_D": []}
    for code in codes:
        f = os.path.join(DATA, code.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            continue
        df_full = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
        dates = df_full.trade_date.tolist()
        if D not in dates:
            continue
        i = dates.index(D)
        prev = dates[i - 1]
        fh = {(str(s["dt"]), s["type"]) for s in ((chan_analyze(to_bars(df_full)) or {}).get("signals") or [])
              if str(s["dt"]) == D}
        if not fh:
            continue
        now = sigset(to_bars(df_full.iloc[max(0, i + 1 - 250): i + 1]))
        bef = sigset(to_bars(df_full.iloc[max(0, i - 250): i]))
        for item in sorted(fh):
            if item in bef:
                out["visible_before"].append((code,) + item)
            elif item in now:
                out["new_at_D"].append((code,) + item)
            else:
                out["never_by_D"].append((code,) + item)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20251201")
    ap.add_argument("--universe", default="all", choices=["all", "pool"])
    ap.add_argument("--warmup", type=int, default=250)
    ap.add_argument("--check-239", action="store_true")
    args = ap.parse_args()

    result = {}
    if args.check_239:
        ck = check_239(args.date)
        result["check_239_visibility"] = ck
        print("239 账本 D 日可见性:", json.dumps(
            {k: len(v) for k, v in ck.items()}, ensure_ascii=False), flush=True)

    rows, meta = pit_select(args.date, args.universe, args.warmup)
    from collections import Counter
    tdist = Counter(r["type"] for r in rows)
    dfw = pd.DataFrame(rows)
    fwd = {}
    if len(dfw):
        for h in HORIZONS:
            col = f"d{h}"
            seg = dfw[col].dropna()
            if len(seg):
                fwd[col] = {"n": int(len(seg)), "mean": round(float(seg.mean()), 4),
                            "win_rate": round(float((seg > 0).mean()), 4)}
        dfw["dir"] = dfw["type"].map(lambda t: "buy" if "买" in str(t) else "sell")
        byd = {}
        for d, seg in dfw.groupby("dir"):
            byd[d] = {"n": int(len(seg)),
                      **{f"d{h}_mean": round(float(seg[f"d{h}"].dropna().mean()), 4)
                         for h in HORIZONS if seg[f"d{h}"].dropna().size}}
        fwd["by_direction"] = byd
    result["selection"] = {"meta": meta, "type_dist": dict(tdist),
                           "selected": rows, "fwd_stats": fwd}

    out = os.path.join(OUT_DIR, f"chanlun_pit_select_{args.date}_{args.universe}.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n===== PIT 选股重放 v2（严格截断双pass）=====")
    us = "全市场" if args.universe == "all" else "缠论池"
    print(f"D={args.date} universe={us}({meta['scanned']}) 耗时 {meta['elapsed_s']}s 误差 {meta['errors']} 短史跳过 {meta['skip_short']}")
    print("D 当天新确认信号:", len(rows), "| 类型分布:", dict(tdist))
    print("前瞻收益(复权):", json.dumps(fwd, ensure_ascii=False))
    print("JSON:", out)

if __name__ == "__main__":
    main()
