# -*- coding: utf-8 -*-
"""粗信号影子清单：收盘后可判的底分型 → 次日开盘入场 → 20 日持有（纸面跟踪）。

为什么要有这个脚本
------------------
①d 的证据说明两件事：细信号（1买/2买/3买）k=0 占比 0%，真人收盘后下不了单；
粗信号（严格底分型 + 力度/量能过滤）收盘即可判、次日开盘可成交，单笔减当日
全市场等权 +1.7%~+2.2%，但 20 日持有互相重叠，非重叠抽样只剩 9 段、t=1.42
—— 样本不够，不能下实盘指令。

唯一诚实的推进方式是把粗信号落成「影子清单」：每天按同一口径记录、结算，
用真实前推样本把 t 值养上去。在 t 值够之前，它只是清单，不是买入建议。

口径（与 tools/research/coarse_signal_bt.py 完全一致）
-----------------------------------------------------
- 分型：严格底分型（中间 K 线 low 最低且 high 最低），顶底间隔 ≥ 4 根 K 线
- 确认：底分型中心 i 的下一根 i+1 收盘确认 → i+2 开盘买入（= 次日开盘）
- 持有：20 个交易日，收盘卖出
- 成本：买 0.075% + 卖 0.125%
- 复权：price × adj_factor
- 北交所剔除（回测单独统计为负）
- 档位：L0 裸底分型 / L1 +力度收窄 / L1n +创新低 / L2 +缩量（L3 已弃）

产出
----
- quant_backtest/data/coarse_shadow.jsonl  原始记录，每 (bar_dt, code, level) 一条
- portal/coarse_shadow_stats.json           聚合统计，页面 ⑦ 块直接读

用法
----
python tools/research/coarse_shadow.py                  # 增量扫到最新交易日 + 结算
python tools/research/coarse_shadow.py --from 20260901   # 首次回填（清单为空时才用）
python tools/research/coarse_shadow.py --settle-only     # 只重算纸面成绩
"""

import argparse
import datetime as dt
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
SHADOW = os.environ.get("CSHADOW_OUT",
                        os.path.join(QBT, "data", "coarse_shadow.jsonl"))
STATS = os.environ.get("CSHADOW_STATS",
                       os.path.join(ROOT, "portal", "coarse_shadow_stats.json"))

HOLD = 20
COST_BUY = 0.00075
COST_SELL = 0.00125
GAP = 4
LOOKBACK = 340          # 取多少交易日历史（够 4 个摆动点 + 20 日结算余量）
GATE_TH = 50            # 扎堆门槛：当日该档信号数 ≥ N 才视为可建仓日
LEVELS = ("L0", "L1", "L1n", "L2")


