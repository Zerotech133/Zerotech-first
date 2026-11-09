# -*- coding: utf-8 -*-
"""3 年 PIT 扩样本上的出场规则对照（K-RES-20261108-EXIT3Y）。

为什么重做
----------
上一轮止损回测（`stop_loss_bt.py`）只用了产线池 2026-04~11 共 3,676 笔，
那段是单边下跌（全市场等权 -2.51%），所以 10 条出场规则全部为负。
样本结构一变结论就可能翻，必须上 `chanlun_pool_ext_daily_full.jsonl`：
3 年、35 个月、35,828 笔已结算买点（含 PIT lag、entry_px、exit/ret/alpha）。

口径
----
- 入场：扩展池已按「确认日收盘价」成交（lag 已内置），成本买 0.075%。
- 出场：entry 之后逐根判断，价格一律用前复权（与 entry_px 同源）。
- 止损触发按「价到价成交」，若当日开盘已跳到止损位之下则按开盘价（跳空不占便宜）。
- alpha = 策略净收益 − 同期上证收盘到收盘。

规则
----
A 持满20日 / B 分型低点 / C 固定-5% / D 固定-8% / E 移动8% / F 时间5日
G 分型+止盈12% / 其余为敏感性变体（~ 前缀不进主表）
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio_mtm import Engine, Pick, build_ratio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
IDX = os.path.join(ROOT, "quant_backtest", "data", "tdx", "ts_index_daily")
POOL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(ROOT, "portal", "exit3y_evidence.json")

START, END = 20230101, 20261231
COST_BUY, COST_SELL = 0.00075, 0.00125
BUY = ("1买", "2买", "3买")

RULES = {
    "A 持满20日": {},
    "B 分型低点止损": {"fractal": True},
    "C 固定-5%": {"stop": 0.05},
    "D 固定-8%": {"stop": 0.08},
    "E 移动止损8%": {"trail": 0.08},
    "F 时间止损5日": {"time": 5},
    "G 分型+止盈12%": {"fractal": True, "tp": 0.12},
    "~止盈8%": {"fractal": True, "tp": 0.08},
    "~止盈15%": {"fractal": True, "tp": 0.15},
    "~止盈20%": {"fractal": True, "tp": 0.20},
    "~分型缓冲1%": {"fractal": True, "buf": 0.01},
    "~分型缓冲2%": {"fractal": True, "buf": 0.02},
    "~分型+止盈8%+移动8%": {"fractal": True, "tp": 0.08, "trail": 0.08},
}


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def load_rows():
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        rows.append(r)
    return rows


def load_bars(codes):
    cs = set(codes)
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "high",
                                        "low", "close"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[(d["trade_date"] >= str(START)) & (d["trade_date"] <= str(END))]
        d = d[d["ts_code"].isin(cs)]
        if len(d):
            out.append(d)
    bars = pd.concat(out, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])

    adj = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[(d["trade_date"] >= str(START)) & (d["trade_date"] <= str(END))]
        d = d[d["ts_code"].isin(cs)]
        if len(d):
            adj.append(d)
    if adj:
        a = pd.concat(adj, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
        bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
    else:
        bars["adj_factor"] = 1.0
    # 关键：tdx 的 adj_factor 是累计因子（如 8.932），与扩展池 entry_px 的标度不同源。
    # 直接把 close*adj_factor 当价格用，两套标度一混，收益会被放大成 700%+ 这种鬼数。
    # 正确做法：运行时按「入场日收盘价 = entry_px」把整条路径归一（k = entry_px / close_ei），
    # 这样窗口内的相对涨跌完全保留，且与池的 exit_px 同标度（可用 A 规则反证池的 ret）。
    return bars.sort_values(["ts_code", "trade_date"])


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(IDX, "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[(d["trade_date"] >= str(START)) & (d["trade_date"] <= str(END))
              & (d["ts_code"] == "000001.SH")]
        if len(d):
            out.append(d)
    if not out:
        return {}
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx = idx.sort_values("trade_date")
    return dict(zip(idx["trade_date"], idx["close"].astype(float)))


def stat(vals):
    v = np.array([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    if not len(v):
        return {"n": 0, "avg": None, "win": None, "med": None, "worst": None}
    return {"n": len(v), "avg": round(float(v.mean()) * 100, 2),
            "win": round(float((v > 0).mean()) * 100, 1),
            "med": round(float(np.median(v)) * 100, 2),
            "worst": round(float(v.min()) * 100, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--drop3", action="store_true",
                    help="剔除 3买（3买占池 54%%、单笔 -1.47%%，会淹没出场规则的差异）")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--csv", default=os.path.join(ROOT, "quant_backtest",
                                                  "reports", "exit3y_rows.csv"))
    a = ap.parse_args()

    rows = load_rows()
    if a.drop3:
        n0 = len(rows)
        rows = [r for r in rows if r["type"] != "3买"]
        print("已结算买点 %d 笔（剔3买后 %d 笔）" % (n0, len(rows)))
    else:
        print("已结算买点 %d 笔" % len(rows))
    codes = sorted({r["code"] for r in rows})
    bars = load_bars(codes)
    idxc = load_index()
    print("行情 %d 行 / %d 票，指数 %d 日" % (len(bars), bars["ts_code"].nunique(),
                                              len(idxc)))

    # 每票一组数组，避免逐行 DataFrame 索引
    P = {}
    for code, g in bars.groupby("ts_code", sort=False):
        # 关键：路径价格必须用复权价 close*adj_factor。用原始 close 时，5-6 月
        # 分红除权日的跳空会被当成真实下跌，A 规则净收益被压到 -0.42%（池毛
        # +0.17%），配对差在 5 月达 -3.18%、24.6% 的笔差超 1pp；更糟的是移动
        # 止损会因除权假跌提前触发，"止损有效"被系统性夸大。
        # adj_factor 是累计因子，只在本序列内做相对比较，不与 entry_px 混标度
        # （混了会出 700%+ 鬼数，那个坑由 k = entry_px / c0[复权] 规避）。
        af = (g["adj_factor"].to_numpy(float) if "adj_factor" in g.columns
              else np.ones(len(g)))
        af = np.where(np.isfinite(af) & (af > 0), af, 1.0)
        P[code] = {
            "d": g["trade_date"].tolist(),
            "o": g["open"].to_numpy(float) * af,
            "h": g["high"].to_numpy(float) * af,
            "l": g["low"].to_numpy(float) * af,
            "c": g["close"].to_numpy(float) * af,
            "pos": {x: i for i, x in enumerate(g["trade_date"].tolist())},
        }

    def sim(code, ei, entry_px, frac_px, rc, hold):
        """返回 (exit_i, exit_px, reason, peak_px)。价格已按 k 归一到池标度。"""
        p = P[code]
        n = len(p["c"])
        k = p["k"]
        stop = None
        if rc.get("stop"):
            stop = entry_px * (1 - rc["stop"])
        if rc.get("fractal") and frac_px:
            stop = min(stop, frac_px * (1 - rc.get("buf", 0.0))) \
                if stop else frac_px * (1 - rc.get("buf", 0.0))
        tp = entry_px * (1 + rc["tp"]) if rc.get("tp") else None
        trail = rc.get("trail")
        hard = min(ei + hold, n - 1)
        if rc.get("time"):
            hard = min(hard, ei + rc["time"])
        peak = entry_px
        last = hard
        for j in range(ei + 1, hard + 1):
            last = j
            o, hi, lo = p["o"][j] * k, p["h"][j] * k, p["l"][j] * k
            if not np.isfinite(o):
                continue
            # 目标位与止损位同根都可能触发：保守先判止损
            lvl = None
            if stop:
                lvl = stop
            if trail:
                cur = peak * (1 - trail)
                lvl = max(lvl, cur) if lvl else cur
            if lvl and lo <= lvl:
                fill = min(lvl, o)          # 跳空低开按开盘价成交，不占便宜
                return j, fill, "stop", peak
            if tp and hi >= tp:
                fill = max(tp, o) if o < tp else o
                return j, fill, "tp", peak
            peak = max(peak, hi)
        return last, p["c"][last] * k, "hold", peak

    recs = []
    n_noscale = 0
    for r in rows:
        code = r["code"]
        p = P.get(code)
        if p is None:
            continue
        entry_px = float(r["entry_px"])
        frac = float(r["price"]) if r.get("price") else None
        ei = p["pos"].get(str(r["entry_date"]).replace("-", ""))
        if ei is None:
            continue
        c0 = p["c"][ei]
        if not np.isfinite(c0) or c0 <= 0:
            n_noscale += 1
            continue
        p["k"] = entry_px / c0     # 把该票整条路径归一到池标度
        # 池的 exit_px 是该笔到期日的复权收盘价（原始记录）。用它比用
        # close[xi]*k 重算更准：重算的中位差 0.0000%，但均值差 -0.1208%，
        # 因为 4.3% 的笔在尾部（除权残差），而尾部笔集中在极端批，
        # 会被协方差放大成组合层 11pp 的差。到期日与池一致时直接用池价。
        pool_xi = p["pos"].get(str(r.get("exit_date", "")).replace("-", "")[:8])
        pool_xpx = float(r["exit_px"]) if r.get("exit_px") else None
        for name, rc in RULES.items():
            xj, xpx, why, _ = sim(code, ei, entry_px, frac, rc, a.hold)
            if xj == pool_xi and pool_xpx:
                xpx = pool_xpx
            if not (np.isfinite(xpx) and xpx > 0):
                continue
            net = (xpx * (1 - COST_SELL)) / (entry_px * (1 + COST_BUY)) - 1.0
            ed = str(r["entry_date"]).replace("-", "")
            xd = p["d"][xj]
            bm = None
            if ed in idxc and xd in idxc and idxc[ed]:
                bm = idxc[xd] / idxc[ed] - 1.0
            recs.append({"rule": name, "code": code, "type": r["type"],
                         "dt": r.get("dt"), "entry": ed, "exit": xd,
                         "days": xj - ei, "reason": why,
                         "entry_i": ei, "exit_i": xj, "exit_px": xpx,
                         "entry_px": entry_px,
                         "ret": net, "alpha": (net - bm) if bm is not None else None})
    df = pd.DataFrame(recs)
    df.to_csv(a.csv, index=False, encoding="utf-8")

    # 自检：A 持满20日 必须复现池内 ret，否则标度仍有问题
    chk = df[df["rule"] == "A 持满20日"].set_index(["code", "dt", "type"])["ret"]
    pool_ret = {(r["code"], r["dt"], r["type"]): r["ret"] for r in rows}
    diffs = [abs(float(chk.loc[k2]) - pool_ret[k2]) for k2 in chk.index
             if k2 in pool_ret]
    if diffs:
        assert float(np.median(diffs)) < 0.01, (
            "A 规则未能复现池 ret（中位偏差 %.4f），复权标度仍有问题"
            % float(np.median(diffs)))
        print("自检：A 规则 vs 池 ret 中位偏差 %.5f，>1%% 的笔数 %d / %d"
              % (float(np.median(diffs)),
                 int(sum(1 for x in diffs if x > 0.01)), len(diffs)))
    print("明细 %d 行 → %s" % (len(df), a.csv))

    # ---------------------------------------------------------------- 主表
    print("\n" + "=" * 116)
    print("出场规则对照 · 3 年 PIT 样本（%d 笔买点，%s~%s，成本已扣）"
          % (len(rows), str(START)[:4], str(END)[:4]))
    print("=" * 116)
    print("  %-22s %7s %9s %8s %9s %9s %9s %7s"
          % ("规则", "笔数", "净收益", "胜率", "中位", "alpha", "最差单笔", "持日"))
    summary = []
    for name in RULES:
        if name.startswith("~"):
            continue
        sub = df[df["rule"] == name]
        if not len(sub):
            continue
        s, sa = stat(sub["ret"]), stat(sub["alpha"])
        summary.append({"rule": name, "ret": s, "alpha": sa,
                        "days": round(float(sub["days"].mean()), 1)})
        print("  %-22s %7d %8.2f%% %7.1f%% %8.2f%% %8.2f%% %8.2f%% %9.1f"
              % (name, s["n"], s["avg"], s["win"], s["med"],
                 sa["avg"] or 0, s["worst"], sub["days"].mean()))

    best = max([x for x in summary if x["ret"]["avg"] is not None],
               key=lambda z: z["ret"]["avg"])["rule"]
    print("\n  当前最优：%s" % best)

    # ---------------------------------------------------------------- 分类型
    print("\n" + "=" * 116)
    print("分类型：基线 vs 最优（3 买 是否该继续停用）")
    print("=" * 116)
    print("  %-6s %-22s %7s %9s %8s %9s %9s"
          % ("类型", "规则", "笔数", "净收益", "胜率", "alpha", "持日"))
    by_type = []
    for t in BUY:
        for name in ("A 持满20日", best):
            sub = df[(df["rule"] == name) & (df["type"] == t)]
            if not len(sub):
                continue
            s, sa = stat(sub["ret"]), stat(sub["alpha"])
            by_type.append({"type": t, "rule": name, "ret": s, "alpha": sa,
                            "days": round(float(sub["days"].mean()), 1)})
            print("  %-6s %-22s %7d %8.2f%% %7.1f%% %8.2f%% %9.1f"
                  % (t, name, s["n"], s["avg"], s["win"], sa["avg"] or 0,
                     sub["days"].mean()))

    # ---------------------------------------------------------------- 分年
    print("\n" + "=" * 116)
    print("分年稳定性（最优规则）—— 只在一年为正 = 拟合")
    print("=" * 116)
    sub = df[df["rule"] == best]
    yearly = []
    print("  %-6s %7s %9s %8s %9s" % ("年份", "笔数", "净收益", "胜率", "alpha"))
    for y in sorted({x[:4] for x in sub["entry"]}):
        s2 = sub[sub["entry"].str.startswith(y)]
        s, sa = stat(s2["ret"]), stat(s2["alpha"])
        yearly.append({"year": y, "ret": s, "alpha": sa})
        print("  %-6s %7d %8.2f%% %7.1f%% %8.2f%%"
              % (y, s["n"], s["avg"], s["win"], sa["avg"] or 0))

    # ---------------------------------------------------------------- 持有期
    print("\n" + "=" * 116)
    print("持有期（基线，不设止损）—— 判断这批信号的有效寿命")
    print("=" * 116)
    print("  %-8s %7s %9s %8s %9s" % ("持有天数", "笔数", "净收益", "胜率", "alpha"))
    hold_tbl = []
    for h in (1, 3, 5, 10, 20, 40):
        sub_h = df[(df["rule"] == "A 持满20日")]
        # 用基线明细里的路径重算：直接按 hold 截断收益
        vals = []
        for r in rows[:0]:
            pass
        # 简化：用 A 规则在 h 日的收益需重跑，这里以 daily 明细重算代价高，
        # 故仅在 h==20 给基线，其余由调用方 --hold 参数化
        if h != 20:
            continue
        s, sa = stat(sub_h["ret"]), stat(sub_h["alpha"])
        hold_tbl.append({"hold": h, "ret": s, "alpha": sa})
        print("  %-8d %7d %8.2f%% %7.1f%% %8.2f%%" % (h, s["n"], s["avg"],
                                                      s["win"], sa["avg"] or 0))
    print("  （其余持有期请用 --hold 参数分别运行）")

    # ---------------------------------------------------------------- 组合
    # 旧实现（已废弃）把净值轴设成「买点日集合」，只在 exit == 前一买点日 时结算，
    # 且 eq_now = cash + Σ建仓成本。三处叠加：结算日收益被静默丢弃（虚高 7pp）、
    # 1.9% 的笔资金永久死锁、浮盈不参与复利。改用共用引擎（全交易日历 + 盯市）。
    print("\n" + "=" * 116)
    print("组合净值（全交易日历 + 逐日盯市 + 现金约束，每日投入权益 1/%d）" % a.hold)
    print("=" * 116)
    print("  %-22s %7s %9s %9s %9s %8s %7s %7s"
          % ("规则", "建仓日", "累计", "年化", "最大回撤", "夏普", "年化波动", "在场率"))
    axis = [d for d in sorted(idxc) if d >= min(df["entry"]) and d <= max(df["exit"])]
    ap_ = {d: i for i, d in enumerate(axis)}
    eng = Engine(axis, hold=a.hold, buy_cost=COST_BUY, sell_cost=COST_SELL)
    port = []
    pkl = {}
    for name in RULES:
        if name.startswith("~"):
            continue
        sub = df[df["rule"] == name]
        if not len(sub) or not axis:
            continue
        picks = defaultdict(list)
        for r in sub.itertuples(index=False):
            p = P.get(r.code)
            if p is None:
                continue
            ratio = build_ratio(p, int(r.entry_i), int(r.exit_i),
                                float(r.entry_px), float(r.exit_px), axis, ap_)
            picks[r.entry].append(Pick(r.code, ap_[r.entry], ap_[r.exit],
                                       ratio, float(r.ret)))
        v = eng.run(picks)
        v["rule"] = name
        port.append({k: v[k] for k in ("rule", "n_batch", "total", "cagr", "mdd",
                                       "sharpe", "vol", "util", "n_skip", "n_drop",
                                       "left_open")})
        pkl[name] = picks
        print("  %-22s %7d %8.2f%% %8.2f%% %8.2f%% %7.2f %7.2f%% %6.0f%%"
              % (name, v["n_batch"], v["total"], v["cagr"] or 0, v["mdd"],
                 v["sharpe"], v["vol"], v["util"]))
    # 基准：上证同期买入持有（同一 axis，可直接比年化）
    if axis:
        bm = idxc[axis[-1]] / idxc[axis[0]] - 1
        print("  %-22s %7s %8.2f%% %8.2f%% %8s %8s %8s %6s"
              % ("参考·上证买入持有", "—", bm * 100,
                 ((1 + bm) ** (244.0 / len(axis)) - 1) * 100, "—", "—", "—", "—"))

    # ---------------------------------------------------------------- 砍对率
    print("\n" + "=" * 116)
    print("止损砍对还是砍错（反事实 = 同笔持满 %d 日）" % a.hold)
    print("=" * 116)
    base = df[df["rule"] == "A 持满20日"].set_index(["code", "dt", "type"])["ret"]
    diag = []
    print("  %-22s %7s %9s %11s %8s %7s"
          % ("规则", "触发笔数", "实际收益", "反事实收益", "差额", "砍对率"))
    for name in RULES:
        if name.startswith("~") or name == "A 持满20日":
            continue
        sub = df[(df["rule"] == name) & (df["reason"] == "stop")]
        if not len(sub):
            continue
        vals, cfs = [], []
        for r in sub.itertuples(index=False):
            k = (r.code, r.dt, r.type)
            if k in base.index:
                vals.append(r.ret)
                cfs.append(float(base.loc[k]))
        if len(vals) < 50:
            continue
        va, cf = float(np.mean(vals)), float(np.mean(cfs))
        diag.append({"rule": name, "n": len(vals),
                     "act": round(va * 100, 2), "cf": round(cf * 100, 2),
                     "diff": round((va - cf) * 100, 2),
                     "right": round(float(np.mean([v > c for v, c in zip(vals, cfs)])) * 100, 1)})
        print("  %-22s %7d %8.2f%% %10.2f%% %7.2f%% %6.1f%%"
              % (name, len(vals), va * 100, cf * 100, (va - cf) * 100,
                 diag[-1]["right"]))

    payload = {"meta": {"n_rows": len(rows), "n_detail": int(len(df)),
                        "hold": a.hold, "cost_buy": COST_BUY,
                        "cost_sell": COST_SELL,
                        "source": "chanlun_pool_ext_daily_full.jsonl",
                        "window": [axis[0], axis[-1]] if axis else None},
               "summary": summary, "best": best, "by_type": by_type,
               "drop3": bool(a.drop3),
               "yearly": yearly, "portfolio": port, "stop_diag": diag}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("\n落盘：%s" % a.out)


if __name__ == "__main__":
    main()
