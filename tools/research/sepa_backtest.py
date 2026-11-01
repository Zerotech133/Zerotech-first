# -*- coding: utf-8 -*-
"""SEPA 完整体系回测（Minervini v2，修正枢轴/突破流程）。

流程：
  每周五扫描「设置」：TT 8 条 + VCP 收缩/缩量 + 枢轴（前 40 日最高，不含当日）→ 入 watch（12 日有效）；
  每日盯突破：收盘 > 枢轴 且 量 > 1.4×50日均量 → 次日开盘入场（T+1，无前视）；
  风险：单笔 2% 风险，仓位=风险预算/每股风险，止损=min(最后收缩低点, 入场-8%)，最多 10 仓/单仓≤25%；
  退出变体：A 硬止损；B 盈利10%后止损上移保本；C 盈利20%卖一半+余仓保本+破SMA50离场；
  市场过滤（可选）：等权市场指数 > 其 MA200。
输出：research/reports/sepa_backtest_<tag>.{json,md}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
RISK_BUDGET = 0.02
MAX_POS = 10
MAX_WEIGHT = 0.25
COST, SLIP = 0.001, 0.001
VARIANTS = ["A_stop", "B_breakeven", "C_partial"]


def load(dts: list[str]) -> dict:
    panel = {}
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code", "open", "high", "low", "close", "vol"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            f = d["adj_factor"].fillna(1.0)
        else:
            f = 1.0
        for c in ("open", "high", "low", "close"):
            d["a_" + c] = d[c] * f
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "a_open", "a_high", "a_low", "a_close", "vol"]])
        if len(parts) >= 300:
            ch = pd.concat(parts, ignore_index=True)
            parts = []
            for col in ("a_open", "a_high", "a_low", "a_close", "vol"):
                p = ch.pivot_table(index="trade_date", columns="ts_code", values=col)
                panel[col] = p if col not in panel else pd.concat([panel[col], p], axis=0)
    if parts:
        ch = pd.concat(parts, ignore_index=True)
        for col in ("a_open", "a_high", "a_low", "a_close", "vol"):
            p = ch.pivot_table(index="trade_date", columns="ts_code", values=col)
            panel[col] = p if col not in panel else pd.concat([panel[col], p], axis=0)
    for k in panel:
        panel[k].index = [str(x) for x in panel[k].index]
        panel[k] = panel[k].sort_index().astype("float32")
    return panel


def vcp_setup(c: np.ndarray, h: np.ndarray, l: np.ndarray, v: np.ndarray, require_near: bool = True) -> dict | None:
    """VCP 设置检测：返回 pivot/last_low/收缩数等；失败 None。"""
    if len(c) < 60 or np.isnan(c[-1]):
        return None
    n = len(c)
    highs, lows = [], []
    for k in range(5, n - 5):
        if c[k] == max(c[k - 5:k + 6]):
            highs.append(k)
        if c[k] == min(c[k - 5:k + 6]):
            lows.append(k)
    depths = []
    for a in range(len(highs)):
        for b in range(a + 1, len(highs)):
            seg = [x for x in lows if highs[a] < x < highs[b]]
            if seg:
                depths.append(float((c[highs[a]] - min(l[x] for x in seg)) / c[highs[a]]))
                break
    depths = depths[-4:]
    if len(depths) < 2:
        return None
    if any(depths[j + 1] > depths[j] * 1.05 for j in range(len(depths) - 1)):
        return None
    if depths[-1] > 0.15:
        return None
    vol_dry = float(np.mean(v[-10:]) / max(1e-9, np.mean(v[-50:])))
    if vol_dry >= 0.9:
        return None
    pivot = float(np.max(c[-41:-1]))   # 不含当日
    last_low = float(np.min(l[-20:]))
    if last_low <= 0 or last_low >= c[-1]:
        return None
    if require_near and not (c[-1] >= pivot * 0.95):    # 回测：接近枢轴（5%内）才入 watch
        return None
    return {"pivot": pivot, "last_low": last_low, "vol_dry": vol_dry, "contractions": depths}


def main(argv=None) -> int:
    global RISK_BUDGET, MAX_POS, MAX_WEIGHT
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=RISK_BUDGET)
    ap.add_argument("--max-pos", type=int, default=MAX_POS)
    ap.add_argument("--max-weight", type=float, default=MAX_WEIGHT)
    ap.add_argument("--expire", type=int, default=20)
    ap.add_argument("--label", default="base")
    ap.add_argument("--fund", choices=("none", "growth", "growth_accel"), default="none")
    ap.add_argument("--scan", choices=("weekly", "twice", "daily"), default="weekly")
    ap.add_argument("--pyramid", action="store_true")
    args = ap.parse_args(argv)
    RISK_BUDGET, MAX_POS, MAX_WEIGHT = args.risk, args.max_pos, args.max_weight
    fund = None
    if args.fund != "none":
        import time as _time
        t0 = _time.time()
        rows = []
        for f in sorted(glob.glob(str(DATA / "fina_indicator" / "*.parquet"))):
            try:
                d = pq.read_table(f, columns=["ts_code", "ann_date", "end_date", "netprofit_yoy",
                                              "tr_yoy", "or_yoy", "roe", "basic_eps_yoy"]).to_pandas()
            except Exception:
                continue
            d = d[d["ann_date"].notna()]
            if d.empty:
                continue
            rows.append(d)
        fd = pd.concat(rows, ignore_index=True)
        fd["ann_date"] = fd["ann_date"].astype(str)
        fd = fd.sort_values(["ts_code", "ann_date", "end_date"])
        fund = {}
        for code, g in fd.groupby("ts_code"):
            fund[code] = (g["ann_date"].values.astype(str),
                          g["netprofit_yoy"].values.astype("float64"),
                          g["tr_yoy"].values.astype("float64"),
                          g["or_yoy"].values.astype("float64"),
                          g["roe"].values.astype("float64"),
                          g["basic_eps_yoy"].values.astype("float64"))
        print("fund loaded:", len(fund), "codes", round(_time.time() - t0, 1), "s", flush=True)

    def fund_ok(code: str, date: str, mode: str) -> bool:
        arr = fund.get(code) if fund else None
        if not arr:
            return False
        ann, npy, tryy, oryy, roe, epsy = arr
        j = int(np.searchsorted(ann, date, side="right")) - 1
        if j < 0 or np.isnan(npy[j]):
            return False
        rev = tryy[j] if not np.isnan(tryy[j]) else oryy[j]
        if np.isnan(rev) or np.isnan(roe[j]):
            return False
        if not (npy[j] >= 15 and rev >= 10 and roe[j] >= 8):
            return False
        if mode == "growth_accel":
            if j < 1 or np.isnan(npy[j - 1]) or not (npy[j] > npy[j - 1]):
                return False
        return True

    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    codes = P["a_close"].columns
    start_i = dates.index(next(d for d in dates if d >= "20190101"))
    print("panel:", P["a_close"].shape, "start", dates[start_i], flush=True)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    listed = (pd.Timestamp(dates[-1]) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
    csum = P["a_close"].cumsum()
    vsum = P["vol"].cumsum()
    vol50 = (vsum - vsum.shift(50)) / 50
    ret = P["a_close"].pct_change().fillna(0)
    mkt_idx = (1 + ret.mean(axis=1)).cumprod()
    mkt_ma200 = mkt_idx.rolling(200).mean()
    wd = pd.to_datetime(pd.Series(dates)).dt.weekday.values
    if args.scan == "weekly":
        scan_idx = [i for i in range(max(250, start_i), len(dates)) if wd[i] == 4]
    elif args.scan == "twice":
        scan_idx = [i for i in range(max(250, start_i), len(dates)) if wd[i] in (0, 4)]
    else:  # daily
        scan_idx = list(range(max(250, start_i), len(dates) - 1))
    print("scan days:", len(scan_idx), flush=True)

    # 1) 周扫 watch（设置）
    watch = []   # dict(code, pivot, last_low, rs, expire_i)
    c_arr = P["a_close"].values
    h_arr = P["a_high"].values
    l_arr = P["a_low"].values
    v_arr = P["vol"].values
    for i in scan_idx:
        cser = P["a_close"].iloc[i]
        s50 = (csum.iloc[i] - csum.iloc[i - 50]) / 50
        s150 = (csum.iloc[i] - csum.iloc[i - 150]) / 150
        s200 = (csum.iloc[i] - csum.iloc[i - 200]) / 200
        s200p = (csum.iloc[i - 21] - csum.iloc[i - 221]) / 200 if i >= 221 else None
        if s200p is None:
            continue
        win = P["a_close"].iloc[i - 249:i + 1]
        hi, lo = win.max(), win.min()
        r252 = cser / P["a_close"].iloc[i - 252] - 1
        rs = r252.rank(pct=True) * 98 + 1
        uni = (~names.reindex(cser.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed.reindex(cser.index).fillna(0) >= 250) & (cser >= 2) & cser.notna()
        tt = (cser > s150) & (cser > s200) & (s150 > s200) & (s200 > s200p) & \
             (s50 > s150) & (s50 > s200) & (cser > s50) & (cser >= 1.30 * lo) & (cser >= 0.75 * hi)
        cands = rs[tt & uni].sort_values(ascending=False).head(160).index
        if args.fund != "none":
            cands = [c for c in cands if fund_ok(c, dates[i], args.fund)]
        for code in cands[:120]:
            ci = codes.get_loc(code)
            c = c_arr[max(0, i - 79): i + 1, ci]
            v = v_arr[max(0, i - 79): i + 1, ci]
            if np.isnan(c[-1]) or np.isnan(v[-1]):
                continue
            st = vcp_setup(c, h_arr[max(0, i - 79): i + 1, ci], l_arr[max(0, i - 79): i + 1, ci], v)
            if st is None:
                continue
            watch.append({"code": code, "pivot": st["pivot"], "last_low": st["last_low"],
                          "rs": float(rs.get(code, 0)), "expire_i": i + args.expire})
    print("watch setups:", len(watch), flush=True)

    # 2) 每日盯突破 → signals
    signals = []
    by_exp = {}
    for w in watch:
        by_exp.setdefault(w["expire_i"], []).append(w)
    active = []
    for i in range(start_i, len(dates) - 1):
        active = [w for w in active if w["expire_i"] >= i]
        active += by_exp.get(i, [])
        if not active:
            continue
        v50 = vol50.iloc[i]
        for w in list(active):
            ci = codes.get_loc(w["code"])
            c = c_arr[i, ci]
            v = v_arr[i, ci]
            if np.isnan(c) or np.isnan(v):
                continue
            if c > w["pivot"] and v > 1.4 * float(v50.iloc[ci] if not np.isnan(v50.iloc[ci]) else np.inf):
                signals.append({"entry_i": i + 1, "code": w["code"], "pivot": w["pivot"],
                                "last_low": w["last_low"], "rs": w["rs"],
                                "market_ok": bool(mkt_idx.iloc[i] > mkt_ma200.iloc[i])})
                active.remove(w)
    print("breakout signals:", len(signals), flush=True)

    def simulate(variant: str, market_filter: bool) -> dict:
        def spx(px_s, code, fallback):
            v = px_s.get(code, np.nan)
            try:
                v = float(v)
            except Exception:
                return float(fallback)
            return float(fallback) if (np.isnan(v) or v <= 0) else v
        pos, cash = {}, 1.0
        eq_hist, trades = [], []
        entry_map = {}
        for s in signals:
            entry_map.setdefault(s["entry_i"], []).append(s)
        for i in range(start_i, len(dates)):
            px_s = P["a_close"].iloc[i]
            op_s = P["a_open"].iloc[i]
            lo_s = P["a_low"].iloc[i]
            for code in list(pos.keys()):
                q = pos[code]
                ci = codes.get_loc(code)
                c = float(c_arr[i, ci]); o = float(op_s.iloc[ci]); l = float(lo_s.iloc[ci])
                if np.isnan(c) or np.isnan(l):
                    continue
                if l <= q["stop"]:
                    fill = (min(o, q["stop"]) if not np.isnan(o) else q["stop"]) * (1 - SLIP)
                    cash += q["shares"] * fill * (1 - COST)
                    trades.append({"ret": fill / q["entry"] - 1, "hold": i - q["entry_i"]})
                    del pos[code]
                    continue
                gain = c / q["entry"] - 1
                if variant == "B_breakeven" and gain >= 0.10:
                    q["stop"] = max(q["stop"], q["entry"] * 1.001)
                # 趋势离场（所有变体）：收盘跌破 SMA50
                if i >= 50:
                    ci2 = ci
                    sm50 = float((csum.iloc[i] - csum.iloc[i - 50]).iloc[ci2] / 50)
                    if c < sm50:
                        cash += q["shares"] * c * (1 - COST)
                        trades.append({"ret": c / q["entry"] - 1, "hold": i - q["entry_i"]})
                        del pos[code]
                        continue
                # 金字塔加仓（Minervini 渐进式：盈利后加仓，最多2次，每次1个风险单位）
                if args.pyramid and gain >= 0.10 and q.get("adds", 0) < 2:
                    risk_ps = q["entry"] - q["stop"]
                    if risk_ps > 0:
                        equity_now = cash + sum(q2["shares"] * spx(px_s, c2, q2["entry"]) for c2, q2 in pos.items())
                        add_budget = min(equity_now * RISK_BUDGET / (risk_ps / q["entry"]), cash, equity_now * MAX_WEIGHT)
                        if add_budget > equity_now * 0.01:
                            add_sh = add_budget / c
                            q["shares"] += add_sh
                            q["entry"] = (q["entry"] * (q["shares"] - add_sh) + c * add_sh) / q["shares"]
                            cash -= add_sh * c * (1 + COST)
                            q["adds"] = q.get("adds", 0) + 1
                            q["stop"] = max(q["stop"], q["entry"] * 0.92)
                if variant == "C_partial":
                    if gain >= 0.20 and not q.get("partial"):
                        half = q["shares"] * 0.5
                        cash += half * c * (1 - COST)
                        q["shares"] -= half
                        q["partial"] = True
                        q["stop"] = max(q["stop"], q["entry"] * 1.001)
                    if q.get("partial") and i >= 50:
                        sm50 = float((csum.iloc[i] - csum.iloc[i - 50]).iloc[ci] / 50)
                        if c < sm50:
                            cash += q["shares"] * c * (1 - COST)
                            trades.append({"ret": c / q["entry"] - 1, "hold": i - q["entry_i"]})
                            del pos[code]
            for s in entry_map.get(i, []):
                if market_filter and not s["market_ok"]:
                    continue
                if len(pos) >= MAX_POS:
                    break
                code = s["code"]
                if code in pos:
                    continue
                ci = codes.get_loc(code)
                o = float(op_s.iloc[ci])
                if np.isnan(o) or o <= 0:
                    continue
                entry = o * (1 + SLIP)
                # Minervini 风控：止损最大 8%（last_low 更近则用 last_low）
                stop = max(s["last_low"], entry * 0.92)
                risk = entry - stop
                if risk <= 0:
                    continue
                equity = cash + sum(q2["shares"] * spx(px_s, c2, q2["entry"]) for c2, q2 in pos.items())
                budget = min(equity * RISK_BUDGET / (risk / entry), equity * MAX_WEIGHT, cash)
                if budget <= equity * 0.01:
                    continue
                shares = budget / entry
                cash -= shares * entry * (1 + COST)
                pos[code] = {"shares": shares, "entry": entry, "stop": stop, "entry_i": i,
                              "partial": False, "adds": 0}
            mv = sum(q["shares"] * spx(px_s, c, q["entry"]) for c, q in pos.items())
            eq_hist.append(cash + mv)
        eq = pd.Series(eq_hist, index=dates[start_i:])
        rets = eq.pct_change().dropna()
        yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_i])).days / 365.25
        wins = [t["ret"] for t in trades if t["ret"] > 0]
        losses = [t["ret"] for t in trades if t["ret"] <= 0]
        return {"variant": variant, "market_filter": market_filter, "nav": round(float(eq.iloc[-1]), 3),
                "cagr_pct": round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
                "vol_pct": round(float(rets.std() * np.sqrt(252)) * 100, 2),
                "trades": len(trades), "win_rate_pct": round(100 * len(wins) / max(1, len(trades)), 1),
                "avg_win_pct": round(float(np.mean(wins)) * 100, 2) if wins else 0,
                "avg_loss_pct": round(float(np.mean(losses)) * 100, 2) if losses else 0,
                "profit_factor": round(float(sum(wins) / max(1e-9, -sum(losses))), 2) if losses else None,
                "avg_hold_days": round(float(np.mean([t["hold"] for t in trades])), 1) if trades else 0}

    results = []
    for v in VARIANTS:
        for mf in (False, True):
            r = simulate(v, mf)
            results.append(r)
            print(r["variant"], "mf=" + str(mf), r["cagr_pct"], r["mdd_pct"], r["trades"], flush=True)
    mkt = mkt_idx.iloc[start_i:]
    yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_i])).days / 365.25
    mkt_cagr = float((mkt.iloc[-1] / mkt.iloc[0]) ** (1 / yrs) - 1)
    mkt_mdd = float((mkt / mkt.cummax() - 1).min())
    mkt_vol = float(mkt.pct_change().std() * np.sqrt(252))
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{dates[start_i]} .. {dates[-1]}", "scan": "weekly setup + daily breakout",
                    "setups": len(watch), "signals": len(signals), "risk_budget": RISK_BUDGET,
                    "max_pos": MAX_POS, "fund": args.fund, "scan": args.scan, "pyramid": bool(args.pyramid),
                    "note": "SEPA 完整体系回测（研究）；T+1 开盘入场；成本+滑点"},
           "market_cagr_pct": round(mkt_cagr * 100, 2), "market_mdd_pct": round(mkt_mdd * 100, 2),
           "market_vol_pct": round(mkt_vol * 100, 2), "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"sepa_backtest_{tag}_{args.label}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# SEPA 完整体系回测（{args.label}｜fund={args.fund}｜scan={args.scan}｜pyramid={args.pyramid}）· {rep['meta']['window']}", "",
             f"- 周扫设置 {len(watch)} 个；突破信号 {len(signals)} 个；单笔风险 2%，最多 10 仓；T+1 开盘入场",
             f"- 市场等权：CAGR {rep['market_cagr_pct']}%／MDD {rep['market_mdd_pct']}%／波动 {rep['market_vol_pct']}%", "",
             "| 变体 | 市场过滤 | NAV | CAGR | MDD | 波动 | 交易数 | 胜率 | 平均盈利 | 平均亏损 | 盈亏比 | 平均持有 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['variant']} | {'是' if r['market_filter'] else '否'} | {r['nav']} | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['vol_pct']}% | {r['trades']} | {r['win_rate_pct']}% | {r['avg_win_pct']}% | {r['avg_loss_pct']}% | {r['profit_factor']} | {r['avg_hold_days']} |")
    lines += ["", "## 口径", "- TT=书版 8 条（C1-C8）；VCP=2-4 次收缩且逐次变小（后≤前×0.9）、最后≤12%、右侧缩量<0.8、接近枢轴3%内；",
              "- 突破=收盘>枢轴(前40日高，不含当日)且量>1.4×50日均量；止损=min(最后收缩低点, 入场-8%)；",
              "- B 盈利10%后止损上移保本；C 盈利20%卖一半+余仓保本+破SMA50离场；",
              "- 边界：current stock_basic 偏差；未计停牌/涨跌停；T+1 开盘成交；滑点0.1%+佣金0.1%。"]
    (OUT / f"sepa_backtest_{tag}_{args.label}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"signals": len(signals), "market_cagr": rep["market_cagr_pct"],
                      "best": sorted(results, key=lambda x: -x["cagr_pct"])[0]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
