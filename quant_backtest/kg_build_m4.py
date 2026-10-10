# -*- coding: utf-8 -*-
"""M4-W2/W3：概念层入图（Concept + BELONGS_TO 层级 + REQUIRES 依赖 + MAPS_TO 标的挂接）。

M4-W3 两源分层融合（解决 GPU=198 半导体全家桶失真）：
  · 根概念（算力/铜连接/通信/电力）：行业桥 via=alias + industry CONTAINS 补漏
    ——宽基语义，行业级粒度可接受
  · 细概念：Company.main_business CONTAINS keywords（via=biztext）
    ——宁缺毋滥；同子树内具体词优先（命中深层概念则其祖先不再挂）
  · keywords 空的细概念（AI芯片/ASIC/HBM/CPO 等）保持断链：实证缺口

词表：data/concept_dict.csv（keywords 列，| 分隔同义词）
     data/concept_industry_alias.csv（仅根概念行业桥）

断链（如 ASIC/HBM 无主营文本命中）是实证发现不是缺陷：
explore 时显式标注，提示数据缺口。

用法：
  python kg_build_m4.py            # 实跑入图
  python kg_build_m4.py --dryrun   # 只打印规模（完全只读）
"""
import sys
import os
import csv
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg_schema as S
from kg_client import KgClient

HERE = os.path.dirname(os.path.abspath(__file__))
DRYRUN = "--dryrun" in sys.argv
DICT_CSV = os.path.join(HERE, "data", "concept_dict.csv")
REQ_CSV = os.path.join(HERE, "data", "concept_requires.csv")
ALIAS_CSV = os.path.join(HERE, "data", "concept_industry_alias.csv")
BUILD_DATE = "20260929"  # 概念层建立日（人工词表版本标记；MERGE 键含此值，勿改）


def load_dict():
    """读词表：返回 (concepts, hier, keywords)。
    concepts: [{name,is_root,level}] level=真实深度（根=0）
    keywords: {概念名: [关键词]}（仅 keywords 非空的细概念）
    """
    rows = list(csv.DictReader(open(DICT_CSV, encoding="utf-8")))
    parent = {r["name"]: (r.get("parent") or "").strip() for r in rows}
    keywords = {}
    concepts, hier = [], []
    for r in rows:
        nm, pa = r["name"], parent[r["name"]]
        kws = [w for w in (r.get("keywords") or "").split("|") if w]
        # 真实深度：沿 parent 链上溯
        lv, p = 0, pa
        while p:
            lv += 1
            p = parent.get(p, "")
        concepts.append({"name": nm, "is_root": pa == "", "level": lv})
        if pa:
            hier.append({"src": nm, "dst": pa,
                         "validStart": BUILD_DATE, "validEnd": S.VALID_END_OPEN,
                         "source": "concept_dict", "updated_at": datetime.now().isoformat()})
        if kws and pa:  # 细概念才有 biztext 匹配（根概念 keywords 必空）
            keywords[nm] = kws
    # 祖先链（用于具体词优先跳过）
    anc = {}
    for c in concepts:
        nm, chain = c["name"], set()
        p = parent[nm]
        while p:
            chain.add(p)
            p = parent.get(p, "")
        anc[nm] = chain
    return concepts, hier, keywords, anc


