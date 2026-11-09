# -*- coding: utf-8 -*-
"""买点 20 日持有 · 组合级回测（诚实口径 · v2）。

三个日期必须分清，否则回测一定是假的
------------------------------------
`chanlun_pool.jsonl` 每行有两个日期，再加本脚本重算出来的第三个：

  dt         信号所在 K 线（买点成立的那个低点，price=该 bar 低点）
  known_dt   该信号「最早能被算出来」的日子（截断重放取首次出现，见
             signal_known_dates.py）。实测 k=0 占 0%，中位落后 25 个交易日
  detect_date 信号被扫进池子的日子（补录批次决定，与信息可得性无关）

所以：
  A  口径 dt+1 开盘      —— **未来函数**，把「事后确认的底」当成当天可知，
                            会算出 1买 97.7% 胜率这种假结果，只作上界展示
  K  口径 known+1 开盘   —— **主口径**，可执行
  C  口径 detect 收盘    —— 旧账本口径（常常比 K 还晚，白白错过）
  R  口径 随机同日入场    —— 对照组，验证买点是否还有选股能力

组合级：20 槽轮动（每日最多开 1 组，1/20 权益，最多 TOP_N 只等权，20 日到期），
逐日盯市。成本：买 0.075%、卖 0.125%（含印花税）。

产出：quant_backtest/reports/portfolio_bt_20d.json
"""
import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool.jsonl")
KNOWN = os.path.join(QBT, "reports", "signal_known.json")
INDEX_DB = os.path.join(TDX, "ts_index_daily")
OUT = os.path.join(QBT, "reports", "portfolio_bt_20d.json")

BUY = ("1买", "2买", "3买")
HOLD = 20
COST_BUY = 0.00075
COST_SELL = 0.00125
TOP_N = 10
STABLE_FROM = "2026-04-15"
START, END = "20260301", "20261106"


# ------------------------------------------------------------------ 数据
def load_signals():
    known = {}
    if os.path.exists(KNOWN):
        for r in json.load(open(KNOWN, encoding="utf-8")):
            known[(r["code"], r["dt"], r["type"])] = r
    rows, seen = [], set()
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") not in BUY:
                continue
            dt, dd, code = r.get("dt"), r.get("detect_date"), r.get("code")
            if not (dt and dd and code):
                continue
            key = (code, dt, r["type"])
            if key in seen:
                continue
            seen.add(key)
            kn = known.get(key) or {}
            rows.append({
                "code": code, "type": r["type"], "dt": dt, "detect_date": dd,
                "known_dt": kn.get("known_dt"),
                "k": kn.get("k"),
                "trend": r.get("trend"), "price": r.get("price"),
                "source": r.get("source"), "lag": r.get("lag"),
            })
    return pd.DataFrame(rows)


def _norm_dates(s):
    return s.str.replace(r"^(\d{4})(\d{2})(\d{2})$", r"\1-\2-\3", regex=True)


def load_bars(codes):
    code_set = set(codes)
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "close"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)]
        if len(d):
            d = d[d["ts_code"].isin(code_set)]
            if len(d):
                out.append(d)
    bars = pd.concat(out, ignore_index=True).drop_duplicates(
        ["ts_code", "trade_date"])
    adj = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor",
                                           "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)]
        if len(d):
            d = d[d["ts_code"].isin(code_set)]
            if len(d):
                adj.append(d)
    if adj:
        a = pd.concat(adj, ignore_index=True).drop_duplicates(
            ["ts_code", "trade_date"])
        bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
        bars["adj_factor"] = bars.groupby("ts_code")["adj_factor"].ffill().bfill()
    else:
        bars["adj_factor"] = 1.0
    bars["adj_factor"] = bars["adj_factor"].fillna(1.0)
    # 池文件是 'YYYY-MM-DD'，仓里是 'YYYYMMDD'：不归一就整表静默全 0
    bars["trade_date"] = _norm_dates(bars["trade_date"])
    return bars


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(INDEX_DB, "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "close"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)
              & (d["ts_code"] == "000001.SH")]
        if len(d):
            out.append(d)
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx["trade_date"] = _norm_dates(idx["trade_date"])
    return idx.sort_values("trade_date").reset_index(drop=True)


