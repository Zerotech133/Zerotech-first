# -*- coding: utf-8 -*-
"""H9 可交易性检验：外盘隔夜信号 → A 股「开盘缺口 vs 日内收益」分解。

对每个 A 股交易日 D：
  us_ret = 前一美股交易日收益（RUT/SPX/IXIC）
  gap_D  = open_D / close_{D-1} - 1     （开盘缺口：隔夜信息直接反映处）
  intra  = close_D / open_D - 1          （日内：开盘后交易可得部分）
判定：
  - corr(us, gap) 高 且 corr(us, intra) ≈0 → 信号已被开盘价吸收，不可交易；
  - corr(us, intra) 显著且 t>2、成本后为正 → 可交易（开盘执行、收盘平仓）。
"""
from __future__ import annotations

import glob
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data"
OUT = REPO / "research" / "reports"
A_IDX = {"000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
         "399006.SZ": "创业板指", "000001.SH": "上证指数"}
US = ["RUT", "SPX", "IXIC"]
COST = 0.003  # 双边 30bp


def load_global() -> dict:
    out = {}
    for f in sorted((DATA / "tushare" / "index_global").glob("*.parquet")):
        if f.stem not in US:
            continue
        tb = pq.read_table(f, columns=["trade_date", "pct_chg"])
        out[f.stem] = {str(d): float(v) for d, v in zip(tb.column("trade_date").to_pylist(),
                                                        tb.column("pct_chg").to_pylist()) if v is not None}
    return out


def load_a() -> dict:
    out = {c: {} for c in A_IDX}
    fs = sorted(glob.glob(str(DATA / "tushare" / "index_daily" / "d_*.parquet")))
    days = [Path(f).stem[2:] for f in fs if Path(f).stem[2:].isdigit()]
    days = [d for d in days if d >= "20230101"]
    for d in days:
        tb = pq.read_table(DATA / "tushare" / "index_daily" / ("d_%s.parquet" % d),
                           columns=["ts_code", "open", "close", "pre_close", "pct_chg"])
        dd = tb.to_pydict()
        for c, o, cl, pre in zip(dd["ts_code"], dd["open"], dd["close"], dd["pre_close"]):
            if c in A_IDX and o and cl and pre:
                out[c][str(d)] = {"open": float(o), "close": float(cl), "pre": float(pre)}
    return out


def corr(xs, ys):
    n = len(xs)
    if n < 20:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    cv = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = math.sqrt(sum((a - mx) ** 2 for a in xs)); vy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if not vx or not vy:
        return None
    r = cv / (vx * vy)
    return {"n": n, "corr": round(r, 4), "t": round(r * math.sqrt((n - 2) / max(1e-12, 1 - r * r)), 2)}


def stats(vals):
    n = len(vals)
    if n < 5:
        return {"n": n, "mean": 0.0, "t": 0.0}
    m = sum(vals) / n
    s = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(m * 100, 3), "t": round(m / (s / math.sqrt(n)), 2) if s else 0.0}


def main() -> int:
    g = load_global()
    a = load_a()
    res = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "cost_pct": COST * 100, "pairs": {}, "tradable_long": {}}
    a_days_all = sorted({d for c in a for d in a[c]})
    for gcode, gmap in g.items():
        for acode, aname in A_IDX.items():
            amap = a[acode]
            gap_x, gap_y, intra_x, intra_y, long_ret = [], [], [], [], []
            for i, d in enumerate(a_days_all):
                if d not in amap or i == 0:
                    continue
                us = [gmap[x] for x in sorted(gmap) if x < d]
                if not us:
                    continue
                ur = us[-1]
                row = amap[d]
                gap = row["open"] / row["pre"] - 1
                intra = row["close"] / row["open"] - 1
                gap_x.append(ur); gap_y.append(gap)
                intra_x.append(ur); intra_y.append(intra)
                if ur > 0:
                    long_ret.append(intra - COST)
            seg = int(len(gap_x) * 0.6)
            def split_corr(xs, ys):
                return {"IS": corr(xs[:seg], ys[:seg]), "OOS": corr(xs[seg:], ys[seg:])}
            res["pairs"]["%s->%s" % (gcode, acode)] = {
                "gap": split_corr(gap_x, gap_y), "intraday": split_corr(intra_x, intra_y)}
            res["tradable_long"]["%s->%s" % (gcode, acode)] = {
                "skip_oos": stats(long_ret[seg:])}
    # 判定：日内 OOS 相关显著且正、且信号日 long 日内收益成本后 >0 有 t
    ok = []
    for k, v in res["pairs"].items():
        o = v["intraday"]["OOS"]
        if o and o["corr"] > 0.05 and o["t"] > 2:
            lr = res["tradable_long"][k]["skip_oos"]
            if lr["n"] >= 50 and lr["mean"] > 0 and lr["t"] > 1.5:
                ok.append((k, o, lr))
    res["verdict"] = ("可交易（信号在日内仍有超额）" if ok else "不可交易：信号集中在开盘缺口/日内无显著超额")
    res["tradable"] = [{"pair": x[0], "intraday_oos": x[1], "long_oos": x[2]} for x in ok]
    print(json.dumps({"verdict": res["verdict"], "cost_pct": res["cost_pct"],
                      "tradable": res["tradable"]}, ensure_ascii=False, indent=1)[:1200])
    # 摘要：最强 gap 相关
    ranks = []
    for k, v in res["pairs"].items():
        go, io = v["gap"]["OOS"], v["intraday"]["OOS"]
        if go and io:
            ranks.append((abs(go["corr"]), k, go, io))
    ranks.sort(reverse=True)
    print("\nTop gap-correlation pairs:")
    for r in ranks[:6]:
        print("  %-22s gap OOS %.3f(t=%.2f) | intraday OOS %.3f(t=%.2f)" % (
            r[1], r[2]["corr"], r[2]["t"], r[3]["corr"], r[3]["t"]))
    OUT.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    js = OUT / ("H9_tradability_%s.json" % day)
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n报告：%s" % js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
