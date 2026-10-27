# -*- coding: utf-8 -*-
"""K261 L2：图分析算法层 —— 六度路径 / 共同邻居 / 社群 / 中心性。

用户命题（K261 立项原话）：「组织架构、网络架构、计算中心程度、
计算社群关系、六度理论」——在图谱上以图论方式把关系讲明白。

算法与实现：
  · path（六度理论）  Cypher 原生 shortestPath（限深默认 6 跳，快）
  · shared（共同邻居） Cypher 一跳交集，按 label 聚合
  · community（社群）  seed N 跳子图（上限 600 节点）→ 语义加权 Louvain + 连通修正
  · centrality（中心） 同子图 → degree / betweenness / pagerank TopN

被 kg_portal.api(kind="gx") 消费（门户知识画布图分析面板）；
CLI 自测：
  python kg_analytics.py path 陈小群 000338.SZ
  python kg_analytics.py shared 陈小群 000338.SZ
  python kg_analytics.py community 陈小群 --hops 2
  python kg_analytics.py centrality 陈小群
"""

import argparse
import json
import os
import re
import sys
import math
from collections import Counter
from datetime import date, time, timedelta

from neo4j.time import Date, DateTime, Duration, Time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient  # noqa: E402
from kg_portal import CANVAS_LABELS, CANVAS_TERMINAL  # noqa: E402

# 历史字段只用于检索；定位和画布 ID 始终使用 CANVAS_LABELS 主键。
_RESOLVE = {
    "Security": "n.name = $n OR $n IN coalesce(n.aliases, [])",
    "Company": "n.com_name = $n OR toString(n.com_id) = $n",
    "Industry": "n.industry_name = $n",
    "SwL1Industry": "n.industry_name = $n",
    "Segment": "n.chain = $n",
}
_MAX_SUB = 600  # 子图节点上限（社群/中心性，防大图拖死）
_MAX_PATH = 6  # 六度：默认 6 跳


