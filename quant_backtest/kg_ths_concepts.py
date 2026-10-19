# -*- coding: utf-8 -*-
"""K264 产业链语义边工程 v1：同花顺概念板块 → 图谱 Concept/MAPS_TO 批量扩展。

数据源：本地仓 ts_ths_index（1664 板块）+ ts_ths_member（37.7 万成分行，
2026-09-24 同步）——零 API 依赖（INTEL_RD_PLAN L4937 判例）。

范围 v1：
  - 仅 type='N'（概念指数）且剔除非主题类（样本股/成份股/融资融券/风格筛选等）
  - 成分 con_code 与图谱 Security.ts_code 对齐（只挂已存在的 Security）
  - Concept 节点 MERGE on name（与既有 56 概念同名即扩展属性，不重复建；
    is_root/level 等既有属性不受 SET += 影响）
  - 边 (Security)-[:MAPS_TO {validStart:'20260924'}]->(Concept)，
    source='ths_member_v1'——与 m4_alias/m4_biztext 链路（validStart=20260929）
    平行可区分，支持按 source 整体回滚。

用法：
  python kg_ths_concepts.py            # dry-run：统计 + 抽样 20 条，不写库
  python kg_ths_concepts.py --commit   # 实写入 Neo4j
"""
import random
import re
import sys

sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
from tdx_read import read_all  # noqa: E402

# 非主题板块名剔除规则（宽基样本/交易机制/风格筛选/涨跌榜单）
EXCLUDE_PAT = re.compile(
    r"样本股|成份股|成分股|融资融券|昨日|涨停|连板|首板|次新股|新高|新进|"
    r"高送转|基金重仓|社保|减持|回购|市盈率|市净率|大盘股|微盘|中盘|小盘|"
    r"同花顺|陆股通|港股通|全A|全收益|A股指数|情绪"
)

VALID_START = "20260924"   # ths 数据同步日
EDGE_SOURCE = "ths_member_v1"


def load_theme_concepts():
    idx = read_all("ts_ths_index")
    idx = idx[idx["type"] == "N"]
    mask = ~idx["name"].astype(str).str.contains(EXCLUDE_PAT)
    themes = idx[mask][["ts_code", "name"]].dropna()
    return dict(zip(themes["ts_code"], themes["name"]))


def load_members(theme_codes):
    mem = read_all("ts_ths_member")
    mem = mem[mem["ts_code"].isin(theme_codes)]
    return mem[["ts_code", "con_code"]].dropna()


def main():
    commit = "--commit" in sys.argv
    from kg_client import KgClient  # noqa: E402

    themes = load_theme_concepts()
    print(f"[1] theme concepts (type=N, excl filter): {len(themes)}")
    mem = load_members(set(themes))
    print(f"[2] member rows: {len(mem)}, unique stocks: {mem['con_code'].nunique()}")

    kg = KgClient()
    with kg._session() as s:
        sec_codes = {r["ts"] for r in s.run(
            "MATCH (s:Security) RETURN s.ts_code AS ts")}
        con_names = {r["n"] for r in s.run(
            "MATCH (c:Concept) RETURN c.name AS n")}
    print(f"[3] graph Security={len(sec_codes)} Concept={len(con_names)}")

    # 对齐：只挂图谱已有 Security
    pairs = [(themes[r.ts_code], r.con_code)
             for r in mem.itertuples() if r.con_code in sec_codes]
    hit = len({p[1] for p in pairs})
    print(f"[4] matched pairs={len(pairs)} hit_stocks={hit} "
          f"({100.0 * hit / max(1, mem['con_code'].nunique()):.1f}% of member stocks)")
    concept_set = {p[0] for p in pairs}
    new_concepts = concept_set - con_names
    overlap = concept_set & con_names
    print(f"[5] concepts to write: {len(concept_set)} "
          f"(new {len(new_concepts)}, overlap-extend {len(overlap)})")

    # 抽样 20 条影子核对
    random.seed(42)
    sample = random.sample(pairs, min(20, len(pairs)))
    print("--- sample 20 (concept <- stock) ---")
    for c, s in sample:
        print(f"  {c} <- {s}")

    if not commit:
        print("DRY-RUN ONLY (add --commit to write)")
        return

    # 写入：Concept 节点 MERGE（SET += 不动 is_root/level）
    n1 = kg.upsert_nodes(
        "Concept", ["name"],
        [{"name": n, "ths_code": t, "source": "ths_index"}
         for t, n in themes.items() if n in concept_set])
    # 边：validStart=数据日（与 m4 链路平行、按 source 回滚）
    n2 = kg.upsert_edge(
        "Security", "ts_code", "Concept", "name", "MAPS_TO",
        [{"src": s, "dst": c, "validStart": VALID_START,
          "source": EDGE_SOURCE} for c, s in pairs])
    print(f"[6] COMMITTED nodes={n1} edges={n2}")
    print("rollback: MATCH ()-[r:MAPS_TO {source:'ths_member_v1'}]->() DELETE r")


if __name__ == "__main__":
    main()
