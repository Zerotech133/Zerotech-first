# -*- coding: utf-8 -*-
"""池内择时：等什么才动手，比「信号一出就买」好多少。

为什么必须测这个
----------------
前一步已经把「用指数位置择势」否掉了：顺向（势好才买）GREEN 组 20 日
−1.38%（t=−2.72），逆向（势弱才买）剔除 2024-09 后 −3.44%（t=−16.14）。
两个方向都不成立。那么三层框架里唯一还能承担「决定买不买」的，只剩
择时——也就是「同样入池的票，等哪个事件才扣扳机」。

四种入场规则（全部只用信号日当天可见的信息，无未来函数）
--------------------------------------------------------
A 立即      信号次日按收盘买（产线现状的代理）
B 等突破    之后 20 日内，收盘首次站上 T1（上方最近笔顶）才买
C 等回踩    之后 20 日内，最低价首次触及 ZG（中枢上沿）才买，按 ZG 成交
D 等回踩信号 之后 20 日内，最低价首次触及信号价才买，按信号价成交
E 等缩量回踩 回踩到 ZG 且当日成交额 < 前 5 日均量的 0.8 倍

关键在「不交易」也算结果：B/C/D 都可能 20 日内一次都不触发，那这笔就不做。
所以不能只比平均收益，必须同时看触发率与总贡献 —— 择时的价值往往是
「少做错单」而不是「每单更赚」。
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

DATA = os.path.join(QB, "data")
ASHARE = os.path.join(DATA, "ashare")
SHADOW = os.path.join(DATA, "combo_shadow.jsonl")
OUT = os.path.join(ROOT, "portal", "entry_timing_lab.json")

HOLD = 20        # 持有天数（与产线 trail 的 20 日上限对齐）
WAIT = 20        # 等待触发的最大天数
BUY_COST = 0.00075
SELL_COST = 0.00125


def _net(buy_px, sell_px):
    return (sell_px * (1 - SELL_COST)) / (buy_px * (1 + BUY_COST)) - 1


def _one(job):
    rec = job
    code = rec["code"]
    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    import chanlun_scan as cs
    import chanlun_signal as csig

    bars = cs._bars_from_parquet(path)
    if not bars or len(bars) < 130:
        return None
    # bars 的 dt 是 "2023-04-03"，清单里的 entry_date 是 "20260422"，
    # 统一成无横线再建索引。早期版本直接拿清单口径去查带横线的 key，
    # 结果 100% 落空、可算 0 笔。
    idx = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
    sd = str(rec.get("entry_date") or rec.get("detect_date") or "").replace("-", "")[:8]
    i0 = idx.get(sd)
    if i0 is None or i0 + 2 >= len(bars):
        return None

    # 信号日结构：T1 与 ZG 都必须只用 i0 及之前的数据算
    st = csig.analyze(bars[: i0 + 1])
    pvs = st.get("pivots") or []
    bis = st.get("bis") or []
    sig_px = float(rec.get("entry_px") or bars[i0]["c"])
    if sig_px <= 0:
        return None
    zg = float(pvs[-1]["zg"]) if pvs else None
    tops = [b["e_price"] for b in bis
            if b["e_price"] > sig_px * 1.02 and b["e_price"] >= b["s_price"]]
    t1 = min(tops) if tops else None

    end = min(i0 + WAIT, len(bars) - 1)
    fwd = min(i0 + 1 + HOLD, len(bars) - 1)
    if fwd <= i0 + 1:
        return None

    res = {"code": code, "sig_dt": bars[i0]["dt"], "sig_px": round(sig_px, 3),
           "t1": round(t1, 3) if t1 else None, "zg": round(zg, 3) if zg else None}

    # A 立即：次日收盘买
    pa = bars[i0 + 1]["c"]
    res["A"] = round(_net(pa, bars[fwd]["c"]) * 100, 3)

    def wait_hit(cond):
        """返回首次满足 cond 的索引，无则 None。"""
        for j in range(i0 + 1, end + 1):
            if cond(j):
                return j
        return None

    # B 等突破 T1：收盘站上 T1
    jb = wait_hit(lambda j: t1 is not None and bars[j]["c"] >= t1)
    if jb is not None and jb + HOLD < len(bars):
        res["B"] = round(_net(bars[jb]["c"], bars[jb + HOLD]["c"]) * 100, 3)
        res["B_wait"] = jb - i0
    else:
        res["B"] = None

    # C 等回踩 ZG：最低价触及 ZG，按 ZG 成交
    jc = wait_hit(lambda j: zg is not None and bars[j]["l"] <= zg)
    if jc is not None and jc + HOLD < len(bars):
        res["C"] = round(_net(zg, bars[jc + HOLD]["c"]) * 100, 3)
        res["C_wait"] = jc - i0
    else:
        res["C"] = None

    # D 等回踩信号价
    jd = wait_hit(lambda j: bars[j]["l"] <= sig_px)
    if jd is not None and jd + HOLD < len(bars):
        res["D"] = round(_net(sig_px, bars[jd + HOLD]["c"]) * 100, 3)
        res["D_wait"] = jd - i0
    else:
        res["D"] = None

    # E 缩量回踩 ZG。缩量用 volume 就够——同一只票内部比较，不跨票，
    # 量纲问题不影响；跨票比成交额才需要 amount 列。
    def voltaic(j):
        if zg is None or bars[j]["l"] > zg:
            return False
        prev = [b["v"] for b in bars[max(0, j - 5): j] if b["v"]]
        return bool(prev) and bars[j]["v"] < 0.8 * (sum(prev) / len(prev))

    je = wait_hit(voltaic)
    if je is not None and je + HOLD < len(bars):
        res["E"] = round(_net(zg, bars[je + HOLD]["c"]) * 100, 3)
        res["E_wait"] = je - i0
    else:
        res["E"] = None
    # F 回踩获支撑后确认：跌破 ZG 附近但当日收在 ZG 上方（拉回来了），
    # 这才是「回踩不破」的完成态。规则 C 只等「触价」是错的——触价那一刻
    # 结构正在被测试，不是被确认。
    def support(j):
        if zg is None:
            return False
        return (bars[j]["l"] <= zg * 1.02 and bars[j]["l"] >= zg * 0.99
                and bars[j]["c"] > zg)

    jf = wait_hit(support)
    if jf is not None and jf + HOLD < len(bars):
        res["F"] = round(_net(bars[jf]["c"], bars[jf + HOLD]["c"]) * 100, 3)
        res["F_wait"] = jf - i0
    else:
        res["F"] = None

    # G 次日开盘买（最接近真实下单：信号当晚决策，次日开盘执行）
    res["G"] = round(_net(bars[i0 + 1]["o"], bars[fwd]["c"]) * 100, 3)
    return res


def summarize(rows, key, label):
    v = np.array([r[key] for r in rows if r.get(key) is not None], float)
    n_all = len(rows)
    if len(v) == 0:
        return None
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 and v.std(ddof=1) else 0
    # 总贡献：未触发的按 0 计（不做单 = 不盈亏）
    total = v.sum()
    return {"label": label, "n": len(v), "trigger_rate_pct": round(len(v) / n_all * 100, 1),
            "mean_pct": round(float(v.mean()), 3), "median_pct": round(float(np.median(v)), 3),
            "win_pct": round(float((v > 0).mean() * 100), 1),
            "t": round(float(t), 2), "sum_pct": round(float(total), 1),
            "sum_per_100": round(float(total) / n_all * 100, 1)}


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
                recs.append(r)
    if args.limit:
        recs = recs[: args.limit]
    print("样本 %d 笔已到期 pass 信号\n" % len(recs))

    jobs = [{"code": r["code"], "entry_date": r.get("entry_date"),
             "detect_date": r.get("detect_date"), "sig_dt": r.get("sig_dt"),
             "entry_px": r.get("entry_px"), "type": r.get("type")} for r in recs]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for k, res in enumerate(ex.map(_one, jobs, chunksize=20)):
            if res:
                rows.append(res)
            if (k + 1) % 200 == 0:
                print("  ...%d/%d" % (k + 1, len(jobs)))
    print("\n可算 %d 笔（%.1f%%）\n" % (len(rows), len(rows) / len(jobs) * 100))

    print("=" * 86)
    print("四种入场规则对比（持有 %d 日，已扣 20bp 往返）" % HOLD)
    print("=" * 86)
    print("%-12s %6s %8s %9s %9s %8s %7s %10s"
          % ("规则", "触发", "触发率", "均收益", "中位", "胜率", "t", "每百单总收益"))
    out = []
    for k, lab in (("G", "G 次日开盘买"), ("A", "A 次日收盘买"),
                   ("D", "D 等回踩信号价"), ("B", "B 等突破T1"),
                   ("F", "F 回踩获支撑"), ("C", "C 等触ZG"),
                   ("E", "E 缩量回踩ZG")):
        s = summarize(rows, k, lab)
        if not s:
            continue
        out.append(s)
        print("%-12s %6d %7.1f%% %8.2f%% %8.2f%% %7.1f%% %7.2f %9.1f%%"
              % (lab, s["n"], s["trigger_rate_pct"], s["mean_pct"],
                 s["median_pct"], s["win_pct"], s["t"], s["sum_per_100"]))

    base = out[0] if out else None
    if base:
        print("\n相对「立即买」的改进：")
        for s in out[1:]:
            print("  %-12s 单笔 %+6.2fpp  每百单总收益 %+6.1fpp  触发率 %.0f%%"
                  % (s["label"], s["mean_pct"] - base["mean_pct"],
                     s["sum_per_100"] - base["sum_per_100"], s["trigger_rate_pct"]))

    # 等待天数分布：这条决定「池子要盯多久」
    print("\n触发所需交易日（中位 / P90）：")
    for k, lab in (("B", "等突破T1"), ("C", "等触ZG"), ("D", "等回踩信号价"),
                   ("F", "回踩获支撑")):
        w = [r["%s_wait" % k] for r in rows if r.get("%s_wait" % k) is not None]
        if w:
            print("  %-12s 中位 %d 日  P90 %d 日  未触发 %d 笔"
                  % (lab, int(np.median(w)), int(np.percentile(w, 90)),
                     len(rows) - len(w)))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n": len(rows), "hold": HOLD, "wait": WAIT,
                   "rules": out}, f, ensure_ascii=False, indent=1)
    print("\n→ %s" % OUT)


if __name__ == "__main__":
    main()
