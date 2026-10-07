# -*- coding: utf-8 -*-
"""K201-D · 四派共识聚合入图（情报本体评估层）。

本体：Commentator（四派分析师，评估主体）-[ASSESSED {verdict,stars,theme,
date}]-> Security。verdict ✓=SUPPORTS / ✗=CONTRADICTS / 缺失=不评估。
种子：data/kg_consensus_seeds.json（从用户分析产出转录，可人工编辑后
重跑 build——upsert 幂等）。

用法：python kg_consensus.py build | query <主题名>
query 输出与用户共识矩阵同构（标的×派系 verdict + 共识度聚合）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SEEDS = os.path.join(HERE, "data", "kg_consensus_seeds.json")
from kg_client import KgClient               # noqa: E402
import kg_schema as S                        # noqa: E402


def build(cli=None, dryrun=False):
    cli = cli or KgClient(retry=1)
    with open(SEEDS, encoding="utf-8") as f:
        seeds = json.load(f)
    schools = seeds.get("schools") or []
    comms = [{"name": sc, "kind": "school"} for sc in schools]
    assessed, miss = [], []
    with cli._session() as s:
        gnames = {r["n"]: r["code"] for r in s.run(
            "MATCH (sec:Security) WHERE sec.name IS NOT NULL "
            "RETURN sec.name AS n, sec.ts_code AS code").data()}
    for th in seeds.get("themes") or []:
        theme = th["theme"]
        for row in th.get("rows") or []:
            code = gnames.get(row["name"])
            if not code:
                miss.append(row["name"])
                continue
            for sc, v in (row.get("verdicts") or {}).items():
                assessed.append({"src": sc, "dst": code,
                                 "verdict": v, "stars": row.get("stars") or 0,
                                 "theme": theme, "date": th.get("date") or "",
                                 "validStart": th.get("date") or "",
                                 "validEnd": S.VALID_END_OPEN,
                                 "source": "kg_consensus_seeds"})
    print("seeds: %d schools / %d assessments（miss 标的 %d）"
          % (len(comms), len(assessed), len(miss)))
    if miss:
        print("WARN 标的未匹配 Security.name: %s" % miss)
    if dryrun:
        return {"assessed": len(assessed)}
    cli.upsert_nodes(S.LBL_COMMENTATOR, ["name"], comms)
    cli.upsert_edge(S.LBL_COMMENTATOR, "name", S.LBL_SECURITY, "ts_code",
                    S.REL_ASSESSED, assessed)
    with cli._session() as s:
        n = s.run("MATCH (:Commentator)-[r:ASSESSED]->(:Security) "
                  "RETURN count(r) AS c").single()["c"]
    print("graph: Commentator %d | ASSESSED %d" % (len(comms), n))
    return {"assessed": n}


def query(theme):
    """共识矩阵：标的 × 派系 verdict + 共识度聚合（与用户矩阵同构）。"""
    cli = KgClient(retry=1)
    with cli._session() as s:
        schools = [r["n"] for r in s.run(
            "MATCH (c:Commentator) RETURN c.name AS n ORDER BY n").data()]
        rows = s.run(
            "MATCH (c:Commentator)-[a:ASSESSED {theme:$t}]->(sec:Security) "
            "RETURN sec.name AS n, c.name AS school, a.verdict AS v, "
            "a.stars AS st ORDER BY n", t=theme).data()
    if not rows:
        print("（%s 无共识评估记录）" % theme)
        return
    by_t = {}
    stars = {}
    for r in rows:
        by_t.setdefault(r["n"], {})[r["school"]] = r["v"]
        stars[r["n"]] = r["st"]
    hdr = "标的".ljust(10) + "".join(
        sc.ljust(8) for sc in ["技术派", "价值派", "概念派", "综合派"]) + "共识度"
    print("== %s 共识矩阵 ==" % theme)
    print(hdr)
    for name in sorted(by_t):
        cells = "".join(
            (by_t[name].get(sc, "–") + "     ").ljust(8)
            for sc in ["技术派", "价值派", "概念派", "综合派"])
        st = stars.get(name) or 0
        print(name.ljust(10) + cells + "★" * st + "☆" * max(0, 5 - st))
    print("\n共 %d 标的 / %d 条派系评估" % (len(by_t), len(rows)))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "query"])
    ap.add_argument("arg", nargs="?", default="")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    else:
        query(a.arg)


if __name__ == "__main__":
    main()
