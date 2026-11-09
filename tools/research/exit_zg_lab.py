# -*- coding: utf-8 -*-
"""择时的真正落点：出场，不是入场。

前面两步的结论把入场这一侧关死了：
· entry_timing_lab 全量 829 笔，七种入场规则没有一种把收益做正
  （最好的是次日开盘 −3.06%，t=−5.86；「等触 ZG」−8.78%，t=−15.69）
· regime_* 三份检验把「用指数位置择势」也否掉了，顺向逆向都靠不住

但「等触 ZG」那 −8.78% 不是无用的坏消息，它反过来是一句极强的断言：
**跌到中枢上沿的票，后面 20 日还会再跌 8%**。如果这句成立，那 ZG 就不该
是买入的触发价，而应该是持有的止损线。

本脚本直接测这个：同一批信号、同一个入场价，只换出场规则，看差多少。
  H1 持满 20 日
  H2 收盘跌破 ZG 即出
  H3 8% 移动止损（产线现行）
  H4 ZG 或 8% 移动止损，先触发者执行
  H5 ZG 止损 + 20 日上限

如果 H2 明显好于 H1，说明「择时」该做在出场侧；如果 H4 最好，说明两者
互补而不是替代。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
for _p in (QB, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ASHARE = os.path.join(QB, "data", "ashare")
SHADOW = os.path.join(QB, "data", "combo_shadow.jsonl")
OUT = os.path.join(ROOT, "portal", "exit_zg_lab.json")

HOLD = 20
TRAIL = 0.08
BUY_COST = 0.00075
SELL_COST = 0.00125


def _net(buy_px, sell_px):
    return (sell_px * (1 - SELL_COST)) / (buy_px * (1 + BUY_COST)) - 1


def _one(rec):
    path = os.path.join(ASHARE, rec["code"].replace(".", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    import chanlun_scan as cs
    import chanlun_signal as csig

    bars = cs._bars_from_parquet(path)
    if not bars or len(bars) < 130:
        return None
    idx = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
    sd = str(rec.get("entry_date") or "").replace("-", "")[:8]
    i0 = idx.get(sd)
    if i0 is None or i0 + 2 >= len(bars):
        return None

    st = csig.analyze(bars[: i0 + 1])
    pvs = st.get("pivots") or []
    zg = float(pvs[-1]["zg"]) if pvs else None
    buy = bars[i0]["c"]
    if buy <= 0:
        return None

    end = min(i0 + HOLD, len(bars) - 1)
    if end <= i0 + 1:
        return None

    out = {"code": rec["code"], "dt": bars[i0]["dt"], "buy": round(buy, 3),
           "zg": round(zg, 3) if zg else None}

    # H1 持满
    out["H1"] = round(_net(buy, bars[end]["c"]) * 100, 3)

    # H2 跌破 ZG 即出（当日收盘价成交，含当日跳空低开的情形）
    j = None
    if zg:
        for k in range(i0 + 1, end + 1):
            if bars[k]["c"] < zg:
                j = k
                break
    out["H2"] = (round(_net(buy, bars[j]["c"]) * 100, 3) if j is not None
                 else out["H1"])
    out["H2_days"] = (j - i0) if j is not None else (end - i0)
    out["H2_fired"] = j is not None

    # H3 8% 移动止损
    peak, j3 = buy, None
    for k in range(i0 + 1, end + 1):
        peak = max(peak, bars[k]["h"])
        if bars[k]["c"] <= peak * (1 - TRAIL):
            j3 = k
            break
    out["H3"] = (round(_net(buy, bars[j3]["c"]) * 100, 3) if j3 is not None
                 else out["H1"])
    out["H3_fired"] = j3 is not None

    # H4 两者先触发
    if j is None and j3 is None:
        out["H4"] = out["H1"]
    elif j is None:
        out["H4"] = out["H3"]
    elif j3 is None:
        out["H4"] = out["H2"]
    else:
        out["H4"] = out["H2"] if j <= j3 else out["H3"]
    out["H4_fired"] = (j is not None) or (j3 is not None)

    # H5 ZG 止损 + 20 日上限（与 H2 同，但若无 ZG 则持满）
    out["H5"] = out["H2"]

    # 跌破 ZG 之后到底还跌多少（验证「ZG 后面还有 8%」这句）
    if zg:
        jj = next((k for k in range(i0 + 1, end + 1) if bars[k]["c"] < zg), None)
        if jj is not None and jj + 20 < len(bars):
            out["post_zg_20d"] = round((bars[jj + 20]["c"] / bars[jj]["c"] - 1) * 100, 3)
            out["post_zg_min20"] = round(
                (min(bars[jj + k]["c"] for k in range(1, 21)) / bars[jj]["c"] - 1) * 100, 3)
    return out


def ss(rows, key):
    v = np.array([r[key] for r in rows if r.get(key) is not None], float)
    if len(v) < 3:
        return None
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) else 0
    return {"n": len(v), "mean": round(float(v.mean()), 3),
            "median": round(float(np.median(v)), 3),
            "win": round(float((v > 0).mean() * 100), 1), "t": round(float(t), 2),
            "p10": round(float(np.percentile(v, 10)), 2),
            "p90": round(float(np.percentile(v, 90)), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    recs = []
    with open(SHADOW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("pass") and r.get("mature"):
                recs.append({"code": r["code"], "entry_date": r.get("entry_date")})
    if args.limit:
        recs = recs[: args.limit]
    print("样本 %d 笔\n" % len(recs))

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for k, res in enumerate(ex.map(_one, recs, chunksize=20)):
            if res:
                rows.append(res)
            if (k + 1) % 200 == 0:
                print("  ...%d/%d" % (k + 1, len(recs)))
    print("\n可算 %d 笔（%.1f%%）\n" % (len(rows), len(rows) / len(recs) * 100))

    print("=" * 84)
    print("只换出场规则（入场一律 = 信号日收盘，持有上限 %d 日，已扣 20bp）" % HOLD)
    print("=" * 84)
    print("%-18s %6s %9s %9s %8s %7s %8s %8s"
          % ("规则", "笔数", "均收益", "中位", "胜率", "t", "P10", "P90"))
    out = {}
    for k, lab in (("H1", "H1 持满20日"), ("H2", "H2 跌破ZG即出"),
                   ("H3", "H3 8%移动止损"), ("H4", "H4 两者先触发")):
        s = ss(rows, k)
        if not s:
            continue
        out[k] = s
        print("%-18s %6d %8.2f%% %8.2f%% %7.1f%% %7.2f %7.2f%% %7.2f%%"
              % (lab, s["n"], s["mean"], s["median"], s["win"], s["t"],
                 s["p10"], s["p90"]))

    if "H1" in out:
        print("\n相对「持满20日」：")
        for k, lab in (("H2", "跌破ZG即出"), ("H3", "8%移动止损"), ("H4", "两者先触发")):
            if k in out:
                print("  %-14s 单笔 %+6.2fpp  中位 %+6.2fpp  t 从 %.2f → %.2f"
                      % (lab, out[k]["mean"] - out["H1"]["mean"],
                         out[k]["median"] - out["H1"]["median"],
                         out["H1"]["t"], out[k]["t"]))

    n_zg = sum(1 for r in rows if r["zg"])
    n_fired = sum(1 for r in rows if r.get("H2_fired"))
    print("\nZG 止损触发率：%.1f%%（%d/%d 笔有 ZG）"
          % (n_fired / n_zg * 100 if n_zg else 0, n_fired, n_zg))
    d = [r["H2_days"] for r in rows if r.get("H2_fired")]
    if d:
        print("触发所需交易日：中位 %d  均值 %.1f" % (int(np.median(d)), np.mean(d)))

    # 验证核心断言：跌破 ZG 之后还跌多少
    post = [r["post_zg_20d"] for r in rows if r.get("post_zg_20d") is not None]
    pmin = [r["post_zg_min20"] for r in rows if r.get("post_zg_min20") is not None]
    if post:
        v = np.array(post, float)
        m = np.array(pmin, float)
        print("\n跌破 ZG 之后 20 个交易日（n=%d）：" % len(v))
        print("  再跌/再涨：均 %+.2f%%  中位 %+.2f%%  下跌占比 %.1f%%"
              % (v.mean(), np.median(v), (v < 0).mean() * 100))
        print("  之后 20 日内最差路径：均 %+.2f%%  中位 %+.2f%%  P10 %+.2f%%"
              % (m.mean(), np.median(m), np.percentile(m, 10)))
        out["post_zg"] = {"n": len(v), "mean_pct": round(float(v.mean()), 3),
                          "median_pct": round(float(np.median(v)), 3),
                          "down_share_pct": round(float((v < 0).mean() * 100), 1),
                          "min_path_mean_pct": round(float(m.mean()), 3)}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n": len(rows), "hold": HOLD, "trail": TRAIL,
                   "rules": out}, f, ensure_ascii=False, indent=1)
    print("\n→ %s" % OUT)


if __name__ == "__main__":
    main()
