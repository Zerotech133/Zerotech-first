# -*- coding: utf-8 -*-
"""情报五要素回填 v0 —— news（13.7 万条）→ intel_facts.db。

做三件事
--------
1. 对 news 的 title(+content 前 240 字) 抽五要素：人 / 地 / 事 / 物 / 组织；
2. 落独立库 ``agent_platform/data/intel/intel_facts.db`` 的 news_facts 表，
   **不改 news.db 一个字节**（news 属采集域，事实属研判域，分库不越界）；
3. 顺带补全原实现里两个空读：``event_type``（jev JSON 中根本不存在，
   旧代码读出来恒为 None）与事件类型分布。

只读纪律
--------
news.db 以 ``mode=ro`` + ``PRAGMA query_only=1`` 打开；本脚本对 news 无任何
写操作。intel_facts.db 每次运行按主键 upsert，可反复跑（幂等）。

用法
----
    python -X utf8 tools/intel_facts_backfill.py --dry-run        # 只统计不写库
    python -X utf8 tools/intel_facts_backfill.py                  # 全量回填
    python -X utf8 tools/intel_facts_backfill.py --since 2026-10-01
    python -X utf8 tools/intel_facts_backfill.py --limit 2000     # 抽样压测

退出码：0 成功；2 数据源/参数错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
NEWS_DB = os.path.join(ROOT, "news_collector", "news.db")
FACTS_DB = os.path.join(ROOT, "agent_platform", "data", "intel",
                        "intel_facts.db")

sys.path.insert(0, PORTAL)


def _ro(path):
    c = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                        uri=True, timeout=15)
    c.execute("PRAGMA query_only=1")
    return c


DDL = """
CREATE TABLE IF NOT EXISTS news_facts (
    news_id    TEXT PRIMARY KEY,
    ts         TEXT,
    source     TEXT,
    title      TEXT,
    event_type TEXT,
    event_kw   TEXT,
    org        TEXT,
    place      TEXT,
    person     TEXT,
    thing      TEXT,
    bias       TEXT,
    conf       REAL,
    scope      TEXT,
    stocks     TEXT,
    schema     TEXT,
    built_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_nf_ts  ON news_facts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_nf_et  ON news_facts(event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_nf_org ON news_facts(org);
CREATE INDEX IF NOT EXISTS idx_nf_src ON news_facts(source, ts DESC);
CREATE TABLE IF NOT EXISTS build_meta (
    key TEXT PRIMARY KEY, val TEXT
);
"""


def build(since: str = "", limit: int = 0, dry: bool = False,
          batch_log: int = 20000) -> dict:
    import intel_extract as X

    if not os.path.isfile(NEWS_DB):
        raise SystemExit("news.db 不存在：%s" % NEWS_DB)
    X.load_org_names()

    sql = ("SELECT id, ts, source, title, content, stocks, jev FROM news "
           "WHERE title IS NOT NULL AND title != ''")
    args: list = []
    if since:
        sql += " AND ts >= ?"
        args.append(since)
    sql += " ORDER BY ts"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)

    src = _ro(NEWS_DB)
    t0 = time.time()
    rows = src.execute(sql, args).fetchall()
    src.close()

    out, et_cnt, org_hit, place_hit = [], {}, 0, 0
    built_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, (nid, ts, source, title, content, stocks, jev) in enumerate(rows):
        f = X.analyze(title, content or "")
        bias = conf = scope = None
        if jev:
            try:
                j = json.loads(jev)
                bias = j.get("bias")
                conf = j.get("conf")
                scope = j.get("scope")
            except Exception:  # noqa: BLE001 —— 坏 jev 不阻断，字段留空
                pass
        # event_type 用规则分类结果（jev 里没有这个字段，旧读法恒为 None）；
        # 未命中记 other，不回退 scope——scope 是地域/资产类别，与事件类型
        # 不同语义，混列会让下游"类型分布"失去意义（scope 单独一列保留）。
        et = f["event_type"]
        et_cnt[et] = et_cnt.get(et, 0) + 1
        if f["org"]:
            org_hit += 1
        if f["place"]:
            place_hit += 1
        if not dry:
            out.append((nid, ts, source, (title or "")[:200], et,
                        json.dumps(f["event_kw"], ensure_ascii=False),
                        json.dumps(f["org"], ensure_ascii=False),
                        json.dumps(f["place"], ensure_ascii=False),
                        json.dumps(f["person"], ensure_ascii=False),
                        json.dumps(f["thing"], ensure_ascii=False),
                        bias, conf, scope, stocks, f["schema"], built_at))
        if batch_log and (i + 1) % batch_log == 0:
            print("  ... %d/%d  %.1fs" % (i + 1, len(rows), time.time() - t0),
                  flush=True)

    n = len(rows)
    stat = {
        "rows": n,
        "seconds": round(time.time() - t0, 1),
        "per_1k_ms": round((time.time() - t0) / max(1, n) * 1000 * 1000, 1),
        "org_hit_pct": round(org_hit * 100.0 / max(1, n), 1),
        "place_hit_pct": round(place_hit * 100.0 / max(1, n), 1),
        "event_type": dict(sorted(et_cnt.items(), key=lambda kv: -kv[1])),
        "dry": dry,
        "built_at": built_at,
    }

    if not dry:
        os.makedirs(os.path.dirname(FACTS_DB), exist_ok=True)
        db = sqlite3.connect(FACTS_DB)
        db.executescript(DDL)
        db.executemany(
            "INSERT INTO news_facts (news_id, ts, source, title, event_type,"
            " event_kw, org, place, person, thing, bias, conf, scope, stocks,"
            " schema, built_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(news_id) DO UPDATE SET ts=excluded.ts,"
            " source=excluded.source, title=excluded.title,"
            " event_type=excluded.event_type, event_kw=excluded.event_kw,"
            " org=excluded.org, place=excluded.place, person=excluded.person,"
            " thing=excluded.thing, bias=excluded.bias, conf=excluded.conf,"
            " scope=excluded.scope, stocks=excluded.stocks,"
            " schema=excluded.schema, built_at=excluded.built_at", out)
        db.execute("INSERT INTO build_meta(key,val) VALUES(?,?)"
                   " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                   ("last_build", json.dumps(stat, ensure_ascii=False)))
        db.commit()
        tot = db.execute("SELECT count(*) FROM news_facts").fetchone()[0]
        db.close()
        stat["db_total"] = tot
        stat["db_path"] = FACTS_DB
    return stat


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="情报五要素回填 v0")
    p.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（抽样）")
    p.add_argument("--dry-run", action="store_true", help="只统计不写库")
    a = p.parse_args(argv)
    stat = build(since=a.since, limit=a.limit, dry=a.dry_run)
    print(json.dumps(stat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
