# -*- coding: utf-8 -*-
"""知识图谱 Phase 2-A ETL：人物实体抽取 + SERVES_AS 关系建图。

数据流：
  stock_company (6294 行) → Person 节点（去重 by name）
                            + SERVES_AS 边（Person -[SERVES_AS {role}]-> Company）
  stk_managers（G5 高管层，按码全量历史）→ Person 属性补全
                            + SERVES_AS 全量任期边（title 拆多职 + 真实起止）

人物来源字段：
  - chairman  董事长（100% 覆盖）
  - manager   总经理（97.7% 覆盖）
  - secretary 董秘（95.5% 覆盖）
  - G5：stk_managers 全量高管（董事会成员/高管/监事/委员会成员，
    title 多职以 \ 分隔，begin_date/end_date 真实任期）

时序图谱铁律：
  - 所有 SERVES_AS 边带 validStart/validEnd
  - stock_company 边：validStart 取 Security.list_date（公司上市日，
    作为下界保守估计，实际任职可能晚于上市日，但 stock_company 是
    当前快照无历史）
  - stk_managers 边（G5）：validStart=begin_date（真实任职日，
    缺省回落 ann_date），validEnd=end_date（离任）或 9999-12-31（在任）
  - 退市公司同样保留人物关系（历史快照）

重名处理：金融高管重名概率低，Person.name 直接做唯一键。
  G5 全量高管后重名必然出现（同名跨公司合并为同一节点）——
  build_managers 统计并打印跨公司同名污染面（口径诚实），
  若污染不可接受再升格 person_id。

用法：
  python kg_build_p2.py             # 全量建图（幂等可重跑，含 G5 高管层）
  python kg_build_p2.py --dryrun    # 只打印不落库
  python kg_build_p2.py --sample 688411.SH   # 抽检某标的的人物
"""
import argparse
import glob
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import datamart as dm


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_str(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v).strip() or None  # 空字符串归一为 None


# 人物角色映射：stock_company 字段 → SERVES_AS.role
ROLE_FIELDS = [
    ("chairman",  "chairman"),    # 董事长
    ("manager",   "manager"),     # 总经理
    ("secretary", "secretary"),   # 董秘
]


def extract_persons(df_company):
    """stock_company → Person 节点（去重 by name）。

    同一人可能在多家公司任职（一人多职）或同公司多职位，
    Person.name 唯一键自动去重。
    """
    names = set()
    for _, r in df_company.iterrows():
        for field, _ in ROLE_FIELDS:
            name = _safe_str(r.get(field))
            if name:
                names.add(name)
    return [{"name": n, "source": "stock_company"} for n in sorted(names)]


def build_serves_as_edges(df_company, df_basic):
    """生成 SERVES_AS 时序边（Person -[SERVES_AS {role}]-> Company）。

    边属性：src=Person.name, dst=Company.com_id, role, validStart, validEnd,
            source, updated_at

    validStart 取 Security.list_date（上市日为下界保守估计）。
    Company 与 Security 通过 ts_code 关联，list_date 从 stock_basic 取。
    """
    now = _now()
    # ts_code → list_date 映射（公司可能多 ts_code 复用，取最早上市日）
    list_date_map = {}
    for _, r in df_basic.iterrows():
        tc = _safe_str(r.get("ts_code"))
        ld = _safe_str(r.get("list_date"))
        if tc and ld:
            # 同一公司多个 ts_code（如 A+H 退市复用），取最小上市日
            if tc not in list_date_map or ld < list_date_map[tc]:
                list_date_map[tc] = ld

    edges = []
    skip_no_com = skip_no_name = skip_no_ld = 0
    for _, r in df_company.iterrows():
        com_id = _safe_str(r.get("com_id"))
        ts_code = _safe_str(r.get("ts_code"))
        if not com_id:
            skip_no_com += 1
            continue
        # 通过 ts_code 取 list_date，无则用 setup_date 作 fallback，再无用 "19900101"
        list_date = list_date_map.get(ts_code)
        if not list_date:
            list_date = _safe_str(r.get("setup_date")) or "19900101"
            skip_no_ld += 1
        for field, role in ROLE_FIELDS:
            name = _safe_str(r.get(field))
            if not name:
                skip_no_name += 1
                continue
            edges.append({
                "src": name,
                "dst": com_id,
                "role": role,
                "validStart": list_date,
                "validEnd": S.VALID_END_OPEN,
                "source": "stock_company",
                "updated_at": now,
            })
    if skip_no_com or skip_no_ld or skip_no_name:
        print("  SERVES_AS 跳过: no_com=%d no_list_date=%d no_name=%d"
              % (skip_no_com, skip_no_ld, skip_no_name))
    return edges


# ── G5：高管层（stk_managers 全量任期） ──────────────────────
MANAGER_PROPS = ("gender", "edu", "birthday", "national")


