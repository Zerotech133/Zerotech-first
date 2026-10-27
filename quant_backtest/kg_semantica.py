# -*- coding: utf-8 -*-
"""M4.5：Semantica 只读适配器（Neo4j 子图 → TemporalGraphQuery dict）。

只读方向：从 Neo4j 导出喂给 semantica.kg.temporal_query.TemporalGraphQuery
（存储无关接口），绝不写回——semantica neo4j_store 用 CREATE 追加，与本项目
MERGE 幂等策略冲突。

时间轴映射：Neo4j 边 validStart/validEnd → Semantica valid_from/valid_until
（9999-12-31 视作有限端点，语义等同开区间）；只用 valid 轴，不填 transaction 轴。
节点不带时间属性（视为长期活跃），时序过滤全靠边——时序事实在边上。

用法（全部只读）：
  python kg_semantica.py 002261.SZ                     # 导出规模概览
  python kg_semantica.py 002261.SZ --at 2026-09-10      # 时点重建（query_at_time）
  python kg_semantica.py 002261.SZ --check              # 时序一致性校验
  python kg_semantica.py 002261.SZ --evolve             # 演化分析（analyze_evolution）
  python kg_semantica.py 002261.SZ --paths 英维克 3买   # 时序路径（find_temporal_paths）
  可选 --hops 1|2（默认 2：标的→Event→MarketEvent 等）
"""

import sys
import os
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kg_client import KgClient


def to_iso(v):
    """validStart/validEnd 统一转 ISO（'20260829' → '2026-08-29'；已是 ISO 直通）。"""
    if v is None:
        return None
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s


def export_subgraph(cli, ts_code, hops=2, close=True):
    """标的子图 → dict{entities, relationships}（Semantica 格式）。

    L1=标的全部直接边（含 Region/Industry/Concept 分类边，量少）。
    L2 只沿业务节点扩展，分类枢纽（Region/SwL1/Industry/Concept）不外扩
    ——否则带出同区域/同行业全部标的（数百个）。
    Seat 特殊处理：只沿 OCCUPIES 归因方向（Seat→HotMoney）；
    反向 TRADED_ON 会带进该席位交易过的全部其他标的（数百个），不收。
    close=False 供批量导出复用连接（M5 蒸馏样本封装）。
    """
    BIZ = ["Event", "MarketEvent", "Company", "Person", "Product"]
    s = cli._session()
    rec = s.run(
        "MATCH (src:Security {ts_code:$c})-[r]-(m) "
        "RETURN src, collect(DISTINCT m) AS l1",
        c=ts_code,
    ).single()
    if rec is None:
        raise SystemExit("未找到标的 %s" % ts_code)
    nodes, sec_node = list(rec["l1"]), rec["src"]
    if hops >= 2:
        q = (
            "MATCH (src:Security {ts_code:$c})-[]-(m1)-[]-(m2) "
            "WHERE any(l IN labels(m1) WHERE l IN $biz) "
            "RETURN collect(DISTINCT m2) AS l2"
        )
        r2 = s.run(q, c=ts_code, biz=BIZ).single()
        nodes += list(r2["l2"])
        # 席位→游资归因链（单向，不扩散其他标的）
        q2 = (
            "MATCH (src:Security {ts_code:$c})-[:TRADED_ON]->(st:Seat)"
            "<-[:OCCUPIES]-(hm:HotMoney) "
            "RETURN collect(DISTINCT hm) + collect(DISTINCT st) AS l2b"
        )
        r3 = s.run(q2, c=ts_code).single()
        nodes += list(r3["l2b"])
    nodes.append(sec_node)  # 标的自身
    ids = set()
    entities = []
    for n in nodes:
        eid = n.element_id if hasattr(n, "element_id") else "n%d" % n.id
        if eid in ids:
            continue
        ids.add(eid)
        d = dict(n)
        label = list(n.labels)[0] if n.labels else "Unknown"
        nm = (
            d.get("name")
            or d.get("com_name")
            or d.get("ts_code")
            or d.get("index_code")
            or d.get("kind")
            or eid
        )
        entities.append(
            {
                "id": eid,
                "label": label,
                "name": str(nm),
                "ts_code": d.get("ts_code"),
                "props": {
                    k: str(v)[:60]
                    for k, v in d.items()
                    if k in ("kind", "date", "mtype", "stype", "group", "industry")
                },
            }
        )
    # 边：两端都在节点集内
    rels = []
    q = (
        "MATCH (a)-[r]->(b) WHERE elementId(a) IN $ids AND elementId(b) IN $ids "
        "RETURN elementId(a) AS sa, elementId(b) AS sb, type(r) AS rt, "
        "properties(r) AS rp ORDER BY rt"
    )
    for row in s.run(q, ids=list(ids)):
        rp = row["rp"]
        rels.append(
            {
                "source": row["sa"],
                "target": row["sb"],
                "type": row["rt"],
                "valid_from": to_iso(rp.pop("validStart", None)),
                "valid_until": to_iso(rp.pop("validEnd", None)),
                "props": {
                    k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in rp.items()
                    if k not in ("updated_at", "source")
                },
            }
        )
    if close:
        cli.close()
    return {"entities": entities, "relationships": rels}


