# -*- coding: utf-8 -*-
"""因子五维标注器 v1（人 / 地 / 事 / 物 / 组织）。

用途：把 research/reports/factor_b*_full_eval_*.json 里的因子，按语义标注到
「人地事物组织」五维，并同时拆解三类冗余：

  1. dim            —— 该因子测的是哪一维
  2. window_family  —— 是否只是同公式换窗口（如 avg_turnover_21d / _63d）
  3. oos            —— 样本外是否存活（OOS |t|>=2 且与 IS 同号）

设计原则：
  · 只读 JSON，不写图谱、不碰 factor_lib、不触发任何计算
  · 映射规则集中在 DIM_RULES，可增删；未命中回落「物」
  · 输出 JSON（机读）+ 摘要（人读），默认打印摘要

为什么需要它：
  271 个因子看上去很多，但在五维坐标系里可能只占一维。本脚本的产出
  「空白维度清单」就是下一批因子该挖的方向。

用法：
    python tools/research/factor_dim_map.py --reports research/reports
    python tools/research/factor_dim_map.py --out /tmp/dim.json
"""

from __future__ import print_function

import argparse
import collections
import glob
import json
import os
import re
import sys

# ── 五维映射规则（顺序即优先级：人 > 地 > 事 > 组织 > 物）──────────────────
# 命中即归该维；都不命中归「物」（价量/财务/产品是默认盘）
DIM_RULES = [
    ("人", [
        r"person", r"holder", r"shareholder", r"exec", r"manager", r"staff",
        r"employee", r"chairman", r"secretary", r"insider", r"hotmoney",
        r"fund_hold", r"inst_hold", r"north_bound",
    ]),
    ("地", [
        r"region", r"province", r"city", r"geo", r"area", r"location",
        r"cluster_",
    ]),
    ("事", [
        r"event", r"news", r"sentiment", r"anno", r"limit_up", r"limit_down",
        r"up_down", r"dragon", r"seat_", r"lhb", r"flow_", r"announce",
    ]),
    ("组织", [
        r"industry", r"sw_l1", r"index", r"_000300", r"_000001", r"_000905",
        r"_399006", r"org", r"institution", r"board", r"segment",
    ]),
]
DIM_ORDER = ("物", "组织", "事", "地", "人")


def dim_of(name):
    """因子名 -> 五维之一。"""
    for dim, pats in DIM_RULES:
        for p in pats:
            if re.search(p, name, re.I):
                return dim
    return "物"


# ── 同公式换窗口：去掉窗口参数与指数代码，得到基础公式名 ──────────────────
def window_family(name):
    """avg_turnover_21d -> avg_turnover ; beta_1320d_000001 -> beta"""
    b = re.sub(r"_\d+d", "", name)          # _21d / _1320d
    b = re.sub(r"_\d{6}", "", b)            # _000300 / _000001
    b = re.sub(r"_+", "_", b).strip("_")
    return b or name


# ── 因子名里显式带窗口参数的，才算「可归族」 ──────────────────────────────
_WIN = re.compile(r"_\d+d")


def has_explicit_window(name):
    return bool(_WIN.search(name))


