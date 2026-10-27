# -*- coding: utf-8 -*-
"""K260 统一回归入口（只读）：修复后基线的晨链事后验证。

用法：python -X utf8 _k260_regression.py
退出码 0=全绿，1=有断言失败（可直接接计划任务/告警）。

断言基线（2026-10-08 修复后；2026-10-09 OPS-1015 改相对断言）：
  A. jsonl 行数 >= 40561（回补只增不减）
  B. jsonl 175 变更集字段自洽：tag=None 无「规则命中」reason / 无 conf=0.7 残留
  C. jsonl 三错类 >= 119（监管处罚+退市风险；下限防错标回流——每日新公告
     合法携带这些 tag，绝对数断言 10-09 晨即被 +4 自然增长打破）
  D. 图谱 Event 三错类 == jsonl 当前值 且逐类一致（防止晨链回灌/漏同步，
     以 jsonl 为锚的同步性不变量）
  E. 图谱无 tag=None 且 reason/conf 残留的节点
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
WRONG_TAGS = ["业绩预亏", "监管处罚", "退市风险"]
BASE_TOTAL, BASE_WRONG = 40561, 119  # 10-08 修复快照；C 用作下限锚

fails = []


def check(name, ok, detail):
    print(
        "[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  <-- " + detail)
    )
    if not ok:
        fails.append(name)


def main():
    rows = []
    with io.open(EVENT_F, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))

    # A. 行数
    check(
        "A jsonl 行数",
        len(rows) >= BASE_TOTAL,
        "实际 %d < 基线 %d" % (len(rows), BASE_TOTAL),
    )

    # B. 变更集字段自洽（全库口径：tag=None 不得带规则命中 reason/conf）
    n_bad = sum(
        1
        for e in rows
        if e.get("tag") is None
        and ("规则命中" in (e.get("reason") or "") or e.get("conf") == 0.7)
    )
    check("B jsonl 字段自洽残留=0", n_bad == 0, "残留 %d" % n_bad)

    # C. jsonl 三错类
    tags = {}
    for e in rows:
        if e.get("tag") in WRONG_TAGS:
            tags[e["tag"]] = tags.get(e["tag"], 0) + 1
    n_wrong = sum(tags.values())
    check(
        "C jsonl 三错类>=%d（防回流下限，当前 %d）" % (BASE_WRONG, n_wrong),
        n_wrong >= BASE_WRONG,
        "实际 %d %s" % (n_wrong, tags),
    )

    # D/E. 图谱侧
    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        g_tags = {}
        for r in s.run(
            "MATCH (e:Event) WHERE e.tag IN $tags RETURN e.tag AS t, count(*) AS n",
            tags=WRONG_TAGS,
        ):
            g_tags[r["t"]] = r["n"]
        g_wrong = sum(g_tags.values())
        check(
            "D 图谱三错类==jsonl(%d) 且逐类一致" % n_wrong,
            g_wrong == n_wrong and g_tags == tags,
            "图谱 %d %s vs jsonl %d %s" % (g_wrong, g_tags, n_wrong, tags),
        )
        r = s.run(
            "MATCH (e:Event) WHERE e.tag IS NULL AND "
            "(e.reason CONTAINS '规则命中' OR e.conf = 0.7) "
            "RETURN count(e) AS c"
        ).single()
        check("E 图谱 reason/conf 残留=0", r["c"] == 0, "残留 %d" % r["c"])
    cli.close()

    print("=" * 40)
    if fails:
        print("回归失败 %d 项: %s" % (len(fails), "、".join(fails)))
        sys.exit(1)
    print(
        "K260 回归全绿（行数>=%d / 三错类>=%d 当前 %d / 残留 0）"
        % (BASE_TOTAL, BASE_WRONG, n_wrong)
    )


if __name__ == "__main__":
    main()
