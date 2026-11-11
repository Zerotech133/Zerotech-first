# -*- coding: utf-8 -*-
"""板块层 PIT 回放：回答「板块买点后面赚不赚」。

为什么必须单独做一次
--------------------
个股回放（pool_replay）跑完 48 万观测，结论是九态无一稳定正超额。但**那个
结论不能搬给板块层**：板块指数是市值加权合成，它的中枢、三买、背驰与个股
完全是两套东西。拿个股的回放去给板块页背书，就是拿测 A 的结果说 B 行。

现在的状态是：板块页上写着「三卖 292 / 买点 15」，但"买点后面涨不涨"一个
数字都没有。这次把它补上。

纪律（与 pool_replay 同源）
---------------------------
1. 判定代码与线上同源：直接 import pool_state.analyse，不另写。
2. PIT：每个 asof 只喂 <= asof 的日线。
3. 超额按「同日全板块等权」算。绝对收益里混着市场 β，剔不掉就看不出判据。
4. t 值按 asof 聚类：同一天几百个板块不独立，先算逐日横截面均值再对日期序列 t。
5. 不设成交额门槛——板块指数没有流动性问题（这一点与个股回放不同）。

一条硬限制，先说清楚
--------------------
**成分共振无法回放。** `ts_ths_member` 只有当前快照（2026-11-05 拉取），
没有历史成分变更记录。用今天的成分去回测三年前，等于用"后来涨了的票"
组板块——幸存者偏差。所以本脚本只回放**板块指数结构**，共振那一路的
有效性目前无证据，页面上也不该给它加权。

用法
----
  python sector_replay.py --run --step 5            # 跑回放，落 parquet
  python sector_replay.py --run --step 5 --limit 40 # 冒烟
  python sector_replay.py --report                  # 读 parquet 出报告
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool_state as ps          # noqa: E402  判定口径唯一来源
import sector_state as ss        # noqa: E402  板块宇宙与过滤口径唯一来源

OUT_PARQUET = os.path.join(ps.REPORTS, "sector_replay.parquet")
FWD = (5, 10, 20, 60)
STEP_DEFAULT = 5

SCHEMA = ["asof", "code", "name", "type", "stage", "pos", "trend",
          "close", "r5", "r20", "r60", "rps20",
          "fwd5", "fwd10", "fwd20", "fwd60",
          "xr5", "xr10", "xr20", "xr60"]

BUY_STAGES = ss.BUY_STAGES
WEAK_STAGES = ("三卖", "中枢下")


# ────────────────────────────────────────────────────────────
# 1. 宇宙
# ────────────────────────────────────────────────────────────
def universe(level=ss.LEVEL_DEFAULT, min_members=ss.MIN_MEMBERS):
    """筛出可回放的板块及其全序列（dict: code → DataFrame）。

    过滤口径直接调 sector_state——两处各写一份，迟早会漂。
    """
    segs = ss.segs_of(level)
    sdef = ss.load_sectors()
    bars = ss.load_bars()
    members = ss.load_members()

    out, names, types = {}, {}, {}
    skip = {"seg": 0, "word": 0, "members": 0, "bars": 0}
    for code, g in bars.groupby("ts_code", sort=False):
        m = sdef.get(code)
        if not m or m["seg"] not in segs:
            skip["seg"] += 1
            continue
        if any(w in m["name"] for w in ss.DROP_WORDS):
            skip["word"] += 1
            continue
        if len(members.get(code) or []) < min_members:
            skip["members"] += 1
            continue
        if len(g) < ss.MIN_BARS:
            skip["bars"] += 1
            continue
        out[code] = g.sort_values("trade_date").reset_index(drop=True)
        names[code] = m["name"]
        types[code] = m["type"]

    # 重复指数去重：60 日收盘指纹。放在这里而不是 build 里，是因为回放要
    # 全历史，两个"同一只指数"在历史上也必须只算一次，否则同日基准被重复计权。
    seen, keep = {}, []
    for code, g in out.items():
        fp = hash(tuple(np.round(g["close"].to_numpy(float)[-60:], 3)))
        if fp in seen:
            continue
        seen[fp] = code
        keep.append(code)
    out = {c: out[c] for c in keep}
    cal = np.sort(bars["trade_date"].unique()).astype(np.int64)
    return out, names, types, cal, skip


def build_grid(cal, start, end, step):
    g = cal[(cal >= start) & (cal <= end)]
    return g[::step]


# ────────────────────────────────────────────────────────────
# 2. 单板块回放（worker）
# ────────────────────────────────────────────────────────────
_G = {}


def _init(series, grid, names, types):
    _G["s"] = series
    _G["grid"] = grid
    _G["n"] = names
    _G["t"] = types


def run_one(code):
    g = _G["s"][code]
    grid = _G["grid"]
    dt = g["trade_date"].astype("int64").to_numpy()
    close = g["close"].to_numpy(float)
    n = len(close)
    bars = ss._to_bars(g)

    out = []
    for asof in grid:
        i = int(np.searchsorted(dt, asof, "right")) - 1
        if i < ss.MIN_BARS - 1 or dt[i] != asof:
            continue                       # 当日无数据或历史不足，本截面不判
        r = ps.analyse(bars.iloc[:i + 1], code)
        if not r:
            continue
        # T+1 收盘入场：信号在 asof 收盘成立，第二天才有价可成交。
        # 用收盘而不是开盘，是因为板块指数没有真实开盘成交，开盘价是
        # 成分股开盘价加权的结果，用它算收益会给出一个买不到的价。
        e = i + 1
        if e >= n:
            continue
        fwd = []
        for N in FWD:
            j = min(e + N, n - 1)
            fwd.append(close[j] / close[e] - 1.0)
        out.append((int(asof), code, _G["n"][code], _G["t"][code],
                    r["stage"], r["pos"], r["trend"], r.get("close"),
                    ss._ret(close[:i + 1], 5), ss._ret(close[:i + 1], 20),
                    ss._ret(close[:i + 1], 60), None,
                    *fwd, None, None, None, None))
    return out


# ────────────────────────────────────────────────────────────
# 3. 跑
# ────────────────────────────────────────────────────────────
def cmd_run(args):
    t0 = time.time()
    level = args.level or ss.LEVEL_DEFAULT
    series, names, types, cal, skip = universe(level=level)
    print("[replay] 板块 %d 个（跳过 %s）· 日历 %d 日（%d~%d）"
          % (len(series), ", ".join("%s=%d" % kv for kv in skip.items()),
             len(cal), cal[0], cal[-1]))

    start = int(str(args.start).replace("-", "")) if args.start else int(cal[0])
    end = int(str(args.end).replace("-", "")) if args.end else int(cal[-1])
    grid = build_grid(cal, start, end, args.step)
    if not len(grid):
        print("[replay] 网格为空")
        return 2
    print("[replay] 截面 %d 个：%d → %d（每 %d 交易日）"
          % (len(grid), grid[0], grid[-1], args.step))

    codes = sorted(series)
    if args.limit:
        codes = codes[:args.limit]
        print("[replay] 冒烟模式，仅 %d 个板块" % len(codes))

    grid_t = tuple(int(x) for x in grid)
    rows = []
    if args.workers > 1:
        from multiprocessing import Pool
        with Pool(args.workers, initializer=_init,
                  initargs=(series, grid_t, names, types)) as pool:
            for res in pool.imap_unordered(run_one, codes, chunksize=2):
                rows.extend(res)
    else:
        _init(series, grid_t, names, types)
        for c in codes:
            rows.extend(run_one(c))

    if not rows:
        print("[replay] 无结果")
        return 3
    df = pd.DataFrame(rows, columns=SCHEMA)

    # RPS：逐截面百分位（同一天内比，不跨日比）。
    # 用 transform 式 rank，不用 groupby.apply——后者在 pandas 2.x 会把结果
    # 拼成一个多索引 Series，赋值时静默错位。
    df["rps20"] = (df.groupby("asof")["r20"].rank(pct=True) * 100).round(1)

    # 超额 = 本板块 fwd − 同日全板块 fwd 均值
    for N in FWD:
        base = df.groupby("asof")["fwd%d" % N].transform("mean")
        df["xr%d" % N] = df["fwd%d" % N] - base

    os.makedirs(ps.REPORTS, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    print("[replay] 完成 %d 行 / %d 个板块 / %d 截面 · %.0fs"
          % (len(df), df["code"].nunique(), df["asof"].nunique(),
             time.time() - t0))
    print("[replay] 落盘 %s（%.1f MB）"
          % (OUT_PARQUET, os.path.getsize(OUT_PARQUET) / 1e6))
    print("[replay] 档位分布：" + "  ".join(
        "%s:%d" % (k, v) for k, v in df["stage"].value_counts().items()))
    return 0


# ────────────────────────────────────────────────────────────
# 4. 报告
# ────────────────────────────────────────────────────────────
def _dw(s):
    w = 0
    for ch in str(s):
        w += 2 if ord(ch) > 0x1100 and not (0x2000 <= ord(ch) <= 0x206F) else 1
    return w


def _p(s, width, right=False):
    s = "" if s is None else str(s)
    pad = max(width - _dw(s), 0)
    return (" " * pad + s) if right else (s + " " * pad)


def _bp(x, n=0):
    """bp 显示：0.0123 → +123bp"""
    if x is None or not np.isfinite(x):
        return "-"
    return "%+.*f" % (n, x * 1e4)


def _daily(df, col, mask, min_count=10):
    """按日等权统计 + 按日聚类 t。

    两个坑，都是这一轮回放踩出来的：

    ① **日等权与行均值会给出相反符号**。买点类行均值 +14bp、日等权 −35bp。
       原因是某一天只有 1 个买点板块（20241008，−6.8%），它在日等权里
       和有 370 个买点板块的日子权重相同。所以日等权必须设最小样本数，
       否则一天的极端值能决定整段结论。

    ② **不能拿行均值去相减做分层比较**。我曾用「最强层买点行均值 −19bp
       对最弱层 +1bp」得出"强者恒强"，换成逐日配对差之后四层是 +1/+28/
       +44/+25bp，完全没有单调性——行均值里混着每日样本构成（辛普森悖论）。

    所以这里只做日等权，分层比较一律走 _paired。
    """
    sub = df[mask]
    if sub.empty:
        return None
    d = sub.groupby("asof")[col].agg(["mean", "count"])
    d = d[d["count"] >= min_count]
    if len(d) < 5:
        return None
    m = d["mean"]
    sd = m.std(ddof=1)
    t = None if sd == 0 else float(m.mean() / (sd / np.sqrt(len(m))))
    return {"n": int(len(sub)), "days": int(len(d)),
            "mean": float(m.mean()), "med": float(m.median()),
            "win": float((m > 0).mean()), "t": t}


def _paired(df, col, mask_a, mask_b, min_count=5):
    """逐日配对差：同一天里 A 组均值 − B 组均值，再对日期序列做 t。

    同日配对把大盘 β 和当天的风格一起削掉了，剩下的才是两组的差别。
    要求两边当天都有 min_count 个样本，否则那天的差是噪声。
    """
    a = df[mask_a].groupby("asof")[col].agg(["mean", "count"])
    b = df[mask_b].groupby("asof")[col].agg(["mean", "count"])
    a = a[a["count"] >= min_count]["mean"]
    b = b[b["count"] >= min_count]["mean"]
    d = (a - b).dropna()
    if len(d) < 5:
        return None
    sd = d.std(ddof=1)
    t = None if sd == 0 else float(d.mean() / (sd / np.sqrt(len(d))))
    return {"days": int(len(d)), "diff": float(d.mean()), "t": t}


def cmd_report(args):
    if not os.path.exists(OUT_PARQUET):
        print("[report] 没有 %s，先 --run" % OUT_PARQUET)
        return 2
    df = pd.read_parquet(OUT_PARQUET)
    asofs = sorted(df["asof"].unique())
    print("\n板块层回放 · %d 个板块 / %d 个截面 / %d 观测"
          % (df["code"].nunique(), len(asofs), len(df)))
    print("区间 %d → %d　（T+1 收盘入场，超额 = 同日全板块等权）"
          % (asofs[0], asofs[-1]))
    print("口径：板块指数跑 pool_state.analyse，与线上同源；"
          "成分共振未回放（无历史成分快照）。")
    print("统计：一律按日等权 + 按日聚类 t，每日至少 %d 个样本；"
          "不给行均值（会被样本多的日子带偏）。" % 10)

    # ── 1. 各档位 ──
    print("\n── 1. 各结构档位（N=20 超额）" + "─" * 62)
    print(_p("档位", 10) + _p("样本", 8, 1) + _p("天数", 6, 1)
          + _p("日等权", 10, 1) + _p("中位", 10, 1) + _p("正天", 8, 1)
          + _p("t", 8, 1) + "  判读")
    order = ["一买", "二买", "三买", "三买待", "中枢", "背驰", "离开段",
             "三卖", "中枢下"]
    for st in order:
        r = _daily(df, "xr20", df["stage"] == st)
        if not r:
            continue
        note = ""
        # 天数太少时不给"显著"二字：二买只有 7 个截面，t=-2.49 看着漂亮，
        # 但 7 个点的 t 分布早就不是 t 分布了，撑不住"显著"这个结论。
        if r["t"] is not None and abs(r["t"]) >= 2 and r["days"] >= 10:
            note = "显著为负" if r["t"] < 0 else "显著为正"
        elif r["days"] < 10:
            note = "天数少"
        print(_p(st, 10) + _p(r["n"], 8, 1) + _p(r["days"], 6, 1)
              + _p(_bp(r["mean"]), 10, 1) + _p(_bp(r["med"]), 10, 1)
              + _p("%.0f%%" % (r["win"] * 100), 8, 1)
              + _p(("%.2f" % r["t"]) if r["t"] is not None else "-", 8, 1)
              + "  " + note)

    # ── 2. 合并 ──
    print("\n── 2. 三类合并" + "─" * 74)
    for lab, m in (("买点类（一/二/三/三买待）", df["stage"].isin(BUY_STAGES)),
                   ("卖点侧（三卖/中枢下）", df["stage"].isin(WEAK_STAGES)),
                   ("中枢/背驰/离开段",
                    ~df["stage"].isin(BUY_STAGES + WEAK_STAGES))):
        r = _daily(df, "xr20", m)
        if not r:
            continue
        print("  %-22s 样本 %6d · %3d 天 · 日等权 %s · 中位 %s · 正天 %.0f%% · t %s"
              % (lab, r["n"], r["days"], _bp(r["mean"]), _bp(r["med"]),
                 r["win"] * 100,
                 ("%.2f" % r["t"]) if r["t"] is not None else "-"))

    # ── 3. 强度分层（逐日配对） ──
    print("\n── 3. 同强度层内：买点类 vs 其余（逐日配对差，N=20）" + "─" * 32)
    print(_p("强度层", 14) + _p("买点", 9, 1) + _p("其余", 9, 1)
          + _p("逐日差", 10, 1) + _p("t", 8, 1) + _p("天数", 6, 1)
          + "  判读")
    bins = [(0, 20, "最弱 0-20"), (20, 50, "偏弱 20-50"),
            (50, 80, "偏强 50-80"), (80, 101, "最强 80-100")]
    buy_m = df["stage"].isin(BUY_STAGES)
    for lo, hi, lab in bins:
        band = (df["rps20"] >= lo) & (df["rps20"] < hi)
        rb = _daily(df, "xr20", band & buy_m, min_count=3)
        ro = _daily(df, "xr20", band & ~buy_m, min_count=3)
        pr = _paired(df, "xr20", band & buy_m, band & ~buy_m)
        if not (rb and ro and pr):
            continue
        note = ""
        if pr["t"] is not None and abs(pr["t"]) >= 2 and pr["days"] >= 10:
            note = "显著" if pr["t"] > 0 else "显著为负"
        print(_p(lab, 14) + _p(_bp(rb["mean"]), 9, 1)
              + _p(_bp(ro["mean"]), 9, 1)
              + _p(_bp(pr["diff"]), 10, 1)
              + _p(("%.2f" % pr["t"]) if pr["t"] is not None else "-", 8, 1)
              + _p(pr["days"], 6, 1) + "  " + note)

    # ── 4. 年代 ──
    print("\n── 4. 按半年分段（看是不是只靠某一段行情）" + "─" * 46)
    df = df.copy()
    df["half"] = (df["asof"] // 10000).astype(str) + "H" + \
                 np.where((df["asof"] // 100) % 100 <= 6, "1", "2")
    print(_p("区间", 9) + _p("买点类", 16, 1) + _p("中枢/背驰/离开", 16, 1)
          + _p("卖点侧", 16, 1))
    for h in sorted(df["half"].unique()):
        s = df[df["half"] == h]
        def _cell(mask):
            r = _daily(s, "xr20", mask, min_count=3)
            return "-" if not r else "%s (%dd)" % (_bp(r["mean"]), r["days"])
        print(_p(h, 9)
              + _p(_cell(s["stage"].isin(BUY_STAGES)), 16, 1)
              + _p(_cell(~s["stage"].isin(BUY_STAGES + WEAK_STAGES)), 16, 1)
              + _p(_cell(s["stage"].isin(WEAK_STAGES)), 16, 1))

    print("\n  注：① 超额已剔掉同日全板块等权，剩下的才是判据本身。"
          "\n      ② t 按日期聚类，不是按行——同日几百个板块不独立。"
          "\n      ③ 第 3 节用逐日配对差，不能用行均值相减：后者混着每日样本"
          "构成，\n         会造出「强者恒强」的假象（本轮实测：行均值差 +39bp 单调，"
          "配对后 +1/+28/+44/+25）。"
          "\n      ④ 成分共振与板块内选股未回放，本报告只说明「板块结构」本身。")
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--step", type=int, default=STEP_DEFAULT)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6,
                    help="进程数上限 16")
    ap.add_argument("--level", default=None)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    a.workers = max(1, min(a.workers, 16))
    if a.run:
        return cmd_run(a)
    if a.report:
        return cmd_report(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