def build(cli):
    s = cli._session()
    concepts, hier, keywords, anc = load_dict()
    roots = [c["name"] for c in concepts if c["is_root"]]
    fine = [c["name"] for c in concepts if not c["is_root"]]
    print("Concept %d 个（根 %d），BELONGS_TO %d 条，biztext 细概念 %d 个"
          % (len(concepts), len(roots), len(hier), len(keywords)))

    # 2) REQUIRES 人工边
    reqs = []
    with open(REQ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            src, dst = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
            if src and dst:
                reqs.append({"src": src, "dst": dst,
                             "validStart": BUILD_DATE, "validEnd": S.VALID_END_OPEN,
                             "source": "manual", "updated_at": datetime.now().isoformat()})
    print("REQUIRES %d 条" % len(reqs))

    maps = {}
    now = datetime.now().isoformat()

    def add_map(ts_code, concept, via):
        k = (ts_code, concept)
        if k not in maps:  # 先到先得：细概念 biztext 与根概念行业桥独立，无同对冲突
            maps[k] = {"src": ts_code, "dst": concept, "via": via,
                       "validStart": BUILD_DATE, "validEnd": S.VALID_END_OPEN,
                       "source": {"alias": "m4_alias", "industry": "m4_industry",
                                  "biztext": "m4_biztext"}[via],
                       "updated_at": now}

    # 3a) 根概念行业桥（alias，仅根概念——细概念行业桥已在 M4-W3 移除）
    alias_pairs = []
    with open(ALIAS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cp, ind = (r.get("concept") or "").strip(), (r.get("industry") or "").strip()
            if not (cp and ind):
                continue
            if cp not in roots:
                print("  [警告] alias 表含非根概念 %s，跳过（细概念只走 biztext）" % cp)
                continue
            alias_pairs.append((cp, ind))
    for cp, ind in alias_pairs:
        for r in s.run("MATCH (i:Industry {name:$ind})<-[:INDUSTRY_OF]-(x:Security) "
                       "RETURN x.ts_code AS c", ind=ind):
            add_map(r["c"], cp, "alias")
    n_alias = len(maps)
    print("MAPS_TO(alias 根概念行业桥) %d 条（%d 桥）" % (n_alias, len(alias_pairs)))

    # 3b) 根概念 industry CONTAINS 补漏（窄但准）
    for rn in roots:
        for r in s.run("MATCH (x:Security) WHERE x.industry CONTAINS $w "
                       "RETURN x.ts_code AS c", w=rn):
            add_map(r["c"], rn, "industry")
    n_ind = len(maps) - n_alias
    print("MAPS_TO(industry 根概念补漏) +%d 条" % n_ind)

    # 3c) 细概念 biztext：main_business CONTAINS keywords
    #     具体词优先：按深度从深到浅匹配，命中即挂并跳过其祖先链
    depth = {c["name"]: c["level"] for c in concepts}
    order = sorted(keywords, key=lambda x: -depth[x])
    n_mb = 0
    for rec in s.run("MATCH (sec:Security)-[:OF_COMPANY]->(c:Company) "
                     "WHERE c.main_business IS NOT NULL "
                     "RETURN sec.ts_code AS tc, c.main_business AS mb"):
        n_mb += 1
        mb, blocked = rec["mb"], set()
        for cp in order:
            if cp in blocked:
                continue
            if any(w in mb for w in keywords[cp]):
                add_map(rec["tc"], cp, "biztext")
                blocked |= anc[cp]
    n_biz = len(maps) - n_alias - n_ind
    print("MAPS_TO(biztext 细概念主营匹配) +%d 条（扫描 main_business %d 家）"
          % (n_biz, n_mb))

    print("MAPS_TO 合计 %d 条" % len(maps))

    # 每概念标的数一览（断链可见）
    cnt = {}
    for (tc, cp), e in maps.items():
        cnt[cp] = cnt.get(cp, 0) + 1
    zero = [nm for nm in fine if nm not in cnt]
    print("断链细概念（0 标的挂接，实证缺口）: %s"
          % (" ".join(zero) if zero else "无"))

    if DRYRUN:
        print("[dryrun] 未写库")
        return

    # 4) 清理：细概念旧 MAPS_TO（M4-W2 的 via=alias/industry 行业级失真边）
    #    via=biztext 的边由 MERGE 幂等更新
    r = s.run("MATCH (:Security)-[e:MAPS_TO]->(c:Concept) "
              "WHERE c.name IN $fine AND e.via <> 'biztext' "
              "DELETE e RETURN count(e) AS n", fine=fine).single()
    print("清理细概念旧行业级 MAPS_TO %d 条" % r["n"])

    cli.upsert_nodes(S.LBL_CONCEPT, ["name"], concepts)
    cli.upsert_edge(S.LBL_CONCEPT, "name", S.LBL_CONCEPT, "name",
                    S.REL_BELONGS_TO, hier)
    cli.upsert_edge(S.LBL_CONCEPT, "name", S.LBL_CONCEPT, "name",
                    S.REL_REQUIRES, reqs)
    cli.upsert_edge(S.LBL_SECURITY, "ts_code", S.LBL_CONCEPT, "name",
                    S.REL_MAPS_TO, list(maps.values()))
    print("入图完成")
    cli.close()


if __name__ == "__main__":
    t0 = time.time()
    cli = KgClient()
    cli.ensure_schema()
    build(cli)
    print("耗时 %.1fs" % (time.time() - t0))
