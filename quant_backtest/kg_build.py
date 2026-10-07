# -*- coding: utf-8 -*-
"""知识图谱 Phase 1 ETL：seed 实体 + 时序关系边建图。

数据流：
  stock_basic (5569 行)  → Security 节点 + Industry/Region 节点
  stock_company (6294 行) → Company 节点
  sw_classify_l1 (511 行) → SwL1Industry 节点
  生成关系边：
    Security -[OF_COMPANY {validStart=list_date, validEnd=9999-12-31}]-> Company
    Security -[INDUSTRY_OF {validStart=list_date, ...}]-> Industry
    Security -[AREA_OF {validStart=list_date, ...}]-> Region

时序图谱铁律：所有边带 validStart/validEnd；当前在市 → validEnd=9999-12-31。
Phase 2 后续：人物/产品/事件/产业链/跨市场实体。

用法：
  python kg_build.py                 # 全量建图（幂等，可重跑）
  python kg_build.py --dryrun        # 只打印不落库
  python kg_build.py --sample 688411.SH   # 抽检某标的子图
"""
import argparse
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import datamart as dm        # 复用统一读层（已识别 stock_basic 等表）


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _clean_props(d):
    """过滤 None / NaN 值（保留给 MERGE key 校验用）。"""
    return {k: v for k, v in d.items()
            if v is not None and not (isinstance(v, float) and pd.isna(v))}


def _safe_str(v):
    """数值/对象 → 字符串（处理 list/NaN）。"""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v)


# ── 1) 从 stock_basic 抽取节点 ────────────────────────────────
def extract_security_and_lookup(df_basic):
    """stock_basic → Security 节点 + Industry 节点 + Region 节点。

    保留所有 SECURITY_PROPS keys（含 None），让 upsert_nodes 的 SET n += row
    自动处理缺失属性（None → 删除属性）。仅跳过 MERGE key(ts_code) 为空的行。
    """
    sec_rows, ind_set, reg_set = [], set(), set()
    for _, r in df_basic.iterrows():
        sec = {col: _safe_str(r.get(src)) for src, col in
               S.SECURITY_PROPS.items()}
        if not sec.get("ts_code"):    # MERGE key 必须
            continue
        sec_rows.append(sec)
        if sec.get("industry"):
            ind_set.add(sec["industry"])
        if sec.get("area"):
            reg_set.add(sec["area"])

    ind_rows = [{"name": n} for n in sorted(ind_set)]
    reg_rows = [{"name": n} for n in sorted(reg_set)]
    return sec_rows, ind_rows, reg_rows


# ── 2) 从 stock_company 抽取 Company 节点 ─────────────────────
def extract_company(df_company):
    """stock_company → Company 节点（com_id 唯一键）。"""
    rows = []
    for _, r in df_company.iterrows():
        c = {col: _safe_str(r.get(src)) for src, col in
             S.COMPANY_PROPS.items()}
        if not c.get("com_id"):       # MERGE key 必须
            continue
        rows.append(c)
    return rows


# ── 3) 从 sw_classify_l1 抽取申万一级行业 ────────────────────
def extract_sw_l1(df_sw):
    """sw_classify_l1 → SwL1Industry 节点（index_code 唯一键）。"""
    rows = []
    for _, r in df_sw.iterrows():
        rows.append(_clean_props({
            "index_code": _safe_str(r.get("index_code")),
            "industry_name": _safe_str(r.get("industry_name")),
            "level": _safe_str(r.get("level")),
            "industry_code": _safe_str(r.get("industry_code")),
            "src": _safe_str(r.get("src")),
        }))
    return [r for r in rows if r.get("index_code")]


# ── 4) 关系边生成 ─────────────────────────────────────────────
def build_edges(df_basic):
    """生成 Phase 1 三类时序关系边。

    边属性：validStart, validEnd, source, updated_at
    Returns: {OF_COMPANY: [...], INDUSTRY_OF: [...], AREA_OF: [...]}
    """
    now = _now()
    of_co, ind_of, area_of = [], [], []
    for _, r in df_basic.iterrows():
        ts_code = _safe_str(r.get("ts_code"))
        list_date = _safe_str(r.get("list_date")) or "19900101"
        industry = _safe_str(r.get("industry"))
        area = _safe_str(r.get("area"))

        # OF_COMPANY: Security -> Company（通过 ts_code 关联）
        of_co.append({
            "src": ts_code, "dst": ts_code,  # Company.ts_code 字段
            "validStart": list_date, "validEnd": S.VALID_END_OPEN,
            "source": S.SOURCE_SEED, "updated_at": now,
        })
        # INDUSTRY_OF: Security -> Industry（industry 字段对应 Industry.name）
        if industry:
            ind_of.append({
                "src": ts_code, "dst": industry,
                "validStart": list_date, "validEnd": S.VALID_END_OPEN,
                "source": S.SOURCE_SEED, "updated_at": now,
            })
        # AREA_OF: Security -> Region
        if area:
            area_of.append({
                "src": ts_code, "dst": area,
                "validStart": list_date, "validEnd": S.VALID_END_OPEN,
                "source": S.SOURCE_SEED, "updated_at": now,
            })
    return {S.REL_OF_COMPANY: of_co,
            S.REL_INDUSTRY_OF: ind_of,
            S.REL_AREA_OF: area_of}


