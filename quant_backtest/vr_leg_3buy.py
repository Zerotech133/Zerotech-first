# -*- coding: utf-8 -*-
"""T1-v2 · 三买×腿级量能交叉验证（2026-11-11，接 T1 信号日量比口径被拒之后的另一口径）。

口径：
- 「缩量回踩」按 PPT/108 课原意解读为**腿级**：回踩笔（dir=-1）日均成交额 vs
  前一离开笔（dir=+1）日均成交额，比值<1 为缩量回踩。
- 笔结构与全池同引擎（chanlun_signal.analyze，四价原始价），信号日 PIT（只喂到信号日）。
- 前瞻 20 日超额 vs 全市场等权（log-cumsum 复合口径，与 magic_pool 一致）。
- 分桶：<0.7 / 0.7-1.0 / 1.0-1.5 / >1.5；流动性分层（20 日日均额中位数切半）复核。

验收门（研发计划同 T1）：单调 + t>=2 + 分层稳健 → 提交互项材料待批准；否则证据入库关闭。
"""
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "data", "ashare")
FWD = 20

from chanlun_signal import analyze as chan_analyze  # noqa: E402


def load_events():
    evs = []
    for fp in (os.path.join(HERE, "data", "sig_events",
                            "sig_events_market_v3_20251013_20261012.json"),):
        d = json.load(open(fp, encoding="utf-8"))
        for e in d["events"]:
            if e["type"] == "3买":
                evs.append({"stock": e["stock"], "day": str(e["dt"]),
                            "vr": e.get("vr")})
    # 去重（同票同日）
    seen = {}
    for e in evs:
        seen.setdefault((e["stock"], e["day"]), e)
    return [(s, d, seen[(s, d)].get("vr")) for s, d in sorted(seen)]


def load_close_matrix():
    """(codes, dates, log价格矩阵 L, 有效掩码)——与 magic_pool 同口径。"""
    files = [f for f in os.listdir(DATA) if f.endswith(".parquet")]
    dates_all, chunks = set(), {}
    for fn in files:
        code = fn[:-8].replace("_", ".")
        if code.endswith(".BJ"):
            continue
        df = pd.read_parquet(os.path.join(DATA, fn))
        if "pct_chg" in df.columns:
            p = df["pct_chg"].values / 100.0
        else:
            c = df["close"].values.astype(float)
            pc = df["pre_close"].values.astype(float)
            p = np.where(pc > 0, c / pc - 1.0, np.nan)
        d = df["trade_date"].astype(str).values
        chunks[code] = (d, p)
        dates_all.update(d.tolist())
    dates = sorted(dates_all)
    didx = {d: i for i, d in enumerate(dates)}
    mat = np.full((len(dates), len(chunks)), np.nan)
    order = sorted(chunks)
    for j, code in enumerate(order):
        d, p = chunks[code]
        idx = np.array([didx[x] for x in d], dtype=int)
        ok = ~np.isnan(p)
        mat[idx[ok], j] = p[ok]
    return order, dates, mat


def leg_stats(bars, bis, sig_dt):
    """回踩腿=最近一根 dir=-1 笔（延伸到信号日 T），离开腿=其前一根 dir=+1 笔。

    注：事件缓存的 dt 是扫描日（延伸中回踩笔的当前端点），重放确认笔的
    e_dt 通常早于它——所以不能按 e_dt==sig_dt 精确匹配，按「最近向下笔+延伸」。
    """
    amt = {b["dt"]: b["amt"] for b in bars}
    idx_pull = None
    for i, bi in enumerate(bis):
        if bi.get("dir") == -1 and bi.get("s_dt", 0) < sig_dt:
            idx_pull = i  # 取最后一根满足条件的向下笔
    if idx_pull is None or idx_pull == 0:
        return None
    pull = bis[idx_pull]
    leaving = bis[idx_pull - 1]
    if leaving.get("dir") != 1:
        return None
    def mean_amt(s_dt, e_dt):
        ds = [b["dt"] for b in bars if s_dt < b["dt"] <= e_dt]
        vs = [amt[d] for d in ds if amt.get(d)]
        return (sum(vs) / len(vs)) if vs else None
    a_pull = mean_amt(pull["s_dt"], sig_dt)      # 回踩腿延伸到信号日
    a_leave = mean_amt(leaving["s_dt"], leaving["e_dt"])
    if not a_pull or not a_leave or a_leave <= 0:
        return None
    n_pull = sum(1 for b in bars if pull["s_dt"] < b["dt"] <= sig_dt)
    n_leave = sum(1 for b in bars
                  if leaving["s_dt"] < b["dt"] <= leaving["e_dt"])
    if n_pull < 3 or n_leave < 3:  # 腿太短不判
        return None
    return a_pull / a_leave, n_pull, n_leave


