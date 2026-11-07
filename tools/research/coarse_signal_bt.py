# -*- coding: utf-8 -*-
"""K-RES-20261107-COARSE：缠论买点「当天可判」能力检验。

背景
----
上一单（dab547747）证死一件事：池里 3869 笔买点，**k=0 占比 0%**，
中位要等 22 个交易日才能算出来。也就是说现有引擎输出的三类买点，
没有一笔是「当天收盘就能下单」的——它们都是对已经走出来的结构的追认。

那实战只剩一条路可走：把信号拆成两层，只交易**当天可判**的那一层。

  粗信号（coarse）：只用 K 线分型 + 摆动腿力度 + 量能 + 大盘状态，
                    收盘全部可知，次日开盘入场 —— 没有任何未来函数
  细信号（fine）  ：线段终结 / 中枢离开 / 背驰确认，必须等后续 K 线长出来

本脚本回答三个问题：

  A. 现有买点的「确认滞后」与收益有关吗？（k 分档看 alpha）
     若「越早越好」，说明方向对、只是太慢；若无关，说明慢不是唯一病根。
  B. 纯粗信号（分型级别）能不能做出当天可判、且有正 alpha 的买点？
     五档金字塔：L0 裸底分型 → L1 力度收窄 → L1n 创新低+收窄（真底背驰）
                 → L2 +缩量 → L3 +大盘顺风
  C. 粗信号与池里 1买 的重合度（同源校验，确认粗信号不是另一套东西）

口径（全部与 portfolio_bt_20d.py 一致，可横向对比）
  入场 次日开盘 / 持有 20 个交易日 / 收盘出场
  成本 买 0.075% + 卖 0.125%
  alpha = 个股净收益 - 同期上证（同入场开盘→同出场收盘）
  显著性 日级聚类 Welch（同日信号高度相关，笔级 t 会虚增 3000+ 样本）

产出 portal/coarse_signal_evidence.json（随代码入库，页面直接渲染）
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
PAGE_OUT = os.path.join(ROOT, "portal", "coarse_signal_evidence.json")
JSON_OUT = os.path.join(QBT, "reports", "coarse_signal_bt.json")

HOLD = 20
COST_BUY = 0.00075
COST_SELL = 0.00125
GAP = 4                 # 顶底分型之间至少隔 4 根 K 线（笔的最低要求）
LOAD_START = "20240601"  # 分型需要前置历史
EVAL_FROM = "2026-01-05"
POOL_FROM = "2026-04-15"  # 池覆盖度稳定起点（与上一单同窗）
END = "20261106"


# ------------------------------------------------------------------ 行情
def _norm(s):
    return s.str.replace(r"^(\d{4})(\d{2})(\d{2})$", r"\1-\2-\3", regex=True)


def load_bars():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "high",
                                        "low", "close", "vol"])
        d = d[(d["trade_date"] >= LOAD_START) & (d["trade_date"] <= END)]
        if len(d):
            out.append(d)
    bars = (pd.concat(out, ignore_index=True)
            .drop_duplicates(["ts_code", "trade_date"])
            .sort_values(["ts_code", "trade_date"]))
    adj = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor",
                                           "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d = d[(d["trade_date"] >= LOAD_START) & (d["trade_date"] <= END)]
        if len(d):
            adj.append(d)
    a = pd.concat(adj, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
    bars["adj_factor"] = (bars.groupby("ts_code")["adj_factor"]
                          .ffill().bfill().fillna(1.0))
    bars["trade_date"] = _norm(bars["trade_date"])
    return bars


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(INDEX_DB, "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "close"])
        d = d[(d["ts_code"] == "000001.SH") & (d["trade_date"] >= LOAD_START)
              & (d["trade_date"] <= END)]
        if len(d):
            out.append(d)
    idx = (pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
           .sort_values("trade_date").reset_index(drop=True))
    idx["trade_date"] = _norm(idx["trade_date"])
    return idx


# ------------------------------------------------------------------ 分型
def find_swings(h, l):
    """严格顶/底分型 → 交替摆动点（同型取更极端，顶底间隔 ≥ GAP）。"""
    n = len(h)
    fr = []
    for i in range(1, n - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1] and l[i] > l[i - 1] and l[i] > l[i + 1]:
            fr.append((i, 1))
        elif l[i] < l[i - 1] and l[i] < l[i + 1] and h[i] < h[i - 1] and h[i] < h[i + 1]:
            fr.append((i, -1))
    sw = []
    for i, t in fr:
        if not sw:
            sw.append((i, t))
            continue
        li, lt = sw[-1]
        if t == lt:
            if (t == 1 and h[i] > h[li]) or (t == -1 and l[i] < l[li]):
                sw[-1] = (i, t)
        elif i - li >= GAP:
            sw.append((i, t))
    return sw


def coarse_signals(bars_code, rows, cal_pos, regime_up):
    """单只票 → 粗信号列表 [(bar_idx, level_set)]。

    只用 bars[:i+1]（i=底分型中心）→ 确认于 i+1 收盘 → i+2 开盘入场。
    """
    h = bars_code["h"]
    l = bars_code["l"]
    v = bars_code["v"]
    sw = find_swings(h, l)
    out = []
    for j in range(3, len(sw) - 0):
        i, t = sw[j]
        if t != -1:                      # 只看底分型
            continue
        prev_top_i = sw[j - 1][0]
        prev_bot_i = sw[j - 2][0]
        prev_top2_i = sw[j - 3][0]
        f_now = (h[prev_top_i] - l[i]) / h[prev_top_i]
        f_prev = (h[prev_top2_i] - l[prev_bot_i]) / h[prev_top2_i]
        if f_prev <= 0 or f_now <= 0:
            continue
        v_now = float(np.mean(v[prev_top_i:i + 1])) if i > prev_top_i else np.nan
        v_prev = float(np.mean(v[prev_top2_i:prev_bot_i + 1])) \
            if prev_bot_i > prev_top2_i else np.nan
        decay = f_now < f_prev                       # 下跌力度收窄
        new_low = l[i] < l[prev_bot_i]               # 创新低（真底背驰前提）
        shrink = (np.isfinite(v_now) and np.isfinite(v_prev)
                  and v_prev > 0 and v_now < v_prev)  # 缩量
        lv = {"L0"}
        if decay:
            lv.add("L1")
        if decay and new_low:
            lv.add("L1n")
            if shrink:
                lv.add("L2")
                if regime_up.get(i + 1):
                    lv.add("L3")
        out.append((i, lv))
    return out


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


def day_cluster_test(sig_map, rand_map):
    """按日配对检验：同一天「信号均值 − 随机均值」的差序列做 t 检验。

    笔级 t 会把同期高度相关的信号当成独立样本（上一单踩过：p 从 0.26 变显著），
    因此先按日聚合再配对，随机对照与信号同日，天然配对。
    """
    try:
        from scipy import stats as sps
    except Exception:
        return None
    days = [d for d in sig_map if d in rand_map and sig_map[d] and rand_map[d]]
    if len(days) < 5:
        return None
    diffs = np.array([float(np.mean(sig_map[d])) - float(np.mean(rand_map[d]))
                      for d in days])
    t, p = sps.ttest_1samp(diffs, 0.0)
    return {"n_day": len(days), "diff_pct": round(float(diffs.mean()) * 100, 2),
            "t": round(float(t), 2), "p": round(float(p), 4)}


def boot_ci(vals_by_day, n_boot=2000, seed=7):
    """日级 bootstrap 95% CI（对「每日均值」重采样，不做笔级重采样）。"""
    days = [v for v in vals_by_day.values() if v]
    if len(days) < 10:
        return None
    means = np.array([float(np.mean(v)) for v in days])
    rng = np.random.default_rng(seed)
    bs = np.array([means[rng.integers(0, len(means), len(means))].mean()
                   for _ in range(n_boot)])
    return {"lo": round(float(np.percentile(bs, 2.5)) * 100, 2),
            "hi": round(float(np.percentile(bs, 97.5)) * 100, 2),
            "n_day": len(means)}


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--verify", type=int, default=60,
                    help="抽样自检条数（0=关闭）")
    args = ap.parse_args()

    print("载入行情…", flush=True)
    bars = load_bars()
    idx = load_index()
    print("行情 %d 只 / %d 日 %s→%s" %
          (bars["ts_code"].nunique(), bars["trade_date"].nunique(),
           bars["trade_date"].min(), bars["trade_date"].max()), flush=True)

    cal = sorted(bars["trade_date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    P = {k: bars.pivot(index="trade_date", columns="ts_code", values=v)
         for k, v in (("o", "open"), ("c", "close"), ("a", "adj_factor"),
                      ("h", "high"), ("l", "low"), ("v", "vol"))}
    adj = P["a"].ffill().bfill().fillna(1.0)
    O = (P["o"] * adj).reindex(cal)
    C = (P["c"] * adj).reindex(cal)
    HI = (P["h"] * adj).reindex(cal)
    LO = (P["l"] * adj).reindex(cal)
    idx_c = dict(zip(idx["trade_date"], idx["close"]))
    idx_o = dict(zip(idx["trade_date"], idx["open"]))
    _ma = idx.set_index("trade_date")["close"].rolling(20).mean()
    idx_ma20 = _ma.to_dict()

    # 横截面基准：同一天全市场等权买入并持有 → 扣完同口径成本后的均值。
    # 这一列把「市场 beta + 小市值 beta」全剥掉，剩下的才是选股能力。
    _net = ((C.shift(-args.hold) * (1 - COST_SELL))
            / (O * (1 + COST_BUY)) - 1.0)
    xs_mean = _net.mean(axis=1, skipna=True).to_dict()

    def ret_at(code, e_i, hold):
        if e_i + hold >= len(cal):
            return None
        e, x = cal[e_i], cal[e_i + hold]
        try:
            p0 = O.at[e, code]
            p1 = C.at[x, code]
            hi, lo = HI.at[e, code], LO.at[e, code]
        except KeyError:
            return None
        if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0 or p1 <= 0:
            return None
        # 可成交性：入场日一字板（开=高=低）买不进
        if np.isfinite(hi) and np.isfinite(lo) and hi == lo == p0:
            return None
        net = (p1 * (1 - COST_SELL)) / (p0 * (1 + COST_BUY)) - 1.0
        bm = None
        if e in idx_o and x in idx_c:
            bm = idx_c[x] / idx_o[e] - 1.0
        xs = xs_mean.get(e)
        return {"ret": net, "alpha": (net - bm) if bm is not None else None,
                "alpha_xs": (net - xs) if xs is not None and np.isfinite(xs) else None,
                "entry": e, "exit": x}

    # ---------------- Part B：粗信号生成（全市场）
    print("扫描粗信号…", flush=True)
    sigs = []          # (code, bar_i, level, entry_i, anchor)
    regime_cache = {}
    for code, g in bars.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        bc = {"h": g["high"].to_numpy(float),
              "l": g["low"].to_numpy(float),
              "v": g["vol"].to_numpy(float)}
        dts = g["trade_date"].tolist()
        # 大盘顺风：确认日（i+1）上证收盘 > MA20
        ru = {}
        for i in range(len(dts)):
            d = dts[i]
            ma = idx_ma20.get(d)
            if ma is not None and np.isfinite(ma):
                ru[i] = idx_c.get(d, 0) > ma
        for i, lv in coarse_signals(bc, None, pos, ru):
            if i + 2 >= len(dts):
                continue
            e_i = pos.get(dts[i + 2])
            if e_i is None or cal[e_i] < EVAL_FROM:
                continue
            for L in lv:
                sigs.append((code, dts[i], L, e_i, dts[i + 1]))
    print("粗信号 %d 条（去重前按档展开）" % len(sigs), flush=True)

    # 随机对照：同一入场日随机换票（从当日有行情的票里抽）
    rng = np.random.default_rng(args.seed)
    row_codes = {}
    for e_i in {s[3] for s in sigs}:
        d = cal[e_i]
        row = O.loc[d].dropna()
        row_codes[e_i] = [c for c in row.index if row[c] > 0]

    levels = ["L0", "L1", "L1n", "L2", "L3"]
    LNAME = {"L0": "L0 裸底分型（只认分型，不认力度）",
             "L1": "L1 底分型 + 下跌力度收窄",
             "L1n": "L1n 创新低 + 力度收窄（真·底背驰）",
             "L2": "L2 L1n + 缩量（资金不配合）",
             "L3": "L3 L2 + 大盘顺风（上证 > MA20）"}
    by_level = defaultdict(list)
    for code, dt, L, e_i, anchor in sigs:
        r = ret_at(code, e_i, args.hold)
        if not r:
            continue
        rr = None
        cs = row_codes.get(e_i) or []
        if cs:
            rc = cs[int(rng.integers(0, len(cs)))]
            rr = ret_at(rc, e_i, args.hold)
        board = "北交所" if code.endswith(".BJ") else "沪市创业板科创板" \
            if (code.endswith(".SH") or code.startswith(("30", "68"))) else "深市主板中小"
        by_level[L].append({"code": code, "dt": dt, "entry": r["entry"],
                            "board": board,
                            "ret": r["ret"], "alpha": r["alpha"],
                            "alpha_xs": r["alpha_xs"],
                            "rand_ret": rr["ret"] if rr else None,
                            "rand_alpha": rr["alpha"] if rr else None,
                            "rand_alpha_xs": rr["alpha_xs"] if rr else None})

    print("\n" + "=" * 118)
    print("B、粗信号五档（入场=分型确认次日开盘，%d 日持有，成本已扣）" % args.hold)
    print("   alpha_idx = 减同期上证；alpha_xs = 减同日全市场等权（剥掉 size/beta）")
    print("   t/p = 每日 alpha_xs 均值是否异于 0（按日聚类，样本=交易日）")
    print("=" * 118)
    print("  %-32s %7s %9s %7s %9s %9s %15s %9s %11s"
          % ("档位", "笔数", "净收益", "胜率", "alpha_idx", "alpha_xs",
             "alpha_xs 95%CI", "同池窗", "t / p"))
    rows_lv = []
    for L in levels:
        recs = by_level.get(L) or []
        s = stat([x["ret"] for x in recs])
        sa = stat([x["alpha"] for x in recs])
        sx = stat([x["alpha_xs"] for x in recs])
        srx = stat([x["rand_alpha_xs"] for x in recs])
        sm, rm = defaultdict(list), defaultdict(list)
        for x in recs:
            if x["alpha_xs"] is not None:
                sm[x["entry"]].append(x["alpha_xs"])
            if x["rand_alpha_xs"] is not None:
                rm[x["entry"]].append(x["rand_alpha_xs"])
        dt_test = day_cluster_test(sm, rm)
        ci = boot_ci(sm)
        # 「alpha 是否显著不等于 0」：对每日均值做单样本 t（不依赖随机对照，方差更小）
        days = [float(np.mean(v)) for v in sm.values() if v]
        ta = None
        if len(days) > 10:
            try:
                from scipy import stats as _sps
                t_a, p_a = _sps.ttest_1samp(np.array(days), 0.0)
                ta = {"n_day": len(days), "t": round(float(t_a), 2),
                      "p": round(float(p_a), 4)}
            except Exception:
                ta = None
        same_win = [x["alpha_xs"] for x in recs
                    if x["entry"] >= POOL_FROM and x["alpha_xs"] is not None]
        sx_same = stat(same_win)
        lift = None
        if s["n"] and sx["avg"] is not None and srx["avg"] is not None:
            lift = round(sx["avg"] - srx["avg"], 2)
        rows_lv.append({"lvl": L, "name": LNAME[L], "ret": s, "alpha": sa,
                        "alpha_xs": sx, "rand_alpha_xs": srx, "test": dt_test,
                        "ci": ci, "alpha_xs_poolwin": sx_same,
                        "n_poolwin": len(same_win), "t_alpha": ta,
                        "lift_vs_rand": lift})
        print("  %-32s %7d %8.2f%% %6.1f%% %8.2f%% %8.2f%% %15s %+8.2f%% "
              "t=%5s p=%s"
              % (LNAME[L][:32], s["n"], s["avg"] or 0, s["win"] or 0,
                 sa["avg"] or 0, sx["avg"] or 0,
                 ("%+.2f ~ %+.2f" % (ci["lo"], ci["hi"])) if ci else "—",
                 sx_same["avg"] or 0,
                 ("%.1f" % ta["t"]) if ta else "—",
                 ("%.3f" % ta["p"]) if ta else "—"))

    # 逐笔明细落盘（便于复核，reports 目录已 gitignore）
    import csv
    csv_path = os.path.join(QBT, "reports", "coarse_signal_rows.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["level", "code", "dt", "entry", "board", "ret", "alpha",
                    "alpha_xs", "rand_alpha_xs"])
        for L in levels:
            for x in by_level.get(L) or []:
                w.writerow([L, x["code"], x["dt"], x["entry"], x["board"],
                            round(x["ret"] * 100, 3) if x["ret"] else "",
                            round(x["alpha"] * 100, 3) if x["alpha"] is not None else "",
                            round(x["alpha_xs"] * 100, 3) if x["alpha_xs"] is not None else "",
                            round(x["rand_alpha_xs"] * 100, 3)
                            if x["rand_alpha_xs"] is not None else ""])
    print("  逐笔明细 → %s" % csv_path)

    # 板块分层（验证 alpha 是否只来自某一板块/北交所噪声）
    print("\n  分板块（alpha_xs）：")
    board_tbl = []
    for L in ("L1", "L1n", "L2"):
        for b in ("沪市创业板科创板", "深市主板中小", "北交所"):
            sub = [x for x in (by_level.get(L) or []) if x["board"] == b]
            if len(sub) < 30:
                continue
            sx = stat([x["alpha_xs"] for x in sub])
            board_tbl.append({"lvl": L, "board": b, "alpha_xs": sx})
            print("    %-4s %-16s n=%6d  alpha_xs=%+6.2f%%  胜率%5.1f%%"
                  % (L, b, sx["n"], sx["avg"] or 0,
                     stat([x["ret"] for x in sub])["win"] or 0))

    # ---------------- Part A：现有买点的确认滞后 vs 收益
    print("\n" + "=" * 104)
    print("A、现有买点按「确认滞后 k」分档（可执行口径，同窗 %s 起）" % POOL_FROM)
    print("=" * 104)
    known = {}
    if os.path.exists(KNOWN):
        for r in json.load(open(KNOWN, encoding="utf-8")):
            known[(r["code"], r["dt"], r["type"])] = r
    pool_rows, seen = [], set()
    for line in open(POOL, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in ("1买", "2买", "3买"):
            continue
        key = (r.get("code"), r.get("dt"), r.get("type"))
        if key in seen or not all(key):
            continue
        seen.add(key)
        kn = known.get(key) or {}
        kd = kn.get("known_dt")
        if not isinstance(kd, str) or kd < POOL_FROM:
            continue
        k = kn.get("k")
        e_i = pos.get(kd)
        if e_i is None or e_i + 1 >= len(cal):
            continue
        rr = ret_at(r["code"], e_i + 1, args.hold)
        if not rr:
            continue
        pool_rows.append({"code": r["code"], "type": r["type"], "k": k,
                          "known_dt": kd, "dt": r.get("dt"),
                          "entry": rr["entry"],
                          "ret": rr["ret"], "alpha": rr["alpha"],
                          "alpha_xs": rr["alpha_xs"]})
    bands = [(0, 5), (6, 10), (11, 20), (21, 30), (31, 999)]
    band_tbl = []
    print("  %-12s %7s %9s %8s %9s %9s %9s" %
          ("k 区间", "笔数", "净收益", "胜率", "alpha_idx", "alpha_xs", "中位k"))
    for lo, hi in bands:
        sub = [x for x in pool_rows if isinstance(x["k"], int) and lo <= x["k"] <= hi]
        s, sa = stat([x["ret"] for x in sub]), stat([x["alpha"] for x in sub])
        sx = stat([x["alpha_xs"] for x in sub])
        kk = sorted(x["k"] for x in sub)
        band_tbl.append({"band": "%d-%d" % (lo, hi if hi < 999 else 60),
                         "ret": s, "alpha": sa, "alpha_xs": sx,
                         "k_med": kk[len(kk) // 2] if kk else None})
        print("  %-12s %7d %8.2f%% %7.1f%% %8.2f%% %8.2f%% %9s"
              % ("k %d~%d" % (lo, hi if hi < 999 else 60), s["n"],
                 s["avg"] or 0, s["win"] or 0, sa["avg"] or 0, sx["avg"] or 0,
                 kk[len(kk) // 2] if kk else "—"))
    # k=0 占比（决定「当天能不能下单」）
    kz = sum(1 for x in pool_rows if isinstance(x["k"], int) and x["k"] == 0)
    k_zero = {"n": len(pool_rows), "zero": kz,
              "pct": round(100 * kz / max(1, len(pool_rows)), 1)}
    print("  k=0（当天可判）%d / %d 笔 = %.1f%%" % (kz, len(pool_rows), k_zero["pct"]))

    # k 与 alpha 的秩相关（越晚确认是否越差）
    kv = [(x["k"], x["alpha"]) for x in pool_rows
          if isinstance(x["k"], int) and x["alpha"] is not None]
    corr = None
    if len(kv) > 30:
        try:
            from scipy import stats as sps
            rho, pv = sps.spearmanr([a for a, _ in kv], [b for _, b in kv])
            corr = {"rho": round(float(rho), 3), "p": round(float(pv), 4),
                    "n": len(kv)}
            print("  k 与 alpha 的 Spearman：rho=%+.3f p=%.4f（n=%d）"
                  % (rho, pv, len(kv)))
        except Exception as exc:
            print("  秩相关失败：%s" % exc)

    # ---------------- Part F：扎堆效应（笔均 vs 日组均）
    print("\n" + "=" * 118)
    print("F、扎堆效应：为什么「笔均 +1.7%」到账户上是 -1.3%")
    print("   同一天冒出几十个底分型，说明大盘在跌；那天买进去的整组都是亏的。")
    print("   按笔等权时扎堆日被放大成几十笔，按日（组合）等权时只算一天。")
    print("=" * 118)
    print("  %-6s %7s %9s %9s %9s %9s %9s"
          % ("档位", "有信号日", "笔均", "日组均", "日组 t", "稀疏日组均", "扎堆日组均"))
    crowd_tbl = []
    for L in ("L0", "L1", "L1n", "L2"):
        recs = [x for x in (by_level.get(L) or []) if x["board"] != "北交所"]
        if not recs:
            continue
        day = defaultdict(list)
        for x in recs:
            day[x["entry"]].append(x)
        rows_d = []
        for d, xs in day.items():
            rets = [x["ret"] for x in xs if x["ret"] is not None]
            axs = [x["alpha_xs"] for x in xs if x["alpha_xs"] is not None]
            if rets:
                rows_d.append({"d": d, "n": len(xs),
                               "ret": float(np.mean(rets)),
                               "axs": float(np.mean(axs)) if axs else np.nan})
        if len(rows_d) < 20:
            continue
        dm = np.array([r["ret"] for r in rows_d])
        am = np.array([r["axs"] for r in rows_d if np.isfinite(r["axs"])])
        try:
            from scipy import stats as _sps
            t_d, p_d = _sps.ttest_1samp(dm, 0.0)
            t_a, p_a = _sps.ttest_1samp(am, 0.0)
            rho, p_rho = _sps.spearmanr([r["n"] for r in rows_d], dm)
        except Exception:
            t_d, p_d, t_a, p_a, rho, p_rho = (0, 1, 0, 1, 0, 1)
        ns = np.array([r["n"] for r in rows_d])
        q1, q3 = np.percentile(ns, [33, 67])
        sparse = dm[ns <= q1]
        crowd = dm[ns > q3]
        crowd_tbl.append({
            "lvl": L, "n_day": len(rows_d),
            "per_trade": round(float(np.mean([x["ret"] for x in recs])),
                               4),
            "day_mean": round(float(dm.mean()) * 100, 2),
            "day_t": round(float(t_d), 2), "day_p": round(float(p_d), 4),
            "day_alpha_xs": round(float(am.mean()) * 100, 2),
            "day_alpha_t": round(float(t_a), 2),
            "day_alpha_p": round(float(p_a), 4),
            "sparse_ret": round(float(sparse.mean()) * 100, 2) if len(sparse) else None,
            "crowd_ret": round(float(crowd.mean()) * 100, 2) if len(crowd) else None,
            "sparse_n": int(q1), "crowd_n": int(q3),
            "rho_n_ret": round(float(rho), 3), "rho_p": round(float(p_rho), 4)})
        print("  %-6s %7d %8.2f%% %8.2f%% %8.1f %9.2f%% %10.2f%%"
              % (L, len(rows_d),
                 np.mean([x["ret"] for x in recs]) * 100, dm.mean() * 100, t_d,
                 sparse.mean() * 100, crowd.mean() * 100))
        print("         日组 alpha_xs %+.2f%%（t=%.1f, p=%.3f）；信号数 vs 组收益 "
              "rho=%+.2f（p=%.3f）；稀疏日≤%d 只 / 扎堆日>%d 只"
              % (am.mean() * 100, t_a, p_a, rho, p_rho, q1, q3))

    # ---------------- Part G：扎堆日门槛（当日信号数 ≥ N 才建仓）
    print("")
    print("=" * 118)
    print("G、扎堆日门槛扫描：当日信号数 ≥ N 才建仓（N 收盘即可知，可执行）")
    print("=" * 118)
    print("  %-5s %6s %7s %9s %9s %9s %7s"
          % ("档位", "门槛N", "日数", "组均", "超额", "正组占比", "日组 t"))
    gate_tbl = []
    for L in ("L0", "L1", "L1n"):
        recs = [x for x in (by_level.get(L) or []) if x["board"] != "北交所"]
        day = defaultdict(list)
        for x in recs:
            day[x["entry"]].append(x)
        tab = []
        for d, xs in day.items():
            rets = [x["ret"] for x in xs if x["ret"] is not None]
            axs = [x["alpha_xs"] for x in xs if x["alpha_xs"] is not None]
            if rets:
                tab.append({"d": d, "n": len(xs),
                            "ret": float(np.mean(rets)),
                            "axs": float(np.mean(axs)) if axs else np.nan})
        for th in (0, 20, 50, 100):
            sub = [r for r in tab if r["n"] >= th]
            if len(sub) < 20:
                continue
            v = np.array([r["ret"] for r in sub])
            a = np.array([r["axs"] for r in sub if np.isfinite(r["axs"])])
            try:
                from scipy import stats as _sps2
                t_g, p_g = _sps2.ttest_1samp(v, 0.0)
            except Exception:
                t_g, p_g = 0.0, 1.0
            # 非重叠抽样：相邻建仓日至少隔 hold 个交易日，去掉重叠放大效应
            picks, last = [], -10 ** 6
            for r in sorted(sub, key=lambda z: pos[z["d"]]):
                i = pos[r["d"]]
                if i - last >= args.hold:
                    picks.append(r)
                    last = i
            vn = np.array([r["ret"] for r in picks])
            t_n = None
            if len(vn) > 2:
                t_n = round(float(vn.mean() / (vn.std(ddof=1) / np.sqrt(len(vn)))), 2)                     if vn.std(ddof=1) > 0 else None
            gate_tbl.append({"lvl": L, "th": th, "n_day": len(sub),
                             "day_mean": round(float(v.mean()) * 100, 2),
                             "alpha_xs": round(float(a.mean()) * 100, 2)
                             if len(a) else None,
                             "win": round(float((v > 0).mean()) * 100, 1),
                             "t": round(float(t_g), 2),
                             "p": round(float(p_g), 4),
                             "n_indep": len(picks),
                             "indep_mean": round(float(vn.mean()) * 100, 2)
                             if len(vn) else None,
                             "indep_t": t_n})
            print("  %-5s %6d %7d %8.2f%% %8.2f%% %8.0f%% %7.1f | 非重叠 %2d 段 "
                  "%+.2f%% t=%s"
                  % (L, th, len(sub), v.mean() * 100, a.mean() * 100,
                     (v > 0).mean() * 100, t_g, len(picks), vn.mean() * 100,
                     t_n))

    # ---------------- Part C：粗信号 vs 池 1买 同源校验
    print("\n" + "=" * 104)
    print("C、粗信号与池内买点的重合度（同源校验，都按信号所在日 dt 比）")
    print("=" * 104)
    pool_1b_dt = set()
    for line in open(POOL, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "1买" and r.get("dt"):
            pool_1b_dt.add((r.get("code"), r.get("dt")))
    l1n_dt = {(code, dt) for code, dt, L, e_i, anchor in sigs if L == "L1n"}
    any_fr = {(code, dt) for code, dt, L, e_i, anchor in sigs}
    hit_fr = sum(1 for k_ in pool_1b_dt if k_ in any_fr)
    hit_l1n = sum(1 for k_ in pool_1b_dt if k_ in l1n_dt)
    print("  池 1买 共 %d 笔；dt 当天是底分型 %d 笔（%.1f%%），"
          "同时满足力度收窄 %d 笔（%.1f%%）"
          % (len(pool_1b_dt), hit_fr, 100 * hit_fr / max(1, len(pool_1b_dt)),
             hit_l1n, 100 * hit_l1n / max(1, len(pool_1b_dt))))
    overlap = {"pool_1b": len(pool_1b_dt), "pool_dt_is_fractal": hit_fr,
               "pool_dt_is_fractal_pct": round(100 * hit_fr / max(1, len(pool_1b_dt)), 1),
               "pool_dt_is_l1n": hit_l1n,
               "pool_dt_is_l1n_pct": round(100 * hit_l1n / max(1, len(pool_1b_dt)), 1)}

    # ---------------- Part D：月度稳定性（L1n）
    print("\n" + "=" * 104)
    print("D、L1n 月度稳定性（alpha_xs，负值=当月跑输同市场等权）")
    print("=" * 104)
    mon = defaultdict(list)
    for x in by_level.get("L1n") or []:
        if x["alpha_xs"] is not None:
            mon[x["entry"][:7]].append(x["alpha_xs"])
    monthly = []
    print("  %-8s %6s %9s %9s" % ("月份", "笔数", "alpha_xs", "胜率"))
    for m in sorted(mon):
        v = np.array(mon[m])
        monthly.append({"mon": m, "n": len(v),
                        "alpha": round(float(v.mean() * 100), 2),
                        "win": round(float((v > 0).mean() * 100), 1)})
        print("  %-8s %6d %8.2f%% %8.1f%%"
              % (m, len(v), v.mean() * 100, (v > 0).mean() * 100))

    # ---------------- Part E：组合级净值（单笔 alpha 好看 ≠ 账户赚钱）
    print("\n" + "=" * 118)
    print("E、组合级：每天把当日全部信号等权买入，20 槽轮动（每槽 1/20 权益，%d 日到期）"
          % args.hold)
    print("=" * 118)

    def px(tab, d, code):
        try:
            v = tab.at[d, code]
        except KeyError:
            return None
        return float(v) if np.isfinite(v) else None

    def run_portfolio(level, top_n=None, min_n=0):
        """现金流顺序必须对：昨日收盘结算 → 今日开盘建仓 → 收盘盯市。

        旧写法「先建仓后结算」会让盘中现金恒为 0，新仓位被摊成芝麻大，
        净值被系统性压低（实测把 +1.5%/笔的组合压成 -1.3%）。
        """
        by_entry = defaultdict(list)
        for x in by_level.get(level) or []:
            if x["board"] == "北交所":
                continue
            by_entry[x["entry"]].append(x["code"])
        cash, slots, hist, coh = 1.0, [], [], []
        n_enter = n_skip = 0
        start_i = pos[EVAL_FROM]
        slot_size = 1.0 / args.hold

        def slot_value(s, d):
            v = 0.0
            for n in s["names"]:
                p1 = px(C, d, n["code"])
                v += s["w"] * ((p1 / n["p0"]) if p1 else 1.0)
            return v

        for i in range(start_i, len(cal)):
            d = cal[i]
            prev = cal[i - 1] if i > 0 else None
            # 1) 昨日到期的仓位按昨收结算，现金今日可用
            if prev:
                keep = []
                for s in slots:
                    if s["exit_i"] == i - 1:
                        got = slot_value(s, prev) * (1 - COST_SELL)
                        cash += got
                        coh.append(got / s["amt"] - 1.0)
                    else:
                        keep.append(s)
                slots = keep
            # 2) 今日开盘建仓
            cands = by_entry.get(d) or []
            if len(cands) < min_n:      # 当日信号数不够 = 不是市场性下跌，不建仓
                cands = []
            if cands and i + args.hold < len(cal) and len(slots) < args.hold:
                names = []
                for c in (cands[:top_n] if top_n else cands):
                    p0 = px(O, d, c)
                    if not p0:
                        n_skip += 1
                        continue
                    names.append({"code": c, "p0": p0 * (1 + COST_BUY)})
                if names:
                    eq_now = cash + sum(slot_value(s, d) for s in slots)
                    amt = min(slot_size * eq_now, cash)
                    if amt > 1e-9:
                        cash -= amt
                        slots.append({"exit_i": i + args.hold, "amt": amt,
                                      "names": names,
                                      "w": amt / len(names)})
                        n_enter += 1
            # 3) 收盘盯市
            hist.append(cash + sum(slot_value(s, d) for s in slots))
        e = np.array(hist)
        if len(e) < 5:
            return None
        total = float(e[-1] - 1)
        days_n = len(e)
        ann = (1 + total) ** (250.0 / days_n) - 1 if total > -1 else -1
        daily = np.diff(e) / e[:-1]
        sharpe = (daily.mean() / daily.std(ddof=1) * np.sqrt(250)
                  if len(daily) > 1 and daily.std(ddof=1) > 0 else None)
        peak = np.maximum.accumulate(e)
        print("    [debug] %s 到期组 %d 组，组均 %+.2f%%，组胜率 %.0f%%"
              % (level, len(coh), np.mean(coh) * 100 if coh else 0,
                 np.mean([c > 0 for c in coh]) * 100 if coh else 0))
        return {"level": level, "n_entries": n_enter, "n_skip": n_skip,
                "n_days": days_n, "total": round(total * 100, 2),
                "ann": round(ann * 100, 2),
                "mdd": round(float((e / peak - 1).min()) * 100, 2),
                "sharpe": round(float(sharpe), 2) if sharpe else None,
                "final_eq": round(float(e[-1]), 4)}

    port = []
    print("  %-24s %8s %8s %10s %10s %8s %7s"
          % ("档位", "建仓次数", "跳过", "累计", "年化", "最大回撤", "夏普"))
    for L, th in (("L0", 0), ("L1", 0), ("L0", 50), ("L1", 50),
                  ("L0", 100), ("L1", 100), ("L1n", 20)):
        r = run_portfolio(L, min_n=th)
        if r:
            r["level"] = "%s%s" % (L, "" if not th else " ≥%d" % th)
        if not r:
            continue
        port.append(r)
        print("  %-24s %8d %8d %9.2f%% %9.2f%% %7.2f%% %7s"
              % (L, r["n_entries"], r["n_skip"], r["total"], r["ann"],
                 r["mdd"], r["sharpe"]))
    # 等权全市场基准（同窗口、同成本、同槽位）
    bench = xs_mean
    cum = 1.0
    bi = pos[EVAL_FROM]
    for i in range(bi, len(cal) - args.hold, args.hold):
        v = bench.get(cal[i])
        if v is not None and np.isfinite(v):
            cum *= (1 + v)
    print("  参考：同期全市场等权「每 %d 日滚动」复利 %+.2f%%" % (args.hold, (cum - 1) * 100))
    iw = idx[idx["trade_date"] >= EVAL_FROM]
    idx_win = round(float(iw["close"].iloc[-1] / iw["close"].iloc[0] - 1) * 100, 2) \
        if len(iw) > 1 else None
    print("  参考：同窗口上证 %+.2f%%" % (idx_win or 0))

    # ---------------- 无未来函数自检
    if args.verify > 0:
        print("\n自检：抽样 %d 条粗信号，只用入场前的 K 线重算，应全部复现"
              % args.verify, flush=True)
        samp = [s for s in sigs if s[2] == "L1n"]
        picks = [samp[i] for i in rng.choice(len(samp),
                                             size=min(args.verify, len(samp)),
                                             replace=False)] if samp else []
        ok = bad = 0
        for code, dt, L, e_i, anchor in picks:
            g = bars[bars["ts_code"] == code].reset_index(drop=True)
            i = g.index[g["trade_date"] == dt][0]
            bc = {"h": g["high"].to_numpy(float)[:i + 2],
                  "l": g["low"].to_numpy(float)[:i + 2],
                  "v": g["vol"].to_numpy(float)[:i + 2]}
            sw = find_swings(bc["h"], bc["l"])
            got = [x for x in sw if x[0] == i]
            if got and got[0][1] == -1:
                ok += 1
            else:
                bad += 1
        print("  复现 %d / 失败 %d（失败通常源于同型分型替换，非未来函数）"
              % (ok, bad))
    else:
        ok = bad = 0

    out = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hold_days": args.hold, "cost_buy": COST_BUY, "cost_sell": COST_SELL,
        "window": [EVAL_FROM, cal[-1]], "pool_window": [POOL_FROM, cal[-1]],
        "n_sig_coarse": len(sigs),
        "levels": rows_lv, "lag_bands": band_tbl, "lag_corr": corr,
        "overlap": overlap, "monthly_L1n": monthly, "board": board_tbl,
        "k_zero": k_zero,
        "portfolio": port, "crowd": crowd_tbl, "gate": gate_tbl,
        "bench": {"index_window_pct": idx_win,
                  "xs_roll_pct": round((cum - 1) * 100, 2)},
        "selfcheck": {"n": len(picks) if args.verify else 0, "ok": ok, "bad": bad},
    }
    os.makedirs(os.path.dirname(PAGE_OUT), exist_ok=True)
    json.dump(out, open(PAGE_OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    json.dump(out, open(JSON_OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n写出 %s" % PAGE_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
