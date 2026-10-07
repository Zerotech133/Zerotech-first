# -*- coding: utf-8 -*-
"""K265 Phase 1 入图：影子 → Neo4j（含品牌概念过滤）。

规则：
- 仅 ext.ok 且 conf>=0.8
- 品牌概念（XX概念 的 XX 是公司/品牌名）不得作为 SUPPLIES 的 src
  （品牌股票池不是"供应商"；作为 DRIVES src / SUPPLIES dst 仍允许）
- 边属性：source=concept_llm_v1（整体回滚键）+ conf/reason/overlap/batch
用法：python k265_phase1_commit.py [batch_tag]
"""
import json
import re
import sys

sys.path.insert(0, r"e:\老田的AI助手\quant_backtest")
from kg_client import KgClient  # noqa: E402

SHADOW = r"e:\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl"

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
    batch = sys.argv[1] if len(sys.argv) > 1 else "b1"
    cli = KgClient(retry=1, retry_wait=1)
    brands = build_brands(cli)
    print(f"[1] brand pool: {len(brands)}")

    rows = [json.loads(l) for l in open(SHADOW, encoding="utf-8")]
    ok = [r for r in rows if r["ext"].get("ok")]
    conf_ok = [r for r in ok if (r["ext"].get("conf") or 0) >= 0.8]
    print(f"[2] shadow rows={len(rows)} ok={len(ok)} conf>=0.8={len(conf_ok)}")

    dropped, kept = [], []
    for r in conf_ok:
        e = r["ext"]
        src = r["a"] if e.get("src") == "A" else r["b"]
        dst = r["b"] if e.get("src") == "A" else r["a"]
        if e.get("edge") == "SUPPLIES" and is_brand_concept(src, brands):
            dropped.append((src, dst))
            continue
        kept.append((src, dst, e, r["overlap"]))
    print(f"[3] brand-filter dropped={len(dropped)} kept={len(kept)}")
    for s, d in dropped[:8]:
        print(f"    drop: {s} -X-> {d}")

    # 幂等入图（MERGE 按 (a,rel,b) 去重，SET 覆盖属性）
    sess = cli._session()
    n = 0
    for src, dst, e, ov in kept:
        q = ("MATCH (a:Concept {name:$s}), (b:Concept {name:$d}) "
             "MERGE (a)-[r:%s]->(b) "
             "SET r.source='concept_llm_v1', r.conf=$conf, "
             "r.reason=$reason, r.overlap=$ov, r.batch=$batch" % e["edge"])
        sess.run(q, s=src, d=dst, conf=e.get("conf"),
                 reason=(e.get("reason") or "")[:120], ov=ov, batch=batch)
        n += 1
    print(f"[4] committed {n} directional edges (batch={batch})")

    # 回滚提示 + 抽验
    chk = ("MATCH ()-[r]->() WHERE r.source='concept_llm_v1' "
           "RETURN type(r) AS t, count(*) AS n")
    print("[5] in-graph now:",
          {row["t"]: row["n"] for row in sess.run(chk)})


if __name__ == "__main__":
    main()