def build_managers(dryrun=False):
    """stk_managers → Person 属性补全 + SERVES_AS 全量任期边。

    与 stock_company 三角色快照边共存（source/role 区分）：
      · stock_company 边：role=chairman/manager/secretary，
        validStart=上市日下界（粗），validEnd=open（快照无历史）
      · stk_managers 边：role=title（\ 分隔多职逐条拆分），
        validStart=begin_date（缺省回落 ann_date），
        validEnd=end_date（离任）或 open（在任）
    重名：与 Phase 2-A 同口径 Person.name 唯一键——跨公司同名合并
    为同一节点，统计并打印污染面（一人多职 vs 重名不可分辨，口径诚实）。
    Person 属性仅写非空键（upsert_nodes 的 SET n += row 语义下
    None 会删属性）。
    """
    now = _now()
    # 直读 parquet 绕过 datamart：_normalize 按 (ts_code, ann_date) 去重
    # 会把同日批量公告多名高管的行折叠（57.5万→12万），事件表无时序唯一键
    mg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "tushare", "stk_managers")
    parts = sorted(glob.glob(os.path.join(mg_dir, "part_*.parquet")))
    if not parts:
        raise SystemExit("stk_managers 为空，先跑 "
                         "tushare_sync.py --batch managers")
    df = pd.concat([pd.read_parquet(p) for p in parts],
                   ignore_index=True)
    print("  源数据: %d 行（直读 %d 个 part）" % (len(df), len(parts)))
    df_comp = dm.read("stock_company")
    tc2com = {str(r.ts_code): str(r.com_id)
              for r in df_comp[["ts_code", "com_id"]].dropna().itertuples()}

    df = df.dropna(subset=["name"])
    for c in ("begin_date", "end_date", "ann_date"):
        if c in df.columns:
            df[c] = df[c].astype(str).str[:8]
            df.loc[df[c].isin(("nan", "NaT", "None")), c] = None
    # 不在 stock_company 的码（退市/未收录）无 Company 节点，跳过计数
    miss = ~df["ts_code"].astype(str).isin(tc2com)
    n_miss = int(miss.sum())
    df = df[~miss].copy()
    df["ts_code"] = df["ts_code"].astype(str)

    # Person 属性：同人首见非空值；仅写非空键
    persons = {}
    for r in df.itertuples():
        p = persons.setdefault(r.name, {"name": r.name})
        for c in MANAGER_PROPS:
            v = _safe_str(getattr(r, c, None))
            if v and c not in p:
                p[c] = v
    person_rows = [persons[k] for k in sorted(persons)]

    # SERVES_AS 边：title 按 \ 拆多职
    edges = skip_no_date = skip_no_title = skip_inverted = 0
    edge_rows = []
    for r in df.itertuples():
        title = _safe_str(getattr(r, "title", None))
        if not title:
            skip_no_title += 1
            continue
        vs = _safe_str(getattr(r, "begin_date", None)) or \
            _safe_str(getattr(r, "ann_date", None))
        if not vs:
            skip_no_date += 1
            continue
        ve = _safe_str(getattr(r, "end_date", None)) or S.VALID_END_OPEN
        # 时序铁律：tushare 偶有 begin_date=end_date+1~2 天的倒挂记录，跳过
        if ve != S.VALID_END_OPEN and vs > ve:
            skip_inverted += 1
            continue
        lev = _safe_str(getattr(r, "lev", None))
        for t in title.split("\\"):
            t = t.strip()
            if not t:
                continue
            e = {"src": r.name, "dst": tc2com[r.ts_code], "role": t,
                 "validStart": vs, "validEnd": ve,
                 "source": "stk_managers", "updated_at": now}
            if lev:
                e["lev"] = lev
            edge_rows.append(e)
    edges = len(edge_rows)

    # 统计：在任/离任 + 跨公司同名污染面
    n_open = sum(1 for e in edge_rows
                 if e["validEnd"] == S.VALID_END_OPEN)
    name2coms = {}
    for e in edge_rows:
        name2coms.setdefault(e["src"], set()).add(e["dst"])
    dup = {k: len(v) for k, v in name2coms.items() if len(v) > 1}
    top_dup = sorted(dup.items(), key=lambda x: -x[1])[:8]
    print("  Person（去重后）: %d；SERVES_AS 新边: %d（在任 %d / 离任 %d）"
          % (len(person_rows), edges, n_open, edges - n_open))
    print("  跳过: 未映射 ts_code=%d 无日期=%d 无职务=%d 时序倒挂=%d"
          % (n_miss, skip_no_date, skip_no_title, skip_inverted))
    print("  跨公司同名: %d 人（一人多职或重名污染不可分辨）；top: %s"
          % (len(dup),
             "·".join("%s(%d)" % kv for kv in top_dup) or "无"))

    if dryrun:
        print("DRYRUN 抽样 5 条边:")
        for e in edge_rows[:5]:
            print("  %s -[role=%s]-> %s [%s,%s]"
                  % (e["src"], e["role"], e["dst"],
                     e["validStart"], e["validEnd"]))
        return

    from kg_client import KgClient
    cli = KgClient()
    cli.ensure_schema()
    n_p = cli.upsert_nodes(S.LBL_PERSON, ["name"], person_rows)
    print("  Person upsert: %d" % n_p)
    n_e = upsert_edge_with_role(
        cli, S.LBL_PERSON, "name", S.LBL_COMPANY, "com_id",
        S.REL_SERVES_AS, edge_rows)
    print("  SERVES_AS upsert: %d" % n_e)
    cli.close()


