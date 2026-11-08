# -*- coding: utf-8 -*-
"""退出规则回测：买点的钱到底是被“持满 20 日”亏掉的，还是被信号本身亏掉的？

背景
----
`portfolio_bt_20d.py` 的可执行口径结论是 −4.41%（胜率 33.6%），当时**没有任何退出规则**，
一律持满 20 个交易日。缠论买点本身是带失效条件的结构：
  · 跌破买入时底分型最低点 → 结构失效
  · 三买跌回中枢内 → 结构失效
不测这两条就断言“信号不行”，是把退出问题算在信号头上。

本脚本做三件事
--------------
1. 多组退出规则并排（同一批信号、同一入场口径），看能否把 −4.41% 拉正。
2. **反向诊断**：每笔止损都记一个反事实（若不止损，持满 20 日会怎样），
   统计“砍对/砍错”的比例和金额差 —— 这是止损值不值的唯一硬证据。
3. 分类型（1买/2买/3买）看，避免好规则被 3买 拖死。

口径
----
· 入场：known_dt（信号可知日）的次一交易日**开盘**买入 —— 与 20 日回测一致，无未来函数
· 价格：全部用后复权（open/high/low/close 同乘 adj_factor）
· 成本：买 0.075% + 卖 0.125%
· 日内触发顺序：止损优先于止盈（同日两者都触及时按更差成交，保守）
· 跳空：开盘已越过止损位，按开盘价成交（不假设能挂到理想价）
· 分型低点：取信号 bar（dt）当日的复权 low
· 中枢下沿：pv_fv[3]（p2_zd，最近中枢下沿）乘 dt 当日 adj_factor
· 剔除北交所（此前单独统计为负）与无法定价的样本

用法
----
    python tools/research/stop_loss_bt.py
    python tools/research/stop_loss_bt.py --hold 20 --verify
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool.jsonl")
KNOWN = os.path.join(QBT, "reports", "signal_known.json")
INDEX_DB = os.path.join(TDX, "ts_index_daily")
OUT = os.path.join(QBT, "reports", "stop_loss_bt.json")
OUT_CSV = os.path.join(QBT, "reports", "stop_loss_rows.csv")

BUY = ("1买", "2买", "3买")
COST_BUY = 0.00075
COST_SELL = 0.00125
START, END = "20260101", "20261110"


# ------------------------------------------------------------------ 数据
def _norm_dates(s):
    return s.str.replace(r"^(\d{4})(\d{2})(\d{2})$", r"\1-\2-\3", regex=True)


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
            if code.endswith(".BJ"):
                continue
            key = (code, dt, r["type"])
            if key in seen:
                continue
            seen.add(key)
            kn = known.get(key) or {}
            pv = r.get("pv_fv")
            rows.append({
                "code": code, "type": r["type"], "dt": dt, "detect_date": dd,
                "known_dt": kn.get("known_dt"), "k": kn.get("k"),
                "price": r.get("price"), "trend": r.get("trend"),
                "reg": r.get("reg"), "conf": r.get("conf"),
                "pv_zd": (pv[3] if isinstance(pv, list) and len(pv) >= 4 else None),
            })
    df = pd.DataFrame(rows)
    return df


def load_bars(codes):
    code_set = set(codes)
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "high",
                                        "low", "close"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)]
        if not len(d):
            continue
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
        if not len(d):
            continue
        d = d[d["ts_code"].isin(code_set)]
        if len(d):
            adj.append(d)
    if adj:
        a = pd.concat(adj, ignore_index=True).drop_duplicates(
            ["ts_code", "trade_date"])
        bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
    else:
        bars["adj_factor"] = 1.0
    bars["adj_factor"] = bars.groupby("ts_code")["adj_factor"].ffill().bfill()
    bars["adj_factor"] = bars["adj_factor"].fillna(1.0)
    bars["trade_date"] = _norm_dates(bars["trade_date"])
    for c in ("open", "high", "low", "close"):
        bars[c] = bars[c] * bars["adj_factor"]
    return bars.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(INDEX_DB, "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "close"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)
              & (d["ts_code"] == "000001.SH")]
        if len(d):
            out.append(d)
    if not out:
        return None
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx["trade_date"] = _norm_dates(idx["trade_date"])
    return idx.sort_values("trade_date").reset_index(drop=True)


# ------------------------------------------------------------------ 统计
def stat(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)],
                   dtype=float)
    if not len(v):
        return {"n": 0, "win": None, "avg": None, "med": None,
                "worst": None, "best": None}
    return {"n": int(len(v)),
            "win": round(float((v > 0).mean() * 100), 1),
            "avg": round(float(v.mean()) * 100, 2),
            "med": round(float(np.median(v)) * 100, 2),
            "worst": round(float(v.min()) * 100, 2),
            "best": round(float(v.max()) * 100, 2)}


def boot_ci(vals, n=2000, seed=7):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)],
                   dtype=float)
    if len(v) < 20:
        return None
    rng = np.random.default_rng(seed)
    m = np.array([v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)])
    return {"lo": round(float(np.percentile(m, 2.5)) * 100, 2),
            "hi": round(float(np.percentile(m, 97.5)) * 100, 2)}


def ttest(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)],
                   dtype=float)
    if len(v) < 5 or v.std(ddof=1) == 0:
        return None
    try:
        from scipy import stats as sps
        t, p = sps.ttest_1samp(v, 0.0)
        return {"n": int(len(v)), "t": round(float(t), 2),
                "p": round(float(p), 4)}
    except Exception:
        return None


# ------------------------------------------------------------------ 出场引擎
def simulate(px, i0, hold, cfg):
    """单笔模拟。px 为 dict(o/h/l/c 数组)，i0 为入场索引。

    返回 (exit_i, net_ret, reason, counterfactual_ret)
    counterfactual = 同一笔不做任何主动退出、持满 hold 日的收益。
    """
    o, h, lo, c = px["o"], px["h"], px["l"], px["c"]
    n = len(c)
    entry = o[i0] * (1 + COST_BUY)
    if not np.isfinite(entry) or entry <= 0:
        return None

    hard = []                                   # 结构性/固定止损位
    if cfg.get("stop_fixed"):
        hard.append(entry * (1 - cfg["stop_fixed"]))
    if cfg.get("stop_price"):
        # 结构性止损位：入场前就已确定（分型低点 / 中枢下沿）
        for sp in cfg["stop_price"]:
            if sp and np.isfinite(sp) and 0 < sp < entry:
                hard.append(float(sp))
    stop = min(hard) if hard else None

    tp = entry * (1 + cfg["tp"]) if cfg.get("tp") else None
    trail_k = cfg.get("trail")
    peak = c[i0]
    last = min(i0 + hold, n - 1)

    exit_i, exit_px, reason = last, c[last], "hold"
    for j in range(i0, last + 1):
        if not np.isfinite(c[j]):
            exit_i, exit_px, reason = j - 1 if j > i0 else i0, c[j - 1] if j > i0 else entry, "nodata"
            break
        # 1) 止损：开盘跳空则按开盘价，否则按止损位
        if stop is not None and lo[j] <= stop:
            exit_i = j
            exit_px = min(o[j], stop) if np.isfinite(o[j]) else stop
            reason = "stop"
            break
        if trail_k:
            # 初始止损位 = 入场价 × (1-k)；创新高后才抬到 peak × (1-k)
            # 注意不能写成 entry*(1+trail_k)：那会让首日必然"止盈"成交，
            # 把整批样本变成当日进出、收益恒约 -0.2%，凭空抬高中位数。
            base_stop = entry * (1 - trail_k)
            cur = max(base_stop, peak * (1 - trail_k) if peak > entry else base_stop)
            if lo[j] <= cur:
                exit_i = j
                exit_px = min(o[j], cur) if np.isfinite(o[j]) else cur
                reason = "trail"
                break
        # 2) 止盈（止损优先，故放在后面）
        if tp is not None and h[j] >= tp:
            exit_i = j
            exit_px = max(o[j], tp) if np.isfinite(o[j]) else tp
            reason = "tp"
            break
        # 3) 时间止损：持有 N 日后仍不盈利就撤
        if cfg.get("time_stop"):
            tn, thr = cfg["time_stop"]
            if j - i0 + 1 >= tn and (c[j] / entry - 1) <= thr:
                exit_i = j
                exit_px = c[j]
                reason = "time"
                break
        peak = max(peak, c[j])

    if not np.isfinite(exit_px) or exit_px <= 0:
        return None
    net = (exit_px * (1 - COST_SELL)) / entry - 1.0
    cf = (c[last] * (1 - COST_SELL)) / entry - 1.0 if np.isfinite(c[last]) else None
    return exit_i, net, reason, cf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--csv", default=OUT_CSV)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    sig = load_signals()
    sig = sig[sig["known_dt"].notna()].copy()
    sig["known_dt"] = sig["known_dt"].astype(str).str.slice(0, 10)
    print("买点 %d 笔（剔除北交所、无已知日），其中 1买 %d / 2买 %d / 3买 %d"
          % (len(sig), *[int((sig["type"] == t).sum()) for t in BUY]))

    bars = load_bars(set(sig["code"]))
    cal = sorted(bars["trade_date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    print("行情 %d 行 / %d 代码 / 交易日 %d（%s → %s）"
          % (len(bars), bars["ts_code"].nunique(), len(cal), cal[0], cal[-1]))

    # 按代码切矩阵（用 dict of ndarray，比 DataFrame.at 快两个数量级）
    P = {}
    for code, g in bars.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        idx = [pos[d] for d in g["trade_date"]]
        n = len(cal)
        arr = {k: np.full(n, np.nan) for k in "ohlc"}
        for k, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
            arr[k][idx] = g[col].to_numpy(float)
        P[code] = arr

    idx = load_index()
    idx_o, idx_c = {}, {}
    if idx is not None:
        idx_o = dict(zip(idx["trade_date"], idx["open"]))
        idx_c = dict(zip(idx["trade_date"], idx["close"]))

    # ------------------------------------------------------------ 规则组
    RULES = {
        "A 基线·持满20日": {},
        "B 分型低点止损": {"use_fractal": True},
        "C 中枢下沿止损": {"use_center": True},
        "D 固定-5%": {"stop_fixed": 0.05},
        "E 固定-8%": {"stop_fixed": 0.08},
        "F 移动止损8%": {"trail": 0.08},
        "G 时间止损5日": {"time_stop": (5, 0.0)},
        "H 分型+移动8%": {"use_fractal": True, "trail": 0.08},
        "I 分型+止盈12%": {"use_fractal": True, "tp": 0.12},
        "J 分型+移动8%+止盈15%": {"use_fractal": True, "trail": 0.08, "tp": 0.15},
        # 敏感性变体：只为检验「最优」是不是拟合出来的，不进主表
        "~止盈8%": {"use_fractal": True, "tp": 0.08},
        "~止盈10%": {"use_fractal": True, "tp": 0.10},
        "~止盈15%": {"use_fractal": True, "tp": 0.15},
        "~止盈20%": {"use_fractal": True, "tp": 0.20},
        "~分型缓冲1%": {"use_fractal": True, "fractal_buf": 0.01},
        "~分型缓冲2%": {"use_fractal": True, "fractal_buf": 0.02},
        "~分型缓冲1%+止盈12%": {"use_fractal": True, "fractal_buf": 0.01,
                          "tp": 0.12},
    }

    rows = []
    skipped_bad_px = 0
    for r in sig.itertuples(index=False):
        code = r.code
        if code not in P:
            skipped_bad_px += 1
            continue
        kd = r.known_dt
        if kd not in pos:
            skipped_bad_px += 1
            continue
        i0 = pos[kd] + 1                       # 可知日次日开盘
        if i0 >= len(cal) - 1:
            continue
        arr = P[code]
        if not np.isfinite(arr["o"][i0]) or arr["o"][i0] <= 0:
            skipped_bad_px += 1
            continue

        # 结构性止损位（入场时就已知）
        frac_px = arr["l"][pos[r.dt]] if (r.dt in pos and np.isfinite(
            arr["l"][pos[r.dt]])) else None
        # pv_zd 是复权价还是原始价？用 dt 收盘做比值校正
        cen_px = None
        if r.pv_zd is not None and r.dt in pos:
            dt_close = arr["c"][pos[r.dt]]
            if dt_close and np.isfinite(dt_close) and r.price:
                cen_px = float(r.pv_zd) * (dt_close / float(r.price))
                if not (0 < cen_px < dt_close * 1.5):
                    cen_px = None

        base = {"hold": args.hold}
        for name, rc in RULES.items():
            cfg = dict(base)
            cfg.update({k: v for k, v in rc.items()
                        if k not in ("use_fractal", "use_center")})
            sps_ = []
            if rc.get("use_fractal") and frac_px:
                sps_.append(frac_px * (1 - rc.get("fractal_buf", 0.0)))
            if rc.get("use_center") and cen_px:
                sps_.append(cen_px)
            if sps_:
                cfg["stop_price"] = sps_
            elif rc.get("use_center"):
                continue                        # 无中枢数据，该规则不计入
            out = simulate(arr, i0, args.hold, cfg)
            if not out:
                continue
            ei, net, reason, cf = out
            ed, xd = cal[i0], cal[ei]
            bm = None
            if ed in idx_o and xd in idx_c and idx_o[ed]:
                bm = idx_c[xd] / idx_o[ed] - 1.0
            rows.append({
                "rule": name, "code": code, "type": r.type, "k": r.k,
                "dt": r.dt, "known_dt": kd, "entry": ed, "exit": xd,
                "ret": net, "alpha": (net - bm) if bm is not None else None,
                "reason": reason, "days": ei - i0 + 1, "cf_ret": cf,
                "has_center": cen_px is not None,
            })

    df = pd.DataFrame(rows)
    print("可用样本 %d 行（跳过无定价 %d）" % (len(df), skipped_bad_px))
    if not len(df):
        return

    # ------------------------------------------------------------ 汇总
    print("\n" + "=" * 112)
    print("退出规则对照（入场=可知日次日开盘，成本已扣，%d 日上限）" % args.hold)
    print("  净收益=相对买入价；alpha=减同期上证；日级t=按入场日聚类后对 0 检验")
    print("=" * 112)
    print("  %-24s %6s %8s %7s %8s %9s %8s %7s %7s"
          % ("规则", "笔数", "净收益", "胜率", "中位", "最差单笔", "alpha",
             "持日", "日级t"))
    summary = []
    for name in RULES:
        if name.startswith("~"):
            continue
        sub = df[df["rule"] == name]
        if not len(sub):
            continue
        s, sa = stat(sub["ret"]), stat(sub["alpha"])
        day = sub.groupby("entry")["ret"].mean()
        tt = ttest(day.values)
        summary.append({
            "rule": name, "ret": s, "alpha": sa, "ci": boot_ci(sub["ret"]),
            "avg_days": round(float(sub["days"].mean()), 1),
            "day_t": tt, "stop_rate": round(float(
                (sub["reason"].isin(["stop", "trail"])).mean() * 100), 1),
            "n_day": int(day.size),
        })
        print("  %-24s %6d %7.2f%% %6.1f%% %7.2f%% %8.2f%% %7.2f%% %7.1f %7s"
              % (name, s["n"], s["avg"], s["win"], s["med"], s["worst"],
                 sa["avg"] or 0, sub["days"].mean(),
                 ("%.2f" % tt["t"]) if tt else "—"))

    best = max([s for s in summary if s["ret"]["avg"] is not None],
               key=lambda z: z["ret"]["avg"])["rule"]

    # ------------------------------------------------------------ 组合净值
    def run_portfolio(sub, hold):
        """账户视角：按「每日投入权益的 1/hold」建仓，当日信号等权分这份预算。

        不要用「槽位=批次」写法：一天就有 20~130 笔信号，按笔数计槽位会瞬间占满，
        之后就再也不建仓（实测只建了 9 批，结论完全失真）。
        现金流顺序：昨日收盘结算 → 今日开盘建仓 → 收盘盯市。
        """
        by = {}
        for r in sub.itertuples(index=False):
            by.setdefault(r.entry, []).append(r)
        slot = 1.0 / hold
        cash, pos, hist, util = 1.0, [], [], []
        n_batch = n_skip = n_open = 0

        def mv(p, i):
            a = P.get(p["code"])
            if a is None:
                return 1.0
            v = a["c"][i]
            if not np.isfinite(v) or v <= 0 or not p["entry_px"]:
                return 1.0
            return v / p["entry_px"]

        for i, d in enumerate(cal):
            prev = cal[i - 1] if i > 0 else None
            if prev:
                keep = []
                for p2 in pos:
                    if p2["exit"] == prev:
                        cash += p2["amt"] * (1 + p2["ret"])
                        n_open -= 1
                    else:
                        keep.append(p2)
                pos = keep
            cands = [r for r in (by.get(d) or []) if P.get(r.code) is not None]
            if cands and i < len(cal) - 1:
                eq_now = cash + sum(p2["amt"] * mv(p2, i) for p2 in pos)
                amt = min(slot * eq_now, cash)
                if amt > 1e-9:
                    per = amt / len(cands)
                    placed = 0
                    for r in cands:
                        a = P.get(r.code)
                        px0 = a["o"][i] if a is not None else np.nan
                        if not np.isfinite(px0) or px0 <= 0:
                            n_skip += 1
                            continue
                        pos.append({"amt": per, "code": r.code, "exit": r.exit,
                                    "ret": r.ret, "entry_px": px0})
                        cash -= per
                        placed += 1
                    if placed:
                        n_batch += 1
                        n_open += 1
            eq = cash + sum(p2["amt"] * mv(p2, i) for p2 in pos)
            hist.append(eq)
            util.append((eq - cash) / eq if eq > 0 else 0.0)
        e = np.array(hist)
        if len(e) < 5:
            return None
        days = len(e)
        rets = np.diff(e) / e[:-1]
        ann = e[-1] ** (244.0 / days) - 1
        sd = rets.std(ddof=1)
        shp = (rets.mean() / sd * np.sqrt(244)) if sd else 0
        peak = np.maximum.accumulate(e)
        return {"total": round((e[-1] - 1) * 100, 2),
                "ann": round(ann * 100, 2),
                "mdd": round(float((e / peak - 1).min() * 100), 2),
                "sharpe": round(float(shp), 2),
                "n_batch": n_batch, "n_skip": n_skip, "days": days,
                "util": round(float(np.mean(util)) * 100, 1),
                "eq_last": round(float(e[-1]), 4)}

    print("\n" + "=" * 112)
    print("组合净值（每日当日信号等权成一批，最多 %d 批在场，账户视角）" % args.hold)
    print("=" * 112)
    print("  %-24s %7s %9s %9s %9s %8s %7s"
          % ("规则", "建仓日", "累计", "年化", "最大回撤", "夏普", "在场率"))
    port = []
    for name in RULES:
        if name.startswith("~"):
            continue
        sub = df[df["rule"] == name]
        if not len(sub):
            continue
        r = run_portfolio(sub, args.hold)
        if not r:
            continue
        r["rule"] = name
        port.append(r)
        print("  %-24s %7d %8.2f%% %8.2f%% %8.2f%% %7.2f %6.0f%%"
              % (name, r["n_batch"], r["total"], r["ann"], r["mdd"],
                 r["sharpe"], r["util"]))

    # 基准：同期全市场等权（每日等权持有一篮子全市场）
    if idx is not None:
        # 关键：先按 ts_code 内的时间序列算涨跌，再按日取横截面均值。
        # 直接 groupby(trade_date).pct_change() 会算成「同日不同股票之间的变化」，
        # 基准会变成 1e100 级的荒谬值。
        bars["_chg"] = bars.groupby("ts_code")["close"].pct_change()
        dr = bars.groupby("trade_date")["_chg"].mean().dropna()
        dr = dr[(dr.index >= cal[1]) & (dr.index <= cal[-1])]
        if len(dr):
            bench_xs = float(np.prod(1 + dr.values) - 1) * 100
            peak_b = np.maximum.accumulate((1 + dr).cumprod().values)
            mdd_b = float(((1 + dr).cumprod().values / peak_b - 1).min() * 100)
            shp_b = (dr.mean() / dr.std(ddof=1) * np.sqrt(244)) if dr.std(ddof=1) else 0
            print("  %-24s %7s %8.2f%% %8.2f%% %8.2f%% %7.2f %6s"
                  % ("参考·全市场等权", "—", bench_xs,
                     ((1 + bench_xs / 100) ** (244.0 / len(dr)) - 1) * 100,
                     mdd_b, shp_b, "100%"))

    # ------------------------------------------------------------ 参数敏感性
    print("\n" + "=" * 112)
    print("参数敏感性（分型止损固定，只扫止盈/缓冲）——曲线不平滑就说明最优是拟合的")
    print("=" * 112)
    print("  %-24s %7s %9s %8s %9s %9s" % ("变体", "笔数", "净收益", "胜率", "alpha", "持日"))
    sens = []
    for name in RULES:
        if not name.startswith("~"):
            continue
        sub = df[df["rule"] == name]
        if not len(sub):
            continue
        st, sa = stat(sub["ret"]), stat(sub["alpha"])
        sens.append({"name": name, "ret": st, "alpha": sa,
                     "days": round(float(sub["days"].mean()), 1)})
        print("  %-24s %7d %8.2f%% %6.1f%% %8.2f%% %9.1f"
              % (name, st["n"], st["avg"], st["win"], sa["avg"] or 0,
                 sub["days"].mean()))

    # ------------------------------------------------------------ 分类型组合
    print("\n" + "=" * 112)
    print("分类型组合净值：1买 单独跑能不能翻正（3买 已停用，2买 待观察）")
    print("=" * 112)
    print("  %-8s %-22s %7s %9s %9s %8s"
          % ("类型", "规则", "建仓日", "累计", "最大回撤", "夏普"))
    port_t = []
    for t in BUY:
        for name in ("A 基线·持满20日", best):
            sub = df[(df["rule"] == name) & (df["type"] == t)]
            if not len(sub):
                continue
            r = run_portfolio(sub, args.hold)
            if not r:
                continue
            r2 = dict(r); r2["type"] = t; r2["rule"] = name
            port_t.append(r2)
            print("  %-8s %-22s %7d %8.2f%% %8.2f%% %7.2f"
                  % (t, name, r["n_batch"], r["total"], r["mdd"], r["sharpe"]))

    # ------------------------------------------------------------ 稳健性
    print("\n" + "=" * 112)
    print("稳健性：最优规则的分月 alpha（月度一致性差 = 规则是拟合出来的）")
    print("=" * 112)
    best_sub = df[df["rule"] == best]
    mon = {}
    for r in best_sub.itertuples(index=False):
        if r.alpha is not None and np.isfinite(r.alpha):
            mon.setdefault(r.entry[:7], []).append(r.alpha)
    monthly = []
    print("  %-8s %6s %9s %8s" % ("月份", "笔数", "净alpha", "胜率"))
    for m in sorted(mon):
        v = np.mean(mon[m])
        monthly.append({"mon": m, "n": len(mon[m]),
                        "alpha": round(float(v) * 100, 2),
                        "win": round(float(np.mean([x > 0 for x in mon[m]])) * 100, 1)})
        print("  %-8s %6d %8.2f%% %7.0f%%"
              % (m, len(mon[m]), v * 100, np.mean([x > 0 for x in mon[m]]) * 100))
    pos_mon = sum(1 for x in monthly if x["alpha"] > 0)
    print("  月度为正 %d / %d" % (pos_mon, len(monthly)))

    # ------------------------------------------------------------ 分类型
    print("\n" + "=" * 112)
    print("分类型：基线 vs 最优止损组（避免好规则被 3买 拖死）")
    print("=" * 112)
    print("  当前最优：%s" % best)
    print("  %-8s %-24s %7s %9s %7s %9s %7s"
          % ("类型", "规则", "笔数", "净收益", "胜率", "alpha", "持日"))
    by_type = {}
    for t in BUY:
        for name in ("A 基线·持满20日", best):
            sub = df[(df["rule"] == name) & (df["type"] == t)]
            if not len(sub):
                continue
            s, sa = stat(sub["ret"]), stat(sub["alpha"])
            by_type["%s|%s" % (t, name)] = {
                "ret": s, "alpha": sa,
                "avg_days": round(float(sub["days"].mean()), 1)}
            print("  %-8s %-24s %7d %8.2f%% %6.1f%% %8.2f%% %7.1f"
                  % (t, name, s["n"], s["avg"], s["win"], sa["avg"] or 0,
                     sub["days"].mean()))

    # ------------------------------------------------------------ 反向诊断
    print("\n" + "=" * 112)
    print("止损到底砍对还是砍错？（每笔止损的反事实：不止损、持满 %d 日）" % args.hold)
    print("=" * 112)
    print("  %-24s %7s %9s %9s %9s %8s"
          % ("规则", "止损笔数", "实际收益", "反事实收益", "差(实际-反事实)", "砍对率"))
    diag = []
    for name in RULES:
        if name.startswith("~"):
            continue
        sub = df[(df["rule"] == name) & (df["reason"].isin(["stop", "trail"]))]
        sub = sub.dropna(subset=["ret", "cf_ret"])
        if not len(sub):
            continue
        d = sub["ret"] - sub["cf_ret"]
        right = float((d > 0).mean() * 100)
        diag.append({"rule": name, "n": int(len(sub)),
                     "actual": round(float(sub["ret"].mean()) * 100, 2),
                     "cf": round(float(sub["cf_ret"].mean()) * 100, 2),
                     "diff": round(float(d.mean()) * 100, 2),
                     "right_pct": round(right, 1)})
        print("  %-24s %7d %8.2f%% %8.2f%% %11.2f%% %7.1f%%"
              % (name, len(sub), sub["ret"].mean() * 100,
                 sub["cf_ret"].mean() * 100, d.mean() * 100, right))

    # ------------------------------------------------------------ 自检
    ok = bad = 0
    if args.verify:
        print("\n" + "=" * 112)
        print("无未来函数自检：入场日必须严格晚于 known_dt")
        print("=" * 112)
        for r in df.drop_duplicates(["code", "known_dt", "type"]).itertuples():
            if pos[r.entry] > pos[r.known_dt]:
                ok += 1
            else:
                bad += 1
        print("  通过 %d / 违规 %d" % (ok, bad))

    # ------------------------------------------------------------ 落盘
    payload = {
        "as_of": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "hold": args.hold, "n_sig": int(len(sig)), "n_rows": int(len(df)),
        "skipped": skipped_bad_px, "summary": summary, "by_type": by_type,
        "stop_diag": diag, "portfolio": port, "monthly_best": monthly,
        "sensitivity": sens, "portfolio_by_type": port_t,
        "best": best,
        "selfcheck": {"ok": ok, "bad": bad},
        "notes": [
            "入场=known_dt 次日开盘，价格全后复权，成本 买0.075%/卖0.125%",
            "止损优先于止盈（同日双触发按更差成交）；跳空按开盘价成交",
            "分型低点=信号 bar 复权 low；中枢下沿用 pv_fv[3] 按 dt 收盘/price 比价换算",
            "反事实 cf_ret=同一笔不止损持满 hold 日的收益",
        ],
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    df.to_csv(args.csv, index=False, encoding="utf-8-sig")
    print("\n落盘：%s\n      %s" % (args.out, args.csv))


if __name__ == "__main__":
    main()
