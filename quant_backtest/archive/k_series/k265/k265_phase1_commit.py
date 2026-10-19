# -*- coding: utf-8 -*-
"""K265 Phase 1 入图：影子 → Neo4j（含品牌概念过滤）。

规则：
- 仅 ext.ok 且 conf>=min-conf（默认 0.8）
- 品牌概念（XX概念 的 XX 是公司/品牌名）不得作为 SUPPLIES 的 src
  （品牌股票池不是"供应商"；作为 DRIVES src / SUPPLIES dst 仍允许）
- 边属性：source=concept_llm_v1（整体回滚键）+ conf/reason/overlap/batch
用法：
  python k265_phase1_commit.py --batch b2 --from 300 --count 2000 --min-conf 0.95
"""
import json
import re
import sys

sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
from kg_client import KgClient  # noqa: E402

SHADOW = r"C:\项目\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl"

# 人工品牌名单（图内 Company 字典之外的知名品牌/厂商）
MANUAL_BRANDS = {
    "华为", "苹果", "特斯拉", "小米", "比亚迪", "宁德时代", "百度", "腾讯",
    "阿里巴巴", "字节跳动", "拼多多", "京东", "中芯国际", "寒武纪", "英伟达",
    "DeepSeek", "OpenAI", "宇树", "优必选", "科大讯飞", "海康威视", "大疆",
    "OPPO", "vivo", "荣耀", "魅族", "蔚来", "理想汽车", "小鹏汽车", "长城汽车",
    "上汽集团", "广汽集团", "一汽解放", "长安汽车", "奇瑞汽车",
}

_BRAND_SUFFIX = re.compile(r"(概念|产业链|概念股|指数|生态圈|供应链)$")


def build_brands(cli):
    """图内公司名 + 人工名单 → 品牌短名集合（归一口径）。"""
    brands = set(MANUAL_BRANDS)
    cy = ("MATCH (c:Company) RETURN c.name AS nm UNION "
          "MATCH (s:Security) RETURN s.name AS nm")
    for r in cli._session().run(cy):
        nm = str(r["nm"] or "").strip()
        if 2 <= len(nm) <= 6:  # 短名才可能是品牌（华为/比亚迪/宁德时代）
            brands.add(nm)
    return brands


def is_brand_concept(name, brands):
    stem = _BRAND_SUFFIX.sub("", name or "")
    return stem in brands


def main():
    args = sys.argv[1:]

    def arg(name, default, cast):
        return cast(args[args.index(name) + 1]) if name in args else default

    batch = arg("--batch", "b1", str)
    frm = arg("--from", 0, int)
    cnt = arg("--count", 10 ** 9, int)
    min_conf = arg("--min-conf", 0.8, float)
    # b3 起：人审排除清单（每行 src|dst，映射后方向；b3 抽样剔除
    # 方向反/无直接链/DRIVES 混入条目），优先于品牌过滤
    excl_file = arg("--exclude-file", "", str)
    excl_set = set()
    if excl_file:
        for ln in open(excl_file, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                excl_set.add(tuple(p.strip() for p in ln.split("|")))
        print(f"[0] manual exclude: {len(excl_set)} pairs")

    cli = KgClient(retry=1, retry_wait=1)
    brands = build_brands(cli)
    print(f"[1] brand pool: {len(brands)}")

    rows = [json.loads(l) for l in open(SHADOW, encoding="utf-8")]
    rows = rows[frm:frm + cnt]
    ok = [r for r in rows if r["ext"].get("ok")]
    conf_ok = [r for r in ok if (r["ext"].get("conf") or 0) >= min_conf]
    print(f"[2] batch slice [{frm}:{frm+cnt}] rows={len(rows)} ok={len(ok)} "
          f"conf>={min_conf}={len(conf_ok)}")

    dropped, mdropped, kept = [], [], []
    for r in conf_ok:
        e = r["ext"]
        src = r["a"] if e.get("src") == "A" else r["b"]
        dst = r["b"] if e.get("src") == "A" else r["a"]
        if (src, dst) in excl_set:
            mdropped.append((src, dst))
            continue
        if e.get("edge") == "SUPPLIES" and is_brand_concept(src, brands):
            dropped.append((src, dst))
            continue
        kept.append((src, dst, e, r["overlap"]))
    print(f"[3] manual-dropped={len(mdropped)} brand-dropped={len(dropped)} "
          f"kept={len(kept)}")
    for s, d in mdropped:
        print(f"    mdrop: {s} -X-> {d}")

    # 幂等入图（MERGE 按 (a,rel,b) 去重，SET 覆盖属性）
    sess = cli._session()
    n = miss = 0
    for src, dst, e, ov in kept:
        q = ("MATCH (a:Concept {name:$s}), (b:Concept {name:$d}) "
             "MERGE (a)-[r:%s]->(b) "
             "SET r.source='concept_llm_v1', r.conf=$conf, "
             "r.reason=$reason, r.overlap=$ov, r.batch=$batch" % e["edge"])
        summ = sess.run(q, s=src, d=dst, conf=e.get("conf"),
                        reason=(e.get("reason") or "")[:200], ov=ov, batch=batch).consume()   # 120→200 保住 reason 角色句式供方向审计
        if summ.counters.contains_updates:
            n += 1
        else:
            miss += 1   # MATCH 未命中（概念节点缺失），静默跳过
    print(f"[4] committed {n} directional edges (batch={batch}), "
          f"skipped-node-miss={miss}")

    # 入图总量 + 抽验
    chk = ("MATCH ()-[r]->() WHERE r.source='concept_llm_v1' "
           "RETURN type(r) AS t, count(*) AS n")
    print("[5] in-graph now:",
          {row["t"]: row["n"] for row in sess.run(chk)})


if __name__ == "__main__":
    main()
