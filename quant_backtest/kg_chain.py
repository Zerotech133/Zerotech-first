# -*- coding: utf-8 -*-
"""K201-C · 产业链知识工程入图（情报本体结构层打样）。

本体：Theme -[CONDUCTION {type}]-> Segment(一级) <-[SUB_OF]- Segment(二级)，
Company(图内 Security by name) -[SUPPLIES_TO {evidence,chain}]-> Segment。
种子：data/kg_chain_seeds.json（人工知识+共识矩阵/K200 接力锚点，可人工
编辑后重跑 build——upsert 幂等）。标的按图内 Security.name 实体链接，
MISS 不入边仅 WARN（防错挂）。

用法：python kg_chain.py build [--dryrun] | query <主题名>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SEEDS = os.path.join(HERE, "data", "kg_chain_seeds.json")
from kg_client import KgClient  # noqa: E402
import kg_schema as S  # noqa: E402


def _load_seeds():
    with open(SEEDS, encoding="utf-8") as f:
        return json.load(f)


def build(cli=None, dryrun=False):
    cli = cli or KgClient(retry=1)
    seeds = _load_seeds()
    themes, segs, cond_edges, sub_edges, sup_edges = [], [], [], [], []
    seg_keys = set()
    miss = []
    for chain in seeds.get("chains") or []:
        theme = chain["theme"]
        themes.append({"name": theme, "snapshot": seeds.get("version", "")})
        for seg in chain.get("segments") or []:
            name = seg["name"]
            if name in seg_keys:
                continue
            seg_keys.add(name)
            segs.append(
                {
                    "name": name,
                    "level": seg.get("level") or 1,
                    "chain": theme,
                    "desc": seg.get("desc") or "",
                }
            )
            cond = seg.get("conduction")
            if cond and seg.get("level") == 1:
                cond_edges.append(
                    {
                        "src": theme,
                        "dst": name,
                        "type": cond,
                        "validStart": seeds.get("version", ""),
                        "validEnd": S.VALID_END_OPEN,
                        "source": "kg_chain_seeds",
                    }
                )
            if seg.get("parent"):
                sub_edges.append(
                    {
                        "src": name,
                        "dst": seg["parent"],
                        "validStart": seeds.get("version", ""),
                        "validEnd": S.VALID_END_OPEN,
                        "source": "kg_chain_seeds",
                    }
                )
        for c in chain.get("conductions") or []:
            if c.get("seg") in seg_keys:
                cond_edges.append(
                    {
                        "src": theme,
                        "dst": c["seg"],
                        "type": c["type"],
                        "validStart": seeds.get("version", ""),
                        "validEnd": S.VALID_END_OPEN,
                        "source": "kg_chain_seeds",
                    }
                )
        for sp in chain.get("supplies") or []:
            sup_edges.append(
                {
                    "src_name": sp["name"],
                    "dst": sp["seg"],
                    "evidence": sp.get("evidence") or "",
                    "chain": theme,
                    "source": "kg_chain_seeds",
                }
            )
    print(
        "seeds: %d themes / %d segments / %d conductions / "
        "%d supplies" % (len(themes), len(segs), len(cond_edges), len(sup_edges))
    )
    if dryrun:
        return {
            "themes": len(themes),
            "segments": len(segs),
            "supplies": len(sup_edges),
        }

    cli.upsert_nodes(S.LBL_THEME, ["name"], themes)
    cli.upsert_nodes(S.LBL_SEGMENT, ["name"], segs)
    cli.upsert_edge(
        S.LBL_THEME, "name", S.LBL_SEGMENT, "name", S.REL_CONDUCTION, cond_edges
    )
    cli.upsert_edge(
        S.LBL_SEGMENT, "name", S.LBL_SEGMENT, "name", S.REL_SUB_OF, sub_edges
    )
    # 实体链接：Company 名 → 图内 Security.name；MISS 不入边仅 WARN
    with cli._session() as s:
        gnames = {
            r["n"]: r["code"]
            for r in s.run(
                "MATCH (sec:Security) WHERE sec.name IS NOT NULL "
                "RETURN sec.name AS n, sec.ts_code AS code"
            ).data()
        }
    linked = []
    for e in sup_edges:
        code = gnames.get(e["src_name"])
        if not code:
            miss.append(e["src_name"])
            continue
        linked.append(
            {
                "src": code,
                "dst": e["dst"],
                "evidence": e["evidence"],
                "chain": e["chain"],
                "company": e["src_name"],
                "validStart": seeds.get("version", ""),
                "validEnd": S.VALID_END_OPEN,
                "source": "kg_chain_seeds",
            }
        )
    if miss:
        print("WARN 标的未匹配 Security.name: %s" % miss)
    if linked:
        cli.upsert_edge(
            S.LBL_SECURITY, "ts_code", S.LBL_SEGMENT, "name", S.REL_SUPPLIES_TO, linked
        )
    with cli._session() as s:
        n_seg = s.run("MATCH (g:Segment) RETURN count(g) AS c").single()["c"]
        n_sup = s.run(
            "MATCH (:Security)-[r:SUPPLIES_TO]->(:Segment) RETURN count(r) AS c"
        ).single()["c"]
        n_cond = s.run(
            "MATCH (:Theme)-[r:CONDUCTION]->(:Segment) RETURN count(r) AS c"
        ).single()["c"]
    print(
        "graph: Segment %d | SUPPLIES_TO %d | CONDUCTION %d | "
        "miss %d" % (n_seg, n_sup, n_cond, len(miss))
    )
    return {"segments": n_seg, "supplies": n_sup, "miss": miss}


def query(theme):
    """传导查询：主题 → 一级环节(传导类型) → 二级环节 → 标的。"""
    cli = KgClient(retry=1)
    with cli._session() as s:
        l1 = s.run(
            "MATCH (t:Theme {name:$t})-[c:CONDUCTION]->(g:Segment) "
            "RETURN g.name AS seg, c.type AS type ORDER BY seg",
            t=theme,
        ).data()
        print("== %s 传导链 ==" % theme)
        for r in l1:
            print("  [%s] %s" % (r["type"], r["seg"]))
            subs = s.run(
                "MATCH (sub:Segment)-[:SUB_OF]->(g:Segment "
                "{name:$g}) RETURN sub.name AS s ORDER BY s",
                g=r["seg"],
            ).data()
            for x in subs:
                print("      └ %s" % x["s"])
            sup = s.run(
                "MATCH (sec:Security)-[sp:SUPPLIES_TO]->(g2:Segment) "
                "WHERE g2.name = $g OR (g2)-[:SUB_OF]->"
                "(:Segment {name:$g}) "
                "RETURN sec.name AS n, g2.name AS g2, "
                "sp.evidence AS ev ORDER BY g2, n",
                g=r["seg"],
            ).data()
            for x in sup:
                print("      ★ %s @%s（%s）" % (x["n"], x["g2"], (x["ev"] or "")[:36]))


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "query"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        cli = KgClient(retry=1)
        cli.ensure_schema()
        build(cli, dryrun=a.dryrun)
    else:
        query(a.arg)


if __name__ == "__main__":
    main()
