# -*- coding: utf-8 -*-
"""hf_feat —— 入场端特征能不能把左尾挑出来（只出数，不下结论）。

## 为什么要有这个脚本

出场侧已经判死：22 条规则在按日聚类后全部落进噪声带，同窗空仓口径下
「死持不动」最好（`2026-11-08_出场规则_v1.md`）。剩下的空间只在入场端：
神仙上界 5,117bp，最好的规则只吃到 280bp。

但"把左尾从 20.7% 压到 10%"这个提法有一个隐藏前提：**入场时能认出左尾**。
本脚本就是查这个前提，而不是先去建模型。

## 口径

- 逐笔结果**不重算**，直接读 `hf_path.py --dump` 落盘的 `portal/hf_trades_path.jsonl`。
  重算就会有两套口径，早晚对不上。
- 特征全部来自信号文件，且都是**入场前可得**（detect_date 当天及之前）：
  `type / lag / depth_bars / mbei / vv / tbe / beta`。
  `bei / bei2 / conf / div_area / div_dif / pv_* / npv` 在这个文件里 **100% 为空**，
  所以本轮不可能拿它们做条件。
- 分组沿用 hf_path：按 250 日毛终值切右尾 >+20% / 中性 / 左尾 <−10%。
- 显著性一律**按入场日聚类**：同一天进场的几十只票共享同一次冲击。
  两组比较（某桶 vs 其余）用「日内先算两组均值之差，再对日序列求 t」，
  不是把 6,903 笔当独立样本。

## 输出

`portal/hf_feat.json`：
- `features[]`：每个特征的每个桶 → n / 左尾率 / 右尾率 / A(250) / 桶内聚类 t /
  **剔除该桶后 A 的变化 ΔA 与 Δt**（ΔA 才是"砍掉它值不值"的直接答案）
- `best`：按 |Δt| 排序的前几名
- `coverage`：特征非空率（先看有多少能用的，再看准不准）

用法::

    python -X utf8 tools/research/hf_path.py --dump hf_trades_path.jsonl
    python -X utf8 tools/research/hf_feat.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_path as HP          # noqa: E402  聚类 t 只留一份实现

ROOT = HP.ROOT
SIG = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
TRADES = os.path.join(ROOT, "portal", "hf_trades_path.jsonl")
OUT = os.path.join(ROOT, "portal", "hf_feat.json")

CAP = 250
G_UP, G_DN = 20.0, -10.0       # 与 hf_path 一致

# 本文件里真正有值的字段（其余 100% 为空，写在这里是为了让"为什么没用它们"有据可查）
FEAT_CAT = ("type", "mbei", "vv", "tbe")
FEAT_NUM = ("lag", "beta", "depth_bars")
EMPTY_FIELDS = ("bei", "bei2", "conf", "div_area", "div_dif", "pv_state",
                "pv_fv", "npv")


# ---------------------------------------------------------------- 载入
def load_trades():
    rows = []
    for line in open(TRADES, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_feats():
    """{ (code, detect_date8) : {field: value} }，只收 1买。"""
    out = {}
    keep = FEAT_CAT + FEAT_NUM + ("price", "entry_px")
    for line in open(SIG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") != "1买":
            continue
        d = str(r.get("detect_date") or "").replace("-", "")
        c = r.get("code")
        if not c or len(d) != 8:
            continue
        out[(c, d)] = {k: r.get(k) for k in keep}
    return out


def join(trades, feats):
    """逐笔 join。**join 不上的要计数**，不能静默丢掉。"""
    ok, miss = [], 0
    for t in trades:
        f = feats.get((t["c"], t["d0"]))
        if f is None:
            miss += 1
            continue
        r = dict(t)
        r.update(f)
        ok.append(r)
    return ok, miss


# ---------------------------------------------------------------- 统计
def _mean(xs):
    return statistics.mean(xs) if xs else None


def cluster_diff_t(vals_b, days_b, vals_r, days_r):
    """两组比较的聚类 t：每天先算「桶内均值 − 其余均值」，再对日序列求 t。

    不做这一步就会犯第十九轮的错：把"同一天几十只票一起跌"当成几十个证据。
    某一天只有一组有样本时，该天不参与（无法配对），但要报出用了多少天。
    """
    byb, byr = {}, {}
    for v, d in zip(vals_b, days_b):
        byb.setdefault(d, []).append(v)
    for v, d in zip(vals_r, days_r):
        byr.setdefault(d, []).append(v)
    diffs = []
    for d in set(byb) & set(byr):
        diffs.append(statistics.mean(byb[d]) - statistics.mean(byr[d]))
    if len(diffs) < 3:
        return None, len(diffs)
    sd = statistics.pstdev(diffs)
    if sd == 0:
        return None, len(diffs)
    return statistics.mean(diffs) / (sd / math.sqrt(len(diffs))), len(diffs)


def bucket_stats(rows, name, buckets, key="a"):
    """buckets: [(label, predicate)]。返回每个桶的画像 + 剔除该桶后的 ΔA。

    key="a"  → 按个股 β 调整的超额（与 hf_path 一致）
    key="a1" → β=1 的超额（不按个股 β 调整）。两个都要看：A 的定义里带 β，
               用 β 分组去解释 A 会是循环论证。
    """
    all_v = [r[key] for r in rows if r.get(key) is not None]
    base = _mean(all_v)
    out = []
    for lab, pred in buckets:
        hit = [r for r in rows if pred(r)]
        rest = [r for r in rows if not pred(r)]
        if len(hit) < 30 or len(rest) < 30:
            continue
        hv = [r[key] for r in hit if r.get(key) is not None]
        rv = [r[key] for r in rest if r.get(key) is not None]
        if not hv or not rv:
            continue
        td, nd = cluster_diff_t(hv, [r["ed"] for r in hit if r.get(key) is not None],
                                rv, [r["ed"] for r in rest if r.get(key) is not None])
        left = sum(1 for r in hit if r["ret"] < G_DN) / len(hit)
        right = sum(1 for r in hit if r["ret"] >= G_UP) / len(hit)
        out.append({
            "feature": name, "bucket": lab, "key": key,
            "n": len(hit), "share": round(100.0 * len(hit) / len(rows), 1),
            "left_pct": round(100 * left, 1),
            "right_pct": round(100 * right, 1),
            # 落盘的 ret / a 已经是百分点，换 bp 只再乘 100（乘 1e4 会大 100 倍）
            "A_bp": round(100 * _mean(hv), 0),
            "A_rest_bp": round(100 * _mean(rv), 0),
            # 砍掉这个桶之后样本整体的 A 变成多少 —— 这是"值不值得砍"的直接答案
            "dA_bp": round(100 * (_mean(rv) - base), 0),
            "dA_t": round(-td, 2) if td is not None else None,
            "dA_ndays": nd,
        })
    return out


def num_buckets(field, rows, qs=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)):
    """连续变量按分位切桶，边界取实际值，标签直接用区间。"""
    vals = sorted(r[field] for r in rows if isinstance(r.get(field), (int, float)))
    if len(vals) < 100:
        return []
    edges = []
    for q in qs:
        i = min(len(vals) - 1, int(round((len(vals) - 1) * q)))
        edges.append(vals[i])
    edges = sorted(set(edges))
    out = []
    nseg = len(edges) - 1
    for i in range(nseg):
        lo, hi = edges[i], edges[i + 1]
        if lo == hi:
            continue
        last = (i == nseg - 1)

        def pred(r, lo=lo, hi=hi, last=last, field=field):
            v = r.get(field)
            if not isinstance(v, (int, float)):
                return False
            return lo <= v <= hi if last else lo <= v < hi

        out.append(("%g~%g" % (lo, hi), pred))
    return out


def run(write=True):
    trades = load_trades()
    feats = load_feats()
    rows, miss = join(trades, feats)
    print("逐笔 %d，join 上 %d，join 不上 %d（%.1f%%）"
          % (len(trades), len(rows), miss, 100.0 * miss / max(1, len(trades))))
    if not rows:
        raise SystemExit("没有可用样本")

    res = {
        "cap": CAP,
        "n": len(rows),
        "n_join_miss": miss,
        "coverage": {},
        "features": [],
        "best": [],
    }
    # 非空率：先看有多少能用的
    for f in FEAT_CAT + FEAT_NUM:
        nn = sum(1 for r in rows if r.get(f) is not None)
        res["coverage"][f] = round(100.0 * nn / len(rows), 1)
    for f in EMPTY_FIELDS:
        res["coverage"][f] = 0.0

    blocks = []

    def add(name, bk):
        """同一个分桶跑两遍：β 调整口径(a) 与 β=1 口径(a1)，再按桶合并。"""
        std = bucket_stats(rows, name, bk, key="a")
        raw = {x["bucket"]: x for x in bucket_stats(rows, name, bk, key="a1")}
        for x in std:
            y = raw.get(x["bucket"])
            x["A1_bp"] = y["A_bp"] if y else None
            x["dA1_bp"] = y["dA_bp"] if y else None
            x["dA1_t"] = y["dA_t"] if y else None
        blocks.append(std)

    # 类别型：type / mbei / vv / tbe
    for f in ("type", "mbei", "vv", "tbe"):
        vals = sorted({str(r.get(f)) for r in rows if r.get(f) is not None})
        if len(vals) < 2:
            continue
        bk = [(v, (lambda v=v, f=f: lambda r: str(r.get(f)) == v)()) for v in vals]
        add(f, bk)

    # 连续型：分位切
    for f in FEAT_NUM:
        bk = num_buckets(f, rows)
        if bk:
            add(f, bk)

    flat = [x for b in blocks for x in b]
    res["features"] = flat
    res["best"] = sorted([x for x in flat if x["dA_t"] is not None],
                         key=lambda x: -abs(x["dA_t"]))[:10]

    if write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    # ---------------- 控制台
    print("\n非空率：" + "  ".join("%s %.0f%%" % (k, v) for k, v in res["coverage"].items()))
    print("\n%-8s %-12s %5s %6s %6s %7s %7s %6s %7s %7s %6s"
          % ("特征", "桶", "n", "左尾%", "右尾%", "A(bp)", "剃后ΔA", "Δt",
             "A(β=1)", "剃后ΔA1", "Δt1"))
    for x in flat:
        print("%-8s %-12s %5d %6.1f %6.1f %7.0f %7.0f %6s %7s %7s %6s"
              % (x["feature"], x["bucket"], x["n"], x["left_pct"], x["right_pct"],
                 x["A_bp"], x["dA_bp"],
                 "—" if x["dA_t"] is None else "%.2f" % x["dA_t"],
                 "—" if x["A1_bp"] is None else "%.0f" % x["A1_bp"],
                 "—" if x["dA1_bp"] is None else "%.0f" % x["dA1_bp"],
                 "—" if x["dA1_t"] is None else "%.2f" % x["dA1_t"]))
    print("\n按 |Δt| 排序（Δt = 剔除该桶后剩余样本 A 的变化的聚类 t）：")
    for x in res["best"]:
        print("  %-8s %-12s n=%4d  ΔA=%+5.0fbp Δt=%+.2f | ΔA1=%+5s Δt1=%+.2f"
              % (x["feature"], x["bucket"], x["n"], x["dA_bp"], x["dA_t"],
                 "—" if x["dA1_bp"] is None else "%+.0f" % x["dA1_bp"],
                 0.0 if x["dA1_t"] is None else x["dA1_t"]))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    run(write=not a.no_write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
