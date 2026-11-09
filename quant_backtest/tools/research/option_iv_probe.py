# -*- coding: utf-8 -*-
"""期权隐含波动率反解探针（只读）

目的：验证 ts_opt_daily 2,394 万行不是"有数据没法用"——
     用 Black-Scholes 从结算价反解 IV，输出 ATM IV / 偏度 / PCR / 期限结构。

数据源：
  tdx/ts_opt_daily   期权日线（close, settle, vol, oi）
  tdx/ts_opt_basic   合约要素（opt_code, call_put, exercise_price, maturity_date）
  tdx/ts_daily       标的 ETF 收盘价

已知简化（必须写进结论）：
  1. 利率取常数 r=1.8%（shibor 未落盘，ts_repo_daily 可替代但未接）
  2. 分红率 q=0（510300 年化约 1%，会系统性高估 Call 的 IV、低估 Put）
  3. ETF 期权为欧式实物交割，此处按欧式现金处理（对 IV 影响 < 0.5 个点）
"""
import os
import sys
import glob
import math
import json
import duckdb

BASE = r"C:/项目/老田的AI助手/quant_backtest/data/tdx"
R = 0.018
Q = 0.0


def _cnd(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, sigma, cp, r=R, q=Q):
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if cp == "C":
        return S * math.exp(-q * T) * _cnd(d1) - K * math.exp(-r * T) * _cnd(d2)
    return K * math.exp(-r * T) * _cnd(-d2) - S * math.exp(-q * T) * _cnd(-d1)


def implied_vol(price, S, K, T, cp, lo=1e-4, hi=5.0, tol=1e-8):
    intrinsic = max(0.0, (S - K) if cp == "C" else (K - S))
    if price <= intrinsic + 1e-9:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, cp) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    v = 0.5 * (lo + hi)
    return v if 1e-4 < v < 4.9 else None


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "20261104"
    underlying = sys.argv[2] if len(sys.argv) > 2 else "510300.SH"
    con = duckdb.connect()

    opt = con.execute(f"""
        SELECT d.ts_code, b.call_put, b.exercise_price, b.maturity_date,
               d.close, d.settle, d.vol, d.oi
        FROM read_parquet('{BASE}/ts_opt_daily/**/*.parquet') d
        JOIN (SELECT ts_code, opt_code, call_put, exercise_price, maturity_date
              FROM read_parquet('{BASE}/ts_opt_basic/**/*.parquet')) b
          ON d.ts_code = b.ts_code
        WHERE d.trade_date = '{target}'
          AND b.opt_code = 'OP{underlying}'
          AND d.vol > 0
    """).fetchall()

    if not opt:
        print(f"无数据: date={target} underlying={underlying}")
        return

    spot_rows = con.execute(f"""
        SELECT close FROM read_parquet('{BASE}/ts_daily/**/*.parquet')
        WHERE ts_code='{underlying}' AND trade_date='{target}' LIMIT 1
    """).fetchall()
    if not spot_rows:
        spot_rows = con.execute(f"""
            SELECT close FROM read_parquet('{BASE}/ts_fund_daily/**/*.parquet')
            WHERE ts_code='{underlying}' AND trade_date='{target}' LIMIT 1
        """).fetchall()
    if not spot_rows:
        print(f"缺标的收盘价: {underlying} {target}")
        return
    S = float(spot_rows[0][0])

    print(f"标的 {underlying}  spot={S:.4f}  日期 {target}")
    print(f"合约数 {len(opt)}   利率 r={R}  分红 q={Q}（简化，见文档头）")
    print()

    rows = []
    import datetime as _dt
    _d0 = _dt.date(int(target[:4]), int(target[4:6]), int(target[6:8]))
    for ts_code, cp, K, mat, close, settle, vol, oi in opt:
        _d1 = _dt.date(int(mat[:4]), int(mat[4:6]), int(mat[6:8]))
        days = (_d1 - _d0).days
        T = max(days, 0) / 365.0
        px = float(settle or close or 0)
        if px <= 0:
            continue
        iv = implied_vol(px, S, float(K), T, cp)
        if iv is None:
            continue
        rows.append({"ts_code": ts_code, "cp": cp, "K": float(K),
                     "T": T, "days": days, "px": px, "iv": iv,
                     "vol": float(vol or 0), "oi": float(oi or 0)})

    if not rows:
        print("全部反解失败（可能结算价低于内在价值）")
        return

    mats = sorted({round(r["T"], 4) for r in rows})[:3]
    print(f"{'到期(天)':>8} {'ATM行权价':>10} {'ATM IV':>8} {'25D偏度':>8} {'Call数':>6} {'Put数':>6}")
    out = []
    for T in mats:
        sub = [r for r in rows if round(r["T"], 4) == T]
        if len(sub) < 6:
            continue
        atm = min(sub, key=lambda r: abs(r["K"] - S))
        calls = [r for r in sub if r["cp"] == "C"]
        puts = [r for r in sub if r["cp"] == "P"]
        otm_put = min([r for r in puts if r["K"] <= S] or puts,
                      key=lambda r: abs(r["K"] - S * 0.95))
        otm_call = min([r for r in calls if r["K"] >= S] or calls,
                       key=lambda r: abs(r["K"] - S * 1.05))
        skew = otm_put["iv"] - otm_call["iv"]
        print(f"{atm['days']:>8} {atm['K']:>10.3f} {atm['iv']*100:>7.2f}% "
              f"{skew*100:>+7.2f}% {len(calls):>6} {len(puts):>6}")
        out.append({"days": atm["days"], "atm_strike": atm["K"],
                    "atm_iv": round(atm["iv"], 4), "skew_25d": round(skew, 4),
                    "n_call": len(calls), "n_put": len(puts)})

    cv = sum(r["vol"] for r in rows if r["cp"] == "C")
    pv = sum(r["vol"] for r in rows if r["cp"] == "P")
    co = sum(r["oi"] for r in rows if r["cp"] == "C")
    po = sum(r["oi"] for r in rows if r["cp"] == "P")
    print()
    print(f"PCR(成交量) = {pv/cv:.4f}   PCR(持仓量) = {po/co:.4f}")
    print(f"总成交 {cv+pv:,.0f} 张   总持仓 {co+po:,.0f} 张")

    p = r"C:/项目/老田的AI助手/quant_backtest/reports/option_iv_probe.json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"date": target, "underlying": underlying, "spot": S,
               "r": R, "q": Q, "n_contracts": len(rows),
               "term_structure": out,
               "pcr_vol": round(pv / cv, 4) if cv else None,
               "pcr_oi": round(po / co, 4) if co else None},
              open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nJSON ->", p)


if __name__ == "__main__":
    main()
