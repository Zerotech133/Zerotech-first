# -*- coding: utf-8 -*-
"""K260 论证边（SUPPORTS/CONTRADICTS）与 175 变更集对齐（只读）。

量化：
  1. 三错类边的 mevt_ids 空/非空分布
  2. 三错类边 src(IntelItem AN号) ∩ 175 变更集(Event AN号) —— 源头对齐面
  3. 非三错类边作对照
"""

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
BAK_F = os.path.join(HERE, "data", "semantica", "events.jsonl.bak-20261008-retag")
WRONG_TAGS = ["业绩预亏", "监管处罚", "退市风险"]


def an_of(s):
    m = re.search(r"AN\d{15,}", s or "")
    return m.group(0) if m else None


def main():
    # 175 变更集的 AN 号
    cur, old = {}, {}
    for path, m in ((EVENT_F, cur), (BAK_F, old)):
        with io.open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                m[e["id"]] = (e.get("tag"), e.get("bias"))
    changed_ans = set()
    for k, v_old in old.items():
        v_cur = cur.get(k)
        if v_cur is not None and v_cur != v_old:
            a = an_of(k)
            if a:
                changed_ans.add(a)
    print("175 变更集 AN 号（去重）: %d" % len(changed_ans))

    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        for rel in ("SUPPORTS", "CONTRADICTS"):
            rows = list(
                s.run(
                    "MATCH (a:IntelItem)-[r:%s]->() WHERE r.tag IN $tags "
                    "RETURN a.item_id AS src, toString(r.mevt_ids) AS mv" % rel,
                    tags=WRONG_TAGS,
                )
            )
            n = len(rows)
            mv_nonempty = len([r for r in rows if (r["mv"] or "").strip()])
            src_ans = set(filter(None, (an_of(r["src"]) for r in rows)))
            hit = src_ans & changed_ans
            print(
                "[%s] 三错类边 %d | mevt_ids 非空 %d | src AN 去重 %d | "
                "∩175 变更集 %d" % (rel, n, mv_nonempty, len(src_ans), len(hit))
            )
        # 非三错类对照：同样口径
        rows = list(
            s.run(
                "MATCH (a:IntelItem)-[r:SUPPORTS|CONTRADICTS]->() "
                "WHERE NOT r.tag IN $tags AND r.tag IS NOT NULL "
                "RETURN a.item_id AS src",
                tags=WRONG_TAGS,
            )
        )
        src_ans = set(filter(None, (an_of(r["src"]) for r in rows)))
        hit = src_ans & changed_ans
        print(
            "[对照] 非三错类边 %d | src AN 去重 %d | ∩175 变更集 %d"
            % (len(rows), len(src_ans), len(hit))
        )
    cli.close()
    print("=== 对齐检查完成（只读）===")


if __name__ == "__main__":
    main()