# ------------------------------------------------------------------ 统计
def stat(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)],
                   dtype=float)
    if len(v) == 0:
        return {"n": 0, "win": None, "avg": None, "med": None, "std": None}
    return {"n": int(len(v)),
            "win": round(float((v > 0).mean() * 100), 1),
            "avg": round(float(v.mean() * 100), 2),
            "med": round(float(np.median(v) * 100), 2),
            "std": round(float(v.std(ddof=1) * 100) if len(v) > 1 else 0.0, 2)}


def max_dd(eq):
    peak = np.maximum.accumulate(eq)
    return round(float((eq / peak - 1.0).min() * 100), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--seed", type=int, default=20261107)
    args = ap.parse_args()

    if not os.path.exists(KNOWN):
        print("缺 %s，先跑 signal_known_dates.py" % KNOWN)
        return 1
    sig = load_signals()
    bars = load_bars(sorted(sig["code"].unique()))
    idx = load_index()
    cal = sorted(bars["trade_date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    print("行情 %d 只 / %d 交易日 %s→%s；指数 %d 日"
          % (bars["ts_code"].nunique(), len(cal), cal[0], cal[-1], len(idx)))

    P = {k: bars.pivot(index="trade_date", columns="ts_code", values=v)
         for k, v in (("o", "open"), ("c", "close"), ("a", "adj_factor"))}
    adj = P["a"].ffill().bfill().fillna(1.0)
    O, C = P["o"] * adj, P["c"] * adj
    idx_c = dict(zip(idx["trade_date"], idx["close"]))
    idx_o = dict(zip(idx["trade_date"], idx["open"]))

    def px(tab, d, code):
        if d not in tab.index or code not in tab.columns:
            return None
        v = tab.at[d, code]
        return float(v) if np.isfinite(v) else None

    # 随机对照：同一天、同池随机换一只票（衡量选股能力）
    rng = np.random.default_rng(args.seed)
    pool_codes = sorted(bars["ts_code"].unique())

    def ret_of(code, entry_mode, anchor):
        """→ dict(ret, alpha, entry, exit) 或 None。"""
        if anchor not in pos:
            return None
        p = pos[anchor]
        e_i = p + 1 if entry_mode == "next_open" else p
        x_i = e_i + args.hold
        if e_i >= len(cal) or x_i >= len(cal):
            return None
        e, x = cal[e_i], cal[x_i]
        p0 = px(O if entry_mode == "next_open" else C, e, code)
        p1 = px(C, x, code)
        if not p0 or not p1 or p0 <= 0 or p1 <= 0:
            return None
        net = (p1 * (1 - COST_SELL)) / (p0 * (1 + COST_BUY)) - 1.0
        bm = None
        if e in idx_c and x in idx_c:
            bm = idx_c[x] / (idx_o[e] if entry_mode == "next_open"
                             else idx_c[e]) - 1.0
        return {"ret": net, "alpha": (net - bm) if bm is not None else None,
                "entry": e, "exit": x}

    rows = []
    for r in sig.itertuples(index=False):
        kd = r.known_dt if isinstance(r.known_dt, str) else None
        if r.dt < STABLE_FROM and (not kd or kd < STABLE_FROM):
            continue
        rec = {"code": r.code, "type": r.type, "dt": r.dt,
               "detect_date": r.detect_date, "known_dt": r.known_dt,
               "k": r.k, "trend": r.trend}
        a = ret_of(r.code, "next_open", r.dt)                  # 未来函数上界
        kk = ret_of(r.code, "next_open", r.known_dt) if r.known_dt else None
        c = ret_of(r.code, "same_close", r.detect_date)        # 旧账本
        for tag, v in (("A", a), ("K", kk), ("C", c)):
            rec[tag] = v["ret"] if v else None
            rec[tag + "_alpha"] = v["alpha"] if v else None
            rec[tag + "_entry"] = v["entry"] if v else None
            rec[tag + "_exit"] = v["exit"] if v else None
        # 随机对照（与 K 同日、不同票）
        if kk:
            rc = pool_codes[int(rng.integers(0, len(pool_codes)))]
            rr = ret_of(rc, "next_open", r.known_dt)
            rec["R"] = rr["ret"] if rr else None
            rec["R_alpha"] = rr["alpha"] if rr else None
        else:
            rec["R"] = rec["R_alpha"] = None
        rows.append(rec)
    df = pd.DataFrame(rows)
    print("信号 %d 笔；A/K/C 可用 %d/%d/%d"
          % (len(df), df["A"].notna().sum(), df["K"].notna().sum(),
             df["C"].notna().sum()))

    out = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
           "hold_days": args.hold, "top_n": args.top_n,
           "cost_buy": COST_BUY, "cost_sell": COST_SELL,
           "n_sig": int(len(df))}

    # ---------------- 一、口径对比
    print("\n" + "=" * 100)
    print("一、四种口径（同一批信号，只改入场时点，%d 日持有）" % args.hold)
    print("=" * 100)
    print("  %-30s %6s %9s %8s %9s %9s %8s"
          % ("口径", "笔数", "净收益", "胜率", "中位", "alpha", "alpha胜率"))
    modes = [("A", "dt+1 开盘（未来函数·上界）"),
             ("K", "known+1 开盘（可执行·主口径）"),
             ("C", "detect 收盘（旧账本）"),
             ("R", "随机同日换票（对照组）")]
    cmp_tbl = []
    for tag, name in modes:
        s, sa = stat(df[tag]), stat(df[tag + "_alpha"])
        cmp_tbl.append({"mode": tag, "name": name, "ret": s, "alpha": sa})
        print("  %-30s %6d %8.2f%% %7.1f%% %8.2f%% %8.2f%% %7.1f%%"
              % (name, s["n"], s["avg"] or 0, s["win"] or 0, s["med"] or 0,
                 sa["avg"] or 0, sa["win"] or 0))
    out["modes"] = cmp_tbl
    lift = (stat(df["K"])["avg"] or 0) - (stat(df["R"])["avg"] or 0)
    print("  → 主口径 vs 随机对照：%+.2f pct（这才是买点的真实选股能力）" % lift)
    out["lift_vs_random"] = round(lift, 2)

    # 背景：同窗口指数 20 日均收益 & 窗口总涨跌
    bm_mean = stat(df["K_alpha"].notna().pipe(lambda m: [
        (df.loc[i, "K"] - df.loc[i, "K_alpha"]) for i in df.index
        if pd.notna(df.loc[i, "K_alpha"]) and pd.notna(df.loc[i, "K"])]))
    iw = idx[(idx["trade_date"] >= STABLE_FROM)]
    win_chg = (iw["close"].iloc[-1] / iw["close"].iloc[0] - 1) * 100 if len(iw) > 1 else None
    print("  参考：同期上证 %+.2f%%；信号平均窗口内指数 %s%%"
          % (win_chg or 0, ("%+.2f" % bm_mean["avg"]) if bm_mean["avg"] is not None else "—"))
    out["benchmark"] = {"index_window_pct": round(win_chg, 2) if win_chg else None,
                        "index_hold_mean_pct": bm_mean["avg"]}

    # 日级聚类检验：主口径 vs 随机（同日信号高度相关，笔级 t 会虚增显著性）
    def day_mean(frame, col):
        g = defaultdict(list)
        for i, v in frame[col].items():
            d = frame.loc[i, "K_entry"]
            if isinstance(d, str) and pd.notna(v):
                g[d].append(float(v))
        return [float(np.mean(v)) for v in g.values()]

    dk, dr = day_mean(df, "K"), day_mean(df, "R")
    try:
        from scipy import stats as sps
        t, p = sps.ttest_ind(dk, dr, equal_var=False)
        print("  日级 Welch（主口径 %d 日 vs 随机 %d 日）：差 %+.2f pct，t=%.2f p=%.4f"
              % (len(dk), len(dr), (np.mean(dk) - np.mean(dr)) * 100, t, p))
        out["day_test"] = {"n_day_k": len(dk), "n_day_r": len(dr),
                           "diff_pct": round((np.mean(dk) - np.mean(dr)) * 100, 2),
                           "t": round(float(t), 2), "p": round(float(p), 4)}
    except Exception as exc:
        print("  日级检验失败：%s" % exc)

    # ---------------- 二、分型
    print("\n" + "=" * 100)
    print("二、按分型（K 口径 = 可执行）")
    print("=" * 100)
    print("  %-6s %6s %9s %8s %9s %9s %8s %8s"
          % ("分型", "笔数", "净收益", "胜率", "中位", "alpha", "alpha胜率", "中位k"))
    by_type = []
    from scipy import stats as _sps

    def _day_mean(frame, col):
        g = defaultdict(list)
        for i, v in frame[col].items():
            d = frame.loc[i, "K_entry"]
            if isinstance(d, str) and pd.notna(v):
                g[d].append(float(v))
        return [float(np.mean(v)) for v in g.values()]

    for t in BUY:
        sub = df[df["type"] == t]
        s, sa = stat(sub["K"]), stat(sub["K_alpha"])
        sr = stat(sub["R"])
        kv = sub["k"].dropna().astype(int)
        lift_t = round((s["avg"] or 0) - (sr["avg"] or 0), 2) if s["n"] and sr["n"] else None
        # 分型内部的日级聚类检验：该型信号 vs 同日随机（信号同日高度相关）
        p_t = None
        try:
            a_d, r_d = _day_mean(sub, "K"), _day_mean(sub, "R")
            if len(a_d) > 2 and len(r_d) > 2:
                _, p_t = _sps.ttest_ind(a_d, r_d, equal_var=False)
                p_t = round(float(p_t), 4)
        except Exception:
            p_t = None
        by_type.append({"type": t, "ret": s, "alpha": sa, "rand": sr,
                        "k_med": int(kv.median()) if len(kv) else None,
                        "lift": lift_t, "lift_p": p_t})
        print("  %-6s %6d %8.2f%% %7.1f%% %8.2f%% %8.2f%% %7.1f%% %7s  随机%8.2f%% 差%+7.2f p=%s"
              % (t, s["n"], s["avg"] or 0, s["win"] or 0, s["med"] or 0,
                 sa["avg"] or 0, sa["win"] or 0,
                 int(kv.median()) if len(kv) else "—",
                 sr["avg"] or 0, lift_t or 0,
                 ("%.3f" % p_t) if p_t is not None else "—"))
    out["by_type"] = by_type

    # ---------------- 三、按月
    print("\n" + "=" * 100)
    print("三、按月（按可执行入场日）")
    print("=" * 100)
    df["mon"] = df["K_entry"].astype(str).str[:7]
    monthly = []
    print("  %-8s %6s %9s %8s %9s" % ("入场月", "笔数", "净收益", "胜率", "alpha"))
    for m, g in df.groupby("mon"):
        if m == "None":
            continue
        s, sa = stat(g["K"]), stat(g["K_alpha"])
        monthly.append({"mon": m, "ret": s, "alpha": sa})
        print("  %-8s %6d %8.2f%% %7.1f%% %8.2f%%"
              % (m, s["n"], s["avg"] or 0, s["win"] or 0, sa["avg"] or 0))
    out["monthly"] = monthly

    # ---------------- 四、组合级
    print("\n" + "=" * 100)
    print("四、组合级 20 槽轮动（每组 1/%d 权益，最多 %d 只等权，%d 日到期）"
          % (args.hold, args.top_n, args.hold))
    print("=" * 100)

    def run(gate, label, only_types=None, anchor="K"):
        day_sig = defaultdict(list)
        for r in df.itertuples(index=False):
            a = getattr(r, anchor + "_entry", None)
            if r.K is None or not isinstance(a, str) or not a:
                continue
            if only_types and r.type not in only_types:
                continue
            # 入场日前一天做决策 = 可知日
            day_sig[r.known_dt].append(
                {"code": r.code, "type": r.type, "trend": r.trend})
        cash, slots, hist = 1.0, [], []
        pending, n_entries, n_skip = [], 0, 0
        cohort = []
        slot_size = 1.0 / args.hold
        for i, d in enumerate(cal):
            for plan in pending:
                names = []
                for c in plan["cands"]:
                    p0 = px(O, d, c["code"])
                    if not p0:
                        n_skip += 1
                        continue
                    names.append({"code": c["code"], "p0": p0 * (1 + COST_BUY)})
                if names:
                    amt = min(slot_size * plan["eq"], cash)
                    if amt > 1e-9:
                        cash -= amt
                        slots.append({"exit_i": i + args.hold, "amt": amt,
                                      "names": names,
                                      "w": amt / len(names)})
                        n_entries += 1
            pending = []

            mtm = 0.0
            for s in slots:
                for nm in s["names"]:
                    p1 = px(C, d, nm["code"])
                    mtm += s["w"] * ((p1 / nm["p0"]) if p1 else 1.0)
            eq = cash + mtm
            hist.append({"d": d, "eq": round(eq, 6), "slots": len(slots)})

            keep = []
            for s in slots:
                if s["exit_i"] == i:
                    v = 0.0
                    for nm in s["names"]:
                        p1 = px(C, d, nm["code"])
                        v += s["w"] * (((p1 or nm["p0"]) * (1 - COST_SELL))
                                       / nm["p0"])
                    cash += v
                    cohort.append(v / s["amt"] - 1.0)
                else:
                    keep.append(s)
            slots = keep

            if i + 1 + args.hold < len(cal):
                cands = day_sig.get(d) or []
                if cands and gate(d, cands):
                    cands = sorted(cands, key=lambda c: (
                        0 if c["type"] == "1买" else
                        (1 if c["type"] == "2买" else 2)))[:args.top_n]
                    pending.append({"cands": cands, "eq": eq})
        e = np.array([h["eq"] for h in hist])
        days = len(e)
        total = float(e[-1] - 1)
        ann = (1 + total) ** (250.0 / days) - 1
        daily = np.diff(e) / e[:-1]
        sharpe = (daily.mean() / daily.std(ddof=1) * np.sqrt(250)
                  if len(daily) > 1 and daily.std(ddof=1) > 0 else None)
        res = {"label": label, "n_days": days, "n_entries": n_entries,
               "n_cohort": len(cohort),
               "cohort_win": round(float(np.mean([x > 0 for x in cohort]) * 100), 1)
               if cohort else None,
               "cohort_avg": round(float(np.mean(cohort) * 100), 2) if cohort else None,
               "total": round(total * 100, 2), "ann": round(ann * 100, 2),
               "mdd": max_dd(e),
               "sharpe": round(float(sharpe), 2) if sharpe else None,
               "skip_nobar": n_skip, "final_eq": round(float(e[-1]), 4)}
        if not args.json:
            print("  %-28s 建仓%3d 到期%3d 组胜率%5s 组均%7s | 累计%8s 年化%8s 回撤%7s 夏普%5s"
                  % (label, n_entries, len(cohort),
                     ("%.0f%%" % res["cohort_win"]) if res["cohort_win"] else "—",
                     ("%+.2f%%" % res["cohort_avg"]) if res["cohort_avg"] else "—",
                     "%+.2f%%" % res["total"],
                     "%+.1f%%" % res["ann"], "%.2f%%" % res["mdd"],
                     ("%.2f" % res["sharpe"]) if res["sharpe"] else "—"))
        return res, hist

    g_all = lambda d, c: True                                    # noqa: E731
    g_up = lambda d, c: sum(1 for x in c if x["trend"] == "up") / len(c) >= 0.5  # noqa: E731
    runs, eqk = [], None
    for label, gate, only in [
        ("无门槛 · 全买点", g_all, None),
        ("无门槛 · 只1买2买", g_all, ("1买", "2买")),
        ("无门槛 · 只1买", g_all, ("1买",)),
        ("顺风门槛 · 全买点", g_up, None),
        ("顺风门槛 · 只1买2买", g_up, ("1买", "2买")),
        ("顺风门槛 · 只1买", g_up, ("1买",)),
    ]:
        r, h = run(gate, label, only)
        runs.append(r)
        if label == "顺风门槛 · 只1买2买":
            eqk = h
    out["portfolio"] = runs
    out["equity_curve"] = eqk

    # ---------------- 五、最近 20 个交易日
    print("\n" + "=" * 100)
    print("五、最近 20 个交易日（信号已可知、尚未到期，按 %s 收盘盯市）" % cal[-1])
    print("=" * 100)
    rec_open = []
    last = cal[-1]
    for r in df.itertuples(index=False):
        kd = r.known_dt if isinstance(r.known_dt, str) else None
        if not kd or kd not in pos:
            continue
        e_i = pos[kd] + 1
        if e_i >= len(cal):
            continue
        if e_i + args.hold <= len(cal) - 1:
            continue          # 已到期，不进"在持"表
        e = cal[e_i]
        p0 = px(O, e, r.code)
        p1 = px(C, last, r.code)
        if not p0 or not p1:
            continue
        rec_open.append({"code": r.code, "type": r.type, "entry": e,
                         "held": len(cal) - 1 - e_i,
                         "mtm": round((p1 / p0 - 1) * 100, 2)})
    if rec_open:
        m = stat([x["mtm"] / 100 for x in rec_open])
        print("  %d 笔在持，均值 %+.2f%%，上涨 %d 笔（%.0f%%）"
              % (len(rec_open), m["avg"], int((np.array([x["mtm"] for x in rec_open]) > 0).sum()),
                 m["win"]))
        for x in sorted(rec_open, key=lambda z: -z["mtm"])[:8]:
            print("    %-10s %s 入 %s  浮盈 %+.2f%%"
                  % (x["code"], x["type"], x["entry"], x["mtm"]))
    out["recent_open"] = {"n": len(rec_open), "items": rec_open[:40]}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 页面用快照：落 portal/ 随代码入库（quant_backtest/reports 被 gitignore，
    # 落那里部署后页面会静默不渲染——上一单已经栽过一次）
    page = {
        "generated": out["generated"],
        "hold_days": out["hold_days"], "top_n": out["top_n"],
        "n_sig": out["n_sig"], "window": [STABLE_FROM, cal[-1]],
        "modes": out["modes"], "by_type": out["by_type"],
        "benchmark": out["benchmark"], "day_test": out.get("day_test"),
        "lift_vs_random": out["lift_vs_random"],
        "portfolio": out["portfolio"],
        "monthly": out["monthly"],
        "recent": {"n": len(rec_open),
                   "avg": stat([x["mtm"] / 100 for x in rec_open])["avg"]
                   if rec_open else None,
                   "win": stat([x["mtm"] / 100 for x in rec_open])["win"]
                   if rec_open else None},
    }
    p = os.path.join(ROOT, "portal", "portfolio_bt_evidence.json")
    json.dump(page, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("写出 %s（页面快照）" % p)
    print("写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
