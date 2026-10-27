# -*- coding: utf-8 -*-
"""知识图谱 Phase 3-B ETL：事件实体（Semantica events.jsonl → Neo4j）。

数据流：
  data/semantica/events.jsonl（event_graph.py 滚动窗口 180 天，跨源去重）
    → Event 节点（id 唯一：kind/ts/date/text/bias/conf/tag/source）
    → INVOLVES 边：Security -[INVOLVES]-> Event（仅 stocks 非空）

定位分工：
  · events.jsonl = 滚动 180 天窗口（向量检索用，旧事件丢弃）
  · Neo4j = 永久历史事件流（只增不删，时序图谱资产）

时序铁律：INVOLVES 边 validStart=事件日（该日起涉及关系成立），
validEnd=9999-12-31（事件关联持续，不随 jsonl 窗口滚动删除）。

用法：
  python kg_build_p3_evt.py             # 全量同步（幂等，可每日增量跑）
  python kg_build_p3_evt.py --dryrun
  python kg_build_p3_evt.py --sample 600519.SH   # 抽检个股事件流
"""

import argparse
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S

EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")

TEXT_MAX = 300  # 事件文本截断（图查询展示用）


def load_events():
    events, stock_pairs = [], []
    with io.open(EVENT_F, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            ts = (e.get("ts") or "")[:10].replace("-", "") or None
            if not e.get("id") or not ts:
                continue
            text = (e.get("text") or "")[:TEXT_MAX]
            reason = (e.get("reason") or "")[:200] or None
            events.append(
                {
                    "id": e["id"],
                    "kind": e.get("kind"),
                    "ts": e.get("ts"),
                    "date": ts,
                    "text": text,
                    "bias": e.get("bias"),
                    "conf": e.get("conf"),
                    "reason": reason,
                    "tag": e.get("tag"),
                    "source": e.get("source"),
                }
            )
            for code in e.get("stocks") or []:
                stock_pairs.append(
                    {
                        "src": code,
                        "dst": e["id"],
                        "validStart": ts,
                        "validEnd": S.VALID_END_OPEN,
                        "source": "semantica_events",
                        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
    return events, stock_pairs


def build(dryrun=False):
    print(
        "=== 知识图谱 Phase 3-B 事件实体开始 %s ===" % datetime.now().strftime("%F %T")
    )
    print("[1/3] 读 events.jsonl…")
    events, pairs = load_events()
    n_bias = len([e for e in events if e["bias"] in ("bull", "bear")])
    print(
        "  事件 %d（bull/bear 已判 %d），个股关联 %d"
        % (len(events), n_bias, len(pairs))
    )

    if dryrun:
        print("DRYRUN 结束。")
        return

    print("[2/3] 落库…")
    from kg_client import KgClient

    cli = KgClient()
    cli.ensure_schema()
    n1 = cli.upsert_nodes("Event", ["id"], events)
    print("  Event upsert: %d" % n1)
    # 关联不上的（股票代码不在 Security 图）会被 MATCH 自然跳过
    n2 = cli.upsert_edge(S.LBL_SECURITY, "ts_code", "Event", "id", "INVOLVES", pairs)
    print("  INVOLVES upsert: %d" % n2)

    print("[3/3] 统计…")
    with cli._session() as s:
        r = s.run("MATCH (n:Event) RETURN count(n) AS c").single()
        print("  Event 节点: %d" % r["c"])
        r = s.run("MATCH ()-[r:INVOLVES]->() RETURN count(r) AS c").single()
        print("  INVOLVES 边: %d" % r["c"])
    cli.close()
    print("=== Phase 3-B 完成 ===")


def sample(ts_code):
    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        rows = list(
            s.run(
                "MATCH (sec:Security {ts_code: $c})-[r:INVOLVES]->(e:Event) "
                "RETURN e.date AS d, e.bias AS b, e.tag AS tag, "
                "e.kind AS k, substring(e.text, 0, 60) AS t "
                "ORDER BY d DESC LIMIT 15",
                c=ts_code,
            )
        )
        print("=== %s 事件流（最新 %d 条）===" % (ts_code, len(rows)))
        for r in rows:
            print(
                "  %s %-7s %-6s %-8s %s"
                % (r["d"], r["b"] or "-", r["k"], r["tag"] or "-", r["t"])
            )
        stat = list(
            s.run(
                "MATCH (sec:Security {ts_code: $c})-[r:INVOLVES]->(e:Event) "
                "WHERE e.bias IN ['bull','bear'] "
                "RETURN e.bias AS b, count(*) AS n",
                c=ts_code,
            )
        )
        for r in stat:
            print("  %s: %d" % (r["b"], r["n"]))
    cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--sample", help="抽检个股事件流")
    args = ap.parse_args()
    if args.sample:
        sample(args.sample)
        return
    build(dryrun=args.dryrun)


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "kg_evt_sync", main)