def build(dryrun=False):
    print("=== 知识图谱 Phase 2-A 人物抽取 %s ===" % _now())
    # 1) 读源
    print("[1/4] 读源数据…")
    df_company = dm.read("stock_company")
    df_basic = dm.read("stock_basic")
    print("  stock_company: %d 行 × %d 列" % df_company.shape)
    print("  stock_basic:   %d 行 × %d 列" % df_basic.shape)

    # 2) 抽取 Person 节点
    print("[2/4] 抽取 Person 节点…")
    person_rows = extract_persons(df_company)
    print("  Person: %d（去重后）" % len(person_rows))

    # 3) 抽取 SERVES_AS 边
    print("[3/4] 抽取 SERVES_AS 边…")
    edges = build_serves_as_edges(df_company, df_basic)
    # 按角色统计
    role_cnt = {}
    for e in edges:
        role_cnt[e["role"]] = role_cnt.get(e["role"], 0) + 1
    for role, cnt in sorted(role_cnt.items()):
        print("  %s: %d 条" % (role, cnt))
    print("  合计: %d 条" % len(edges))

    if dryrun:
        # dryrun 抽样 5 条边
        print("DRYRUN 抽样 5 条边:")
        for e in edges[:5]:
            print("  %s -[role=%s]-> %s  [%s, %s]"
                  % (e["src"], e["role"], e["dst"], e["validStart"],
                     e["validEnd"]))
        print("[5/5] G5 高管层（stk_managers）dryrun…")
        build_managers(dryrun=True)
        print("DRYRUN 结束，未连接 Neo4j。")
        return

    # 4) 落库
    print("[4/4] 连接 Neo4j 落库…")
    from kg_client import KgClient
    cli = KgClient()
    cli.ensure_schema()  # 幂等，会创建 person_name 约束

    n_p = cli.upsert_nodes(S.LBL_PERSON, ["name"], person_rows)
    print("  Person upsert: %d" % n_p)

    # SERVES_AS 边：src=Person.name, dst=Company.com_id
    # upsert_edge 用 (src, dst, validStart) 作 MERGE 键——
    # 同一人同一公司同一上市日下，不同 role 会冲突（MERGE 单边）。
    # 修复：把 role 也加入 MERGE 键。
    n_e = upsert_edge_with_role(
        cli, S.LBL_PERSON, "name", S.LBL_COMPANY, "com_id",
        S.REL_SERVES_AS, edges)
    print("  SERVES_AS upsert: %d" % n_e)
    cli.close()

    # 5) G5 高管层（stk_managers 全量任期，幂等追加层）
    print("[5/5] G5 高管层（stk_managers）…")
    build_managers(dryrun=dryrun)
    print("=== Phase 2-A 完成 %s ===" % _now())


def upsert_edge_with_role(cli, src_lbl, src_key, dst_lbl, dst_key,
                          rel_type, edge_rows, batch=2000):
    """带 role 的 SERVES_AS 边 upsert（MERGE 键含 role 防冲突）。

    普通人-公司-上市日可能同一个人有多个 role（chairman+manager），
    必须把 role 加入 MERGE 键才能存多条边。
    """
    if not edge_rows:
        return 0
    sample = edge_rows[0]
    extra_cols = [c for c in sample if c not in ("src", "dst")]
    # MERGE 键：validStart + role（同一公司同一人同一上市日的不同 role 各占一边）
    set_cols = [c for c in extra_cols if c != "role"]  # role 已在 MERGE
    set_clause = ", ".join("r.%s = row.%s" % (c, c) for c in set_cols)
    cy = (
        "UNWIND $rows AS row "
        "MATCH (s:%s {%s: row.src}) "
        "MATCH (d:%s {%s: row.dst}) "
        "MERGE (s)-[r:%s {validStart: row.validStart, role: row.role}]->(d) "
        "SET %s" % (src_lbl, src_key, dst_lbl, dst_key,
                    rel_type, set_clause)
    )
    n = 0
    with cli._session() as s:
        for i in range(0, len(edge_rows), batch):
            chunk = edge_rows[i:i + batch]
            s.run(cy, rows=chunk)
            n += len(chunk)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true",
                    help="只打印不连接 Neo4j")
    ap.add_argument("--sample", help="抽检某 ts_code 子图（含人物）")
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
