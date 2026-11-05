# -*- coding: utf-8 -*-
"""K260 断言 D 漂移修复：以 events.jsonl 为锚，幂等回灌 Event 节点属性。

背景（2026-11-05）
------------------
\\ZeroBound\\NoticeBackfill 每日 06:45 运行，而 KgDaily 链的 kg_evt_sync
（kg_build_p3_evt.py 全量 upsert）在 06:2x 已经跑完。NoticeBackfill 给
events.jsonl 里的 notice 补 LLM 标签（tag/bias/conf/reason）之后，图谱侧
仍停留在「打标前」版本，于是断言 D 报「图谱三错类 < jsonl」。

注意：缺的不只是 tag。图谱里这些节点连 bias/conf/reason 都没有，所以正确
修法是按 kg_build_p3_evt.load_events 的同一套属性构造逻辑重放整行，而不是
只 SET e.tag（那会让断言侥幸转绿、节点仍是半截数据）。

用法
----
  python -X utf8 k260_fix_tag_drift.py             # 默认 dryrun，只报差集
  python -X utf8 k260_fix_tag_drift.py --apply     # 实际回灌

范围与安全性
------------
* 只处理三错类（业绩预亏/监管处罚/退市风险）中「图谱 tag != jsonl tag」
  的节点，即断言 D 的差集本身；不扫全库、不改边、不删节点。
* 写前把受影响节点的完整原属性备份到
  data/k260_tag_drift_backup_<YYYYmmdd-HHMMSS>.json，可据此逐节点回滚。
* upsert 复用 KgClient.upsert_nodes（UNWIND + MERGE + SET n += row），
  与每日 ETL 完全同路径，重复执行无副作用。
* 退出码：0=一致（或修复后一致），1=仍有漂移，2=依赖/连接失败。
"""

import argparse
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_build_p3_evt as etl  # noqa: E402  复用其 load_events，保证属性构造一致

WRONG_TAGS = ["业绩预亏", "监管处罚", "退市风险"]
EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
BACKUP_DIR = os.path.join(HERE, "data")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def jsonl_tagged_rows():
    """返回 {id: row}，仅三错类。"""
    out = {}
    with io.open(EVENT_F, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("tag") in WRONG_TAGS and e.get("id"):
                out[e["id"]] = e
    return out


def graph_state(cli, ids):
    """取这些 id 在图谱里的现状；顺带找出图谱里多出来的三错类节点。"""
    with cli._session() as s:
        cur = {}
        for r in s.run(
            "MATCH (e:Event) WHERE e.id IN $ids "
            "RETURN e.id AS id, properties(e) AS p",
            ids=list(ids),
        ):
            cur[r["id"]] = dict(r["p"] or {})
        extra = [
            (r["id"], r["tag"])
            for r in s.run(
                "MATCH (e:Event) WHERE e.tag IN $tags AND NOT e.id IN $ids "
                "RETURN e.id AS id, e.tag AS tag",
                tags=WRONG_TAGS,
                ids=list(ids),
            )
        ]
    return cur, extra


def collect(apply=False):
    tagged = jsonl_tagged_rows()
    from kg_client import KgClient

    cli = KgClient()
    try:
        cur, extra = graph_state(cli, set(tagged))
        drift_ids = sorted(
            i for i, row in tagged.items()
            if (cur.get(i) or {}).get("tag") != row["tag"]
        )
        print("jsonl 三错类 %d 条；图谱命中 %d 条；漂移 %d 条；图谱多出 %d 条"
              % (len(tagged), len(cur), len(drift_ids), len(extra)))
        for i in drift_ids:
            before = cur.get(i) or {}
            print("  DRIFT %s  tag %r -> %r  缺字段=%s"
                  % (i, before.get("tag"), tagged[i]["tag"],
                     sorted({k for k in ("tag", "bias", "conf", "reason")
                             if k not in before})))
        for i, t in extra[:10]:
            print("  EXTRA %s tag=%s（本脚本不删除，仅报告）" % (i, t))
        if not drift_ids:
            print("无漂移，无需修复。")
            return 0
        if not apply:
            print("DRYRUN：未写库。加 --apply 执行回灌。")
            return 0

        # 用 ETL 同一套属性构造逻辑，只取漂移 id 对应的行
        events, _pairs = etl.load_events()
        drift = set(drift_ids)
        patch = [e for e in events if e["id"] in drift]
        missing = drift - {e["id"] for e in patch}
        if missing:
            print("[error] 以下 id 在 load_events 中被过滤（缺 id/ts），拒绝写库：%s"
                  % sorted(missing))
            return 1

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(BACKUP_DIR, "k260_tag_drift_backup_%s.json" % stamp)
        with io.open(backup, "w", encoding="utf-8") as f:
            json.dump(
                {"stamp": stamp, "reason": "K260 assertion-D drift repair",
                 "ids": drift_ids, "before": {i: cur.get(i) for i in drift_ids},
                 "after": {e["id"]: e for e in patch}},
                f, ensure_ascii=False, indent=1, default=str,
            )
        print("备份原属性 -> %s" % backup)

        written = cli.upsert_nodes("Event", ["id"], patch)
        print("upsert Event 节点 %d 行" % written)

        after, extra2 = graph_state(cli, set(tagged))
        still = [i for i in drift_ids
                 if (after.get(i) or {}).get("tag") != tagged[i]["tag"]]
        print("复核：仍有漂移 %d 条；图谱三错类总数 %d / jsonl %d"
              % (len(still),
                 sum(1 for i, p in after.items() if p.get("tag") in WRONG_TAGS),
                 len(tagged)))
        if still or extra2:
            print("[error] 修复后仍不一致：still=%s extra=%d" % (still, len(extra2)))
            return 1
        print("修复完成。")
        return 0
    finally:
        cli.close()


def main():
    p = argparse.ArgumentParser(description="K260 断言 D 图谱漂移修复")
    p.add_argument("--apply", action="store_true", help="实际写库（默认 dryrun）")
    args = p.parse_args()
    try:
        sys.exit(collect(apply=args.apply))
    except Exception as exc:  # noqa: BLE001
        print("[error] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
