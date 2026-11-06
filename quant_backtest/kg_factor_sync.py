# -*- coding: utf-8 -*-
"""因子库 -> 知识图谱 同步管线（L1：因子资产图）。

背景：张松/FACTOR1 已把 187 个因子跑完全量评估（research/reports/
      factor_b1_full_eval_*.json / factor_b2_full_eval_*.json），但这些因子
      的元数据只存在于 JSON 报告里，图谱里没有任何 :Factor 节点。
      本管线把「因子」实体化进 Neo4j，形成 因子-族-数据源 三层资产图。

只读 JSON，不改 factor_lib、不改训练代码、不动任何既有标签。

建图内容
--------
节点：
  :Factor          187 个（name 为键）+ IC/t/IR/逐年 IC/衰减分类/证据等级
  :FactorFamily    6 个（Alpha101/流动性/风险/动量/反转/规模）
  :FactorDataSource 1 个（tushare.factor_list）
边（必带 validStart/validEnd，遵守时序图谱铁律）：
  (Factor)-[:BELONGS_TO]->(FactorFamily)
  (Factor)-[:SHARES_SOURCE]->(FactorDataSource)

证据等级（Admiralty 简化）
--------------------------
  全部为 2023-01-03~2026-09-29 全样本 in-sample，无样本外 -> 最高 C2。
  脚本显式写 in_sample=True / pit_verified=False，禁止下游当 A 级用。

用法
----
  python -X utf8 quant_backtest/kg_factor_sync.py                # dry-run，只统计
  python -X utf8 quant_backtest/kg_factor_sync.py --apply        # 写图谱
  python -X utf8 quant_backtest/kg_factor_sync.py --out C:/Temp/factor_graph.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_REPORTS = os.path.join(REPO, "research", "reports")

# 衰减判定：2026 相对 2023 的 |IC| 保留比例
DECAY_RATIO = 0.5


def _load_latest(reports_dir):
    """按「文件名去日期」分组，每组取 generated_at 最新的一份。

    这样 b1 / b2 / b3 / b1_prior 视为不同批次全部载入（不同批次因子集不同），
    同一批次内只保留最新评估，避免旧版覆盖新版。
    """
    best = {}
    for path in glob.glob(os.path.join(reports_dir, "factor_b*_full_eval_*.json")):
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]
        parts = stem.split("_")
        if parts and parts[-1].isdigit():
            batch = "_".join(parts[:-1])
        else:
            batch = stem
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[skip] %s: %s" % (base, e), file=sys.stderr)
            continue
        gen = str((d.get("meta") or {}).get("generated_at") or "")
        cur = best.get(batch)
        if cur is None or gen > cur[0]:
            best[batch] = (gen, path, d)
    return sorted((v[0], v[1], v[2]) for v in best.values())


def _year_ic(one):
    """{2023: mean, ...}。

    by_year 内部是平铺结构 {n_days, mean, std, ir, t, positive_rate}，
    个别版本可能包一层 ic_directed / ic，这里三种都兼容。
    """
    out = {}
    by = one.get("by_year") or {}
    for y, stat in by.items():
        if not isinstance(stat, dict):
            continue
        src = stat.get("ic_directed") or stat.get("ic") or stat
        m = src.get("mean")
        if m is not None:
            out[str(y)] = float(m)
    return out


def _decay_class(yic):
    """stable / decay / flip / weak / insufficient。"""
    years = sorted(yic)
    if len(years) < 2:
        return "insufficient"
    first, last = yic[years[0]], yic[years[-1]]
    if first == 0:
        return "insufficient"
    if first * last < 0:
        return "flip"
    ratio = abs(last) / abs(first)
    if ratio < DECAY_RATIO:
        return "decay"
    if ratio < 0.8:
        return "soft_decay"
    return "stable"


def build_rows(reports):
    """返回 (factors, families, sources, edges_belongs, edges_source, meta)。"""
    factors = {}
    gen_at = ""
    for gen, path, d in reports:
        gen_at = max(gen_at, gen)
        for name, one in (d.get("factors") or {}).items():
            if not isinstance(one, dict):
                continue
            ic = one.get("ic_directed") or one.get("ic") or {}
            yic = _year_ic(one)
            row = {
                "name": name,
                "family": str(one.get("family") or "unknown"),
                "kind": str(one.get("kind") or ""),
                "direction": int(one.get("direction") or 0),
                "description": str(one.get("description") or "")[:200],
                "source": str(one.get("source") or "unknown"),
                "n_obs": int(one.get("n_obs") or 0),
                "n_days": int(one.get("n_days") or 0),
                "ic_mean": round(float(ic.get("mean") or 0.0), 6),
                "ic_t": round(float(ic.get("t") or 0.0), 3),
                "ic_ir": round(float(ic.get("ir") or 0.0), 4),
                "ic_pos_rate": round(float(ic.get("positive_rate") or 0.0), 4),
                "abs_ic": round(abs(float(ic.get("mean") or 0.0)), 6),
            }
            for _y, _v in yic.items():
                row["year_ic_%s" % _y] = round(_v, 6)
            row["decay_class"] = _decay_class(yic)
            # 证据等级：全样本 in-sample，无样本外 -> C2（不可当 A 级）
            row["grade"] = "C2"
            row["in_sample"] = True
            row["pit_verified"] = False
            factors[name] = row

    fam_count = {}
    src_count = {}
    for f in factors.values():
        fam_count[f["family"]] = fam_count.get(f["family"], 0) + 1
        src_count[f["source"]] = src_count.get(f["source"], 0) + 1

    families = [
        {"name": k, "n_factors": v, "updated_at": gen_at}
        for k, v in sorted(fam_count.items())
    ]
    sources = [
        {"name": k, "n_factors": v, "updated_at": gen_at}
        for k, v in sorted(src_count.items())
    ]

    valid_start = (gen_at or datetime.now().isoformat())[:10]
    e_belongs = [
        {
            "src": f["name"],
            "dst": f["family"],
            "validStart": valid_start,
            "validEnd": None,
            "source": "kg_factor_sync",
            "updated_at": gen_at,
        }
        for f in factors.values()
    ]
    e_source = [
        {
            "src": f["name"],
            "dst": f["source"],
            "validStart": valid_start,
            "validEnd": None,
            "source": "kg_factor_sync",
            "updated_at": gen_at,
        }
        for f in factors.values()
    ]
    return factors, families, sources, e_belongs, e_source, {"generated_at": gen_at}


def summarize(factors, families, sources, meta):
    rows = list(factors.values())
    strong = sorted(rows, key=lambda r: -r["abs_ic"])[:10]
    decay = [r for r in rows if r["decay_class"] in ("decay", "flip")]
    out = {
        "generated_at": meta.get("generated_at"),
        "n_factors": len(rows),
        "n_families": len(families),
        "n_sources": len(sources),
        "median_abs_ic": round(
            sorted(r["abs_ic"] for r in rows)[len(rows) // 2], 6
        )
        if rows
        else 0,
        "families": {f["name"]: f["n_factors"] for f in families},
        "sources": {s["name"]: s["n_factors"] for s in sources},
        "decay_counts": {},
        "top10_abs_ic": [
            {
                "name": r["name"],
                "family": r["family"],
                "ic": r["ic_mean"],
                "t": r["ic_t"],
                "decay": r["decay_class"],
            }
            for r in strong
        ],
        "decay_list": [
            {
                "name": r["name"],
                "family": r["family"],
                "ic": r["ic_mean"],
                "decay": r["decay_class"],
            }
            for r in sorted(decay, key=lambda x: -x["abs_ic"])[:15]
        ],
    }
    for r in rows:
        k = r["decay_class"]
        out["decay_counts"][k] = out["decay_counts"].get(k, 0) + 1
    return out


def apply_to_graph(factors, families, sources, e_belongs, e_source):
    """写 Neo4j。延迟 import，dry-run 时不依赖 driver。"""
    sys.path.insert(0, HERE)
    from kg_client import KgClient

    cli = KgClient()
    try:
        n_f = cli.upsert_nodes("Factor", ["name"], list(factors.values()))
        n_fam = cli.upsert_nodes("FactorFamily", ["name"], families)
        n_src = cli.upsert_nodes("FactorDataSource", ["name"], sources)
        n_eb = cli.upsert_edge(
            "Factor", "name", "FactorFamily", "name", "BELONGS_TO", e_belongs
        )
        n_es = cli.upsert_edge(
            "Factor", "name", "FactorDataSource", "name", "SHARES_SOURCE", e_source
        )
        print(
            "写入完成: Factor=%d Family=%d Source=%d BELONGS_TO=%d SHARES_SOURCE=%d"
            % (n_f, n_fam, n_src, n_eb, n_es)
        )
        with cli._session() as s:
            r = s.run("MATCH (n:Factor) RETURN count(n) AS c").single()
            print("图谱校验: :Factor 节点数 = %d" % r["c"])
    finally:
        cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-dir", default=DEFAULT_REPORTS)
    ap.add_argument("--apply", action="store_true", help="真正写图谱（默认 dry-run）")
    ap.add_argument("--out", default="", help="统计结果输出路径")
    a = ap.parse_args()

    reports = _load_latest(a.reports_dir)
    if not reports:
        print("未找到 factor_b*_full_eval_*.json，退出", file=sys.stderr)
        return 2
    for gen, path, d in reports:
        print("载入 %s  generated_at=%s  n=%d"
              % (os.path.basename(path), gen, len(d.get("factors") or {})))

    factors, families, sources, e_belongs, e_source, meta = build_rows(reports)
    stat = summarize(factors, families, sources, meta)

    print("\n=== 因子资产概览 ===")
    print("因子 %d 个｜族 %d｜数据源 %d｜|IC| 中位数 %.4f"
          % (stat["n_factors"], stat["n_families"], stat["n_sources"],
             stat["median_abs_ic"]))
    print("族分布: " + ", ".join("%s %d" % (k, v) for k, v in stat["families"].items()))
    print("源分布: " + ", ".join("%s %d" % (k, v) for k, v in stat["sources"].items()))
    print("衰减分类: " + ", ".join("%s %d" % (k, v)
                                for k, v in sorted(stat["decay_counts"].items())))

    print("\n=== |IC| 前 10 ===")
    for r in stat["top10_abs_ic"]:
        print("  %-22s %-16s IC=%+.4f t=%6.2f %s"
              % (r["name"], r["family"], r["ic"], r["t"], r["decay"]))

    if stat["decay_list"]:
        print("\n=== 衰减/翻转（前 15） ===")
        for r in stat["decay_list"]:
            print("  %-22s %-16s IC=%+.4f %s"
                  % (r["name"], r["family"], r["ic"], r["decay"]))

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(stat, fh, ensure_ascii=False, indent=2)
        print("\n统计已写入 %s" % a.out)

    if a.apply:
        print("\n--- 写图谱 ---")
        apply_to_graph(factors, families, sources, e_belongs, e_source)
    else:
        print("\n[dry-run] 未写图谱。确认后加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