def brief(g, tag=""):
    cnt = Counter(r["type"] for r in g["relationships"])
    print(
        "  %s实体 %d / 关系 %d：%s"
        % (
            tag,
            len(g["entities"]),
            len(g["relationships"]),
            " ".join("%s=%d" % kv for kv in cnt.most_common(12)) or "空",
        )
    )


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    code, hops = args[0], 2
    if "--hops" in args:
        hops = int(args[args.index("--hops") + 1])
    t0 = time.time()
    cli = KgClient()
    g = export_subgraph(cli, code, hops=hops)
    sec = [e for e in g["entities"] if e.get("ts_code") == code][0]
    print("=== M4.5 只读适配器：%s（%s）%d 跳子图 ===" % (sec["name"], code, hops))
    brief(g, "导出 ")

    from semantica.kg.temporal_query import TemporalGraphQuery

    tq = TemporalGraphQuery(temporal_granularity="day")
    if not hasattr(tq, "_relationship_key"):
        # workaround：semantica 0.7.0 的 validate_temporal_consistency 调用了
        # 只在 TemporalVersionManager 上定义的 _relationship_key（包内 bug）
        tq._relationship_key = lambda r: (
            "%s|%s|%s"
            % (
                r.get("source", ""),
                r.get("type", r.get("relationship", "")),
                r.get("target", ""),
            )
        )
    idname = {e["id"]: "%s:%s" % (e["label"], e["name"]) for e in g["entities"]}

    if "--check" in args:
        rep = tq.validate_temporal_consistency(g)
        print(
            "\n-- 时序一致性校验：errors=%d warnings=%d"
            % (len(rep.errors), len(rep.warnings))
        )
        for e in rep.errors[:8]:
            print("  ✗ [%s] %s (%s)" % (e["issue_type"], e["message"], e["fact_id"]))
        for w in rep.warnings[:8]:
            print("  ⚠ [%s] %s (%s)" % (w["issue_type"], w["message"], w["fact_id"]))

    if "--at" in args:
        at = args[args.index("--at") + 1]
        res = tq.query_at_time(g, "", at_time=at)
        print(
            "\n-- 时点重建 @ %s：活跃实体 %d / 活跃关系 %d"
            % (res["at_time"].date(), res["num_entities"], res["num_relationships"])
        )
        brief(res, "  ")
        # 展示该时点活跃的时序事实样本
        for r in res["relationships"][:10]:
            vf, vu = r["valid_from"], r["valid_until"] or "…"
            print(
                "    %s → %s [%s %s~%s] %s"
                % (
                    idname.get(r["source"], "?")[:24],
                    idname.get(r["target"], "?")[:24],
                    r["type"],
                    vf,
                    vu,
                    r.get("props", ""),
                )
            )

    if "--evolve" in args:
        ev = tq.analyze_evolution(g)
        print(
            "\n-- 演化分析：关系 %d 条 / 类型 %d 种 / 平均有效期 %.0f 秒"
            % (ev["count"], ev["diversity"], ev["stability"])
        )

    if "--paths" in args:
        i = args.index("--paths")
        na, nb = args[i + 1], args[i + 2]
        idm = {e["name"]: e["id"] for e in g["entities"]}
        a, b = idm.get(na), idm.get(nb)
        if not (a and b):
            print(
                "\n-- 路径查询失败：节点名不在子图内（可用名样本：%s）"
                % " / ".join(list(idm)[:8])
            )
        else:
            pr = tq.find_temporal_paths(g, a, b, max_path_length=4)
            print("\n-- 时序路径 %s → %s：%d 条" % (na, nb, pr["num_paths"]))
            for p in pr["paths"][:3]:
                names = [idname.get(nid, nid[:6]) for nid in p["path"]]
                print("    %s" % " - ".join(names))
                for e in p["edges"]:
                    print(
                        "      [%s %s~%s]"
                        % (e["type"], e["valid_from"], e["valid_until"] or "…")
                    )
    print("耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