def _num(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def oos_stat(one):
    """返回 (IS t, OOS t, 是否存活, split_date)。存活 = 同号 且 |OOS t|>=2。"""
    o = one.get("is_oos")
    if not isinstance(o, dict):
        return None, None, None, None
    I = o.get("IS") or {}
    O = o.get("OOS") or {}
    ti, to = _num(I.get("t")), _num(O.get("t"))
    alive = None
    if ti is not None and to is not None:
        alive = bool((ti > 0) == (to > 0) and abs(to) >= 2.0)
    return ti, to, alive, o.get("split_date")


def load_factors(reports_dir):
    """读全部 factor_b*_full_eval_*.json，按因子名合并（后读覆盖先读）。"""
    pattern = os.path.join(reports_dir, "factor_b*_full_eval_*.json")
    out, src = {}, {}
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print("[skip] %s: %s" % (os.path.basename(path), exc), file=sys.stderr)
            continue
        for name, one in (d.get("factors") or {}).items():
            out[name] = one
            src[name] = os.path.basename(path)
    return out, src


def build(reports_dir):
    factors, src = load_factors(reports_dir)
    rows = []
    for name, one in factors.items():
        ti, to, alive, split = oos_stat(one)
        fam = window_family(name)
        rows.append({
            "name": name,
            "dim": dim_of(name),
            "window_family": fam,
            "has_explicit_window": has_explicit_window(name),
            "family": one.get("family"),
            "direction": one.get("direction"),
            "ic_mean": _num((one.get("ic") or {}).get("mean")),
            "ic_t": _num((one.get("ic") or {}).get("t")),
            "is_t": ti,
            "oos_t": to,
            "oos_survived": alive,
            "split_date": split,
            "source_report": src.get(name),
        })

    # 聚合
    by_dim = collections.defaultdict(list)
    for r in rows:
        by_dim[r["dim"]].append(r)

    dim_summary = {}
    for d in DIM_ORDER:
        rs = by_dim.get(d) or []
        evald = [r for r in rs if r["oos_survived"] is not None]
        alive = [r for r in evald if r["oos_survived"]]
        dim_summary[d] = {
            "n": len(rs),
            "n_evaluable": len(evald),
            "n_oos_survived": len(alive),
            "oos_survive_rate": round(len(alive) / len(evald), 4) if evald else None,
        }

    # 窗口族
    fams = collections.defaultdict(list)
    for r in rows:
        if r["has_explicit_window"]:
            fams[r["window_family"]].append(r["name"])
    multi = {k: sorted(v) for k, v in fams.items() if len(v) > 1}

    splits = collections.Counter(r["split_date"] for r in rows if r["split_date"])
    base_formulas = sorted({r["window_family"] for r in rows})
    evald_all = [r for r in rows if r["oos_survived"] is not None]

    return {
        "meta": {
            "reports_dir": os.path.abspath(reports_dir),
            "n_factors": len(rows),
            "n_base_formulas": len(base_formulas),
            "dims_present": [d for d in DIM_ORDER if dim_summary[d]["n"] > 0],
            "dims_empty": [d for d in DIM_ORDER if dim_summary[d]["n"] == 0],
        },
        "dim_summary": dim_summary,
        "window_families": {k: {"n": len(v), "members": v}
                            for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))},
        "n_window_redundant": sum(len(v) for v in multi.values()),
        "oos": {
            "n_evaluable": len(evald_all),
            "n_survived": sum(1 for r in evald_all if r["oos_survived"]),
            "n_flipped": sum(1 for r in evald_all
                             if r["is_t"] is not None and r["oos_t"] is not None
                             and (r["is_t"] > 0) != (r["oos_t"] > 0)),
            "survive_rate": round(sum(1 for r in evald_all if r["oos_survived"])
                                  / len(evald_all), 4) if evald_all else None,
            "split_date_dist": dict(splits),
            "split_date_single": len(splits) == 1,
        },
        "factors": sorted(rows, key=lambda r: (DIM_ORDER.index(r["dim"]), r["name"])),
    }


def render(stat):
    m, o, ds = stat["meta"], stat["oos"], stat["dim_summary"]
    L = []
    L.append("=" * 62)
    L.append("因子五维标注 · %d 个因子 / %d 个基础公式" % (m["n_factors"], m["n_base_formulas"]))
    L.append("=" * 62)
    L.append("")
    L.append("【五维分布】")
    for d in DIM_ORDER:
        s = ds[d]
        if s["n"] == 0:
            L.append("  %s维      0 个   ← 空白" % d)
            continue
        pct = s["n"] / m["n_factors"] * 100
        bar = "█" * max(1, int(pct / 2))
        rate = "%.0f%%" % (s["oos_survive_rate"] * 100) if s["oos_survive_rate"] is not None else "n/a"
        L.append("  %s维 %4d 个 (%4.1f%%) %-24s OOS存活 %s (%d/%d)"
                 % (d, s["n"], pct, bar, rate, s["n_oos_survived"], s["n_evaluable"]))
    if m["dims_empty"]:
        L.append("")
        L.append("  ⚠ 空白维度：%s —— 图上这些维度的实体是零利用资产"
                 % "、".join(m["dims_empty"]))
    L.append("")
    L.append("【同公式换窗口】")
    L.append("  基础公式 %d 个（原 %d 个），冗余 %d 个 = %.0f%%"
             % (m["n_base_formulas"], m["n_factors"], stat["n_window_redundant"],
                stat["n_window_redundant"] / m["n_factors"] * 100))
    for k, v in list(stat["window_families"].items())[:8]:
        L.append("    %-22s %d 个: %s%s"
                 % (k, v["n"], ", ".join(v["members"][:3]), " …" if v["n"] > 3 else ""))
    L.append("")
    L.append("【样本外】")
    L.append("  可评 %d 个｜存活 %d 个 (%.1f%%)｜符号翻转 %d 个"
             % (o["n_evaluable"], o["n_survived"],
                (o["survive_rate"] or 0) * 100, o["n_flipped"]))
    if not o["split_date_single"]:
        L.append("  ⚠ split_date 有 %d 个不同值 %s —— 跨批次 OOS 不可直接比较"
                 % (len(o["split_date_dist"]), o["split_date_dist"]))
    L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="因子五维标注（人地事物组织）")
    ap.add_argument("--reports", default="research/reports", help="因子评估 JSON 目录")
    ap.add_argument("--out", default=None, help="输出 JSON 路径（默认只打印摘要）")
    ap.add_argument("--quiet", action="store_true", help="只写文件不打印摘要")
    args = ap.parse_args(argv)

    stat = build(args.reports)
    text = render(stat)
    if not args.quiet:
        print(text)

    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(stat, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print("JSON -> %s (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