# ── 5) 主流程 ─────────────────────────────────────────────────
def build(dryrun=False):
    print("=== 知识图谱 Phase 1 建图开始 %s ===" % _now())
    # 1) 读源数据
    print("[1/5] 读源数据…")
    df_basic = dm.read("stock_basic")
    # datamart 优先返回 tdx 仓 ts_stock_basic（5908 行含退市，但缺 4 字段）
    # 从 tushare parquet 按 ts_code merge 补齐 market/cnspell/act_name/act_ent_type
    tu_basic = os.path.join(HERE, "data", "tushare", "stock_basic",
                            "part_0000.parquet")
    if os.path.exists(tu_basic):
        df_tu = pd.read_parquet(tu_basic)
        extra = [c for c in ["market", "cnspell", "act_name", "act_ent_type"]
                 if c in df_tu.columns and c not in df_basic.columns]
        if extra:
            df_basic = df_basic.merge(df_tu[["ts_code"] + extra],
                                      on="ts_code", how="left")
            print("  补 tushare 字段: %s" % extra)
    df_company = dm.read("stock_company")
    df_sw = dm.read("sw_classify_l1")
    print("  stock_basic: %d 行 × %d 列" % df_basic.shape)
    print("  stock_company: %d 行 × %d 列" % df_company.shape)
    print("  sw_classify_l1: %d 行 × %d 列" % df_sw.shape)

    # 2) 抽取节点
    print("[2/5] 抽取节点…")
    sec_rows, ind_rows, reg_rows = extract_security_and_lookup(df_basic)
    co_rows = extract_company(df_company)
    sw_rows = extract_sw_l1(df_sw)
    print("  Security: %d  Company: %d  Industry: %d  Region: %d  SwL1: %d"
          % (len(sec_rows), len(co_rows), len(ind_rows),
             len(reg_rows), len(sw_rows)))

    # 3) 抽取边
    print("[3/5] 抽取时序关系边…")
    edges = build_edges(df_basic)
    for k, v in edges.items():
        print("  %s: %d 条" % (k, len(v)))

    if dryrun:
        print("DRYRUN 结束，未连接 Neo4j。")
        return

    # 4) 落库
    print("[4/5] 连接 Neo4j 并落库…")
    from kg_client import KgClient
    cli = KgClient()
    cli.ensure_schema()

    n_sec = cli.upsert_nodes(S.LBL_SECURITY, ["ts_code"], sec_rows)
    print("  Security upsert: %d" % n_sec)
    n_co = cli.upsert_nodes(S.LBL_COMPANY, ["com_id"], co_rows)
    print("  Company upsert: %d" % n_co)
    n_ind = cli.upsert_nodes(S.LBL_INDUSTRY, ["name"], ind_rows)
    print("  Industry upsert: %d" % n_ind)
    n_reg = cli.upsert_nodes(S.LBL_REGION, ["name"], reg_rows)
    print("  Region upsert: %d" % n_reg)
    n_sw = cli.upsert_nodes(S.LBL_SW_L1, ["index_code"], sw_rows)
    print("  SwL1Industry upsert: %d" % n_sw)

    # 边：OF_COMPANY (Security.ts_code -> Company.ts_code)
    # 注意：Company 节点的 key 是 com_id 不是 ts_code，需要先按 ts_code 索引
    # 这里通过 MATCH Company {ts_code: row.dst} 来匹配
    n_e1 = cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_COMPANY, "ts_code",
        S.REL_OF_COMPANY, edges[S.REL_OF_COMPANY])
    print("  OF_COMPANY upsert: %d" % n_e1)
    n_e2 = cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_INDUSTRY, "name",
        S.REL_INDUSTRY_OF, edges[S.REL_INDUSTRY_OF])
    print("  INDUSTRY_OF upsert: %d" % n_e2)
    n_e3 = cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_REGION, "name",
        S.REL_AREA_OF, edges[S.REL_AREA_OF])
    print("  AREA_OF upsert: %d" % n_e3)

    # 5) 统计 + 抽检
    print("[5/5] 统计 + 抽检…")
    cli.stats()
    cli.close()
    print("=== Phase 1 建图完成 %s ===" % _now())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true",
                    help="只打印不连接 Neo4j")
    ap.add_argument("--sample", help="抽检某 ts_code 子图")
    args = ap.parse_args()

    if args.sample:
        from kg_client import KgClient
        cli = KgClient()
        cli.sample_subgraph(args.sample)
        cli.close()
        return

    build(dryrun=args.dryrun)


if __name__ == "__main__":
    main()
