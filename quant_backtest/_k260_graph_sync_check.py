# -*- coding: utf-8 -*-
"""K260 图谱侧语义边同步检查（只读，不写库）。

背景：2026-10-08 11:30 events.jsonl 重算 175 条（三错类 tag 清理）。
图谱 Event 节点带 tag 快照未同步。本脚本量化：

  1. jsonl 重算前后 diff → 变更 id 清单（tag/bias 旧→新）
  2. 图谱 Event 中这些 id 的当前 tag（应=旧值，待修面）
  3. 图谱三错类中 id 已滚出 jsonl 窗口的孤儿（重跑 p3 修不到的面）
  4. 其他 label（MarketEvent/EXP_* 等）是否带 tag
  5. INVOLVES 边属性抽样（确认边不带语义）
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
BAK_F = os.path.join(HERE, "data", "semantica", "events.jsonl.bak-20261008-retag")
WRONG_TAGS = ["业绩预亏", "监管处罚", "退市风险"]


def load(path):
    m = {}
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            m[e["id"]] = (e.get("tag"), e.get("bias"))
    return m


def main():
    cur = load(EVENT_F)
    old = load(BAK_F)
    print("jsonl 当前 %d 行 / 重算前 %d 行" % (len(cur), len(old)))

    changed = {}
    for k, v_old in old.items():
        v_cur = cur.get(k)
        if v_cur is not None and v_cur != v_old:
            changed[k] = (v_old, v_cur)
    print("重算变更条目: %d" % len(changed))
    flows = {}
    for v_old, v_cur in changed.values():
        key = "%s→%s" % (v_old[0], v_cur[0])
        flows[key] = flows.get(key, 0) + 1
    for k in sorted(flows, key=flows.get, reverse=True):
        print("  流转 %-24s %d" % (k, flows[k]))

    from kg_client import KgClient
    cli = KgClient()
    with cli._session() as s:
        # ── 1. 变更 id 在图中的待修面 ──
        ids = list(changed.keys())
        stale = match_cnt = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = list(s.run(
                "UNWIND $ids AS x MATCH (e:Event {id: x}) "
                "RETURN e.id AS id, e.tag AS tag", ids=chunk))
            for r in rows:
                match_cnt += 1
                v_cur = changed[r["id"]][1]
                if (r["tag"] or None) != v_cur[0]:
                    stale += 1
        print("\n[1] 变更 id 图中命中 %d / %d，其中 tag 仍旧值（待修）: %d"
              % (match_cnt, len(ids), stale))

        # ── 2. 图谱三错类孤儿（id 不在当前 jsonl）──
        rows = list(s.run(
            "MATCH (e:Event) WHERE e.tag IN $tags "
            "RETURN e.id AS id, e.tag AS tag", tags=WRONG_TAGS))
        orphan_by_tag = {t: 0 for t in WRONG_TAGS}
        in_jsonl = {t: 0 for t in WRONG_TAGS}
        for r in rows:
            if r["id"] in cur:
                in_jsonl[r["tag"]] += 1
            else:
                orphan_by_tag[r["tag"]] += 1
        print("[2] 图谱三错类总数 %d（已修复后基线：119=监管52+退市67，预亏0）：" % len(rows))
        for t in WRONG_TAGS:
            print("    %-4s 在 jsonl 窗口内 %d（应=jsonl 该类残留）/ 滚出孤儿 %d（需专门清）"
                  % (t, in_jsonl[t], orphan_by_tag[t]))

        # ── 3. 其他 label 是否带 tag ──
        labels = [r["label"] for r in s.run("CALL db.labels()")]
        print("\n[3] 各 label 带 tag/三错类情况：")
        for lb in labels:
            if lb == "Event":
                continue
            r = s.run(
                "MATCH (n:`%s`) RETURN count(n) AS total, "
                "count(n.tag) AS wt" % lb).single()
            if r["wt"] == 0:
                continue
            w = s.run(
                "MATCH (n:`%s`) WHERE n.tag IN $tags "
                "RETURN count(n) AS c" % lb, tags=WRONG_TAGS).single()["c"]
            print("    %-14s total=%-7d with_tag=%-7d 三错类=%d"
                  % (lb, r["total"], r["wt"], w))

        # ── 4. INVOLVES 边属性抽样 ──
        r = s.run(
            "MATCH ()-[r:INVOLVES]->() RETURN count(r) AS c").single()
        rows = list(s.run(
            "MATCH (a)-[r:INVOLVES]->(b:Event) "
            "RETURN keys(r) AS ks LIMIT 1"))
        print("\n[4] INVOLVES 边总数 %d，属性 keys=%s（无 tag/bias=边无语义）"
              % (r["c"], rows[0]["ks"] if rows else "无边"))

        # ── 6. EXP_Event 影子镜像与 175 变更的重叠 ──
        ids = list(changed.keys())
        hit = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = list(s.run(
                "UNWIND $ids AS x MATCH (n:EXP_Event {id: x}) "
                "RETURN count(*) AS c", ids=chunk))
            hit += rows[0]["c"] if rows else 0
        r = s.run(
            "MATCH (n:EXP_Event)-[r:EXP_SUPPORTS|EXP_CONTRADICTS]->() "
            "RETURN count(r) AS c").single()
        print("[6] EXP_Event 影子镜像：175 变更 id 命中 %d；"
              "论证边（EXP_SUP/CONTRA）%d 条挂其上"
              % (hit, r["c"]))

        # ── 5. Event 全量 tag 分布（重跑后预期）──
        rows = list(s.run(
            "MATCH (e:Event) WHERE e.tag IN $tags RETURN e.tag AS t, "
            "count(*) AS n", tags=WRONG_TAGS))
        print("[5] 图谱三错类现状（修复后基线 119；若偏离需排查）：")
        for r in rows:
            print("    %-4s %d" % (r["t"], r["n"]))
    cli.close()
    print("\n=== 检查完成（只读，未写库）===")


if __name__ == "__main__":
    main()