def _scope_params(scope):
    if scope is None:
        return {}, {}
    if not isinstance(scope, dict) or set(scope) - {"types", "at"}:
        raise ValueError("scope 仅支持 types / at")
    result, params = {}, {}
    if "types" in scope:
        types = scope["types"]
        if not isinstance(types, list) or any(
            not isinstance(t, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", t)
            for t in types
        ):
            raise ValueError("scope.types 必须是合法关系类型列表")
        result["types"] = list(types)
        params["rel_types"] = list(types)
    if "at" in scope:
        at = scope["at"]
        if not isinstance(at, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", at
        ):
            raise ValueError("scope.at 必须是 YYYY-MM-DD")
        try:
            date.fromisoformat(at)
        except ValueError as exc:
            raise ValueError("scope.at 必须是有效的 YYYY-MM-DD 日期") from exc
        result["at"] = at
        params["at"] = at.replace("-", "")
    return result, params


def _rel_filter(var, scope):
    parts = []
    if "types" in scope:
        parts.append("type(%s) IN $rel_types" % var)
    if "at" in scope:
        parts.extend(
            (
                "%s.validStart IS NOT NULL" % var,
                "replace(toString(%s.validStart), '-', '') <= $at" % var,
                "(%s.validEnd IS NULL OR " % var
                + "replace(toString(%s.validEnd), '-', '') >= $at)" % var,
            )
        )
    return " AND ".join(parts)


def _json_value(value):
    """Neo4j 时间属性转为可序列化字符串，保留其余原值。"""
    if isinstance(value, (Date, DateTime, Time, Duration, date, time, timedelta)):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _canvas_identity(labels, props):
    lbl = next((l for l in labels if l in CANVAS_LABELS), None)
    if lbl is None:
        return None, None, None
    key, _, fields = CANVAS_LABELS[lbl]
    value = props.get(key)
    if value is None:
        return None, None, None
    return "%s|%s" % (lbl, value), lbl, str(props.get(fields[0]) or value)[:24]


def _resolve(cli, name):
    """名称/代码/Label|key → 画布主键定位结果或 None。"""
    text = str(name).strip()
    if "|" in text:
        lbl, text = text.split("|", 1)
        labels = (lbl,) if lbl in CANVAS_LABELS else ()
    else:
        labels = CANVAS_LABELS
    for lbl in labels:
        key = CANVAS_LABELS[lbl][0]
        cond = "toString(n.%s) = $n" % key
        if "|" not in str(name) and lbl in _RESOLVE:
            cond += " OR " + _RESOLVE[lbl]
        cy = "MATCH (n:%s) WHERE %s RETURN n.%s AS k LIMIT 1" % (lbl, cond, key)
        with cli._session() as session:
            r = session.run(cy, n=text).single()
            if r and r["k"] is not None:
                return {"label": lbl, "name": str(r["k"])}
    return None


def _find_node(cli, ent):
    """ent（dict 或字符串）→ 图内节点定位。"""
    if isinstance(ent, dict):
        lbl = ent.get("label")
        if lbl not in CANVAS_LABELS:
            return None
        return _resolve(
            cli, ent.get("id") or "%s|%s" % (lbl, ent.get("key", ent.get("name", "")))
        )
    return _resolve(cli, ent)


def _node_match(e):
    """定位结果 → 画布主键 Cypher MATCH 片段。"""
    lbl = e["label"]
    key = CANVAS_LABELS[lbl][0]
    return (
        "(a:%s) WHERE toString(a.%s) = $an" % (lbl, key),
        "(b:%s) WHERE toString(b.%s) = $bn" % (lbl, key),
    )


def graph_path(a, b, hops=None, scope=None):
    """六度理论：两实体最短传导路径（Cypher shortestPath）。"""
    scope, params = _scope_params(scope)
    hops = min(int(hops or _MAX_PATH), 10)
    cli = KgClient(retry=1, retry_wait=1)
    try:
        ea, eb = _find_node(cli, a), _find_node(cli, b)
        if not ea or not eb:
            return {"ok": False, "error": "实体未找到：%s" % (a if not ea else b)}
        ma, _ = _node_match(ea)
        _, mb = _node_match(eb)
        path_where = (
            ("WHERE ALL(r IN relationships(p) WHERE %s) " % _rel_filter("r", scope))
            if scope
            else ""
        )
        cy = (
            "MATCH %s MATCH %s "
            "MATCH p = shortestPath((a)-[*..%d]-(b)) "
            + path_where
            + "RETURN [n IN nodes(p) | {ls:labels(n), p:properties(n), "
            "eid:elementId(n)}] AS ns, "
            "[r IN relationships(p) | {type:type(r), "
            "s:elementId(startNode(r)), "
            "t:elementId(endNode(r)), id:elementId(r), "
            "p:properties(r)}] AS rs, length(p) AS d"
        ) % (ma, mb, hops)
        with cli._session() as session:
            result = session.run(cy, an=ea["name"], bn=eb["name"], **params).single()
        if result:
            r = result
            nodes = []
            for raw in r["ns"]:
                lbl = next((l for l in raw["ls"] if l in CANVAS_LABELS), None)
                p = raw["p"] or {}
                if lbl and p.get(CANVAS_LABELS[lbl][0]) is not None:
                    key, cat, fields = CANVAS_LABELS[lbl]
                    value = p[key]
                    node_id = "%s|%s" % (lbl, value)
                    name = str(p.get(fields[0]) or value)[:24]
                    details = {f: _json_value(p.get(f)) for f in fields}
                else:
                    lbl, cat = "?", "unknown"
                    node_id = "?|%s" % raw["eid"]
                    name = str(p.get("name") or node_id)[:24]
                    details = {}
                nodes.append(
                    {
                        "id": node_id,
                        "label": lbl,
                        "category": cat,
                        "name": name,
                        "fields": details,
                        "terminal": lbl in CANVAS_TERMINAL or lbl == "?",
                    }
                )
            ids = {raw["eid"]: node["id"] for raw, node in zip(r["ns"], nodes)}
            rels = [rel["type"] for rel in r["rs"]]
            graph = {
                "nodes": nodes,
                "edges": [
                    {
                        "s": ids[rel["s"]],
                        "t": ids[rel["t"]],
                        "type": rel["type"],
                        "id": rel["id"],
                        "properties": _json_value(rel["p"] or {}),
                        "source": _json_value((rel["p"] or {}).get("source")),
                        "validStart": _json_value((rel["p"] or {}).get("validStart")),
                        "validEnd": _json_value((rel["p"] or {}).get("validEnd")),
                        "weight": _json_value((rel["p"] or {}).get("weight")),
                    }
                    for rel in r["rs"]
                ],
            }
            return {
                "ok": True,
                "a": ea,
                "b": eb,
                "hops": r["d"],
                "nodes": [n["name"] for n in nodes],
                "rels": rels,
                "六度内": r["d"] <= 6,
                "graph": graph,
                "scope": scope,
            }
        return {
            "ok": True,
            "a": ea,
            "b": eb,
            "hops": None,
            "nodes": [],
            "rels": [],
            "六度内": False,
            "graph": {"nodes": [], "edges": []},
            "scope": scope,
            "note": "%d 跳内无路径（不连通或超深）" % hops,
        }
    finally:
        cli.close()


def shared_neighbors(a, b, top=15, scope=None):
    """共同邻居：两实体的一跳交集（按 label 聚合）。"""
    scope, params = _scope_params(scope)
    cli = KgClient(retry=1, retry_wait=1)
    try:
        ea, eb = _find_node(cli, a), _find_node(cli, b)
        if not ea or not eb:
            return {"ok": False, "error": "实体未找到：%s" % (a if not ea else b)}
        ma, _ = _node_match(ea)
        _, mb = _node_match(eb)
        top = 15 if top is None else int(top)
        where = (
            ("WHERE %s AND %s " % (_rel_filter("r1", scope), _rel_filter("r2", scope)))
            if scope
            else ""
        )
        cy = (
            "MATCH %s MATCH %s MATCH (a)-[r1]-(x)-[r2]-(b) "
            + where
            + "RETURN labels(x) AS ls, properties(x) AS p, "
            "coalesce(x.name, x.ts_code, x.com_name, "
            "x.industry_name) AS nm, count(*) AS w "
            "ORDER BY w DESC LIMIT %d"
        ) % (ma, mb, top * 3)
        with cli._session() as session:
            rows = [
                {
                    "labels": [l for l in r["ls"] if l != "_GraphMetaData"],
                    "name": r["nm"],
                    "w": r["w"],
                    "id": _canvas_identity(r["ls"], r["p"] or {})[0],
                }
                for r in session.run(cy, an=ea["name"], bn=eb["name"], **params)
            ]
        by_label = Counter()
        for r in rows:
            for l in r["labels"]:
                by_label[l] += r["w"]
        return {
            "ok": True,
            "a": ea,
            "b": eb,
            "n": len(rows),
            "neighbors": rows[:top],
            "scope": scope,
            "by_label": dict(by_label.most_common(8)),
        }
    finally:
        cli.close()


def _subgraph(cli, seed, hops=2, scope=None):
    """seed N 跳子图（节点集 + 集内完整边）→ 原图 networkx.Graph。

    只收集 seed 放射边会得纯星形；补齐内边并按关系 ID 去重，
    同一对节点的平行关系保存在 relations 中。游资 seed 可选 hops=3。
    """
    import networkx as nx

    scope, params = _scope_params(scope)
    e = _find_node(cli, seed)
    if not e:
        return None, None, "实体未找到：%s" % seed
    ma, _ = _node_match(e)
    hops = min(int(hops or 2), 3)
    traversal = (
        ("WHERE ALL(edge IN relationships(p) WHERE %s) " % _rel_filter("edge", scope))
        if scope
        else ""
    )
    internal = (" AND %s" % _rel_filter("r", scope)) if scope else ""
    cy = (
        "MATCH %s "
        "OPTIONAL MATCH p = (a)-[*1..%d]-(x) "
        + traversal
        + "WITH a, [n IN collect(DISTINCT x) WHERE n <> a] AS candidates "
        "WITH a, size(candidates) > %d AS sampled, "
        "candidates[..%d] AS ns "
        "WITH a, sampled, ns + [a] AS all "
        "UNWIND all AS n "
        "OPTIONAL MATCH (n)-[r]-(m) WHERE m IN all" + internal + " "
        "RETURN properties(n) AS sp, labels(n) AS ls, "
        "properties(m) AS tp, labels(m) AS lt, "
        "elementId(r) AS rid, type(r) AS rtype, "
        "properties(r) AS rp, sampled"
    ) % (ma, hops, _MAX_SUB - 1, _MAX_SUB - 1)
    g = nx.Graph()
    sampled = False
    seen_rels = set()
    with cli._session() as session:
        for r in session.run(cy, an=e["name"], **params):
            sampled = r["sampled"]
            s, sl, sn = _canvas_identity(r["ls"], r["sp"] or {})
            t, tl, tn = _canvas_identity(r["lt"] or [], r["tp"] or {})
            if s:
                g.add_node(s, label=sl, name=sn)
            if t and s:
                g.add_node(t, label=tl, name=tn)
            if s and t and s != t:
                rid = r.get("rid")
                # 无向 MATCH 会返回同一关系的两个方向；按关系 ID 去重，平行关系保留。
                if rid is not None and rid in seen_rels:
                    continue
                if rid is not None:
                    seen_rels.add(rid)
                if not g.has_edge(s, t):
                    g.add_edge(s, t, relations=[])
                g[s][t]["relations"].append((r.get("rtype"), r.get("rp") or {}))
    seed_id = "%s|%s" % (e["label"], e["name"])
    if not g.has_node(seed_id):
        g.add_node(seed_id, label=e["label"], name=e["name"])
    g.graph["sampled"] = sampled
    g.graph["scope"] = scope
    return e, g, None


_LEAF_LABELS = {"MarketEvent", "Event", "IntelItem", "TradeDate"}
_HUB_LABELS = {"Region", "Industry", "SwL1Industry", "TradeDate"}
_STRONG_RELS = {"SUPPLIES_TO", "IN_GROUP", "OCCUPIES", "SERVES_AS"}
_WEIGHT_BASIS = (
    "算法语义权重：SUPPLIES_TO/IN_GROUP/OCCUPIES/SERVES_AS=3，"
    "其他=1；事件叶节点边×0.2，Region/Industry/SwL1Industry/TradeDate"
    "枢纽边×0.1；有效正数关系属性 weight 乘入（非数据库权重时使用1）；"
    "平行关系求和，原图度数为去重邻居数。核心排除事件及通用枢纽，"
    "附属按邻接总权重回填，按原图加权 modularity 评价。"
)


def _community_weights(g):
    """保留原图拓扑，仅在社群专用副本上计算语义权重。"""
    import networkx as nx

    weighted = nx.Graph()
    weighted.add_nodes_from(g.nodes(data=True))
    for u, v, data in g.edges(data=True):
        factor = (
            0.2
            if (
                g.nodes[u].get("label") in _LEAF_LABELS
                or g.nodes[v].get("label") in _LEAF_LABELS
            )
            else 1.0
        )
        if (
            g.nodes[u].get("label") in _HUB_LABELS
            or g.nodes[v].get("label") in _HUB_LABELS
        ):
            factor *= 0.1
        weight = 0.0
        for rtype, props in data.get("relations", [(None, {})]):
            raw = props.get("weight")
            db_weight = (
                float(raw)
                if isinstance(raw, (int, float))
                and not isinstance(raw, bool)
                and math.isfinite(raw)
                and raw > 0
                else 1.0
            )
            weight += (3.0 if rtype in _STRONG_RELS else 1.0) * db_weight * factor
        weighted.add_edge(u, v, weight=weight)
    return weighted


def community(seed, hops=2, scope=None):
    """语义加权 Louvain 核心划分，附属回填并修正社区连通性。"""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities, modularity

    scope, _ = _scope_params(scope)
    cli = KgClient(retry=1, retry_wait=1)
    try:
        e, g, err = _subgraph(cli, seed, hops, scope)
        if err:
            return {"ok": False, "error": err}
        result = {
            "ok": True,
            "seed": e,
            "n_nodes": g.number_of_nodes(),
            "n_edges": g.number_of_edges(),
            "sampled": g.graph["sampled"],
            "scope": scope,
            "algorithm": "语义加权 Louvain + 连通性修正",
            "weight_basis": _WEIGHT_BASIS,
        }
        if g.number_of_nodes() < 3:
            result.update(
                n_communities=0,
                communities=[],
                modularity=None,
                note="子图太小（<3 节点）",
            )
            return result
        weighted = _community_weights(g)
        core = {
            n for n in g if g.nodes[n].get("label") not in (_LEAF_LABELS | _HUB_LABELS)
        }
        core_graph = weighted.subgraph(core).copy()
        # 排序节点顺序使 Louvain 的随机种子在相同图上稳定。
        ordered = nx.Graph()
        ordered.add_nodes_from((n, core_graph.nodes[n]) for n in sorted(core))
        ordered.add_edges_from(
            (u, v, core_graph[u][v])
            for u, v in sorted((min(u, v), max(u, v)) for u, v in core_graph.edges)
        )
        groups = sorted(
            (set(c) for c in louvain_communities(ordered, seed=7, weight="weight")),
            key=lambda c: min(c),
        )
        owner = {n: i for i, c in enumerate(groups) for n in c}
        # 优先回填直接连接核心的节点，再迭代处理附属链；无核心的连通分量独立成群。
        pending = set(g) - set(owner)
        while pending:
            choices = []
            for n in sorted(pending):
                scores = Counter()
                for neighbor, data in weighted[n].items():
                    if neighbor in owner:
                        scores[owner[neighbor]] += data["weight"]
                if scores:
                    # 用固定的群组编号打破平局，避免回填改变群组最小成员后影响选择。
                    best = min(scores, key=lambda i: (-scores[i], i))
                    choices.append((n, best))
            if not choices:
                n = min(pending)
                component = nx.node_connected_component(weighted.subgraph(pending), n)
                groups.append(set(component))
                owner.update((node, len(groups) - 1) for node in component)
                pending -= component
                continue
            for n, i in choices:
                groups[i].add(n)
                owner[n] = i
                pending.remove(n)
        # Louvain 并不保证每个结果连通；回填也可能造成断裂。
        groups = [
            set(cc)
            for c in groups
            for cc in nx.connected_components(weighted.subgraph(c))
        ]
        groups.sort(key=lambda c: (-len(c), min(c)))
        out = []
        for i, c in enumerate(groups, 1):
            cores = c & core
            ranking = sorted(c, key=lambda n: (n not in cores, -g.degree(n), n))
            lbls = Counter(g.nodes[n].get("label", "?") for n in c)
            out.append(
                {
                    "id": i,
                    "size": len(c),
                    "core_members": sorted(cores),
                    "core_size": len(cores),
                    "attachment_size": len(c) - len(cores),
                    "labels": dict(lbls.most_common(4)),
                    "tops": [
                        {"id": n, "name": g.nodes[n]["name"], "deg": g.degree(n)}
                        for n in ranking[:6]
                    ],
                    "members": sorted(c),
                    "has_seed": "%s|%s" % (e["label"], e["name"]) in c,
                }
            )
        result.update(
            n_communities=len(out),
            communities=out,
            modularity=round(modularity(weighted, groups, weight="weight"), 4)
            if weighted.number_of_edges()
            else None,
        )
        return result
    finally:
        cli.close()


def centrality(seed, hops=2, top=10, scope=None):
    """中心性：degree / betweenness / pagerank 三口径 TopN。"""
    import networkx as nx

    scope, _ = _scope_params(scope)
    cli = KgClient(retry=1, retry_wait=1)
    try:
        e, g, err = _subgraph(cli, seed, hops, scope)
        if err:
            return {"ok": False, "error": err}
        if g.number_of_nodes() < 2:
            return {
                "ok": True,
                "seed": e,
                "n_nodes": g.number_of_nodes(),
                "n_edges": g.number_of_edges(),
                "sampled": g.graph["sampled"],
                "scope": scope,
                "degree": [],
                "betweenness": [],
                "pagerank": [],
            }
        top = 10 if top is None else int(top)
        deg = sorted(nx.degree_centrality(g).items(), key=lambda kv: -kv[1])[:top]
        bt = sorted(nx.betweenness_centrality(g).items(), key=lambda kv: -kv[1])[:top]
        pr = sorted(nx.pagerank(g).items(), key=lambda kv: -kv[1])[:top]
        return {
            "ok": True,
            "seed": e,
            "n_nodes": g.number_of_nodes(),
            "n_edges": g.number_of_edges(),
            "sampled": g.graph["sampled"],
            "scope": scope,
            "degree": [
                {"id": n, "name": g.nodes[n]["name"], "v": round(v, 4)} for n, v in deg
            ],
            "betweenness": [
                {"id": n, "name": g.nodes[n]["name"], "v": round(v, 4)} for n, v in bt
            ],
            "pagerank": [
                {"id": n, "name": g.nodes[n]["name"], "v": round(v, 5)} for n, v in pr
            ],
        }
    finally:
        cli.close()


def run(sub, a, b=None, hops=None, top=None, scope=None):
    """子命令分发（CLI / kg_portal 共用）。"""
    if sub == "path":
        return graph_path(a, b, hops, scope)
    if sub == "shared":
        return shared_neighbors(a, b, top, scope)
    if sub == "community":
        return community(a, hops, scope)
    if sub == "centrality":
        return centrality(a, hops, top, scope)
    return {
        "ok": False,
        "error": "未知子命令 %s（path/shared/community/centrality）" % sub,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=["path", "shared", "community", "centrality"])
    ap.add_argument("a")
    ap.add_argument("b", nargs="?", default=None)
    ap.add_argument("--hops", type=int, default=None)
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()
    print(
        json.dumps(
            run(args.sub, args.a, args.b, args.hops, args.top),
            ensure_ascii=False,
            indent=1,
        )
    )
