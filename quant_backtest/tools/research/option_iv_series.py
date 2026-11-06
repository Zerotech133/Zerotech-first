# -*- coding: utf-8 -*-
"""期权 IV 时序与预测力探针（只读）

问题：ts_opt_daily 2,394 万行，除了"能算 IV"，到底有没有用？
实测三件事：
  1. 近月 ATM IV 的日频时序（2026 全年）
  2. ΔIV 与未来 1/5/20 日收益的相关性（IV 是领先还是滞后）
  3. 方差风险溢价 IV - RV（卖出波动率策略的地基）

输出：reports/option_iv_series_<underlying>.csv + 控制台统计
"""
import os
import sys
import math
import json
import duckdb
import pandas as pd

BASE = r"C:/项目/老田的AI助手/quant_backtest/data/tdx"
R = 0.018
Q = 0.0


def _cnd(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(S, K, T, sig, cp):
    if T <= 0 or sig <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    sT = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R - Q + 0.5 * sig * sig) * T) / sT
    d2 = d1 - sT
    if cp == "C":
        return S * math.exp(-Q * T) * _cnd(d1) - K * math.exp(-R * T) * _cnd(d2)
    return K * math.exp(-R * T) * _cnd(-d2) - S * math.exp(-Q * T) * _cnd(-d1)


def iv(px, S, K, T, cp, lo=1e-4, hi=5.0):
    if px <= max(0.0, (S - K) if cp == "C" else (K - S)) + 1e-9:
        return None
    for _ in range(120):
        m = 0.5 * (lo + hi)
        if bs(S, K, T, m, cp) < px:
            lo = m
        else:
            hi = m
    v = 0.5 * (lo + hi)
    return v if 1e-4 < v < 4.9 else None


def main():
    und = sys.argv[1] if len(sys.argv) > 1 else "510300.SH"
    d0 = sys.argv[2] if len(sys.argv) > 2 else "20260101"
    d1 = sys.argv[3] if len(sys.argv) > 3 else "20261104"
    con = duckdb.connect()

    spot = con.execute(f"""
        SELECT trade_date, close FROM read_parquet('{BASE}/ts_fund_daily/**/*.parquet')
        WHERE ts_code='{und}' AND trade_date BETWEEN '{d0}' AND '{d1}'
        ORDER BY trade_date
    """).df()
    if spot.empty:
        print("无标的行情"); return
    spot["trade_date"] = spot["trade_date"].astype(str)
    smap = dict(zip(spot["trade_date"], spot["close"]))

    opt = con.execute(f"""
        SELECT d.trade_date, d.ts_code, b.call_put, b.exercise_price K,
               b.maturity_date, d.close, d.settle, d.vol
        FROM read_parquet('{BASE}/ts_opt_daily/**/*.parquet') d
        JOIN (SELECT ts_code, opt_code, call_put, exercise_price, maturity_date
              FROM read_parquet('{BASE}/ts_opt_basic/**/*.parquet')) b
          ON d.ts_code=b.ts_code
        WHERE b.opt_code='OP{und}' AND d.trade_date BETWEEN '{d0}' AND '{d1}'
          AND d.vol>0
    """).df()
    if opt.empty:
        print("无期权数据"); return
    opt["trade_date"] = opt["trade_date"].astype(str)
    opt["maturity_date"] = opt["maturity_date"].astype(str)

    import datetime as dt
    recs = []
    for day, g in opt.groupby("trade_date"):
        S = smap.get(day)
        if not S:
            continue
        base = dt.date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        g = g.copy()
        g["days"] = [(dt.date(int(m[:4]), int(m[4:6]), int(m[6:8])) - base).days
                     for m in g["maturity_date"]]
        g = g[(g["days"] >= 7) & (g["days"] <= 45)]
        if len(g) < 10:
            continue
        atm_k = g.iloc[(g["K"] - S).abs().argsort()[:2]]["K"].mean()
        ivs = []
        for _, r in g[g["K"] == atm_k].iterrows():
            px = float(r["settle"] or r["close"] or 0)
            if px <= 0:
                continue
            v = iv(px, S, float(atm_k), r["days"] / 365.0, r["call_put"])
            if v:
                ivs.append(v)
        if not ivs:
            continue
        recs.append({"date": day, "spot": float(S), "atm_k": float(atm_k),
                     "days": int(g["days"].min()), "atm_iv": sum(ivs) / len(ivs)})

    if not recs:
        print("IV 序列为空"); return
    df = pd.DataFrame(recs).sort_values("date").reset_index(drop=True)
    df["ret1"] = df["spot"].pct_change().shift(-1)
    df["ret5"] = df["spot"].shift(-5) / df["spot"] - 1
    df["ret20"] = df["spot"].shift(-20) / df["spot"] - 1
    df["rv20"] = df["spot"].pct_change().rolling(20).std() * math.sqrt(242)
    df["d_iv"] = df["atm_iv"].diff()
    df["vrp"] = df["atm_iv"] - df["rv20"]

    print(f"标的 {und}   样本 {len(df)} 交易日   {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    print(f"ATM IV  均值 {df['atm_iv'].mean()*100:.2f}%  "
          f"分位 p05 {df['atm_iv'].quantile(.05)*100:.2f}%  "
          f"p95 {df['atm_iv'].quantile(.95)*100:.2f}%")
    print()
    print("ΔIV 与未来收益的相关（负=IV升后跌）")
    for c in ("ret1", "ret5", "ret20"):
        s = df[["d_iv", c]].dropna()
        print(f"  corr(ΔIV, {c:>5}) = {s['d_iv'].corr(s[c]):+.4f}   n={len(s)}")
    print()
    print("IV 水平分位 → 未来 5 日收益（分五组）")
    t = df.dropna(subset=["atm_iv", "ret5"]).copy()
    if len(t) > 30:
        t["q"] = pd.qcut(t["atm_iv"], 5, labels=False, duplicates="drop")
        for q, gg in t.groupby("q"):
            print(f"  Q{int(q)+1}  IV均值{gg['atm_iv'].mean()*100:5.2f}%  "
                  f"未来5日均值{gg['ret5'].mean()*100:+6.3f}%  "
                  f"胜率{(gg['ret5']>0).mean()*100:5.1f}%  n={len(gg)}")
    print()
    v = df["vrp"].dropna()
    print(f"方差风险溢价 IV-RV20  均值 {v.mean()*100:+.2f}%  "
          f"为正占比 {(v>0).mean()*100:.1f}%  n={len(v)}")

    p = rf"C:/项目/老田的AI助手/quant_backtest/reports/option_iv_series_{und.replace('.','_')}.csv"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    print("\nCSV ->", p)


if __name__ == "__main__":
    main()
