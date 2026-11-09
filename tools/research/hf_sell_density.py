# -*- coding: utf-8 -*-
"""hf_sell_density：卖点密度审计（第十六轮 G1）

**为什么单独量这一件事**

§4 那个卖点离场对照里，`days_med = 20`——中位持期就是满期。意思是
**卖点在 20 日窗口内几乎不触发**，所以 B/C/D 三种卖点离场政策和 A 满期
退出的差别只有不到 0.1pp。但那个实验只告诉我们"没触发"，没告诉我们
**到底要等多久**。

这个数决定 hold20 的性质：

    若 1买 之后第一个卖点的中位间隔 ≈ 15~25 日
        → hold20 就是「结构走完」，持期上限是结构派生的，不是加的
    若中位 ≈ 60~200 日
        → hold20 是硬砍在半路，纯正缠论的闭环根本没闭合
    若很大比例永远不出卖点
        → 卖点不能当离场依据，这才是真的

**口径**

    买点      只 1买
    计时起点  detect_date（确认日，实盘唯一能用来交易的那天）
            ✗ 不用 dt（结构日）——那是事后才知道的
    终点      同标的之后第一个卖点的 detect_date（1卖/2卖/3卖 任取）
    间隔      交易日数（用 000985.CSI 的 trade_date 做日历）
    右删失    观察窗内没等到卖点的，单独计 censored，不填 0、不当淘汰

用法::

    python -X utf8 tools/research/hf_sell_density.py
    python -X utf8 tools/research/hf_sell_density.py --degrade   # 退化副本
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
IDX = os.path.join(ROOT, "quant_backtest", "data", "tdx", "ts_index_daily")
OUT = os.path.join(ROOT, "portal", "hf_sell_density.json")

BUY = "1买"
SELL = ("1卖", "2卖", "3卖")
SELL1 = ("1卖",)

HOLD_REF = (5, 10, 20, 30, 60)


def load_calendar():
    """交易日历：主判指数 000985.CSI 的 trade_date 全集。

    统一成 8 位无横线（`20231009`）：信号侧 detect_date 是带横线的
    `2023-10-09`，两边不转换就会 100% 落不进日历，表现为"全部右删失"。
    """
    import pandas as pd

    rows = []
    for p in sorted(glob.glob(os.path.join(IDX, "part_*.parquet"))):
        df = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        sub = df[df["ts_code"] == "000985.CSI"]
        if len(sub):
            rows.extend(_n8(x) for x in sub["trade_date"].tolist())
    cal = sorted(set(rows))
    return cal, {d: i for i, d in enumerate(cal)}


def _n8(d):
    return str(d).replace("-", "")


def load_signals(degrade=False):
    """按 code 聚合：1买 的确认日集合 + 卖点（确认日, 类型）列表。"""
    per = defaultdict(lambda: {"buy": set(), "sell": []})
    n_line = 0
    n_keep = 0
    miss_detect = 0
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_line += 1
            r = json.loads(line)
            t = r.get("type")
            if t != BUY and t not in SELL:
                continue
            if degrade and t in SELL:
                continue  # 退化档：删掉全部卖点 → 必须 100% 右删失
            c = r.get("code")
            d = r.get("detect_date")
            if not c or not d:
                miss_detect += 1
                continue
            n_keep += 1
            if t == BUY:
                per[c]["buy"].add(_n8(d))
            else:
                per[c]["sell"].append((_n8(d), t))
    for c in per:
        per[c]["sell"].sort()
    return per, {"n_line": n_line, "n_keep": n_keep, "miss_detect": miss_detect}


def _gap(cal_idx, cal, d0, d1):
    """两个日期之间的交易日数。两端都必须落在日历里，否则返回 None。"""
    i0 = cal_idx.get(d0)
    i1 = cal_idx.get(d1)
    if i0 is None or i1 is None:
        return None
    return i1 - i0


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _dist(gaps, censored, total):
    s = sorted(gaps)
    out = {
        "n_total": total,
        "n_with": len(s),
        "n_censored": censored,
        "pct_with": round(100.0 * len(s) / total, 2) if total else None,
        "mean": round(statistics.mean(s), 2) if s else None,
        "p10": round(_pct(s, 0.10), 1) if s else None,
        "p25": round(_pct(s, 0.25), 1) if s else None,
        "median": round(_pct(s, 0.50), 1) if s else None,
        "p75": round(_pct(s, 0.75), 1) if s else None,
        "p90": round(_pct(s, 0.90), 1) if s else None,
    }
    # 「在 N 日内等到卖点」的比例——直接回答「hold20 是等到了还是砍掉了」
    for h in HOLD_REF:
        out["within_%d" % h] = round(100.0 * sum(1 for g in s if g <= h) / total, 2) if total else None
    return out


def run(degrade=False, write=True):
    cal, cal_idx = load_calendar()
    if not cal:
        raise SystemExit("日历为空：%s" % IDX)
    per, meta = load_signals(degrade=degrade)

    gaps_any, gaps_1s = [], []
    cens_any = cens_1s = 0
    total = 0
    n_out_of_cal = 0
    by_year = defaultdict(lambda: {"n": 0, "with": 0, "gaps": []})

    for code, g in per.items():
        if not g["buy"]:
            continue
        sells = g["sell"]
        sell_dates = [d for d, _ in sells]
        sell1_dates = [d for d, t in sells if t in SELL1]
        for d0 in sorted(g["buy"]):
            total += 1
            yr = d0[:4]
            by_year[yr]["n"] += 1

            # 任一卖点：第一个严格晚于确认日的
            j = bisect.bisect_right(sell_dates, d0)
            if j < len(sell_dates):
                gp = _gap(cal_idx, cal, d0, sell_dates[j])
                if gp is None:
                    n_out_of_cal += 1
                else:
                    gaps_any.append(gp)
                    by_year[yr]["gaps"].append(gp)
                    by_year[yr]["with"] += 1
            else:
                cens_any += 1

            # 仅 1卖
            k = bisect.bisect_right(sell1_dates, d0)
            if k < len(sell1_dates):
                gp = _gap(cal_idx, cal, d0, sell1_dates[k])
                if gp is not None:
                    gaps_1s.append(gp)
            else:
                cens_1s += 1

    res = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "src": os.path.relpath(JSONL, ROOT).replace("\\", "/"),
        "degrade": bool(degrade),
        "caliber": {
            "buy": "只 1买",
            "clock": "detect_date（确认日）起算；不用 dt 结构日——那是事后信息",
            "end": "同标的后第一个卖点 detect_date（1卖/2卖/3卖 任取）",
            "unit": "交易日（000985.CSI trade_date 日历）",
            "censor": "观察窗内未等到卖点 → 右删失，单列，不填 0",
            "within_N": "N 个交易日内等到卖点的样本占比（分母含删失）",
        },
        "meta": dict(meta, n_calendar=len(cal), n_codes=len(per), n_out_of_cal=n_out_of_cal),
        "dist_any_sell": _dist(gaps_any, cens_any, total),
        "dist_1sell": _dist(gaps_1s, cens_1s, total),
        "by_year": [
            {
                "year": y,
                "n": v["n"],
                "with": v["with"],
                "pct_with": round(100.0 * v["with"] / v["n"], 2) if v["n"] else None,
                "median": round(_pct(sorted(v["gaps"]), 0.50), 1) if v["gaps"] else None,
            }
            for y, v in sorted(by_year.items())
        ],
        "note": "hold20 是不是硬砍，看 dist_any_sell.within_20 与 median",
    }

    if write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    d = res["dist_any_sell"]
    print("样本 %d ｜ 等到卖点 %d (%.1f%%) ｜ 右删失 %d" % (
        d["n_total"], d["n_with"], d["pct_with"] or 0, d["n_censored"]))
    print("间隔(交易日)  中位 %s ｜ p25 %s ｜ p75 %s ｜ p90 %s ｜ 均值 %s" % (
        d["median"], d["p25"], d["p75"], d["p90"], d["mean"]))
    for h in HOLD_REF:
        print("  ≤%2d 日内等到：%.2f%%" % (h, d["within_%d" % h] or 0))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    run(degrade=a.degrade, write=not a.no_write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
