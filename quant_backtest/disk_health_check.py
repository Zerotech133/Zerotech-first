# -*- coding: utf-8 -*-
"""磁盘仓质量监控 + 断供告警（PG meta.disk_health 账本）。

2026-10-17 治理补齐：PG 侧镜像有 meta.sync_drift 账本，磁盘仓（parquet/
sqlite/jsonl 共 223 表）此前无等价物、101 表 stale 无人在看。本脚本读
catalog.json（build_catalog.py 已落实测判级 verification_status）推导告警
级并 UPSERT 落账，供计划任务与门户消费。

告警级（与 catalog verification_status 对齐）：
  schema_error         parquet footer 损坏
  no_data              占位/空表（断供监控主体）
  persistent_no_data   no_data 持续 ≥7 天（升级，断供实锤）
  manifest_gap         有数据但 manifest 缺登记
  stale                freshness < 7 天前
  ok                   正常（verified / verified_external / static_no_date）
                      static_no_date=无时间维的静态数据（如情感词典词表），
                      无日期是其正常属性，故不告警（WO-QT-20261021-102）

first_seen 语义：首次进入非 ok 状态的时间，UPSERT 保留旧值——断供计时
不因重复检查而清零；恢复 ok 时清空。
"""
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "data", "catalog.json")
ENV_LOCAL = os.path.join(HERE, ".env.local")
PG_DSN = dict(host="127.0.0.1", port=5442, dbname="quant_meta",
              user="quant_user")
PERSIST_DAYS = 7

ALERT_BY_VERIFICATION = {
    "schema_error": "schema_error",
    "placeholder_empty": "no_data",
    "manifest_missing": "manifest_gap",
    "verified_stale": "stale",
    "verified": "ok",
    "verified_external": "ok",
    # WO-QT-20261021-102：无时间维的静态数据（chinese-dictionary-of-financial-
    # sentiment 词表等）→ 不告警。理由：这类资产"没有日期列"是数据本身的
    # 正常属性，不是断供也不是质量缺陷；映射成 ok 之外的任何值都会把正常
    # 资产永久钉在告警栏，久了就是狼来了。若它真出问题，会在 build_catalog
    # 的更具体状态上先落地（schema_error/placeholder_empty/manifest_missing
    # 在 _verify_status 里优先于 static_no_date），不会因本条映射而失明。
    "static_no_date": "ok",
}


def load_pg_password() -> str:
    with io.open(ENV_LOCAL, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("XMAP_PG_PASSWORD="):
                return ln.split("=", 1)[1].strip()
    raise RuntimeError("XMAP_PG_PASSWORD not found in .env.local")


def ensure_table(pg):
    with pg.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS meta")
        cur.execute("""CREATE TABLE IF NOT EXISTS meta.disk_health (
            asset text PRIMARY KEY,
            source text, verification text, freshness text,
            rows bigint, files int, manifest_status text,
            alert text, first_seen timestamptz, days_in_alert int,
            checked_at timestamptz DEFAULT now())""")


def derive(entries):
    now = datetime.now()
    out = []
    for asset, e in entries.items():
        v = e.get("verification_status", "unknown")
        alert = ALERT_BY_VERIFICATION.get(v, "unknown")
        out.append({
            "asset": asset, "source": e.get("source"),
            "verification": v, "freshness": e.get("freshness"),
            "rows": e.get("rows"), "files": e.get("files"),
            "manifest_status": e.get("manifest_status"),
            "alert": alert, "_now": now,
        })
    return out


def upsert(pg, rows):
    cutoff = datetime.now() - timedelta(days=PERSIST_DAYS)
    with pg.cursor() as cur:
        cur.executemany("""INSERT INTO meta.disk_health
            (asset, source, verification, freshness, rows, files,
             manifest_status, alert, first_seen, days_in_alert, checked_at)
            VALUES (%(asset)s, %(source)s, %(verification)s, %(freshness)s,
                    %(rows)s, %(files)s, %(manifest_status)s, %(alert)s,
                    CASE WHEN %(alert)s = 'ok' THEN NULL ELSE now() END,
                    CASE WHEN %(alert)s = 'ok' THEN NULL ELSE 0 END,
                    now())
            ON CONFLICT (asset) DO UPDATE SET
              source=EXCLUDED.source, verification=EXCLUDED.verification,
              freshness=EXCLUDED.freshness, rows=EXCLUDED.rows,
              files=EXCLUDED.files, manifest_status=EXCLUDED.manifest_status,
              alert=EXCLUDED.alert,
              first_seen=CASE
                WHEN EXCLUDED.alert = 'ok' THEN NULL
                WHEN meta.disk_health.alert = EXCLUDED.alert
                     AND meta.disk_health.first_seen IS NOT NULL
                     THEN meta.disk_health.first_seen
                ELSE now() END,
              days_in_alert=CASE
                WHEN EXCLUDED.alert = 'ok' THEN NULL
                WHEN meta.disk_health.alert = EXCLUDED.alert
                     AND meta.disk_health.first_seen IS NOT NULL
                     THEN GREATEST(0, EXTRACT(DAY FROM
                          now() - meta.disk_health.first_seen)::int)
                ELSE 0 END,
              checked_at=now()""", rows)
        # no_data 超 7 天升级 persistent_no_data（断供实锤）
        cur.execute("""UPDATE meta.disk_health
                       SET alert = 'persistent_no_data'
                       WHERE alert = 'no_data'
                         AND first_seen IS NOT NULL AND first_seen < %s""",
                    (cutoff,))
        upgraded = cur.rowcount
    pg.commit()
    return upgraded


def main():
    cat = json.load(io.open(CATALOG, encoding="utf-8"))
    rows = derive(cat.get("tables") or {})
    pg = psycopg2.connect(**PG_DSN, password=load_pg_password())
    try:
        ensure_table(pg)
        upgraded = upsert(pg, rows)
        with pg.cursor() as cur:
            cur.execute("""SELECT alert, COUNT(*) FROM meta.disk_health
                           GROUP BY alert ORDER BY 2 DESC""")
            dist = cur.fetchall()
            cur.execute("""SELECT asset, source, days_in_alert
                           FROM meta.disk_health
                           WHERE alert = 'persistent_no_data'
                           ORDER BY days_in_alert DESC""")
            persist = cur.fetchall()
        print("catalog v%s, %d tables checked" % (cat.get("version"),
                                                  len(rows)))
        for a, n in dist:
            print("  %-20s %d" % (a, n))
        print("persistent_no_data (断供≥%d天): %d" % (PERSIST_DAYS,
                                                      len(persist)))
        for r in persist:
            print("   %-22s %-10s %dd" % r)
        if upgraded:
            print("upgraded this run:", upgraded)
    finally:
        pg.close()


if __name__ == "__main__":
    sys.exit(main())