def _norm(s):
    return (s.astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def board_of(code):
    if code.endswith(".BJ"):
        return "北交所"
    s = code.split(".")[0]
    if s.startswith("688") or s.startswith("689"):
        return "科创板"
    if s.startswith(("300", "301")):
        return "创业板"
    if s.startswith(("8", "4", "920")):
        return "北交所"
    return "主板"


# ------------------------------------------------------------------ 数据
def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            out.append(d)
    idx = (pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
           .sort_values("trade_date").reset_index(drop=True))
    idx["trade_date"] = (idx["trade_date"].astype(str)
                         .str.replace("-", "", regex=False).str.slice(0, 8))
    idx = idx.sort_values("trade_date").reset_index(drop=True)
    return idx


def load_market(start_date):
    fr = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open", "high",
                                        "low", "close", "vol"])
        d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
        d = d[d["trade_date"] >= str(start_date)]
        if len(d):
            fr.append(d)
    bars = (pd.concat(fr, ignore_index=True)
            .drop_duplicates(["ts_code", "trade_date"])
            .sort_values(["ts_code", "trade_date"]))
    fr = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
        d = d[d["trade_date"] >= str(start_date)]
        if len(d):
            fr.append(d)
    a = pd.concat(fr, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
    bars["adj_factor"] = (bars.groupby("ts_code")["adj_factor"]
                          .ffill().bfill().fillna(1.0))
    bars["trade_date"] = _norm(bars["trade_date"])
    return bars


def load_names():
    fr = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_stock_basic", "part_*.parquet"))):
        fr.append(pd.read_parquet(p, columns=["ts_code", "name", "industry"]))
    d = pd.concat(fr, ignore_index=True).drop_duplicates("ts_code", keep="last")
    return dict(zip(d["ts_code"], d["name"])), dict(zip(d["ts_code"], d["industry"]))


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


def scan_codes(bars, cal_pos, scan_from, scan_to):
    """全市场扫一遍，返回 [(bar_dt, code, level, close, low)]。"""
    rows = []
    for code, g in bars.groupby("ts_code", sort=False):
        if board_of(code) == "北交所":
            continue
        dates = g["trade_date"].tolist()
        n = len(dates)
        if n < 40:
            continue
        adj = g["adj_factor"].to_numpy(float)
        h = g["high"].to_numpy(float) * adj
        l = g["low"].to_numpy(float) * adj
        c = g["close"].to_numpy(float) * adj
        c_raw = g["close"].to_numpy(float)      # 清单里给人看的价格必须是原始价
        v = g["vol"].to_numpy(float)
        sw = find_swings(h, l)
        for j in range(3, len(sw)):
            i, t = sw[j]
            if t != -1:
                continue
            if i + 1 >= n:                      # 还没有确认 K 线
                continue
            d = dates[i + 1]                     # 确认日（收盘可知）
            if not (scan_from <= d <= scan_to):
                continue
            pt, pb, pt2 = sw[j - 1][0], sw[j - 2][0], sw[j - 3][0]
            f_now = (h[pt] - l[i]) / h[pt] if h[pt] > 0 else 0.0
            f_prev = (h[pt2] - l[pb]) / h[pt2] if h[pt2] > 0 else 0.0
            if f_now <= 0 or f_prev <= 0:
                continue
            v_now = float(np.mean(v[pt:i + 1])) if i > pt else np.nan
            v_prev = float(np.mean(v[pt2:pb + 1])) if pb > pt2 else np.nan
            decay = f_now < f_prev
            new_low = l[i] < l[pb]
            shrink = (np.isfinite(v_now) and np.isfinite(v_prev)
                      and v_prev > 0 and v_now < v_prev)
            lv = ["L0"]
            if decay:
                lv.append("L1")
                if new_low:
                    lv.append("L1n")
                    if shrink:
                        lv.append("L2")
            for x in lv:
                rows.append((d, code, x, float(c_raw[i + 1]), float(l[i + 1])))
    return rows


# ------------------------------------------------------------------ 结算
def settle(recs, cal, pos, O, C, idx_c, idx_o):
    """按「确认日次日开盘买 → 20 日后收盘卖」结算，返回逐条结果。"""
    out = []
    for r in recs:
        d, code = r["bar_dt"], r["code"]
        bi = pos.get(d)
        if bi is None or bi + 1 >= len(cal):
            out.append(dict(r, status="pending"))
            continue
        ei = bi + 1
        if ei + HOLD >= len(cal):
            out.append(dict(r, status="pending"))
            continue
        ed, xd = cal[ei], cal[ei + HOLD]
        try:
            p0 = float(O.at[ed, code]) * (1 + COST_BUY)
            p1 = float(C.at[xd, code])
        except KeyError:
            out.append(dict(r, status="no_data"))
            continue
        if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0 or p1 <= 0:
            out.append(dict(r, status="no_data"))
            continue
        net = (p1 * (1 - COST_SELL)) / p0 - 1.0
        bm = None
        if idx_o.get(ed) and idx_c.get(xd):
            bm = idx_c[xd] / idx_o[ed] - 1.0
        out.append(dict(r, status="settled", entry_dt=ed, exit_dt=xd,
                        ret=round(net * 100, 2),
                        alpha=round((net - bm) * 100, 2) if bm is not None else None))
    return out


def _agg(rows):
    v = [x["ret"] for x in rows if x.get("ret") is not None]
    a = [x["alpha"] for x in rows if x.get("alpha") is not None]
    if not v:
        return {"n": 0, "avg": None, "alpha": None, "win": None, "day_mean": None}
    byd = defaultdict(list)
    for x in rows:
        if x.get("ret") is not None:
            byd[x["entry_dt"]].append(x["ret"])
    dm = float(np.mean([np.mean(z) for z in byd.values()])) if byd else None
    t = None
    if len(v) > 2 and np.std(v, ddof=1) > 0:
        t = round(float(np.mean(v) / (np.std(v, ddof=1) / np.sqrt(len(v)))), 2)
    tday = None
    if len(byd) > 2:
        dv = np.array([np.mean(z) for z in byd.values()])
        if dv.std(ddof=1) > 0:
            tday = round(float(dv.mean() / (dv.std(ddof=1) / np.sqrt(len(dv)))), 2)
    return {"n": len(v), "avg": round(float(np.mean(v)), 2),
            "alpha": round(float(np.mean(a)), 2) if a else None,
            "win": round(float(np.mean([1.0 if z > 0 else 0.0 for z in v])) * 100, 1),
            "day_mean": round(dm, 2) if dm is not None else None,
            "n_day": len(byd), "t": t, "t_day": tday}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="",
                    help="回填起始 YYYYMMDD（仅清单为空时用）")
    ap.add_argument("--settle-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 只票（自测）")
    args = ap.parse_args()

    idx = load_index()
    cal = list(idx["trade_date"])
    pos = {d: i for i, d in enumerate(cal)}
    latest = cal[-1]

    existing = {}
    if os.path.exists(SHADOW):
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                existing[(r["bar_dt"], r["code"], r["level"])] = r

    added = 0
    if not args.settle_only:
        last = max((r["bar_dt"] for r in existing.values()), default="")
        # 清单为空：有 --from 就回填，没有就只扫最新一天（不偷偷回填半年）
        scan_from = last or args.frm or latest
        # 分型预热：扫描起点前至少 150 个交易日，否则最早的信号缺摆动点上下文
        warm = max(0, pos[scan_from] - 150) if scan_from in pos else 0
        start_date = cal[warm]
        bars = load_market(start_date)
        if args.limit:
            keep = sorted(bars["ts_code"].unique())[:args.limit]
            bars = bars[bars["ts_code"].isin(keep)]
        rows = scan_codes(bars, pos, scan_from, latest)
        names, inds = load_names()
        by_day_lv = defaultdict(int)
        for d, code, lv, cl, lo in rows:
            by_day_lv[(d, lv)] += 1
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SHADOW, "a", encoding="utf-8") as f:
            for d, code, lv, cl, lo in rows:
                key = (d, code, lv)
                if key in existing:
                    continue
                rec = {"bar_dt": d, "code": code,
                       "name": names.get(code, ""), "industry": inds.get(code, ""),
                       "level": lv, "board": board_of(code),
                       "close": round(cl, 2), "n_day": by_day_lv[(d, lv)],
                       "gate": by_day_lv[(d, lv)] >= GATE_TH,
                       "src": "backfill" if args.frm else "live",
                       "scan_at": now}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                existing[key] = rec
                added += 1
        print("扫描窗口 %s ~ %s：新增 %d 条，清单累计 %d 条"
              % (scan_from, latest, added, len(existing)))

    # ---------------- 结算
    recs = sorted(existing.values(), key=lambda r: (r["bar_dt"], r["code"]))
    if not recs:
        print("清单为空，先跑扫描")
        return
    first = min(r["bar_dt"] for r in recs)
    start_i = max(0, pos.get(first, 0) - 2)
    mk = load_market(cal[start_i])
    O = mk.pivot_table(index="trade_date", columns="ts_code", values="open")
    C = mk.pivot_table(index="trade_date", columns="ts_code", values="close")
    adj = mk.pivot_table(index="trade_date", columns="ts_code", values="adj_factor")
    O = O * adj
    C = C * adj
    idx_c = dict(zip(idx["trade_date"], idx["close"]))
    idx_o = dict(zip(idx["trade_date"], idx["open"]))
    res = settle(recs, cal, pos, O, C, idx_c, idx_o)

    settled = [x for x in res if x["status"] == "settled"]
    pending = [x for x in res if x["status"] == "pending"]
    lv_tbl = []
    for lv in LEVELS:
        a = _agg([x for x in settled if x["level"] == lv])
        a["lvl"] = lv
        lv_tbl.append(a)
    gate_tbl = []
    for lv in ("L1", "L1n"):
        for g in (True, False):
            a = _agg([x for x in settled if x["level"] == lv and x["gate"] is g])
            a.update({"lvl": lv, "gate": g})
            gate_tbl.append(a)

    # 今日（最新确认日）清单
    today = max(r["bar_dt"] for r in recs)
    items = [r for r in recs if r["bar_dt"] == today]
    pri = {"L2": 0, "L1n": 1, "L1": 2, "L0": 3}
    best = {}
    for r in items:
        k = r["code"]
        if k not in best or pri[r["level"]] < pri[best[k]["level"]]:
            best[k] = r
    latest_list = sorted(best.values(), key=lambda r: (pri[r["level"]], r["code"]))
    # 窗口基准：绝对收益必须跟同期大盘并排看，否则「-2.5%」会被误读成策略崩了
    win = [d for d in cal if first <= d <= today]
    bench = {}
    if len(win) > 2:
        cw = C.reindex(win)
        dr = cw.pct_change().mean(axis=1).dropna()
        bench["xs_pct"] = round((float(np.prod(1 + dr.values)) - 1) * 100, 2)
        if win[0] in idx_c and today in idx_c and idx_c[win[0]]:
            bench["index_pct"] = round(
                (idx_c[today] / idx_c[win[0]] - 1) * 100, 2)
        bench["days"] = len(win)

    ti = pos.get(today, len(cal) - 1)
    entry_dt = cal[ti + 1] if ti + 1 < len(cal) else ""

    stats = {
        "asof": {"bar_dt": today, "entry_dt": entry_dt,
                 "scanned_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")},
        "latest": {
            "bar_dt": today, "entry_dt": entry_dt,
            "n": {lv: sum(1 for r in items if r["level"] == lv) for lv in LEVELS},
            "gate_th": GATE_TH,
            "gate_on": {lv: sum(1 for r in items if r["level"] == lv) >= GATE_TH
                        for lv in ("L1", "L1n")},
            "items": [{"code": r["code"], "name": r.get("name", ""),
                       "level": r["level"], "board": r["board"],
                       "close": r["close"], "industry": r.get("industry", "")}
                      for r in latest_list[:80]],
            "n_code": len(latest_list),
        },
        "shadow": {
            "n_rec": len(recs), "n_day": len({r["bar_dt"] for r in recs}),
            "settled": len(settled), "pending": len(pending),
            "window": [first, today], "hold": HOLD, "bench": bench,
            "by_level": lv_tbl, "by_gate": gate_tbl,
            "all": _agg(settled),
        },
    }
    with open(STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print("\n影子清单纸面成绩（%s ~ %s，已结算 %d 笔，待结算 %d 笔）"
          % (first, today, len(settled), len(pending)))
    print("  %-6s %5s %8s %8s %8s %8s %6s" %
          ("档位", "笔数", "笔均", "日组均", "胜率", "减上证", "日 t"))
    for a in lv_tbl:
        print("  %-6s %5d %8s %8s %8s %8s %6s"
              % (a["lvl"], a["n"],
                 "—" if a["avg"] is None else "%+.2f%%" % a["avg"],
                 "—" if a["day_mean"] is None else "%+.2f%%" % a["day_mean"],
                 "—" if a["win"] is None else "%.0f%%" % a["win"],
                 "—" if a["alpha"] is None else "%+.2f%%" % a["alpha"],
                 "—" if a.get("t") is None else "%.2f" % a["t"]))
    if bench:
        print("  同期基准：上证 %+.2f%% / 全市场等权 %+.2f%%（%d 日）"
              % (bench.get("index_pct") or 0, bench.get("xs_pct") or 0,
                 bench.get("days") or 0))
    print("\n今日（%s 确认，%s 开盘入场）L1 %d 只 / L1n %d 只 / 门槛(≥%d) %s"
          % (today, entry_dt or "下一交易日",
             stats["latest"]["n"]["L1"], stats["latest"]["n"]["L1n"], GATE_TH,
             "过" if stats["latest"]["gate_on"]["L1"] else "未过"))
    print("  → %s" % STATS)


if __name__ == "__main__":
    main()
