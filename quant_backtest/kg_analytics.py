# -*- coding: utf-8 -*-
"""K261 L2：图分析算法层 —— 六度路径 / 共同邻居 / 社群 / 中心性。

用户命题（K261 立项原话）：「组织架构、网络架构、计算中心程度、
计算社群关系、六度理论」——在图谱上以图论方式把关系讲明白。

算法与实现：
  · path（六度理论）  Cypher 原生 shortestPath（限深默认 6 跳，快）
  · shared（共同邻居） Cypher 一跳交集，按 label 聚合
  · community（社群）  seed N 跳子图（上限 600 节点）→ networkx louvain
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
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient   # noqa: E402

# 实体定位字段（与 kg_portal.LINK_LABELS 同源，精简为可搜索主字段）
_RESOLVE = [
    ("Security", "n.ts_code = $n OR n.name = $n OR $n IN coalesce(n.aliases, [])"),
    ("Company", "n.com_name = $n OR toString(n.com_id) = $n"),
    ("Concept", "n.name = $n"),
    ("Product", "n.name = $n"),
    ("Industry", "n.name = $n OR n.industry_name = $n"),
    ("SwL1Industry", "n.industry_name = $n"),
    ("Theme", "n.name = $n"),
    ("Segment", "n.name = $n OR n.chain = $n"),
    ("Technique", "n.name = $n"),
    ("HotMoney", "n.name = $n"),
    ("Commentator", "n.name = $n"),
    ("Seat", "n.name = $n"),
    ("Region", "n.name = $n"),
]
_MAX_SUB = 600        # 子图节点上限（社群/中心性，防大图拖死）
_MAX_PATH = 6         # 六度：默认 6 跳


def _resolve(cli, name):
    """名称/代码 → {label, key, name} 或 None。"""
    for lbl, cond in _RESOLVE:
        cy = ("MATCH (n:%s) WHERE %s RETURN n.name AS nm LIMIT 1"
              % (lbl, cond))
        for r in cli._session().run(cy, n=str(name).strip()):
            return {"label": lbl, "name": r["nm"] or name}
    return None


def _find_node(cli, ent):
    """ent（dict 或字符串）→ 图内节点定位。"""
    if isinstance(ent, dict) and ent.get("label"):
        return ent
    return _resolve(cli, ent)


def _node_match(e):
    """定位结果 → Cypher MATCH 片段。"""
    lbl = e["label"]
    if lbl == "Security":
        return ("(a:Security {name:$an})",
                "(b:Security {name:$bn})")
    if lbl in ("Industry", "SwL1Industry"):
        return ("(a:%s {industry_name:$an})" % lbl,
                "(b:%s {industry_name:$bn})" % lbl)
    return ("(a:%s {name:$an})" % lbl, "(b:%s {name:$bn})" % lbl)


def graph_path(a, b, hops=None):
    """六度理论：两实体最短传导路径（Cypher shortestPath）。"""
    hops = min(int(hops or _MAX_PATH), 10)
    cli = KgClient(retry=1, retry_wait=1)
    try:
        ea, eb = _find_node(cli, a), _find_node(cli, b)
        if not ea or not eb:
            return {"ok": False, "error": "实体未找到：%s" % (
                a if not ea else b)}
        ma, _ = _node_match(ea)
        # b 侧 label 可能不同 → 分别生成
        mb = "(b:%s)" % eb["label"]
        if eb["label"] in ("Industry", "SwL1Industry"):
            mb = "(b:%s {industry_name:$bn})" % eb["label"]
        elif eb["label"] != "Security" or True:
            mb = "(b:%s {name:$bn})" % eb["label"]
        cy = ("MATCH %s MATCH %s "
              "MATCH p = shortestPath((a)-[*..%d]-(b)) "
              "RETURN [n IN nodes(p) | coalesce(n.name, n.ts_code, "
              "n.com_name, n.industry_name)] AS ns, "
              "[r IN relationships(p) | type(r)] AS rs, length(p) AS d"
              % (ma, mb, hops))
        for r in cli._session().run(cy, an=ea["name"], bn=eb["name"]):
            return {"ok": True, "a": ea, "b": eb, "hops": r["d"],
                    "nodes": r["ns"], "rels": r["rs"],
                    "六度内": r["d"] <= 6}
        return {"ok": True, "a": ea, "b": eb, "hops": None,
                "nodes": [], "rels": [], "六度内": False,
                "note": "%d 跳内无路径（不连通或超深）" % hops}
    finally:
        cli.close()


def shared_neighbors(a, b, top=15):
    """共同邻居：两实体的一跳交集（按 label 聚合）。"""
    cli = KgClient(retry=1, retry_wait=1)
    try:
        ea, eb = _find_node(cli, a), _find_node(cli, b)
        if not ea or not eb:
            return {"ok": False, "error": "实体未找到：%s" % (
                a if not ea else b)}
        ma, _ = _node_match(ea)
        mb = "(b:%s {name:$bn})" % eb["label"]
        cy = ("MATCH %s MATCH %s MATCH (a)--(x)--(b) "
              "RETURN labels(x) AS ls, coalesce(x.name, x.ts_code, "
              "x.com_name, x.industry_name) AS nm, count(*) AS w "
              "ORDER BY w DESC LIMIT %d" % (ma, mb, int(top) * 3))
        rows = [{"labels": [l for l in r["ls"] if l != "_GraphMetaData"],
                 "name": r["nm"], "w": r["w"]}
                for r in cli._session().run(cy, an=ea["name"], bn=eb["name"])]
        by_label = Counter()
        for r in rows:
            for l in r["labels"]:
                by_label[l] += r["w"]
        return {"ok": True, "a": ea, "b": eb, "n": len(rows),
                "neighbors": rows[:int(top)],
                "by_label": dict(by_label.most_common(8))}
    finally:
        cli.close()


def _subgraph(cli, seed, hops=2):
    """seed N 跳子图（节点集 + 集内完整边）→ networkx.Graph。

    ⚠️ 只收集 seed 放射边会得纯星形（社群恒为 1）——必须补齐
    节点集内部互边，louvain 才有结构。游资类 seed 建议 hops=3
    （引入 股票→概念→股票 层，社群才有意义）。
    """
    import networkx as nx
    e = _find_node(cli, seed)
    if not e:
        return None, None, "实体未找到：%s" % seed
    ma, _ = _node_match(e)
    hops = min(int(hops or 2), 3)
    cy = ("MATCH %s "
          "OPTIONAL MATCH (a)-[*1..%d]-(x) "
          "WITH a, collect(DISTINCT x)[..%d] AS ns "
          "WITH ns + [a] AS all "
          "UNWIND all AS n "
          "MATCH (n)-[r]-(m) WHERE m IN all "
          "RETURN coalesce(n.name, n.ts_code, n.com_name, "
          "n.industry_name) AS s, labels(n) AS ls, "
          "coalesce(m.name, m.ts_code, m.com_name, "
          "m.industry_name) AS t, labels(m) AS lt" % (ma, hops, _MAX_SUB))
    g = nx.Graph()
    for r in cli._session().run(cy, an=e["name"]):
        s, t = r["s"], r["t"]
        if not s or not t or s == t:
            continue                       # 无名节点/自环跳过
        for nm, ls in ((s, r["ls"]), (t, r["lt"])):
            if nm and not g.has_node(nm):
                lbl = [l for l in ls if l != "_GraphMetaData"]
                g.add_node(nm, label=lbl[0] if lbl else "?")
        g.add_edge(s, t)
    g.add_node(e["name"], label=e["label"])   # 防孤立 seed
    return e, g, None


def community(seed, hops=2):
    """社群检测：seed 子图 louvain → 社群划分+代表节点。"""
    from networkx.algorithms.community import louvain_communities
    cli = KgClient(retry=1, retry_wait=1)
    try:
        e, g, err = _subgraph(cli, seed, hops)
        if err:
            return {"ok": False, "error": err}
        if g.number_of_nodes() < 3:
            return {"ok": True, "seed": e, "n_nodes": g.number_of_nodes(),
                    "communities": [], "note": "子图太小（<3 节点）"}
        comms = louvain_communities(g, seed=7, weight=None)
        out = []
        for i, c in enumerate(sorted(comms, key=len, reverse=True)[:12], 1):
            deg = sorted(((g.degree(n), n) for n in c), reverse=True)
            lbls = Counter(g.nodes[n].get("label", "?") for n in c)
            out.append({"id": i, "size": len(c),
                        "labels": dict(lbls.most_common(4)),
                        "tops": [{"name": n, "deg": d} for d, n in deg[:6]],
                        "members": sorted(c)[:60],
                        "has_seed": e["name"] in c})
        return {"ok": True, "seed": e, "n_nodes": g.number_of_nodes(),
                "n_edges": g.number_of_edges(),
                "n_communities": len(comms), "communities": out}
    finally:
        cli.close()


def centrality(seed, hops=2, top=10):
    """中心性：degree / betweenness / pagerank 三口径 TopN。"""
    import networkx as nx
    cli = KgClient(retry=1, retry_wait=1)
    try:
        e, g, err = _subgraph(cli, seed, hops)
        if err:
            return {"ok": False, "error": err}
        if g.number_of_nodes() < 2:
            return {"ok": True, "seed": e, "n_nodes": g.number_of_nodes(),
                    "degree": [], "betweenness": [], "pagerank": []}
        deg = sorted(nx.degree_centrality(g).items(),
                     key=lambda kv: -kv[1])[:int(top)]
        bt = sorted(nx.betweenness_centrality(g).items(),
                    key=lambda kv: -kv[1])[:int(top)]
        pr = sorted(nx.pagerank(g).items(),
                    key=lambda kv: -kv[1])[:int(top)]
        return {"ok": True, "seed": e, "n_nodes": g.number_of_nodes(),
                "n_edges": g.number_of_edges(),
                "degree": [{"name": n, "v": round(v, 4)} for n, v in deg],
                "betweenness": [{"name": n, "v": round(v, 4)} for n, v in bt],
                "pagerank": [{"name": n, "v": round(v, 5)} for n, v in pr]}
    finally:
        cli.close()


def run(sub, a, b=None, hops=None, top=None):
    """子命令分发（CLI / kg_portal 共用）。"""
    if sub == "path":
        return graph_path(a, b, hops)
    if sub == "shared":
        return shared_neighbors(a, b, top)
    if sub == "community":
        return community(a, hops)
    if sub == "centrality":
        return centrality(a, hops, top)
    return {"ok": False, "error": "未知子命令 %s（path/shared/community/"
            "centrality）" % sub}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=["path", "shared", "community",
                                    "centrality"])
    ap.add_argument("a")
    ap.add_argument("b", nargs="?", default=None)
    ap.add_argument("--hops", type=int, default=None)
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()
    print(json.dumps(run(args.sub, args.a, args.b, args.hops, args.top),
                     ensure_ascii=False, indent=1))
