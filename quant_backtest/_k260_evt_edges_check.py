# -*- coding: utf-8 -*-
"""K260 专项整理·补盲区：Event 起点 188 条三错类论证边与 175 变更集对齐（只读）。"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
BAK_F = EVENT_F + ".bak-20261008-retag"
WRONG_TAGS = ["业绩预亏", "监管处罚", "退市风险"]


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def main():
    cur = {e["id"]: (e.get("tag"), e.get("bias")) for e in load(EVENT_F)}
    old = {e["id"]: (e.get("tag"), e.get("bias")) for e in load(BAK_F)}
    changed = {k for k in old if k in cur and old[k] != cur[k]}
    print("175 变更集: %d" % len(changed))

    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        rows = list(
            s.run(
                "MATCH (e:Event)-[r:SUPPORTS|CONTRADICTS]->() "
                "WHERE r.tag IN $tags RETURN DISTINCT e.id AS id",
                tags=WRONG_TAGS,
            )
        )
        ids = {r["id"] for r in rows}
        hit = ids & changed
        print(
            "Event 起点三错类边（去重起点）: %d | ∩175 变更集: %d"
            % (len(ids), len(hit))
        )
        # 这些起点 Event 节点自身的 tag 现状（p3 已同步后应=None 或新 tag）
        n_none = n_stale = 0
        for i in range(0, len(ids), 500):
            chunk = list(ids)[i : i + 500]
            for r in s.run(
                "UNWIND $ids AS x MATCH (e:Event {id: x}) RETURN e.tag AS t", ids=chunk
            ):
                if r["t"] is None:
                    n_none += 1
                elif r["t"] in WRONG_TAGS:
                    n_stale += 1
        print(
            "起点 Event 节点 tag: None=%d | 仍三错类=%d（边快照旧、节点新=历史快照语义）"
            % (n_none, n_stale)
        )
    cli.close()


if __name__ == "__main__":
    main()