def main():
    t0 = time.time()
    today = time.strftime("%Y%m%d")
    events = load_events()
    stocks = sorted({s for s, _, _ in events})
    print("3买 事件 %d 条（去重后），涉及 %d 只票" % (len(events), len(stocks)),
          flush=True)

    # ---- 逐票重放缠论笔，算腿级量比（信号日 PIT）----
    leg = {}      # (stock, day) -> leg_ratio
    legn = {}     # 腿长
    for k, st in enumerate(stocks):
        fp = os.path.join(DATA, st.replace(".", "_") + ".parquet")
        if not os.path.exists(fp):
            continue
        df = pd.read_parquet(fp, columns=["trade_date", "open", "high", "low",
                                          "close", "amount"])
        df = df[df["amount"] > 0]
        days = df["trade_date"].astype(int).values
        sig_days = [int(d) for s, d, _ in events if s == st]
        cut = max(sig_days)
        df = df[df["trade_date"].astype(int) <= cut]
        bars = [{"dt": int(r.trade_date), "o": float(r.open), "h": float(r.high),
                 "l": float(r.low), "c": float(r.close), "amt": float(r.amount)}
                for r in df.itertuples(index=False)]
        if len(bars) < 60:
            continue
        try:
            st_res = chan_analyze(bars)
        except Exception:
            continue
        bis = (st_res or {}).get("bis") or []
        for d in sig_days:
            r = leg_stats(bars, bis, d)
            if r:
                leg[(st, str(d))] = r[0]
                legn[(st, str(d))] = (r[1], r[2])
        if (k + 1) % 100 == 0:
            print("  replay %d/%d %.1fs" % (k + 1, len(stocks), time.time() - t0),
                  flush=True)
    print("腿级量比可判: %d/%d (%.0f%%)"
          % (len(leg), len(events), 100 * len(leg) / max(1, len(events))),
          flush=True)

    # ---- 前瞻收益矩阵 ----
    order, dates, mat = load_close_matrix()
    cidx = {c: j for j, c in enumerate(order)}
    L = np.cumsum(np.log1p(np.nan_to_num(mat, nan=0.0)), axis=0)
    valid = np.cumsum(~np.isnan(mat), axis=0) > 0
    n = len(dates)
    Lf = np.vstack([L[FWD:], np.tile(L[-1:], (FWD, 1))])
    fwd = np.expm1(Lf - L)
    fwd[~valid] = np.nan
    fwd[n - FWD:, :] = np.nan
    didx = {d: i for i, d in enumerate(dates)}

    # 流动性分层：20 日日均额中位数（事件前）
    liq = {}
    for (st, day) in list(leg):
        fp = os.path.join(DATA, st.replace(".", "_") + ".parquet")
        df = pd.read_parquet(fp, columns=["trade_date", "amount"])
        df = df[df["trade_date"].astype(int) <= int(day)]
        a = df["amount"].values[-20:]
        liq[(st, day)] = float(np.median(a)) if len(a) else None

    vr_map = {(s, d): v for s, d, v in events}
    rows = []
    for key, lr in leg.items():
        st, day = key
        i, j = didx.get(day), cidx.get(st)
        if i is None or j is None:
            continue
        f = fwd[i, j]
        if np.isnan(f):
            continue
        mkt_row = fwd[i]
        mkt = np.nanmean(mkt_row)
        rows.append({"stock": st, "day": day, "leg_ratio": lr,
                     "vr": vr_map.get(key), "fwd_bp": f * 1e4,
                     "excess_bp": (f - mkt) * 1e4,
                     "liq": liq.get(key) or 0.0})
    df = pd.DataFrame(rows)
    print("有效样本 %d 条（含基准可算）" % len(df), flush=True)

    buckets = [("<0.7", 0.0, 0.7), ("0.7-1.0", 0.7, 1.0),
               ("1.0-1.5", 1.0, 1.5), (">1.5", 1.5, 99.0)]
    def summarize(sub):
        x = sub["excess_bp"].values
        return {"n": int(len(x)), "mean_bp": round(float(np.mean(x)), 1),
                "t": round(float(np.mean(x) / (np.std(x, ddof=1)
                          / math.sqrt(len(x)))), 2) if len(x) > 2 else None,
                "win": round(float((x > 0).mean()), 3)}
    out_b, out_s, out_b8 = {}, {}, {}
    for name, lo, hi in buckets:
        sub = df[(df["leg_ratio"] >= lo) & (df["leg_ratio"] < hi)]
        out_b[name] = summarize(sub)
        med = df["liq"].median()
        out_s.setdefault("low_liq", {})[name] = summarize(sub[sub["liq"] <= med])
        out_s.setdefault("high_liq", {})[name] = summarize(sub[sub["liq"] > med])

    rep = {
        "date": today, "task": "T1-v2 三买×腿级量能（回踩笔vs离开笔日均额）",
        "events_total": len(events), "leg_judged": len(leg),
        "n_valid": int(len(df)),
        "buckets": out_b,
        "liq_split": out_s,
        "note": "验收门：单调 + t>=2 + 分层稳健 → 交互项材料；否则关闭该线",
        "runtime_s": round(time.time() - t0, 1),
        "records": df.to_dict(orient="records"),
    }
    out = os.path.join(HERE, "reports", "vr_leg_3buy_%s.json" % today)
    json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n== 结果 ==")
    for k, v in out_b.items():
        print("  腿量比 %-8s n=%-5d mean=%+8.1fbp t=%s win=%s"
              % (k, v["n"], v["mean_bp"], v["t"], v["win"]))
    print("落盘:", out, "| 用时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
