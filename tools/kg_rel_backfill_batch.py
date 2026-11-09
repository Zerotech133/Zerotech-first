#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C4 配套：大边型 valid_from 批量化回填（IN TRANSACTIONS，防单事务 OOM）。

背景（2026-11-07 实证）：kg_migrate_bitemporal --apply 的整段式回填在
ON_DATE（82 万条）上触发 Transaction MemoryPoolOutOfMemoryError；
本工具用 CALL IN TRANSACTIONS OF N ROWS 分批重放，IS NULL 守卫幂等。

用法（凭据与 kg_migrate_bitemporal 同约定，走 env / kg_schema）：
    python kg_rel_backfill_batch.py ON_DATE
    python kg_rel_backfill_batch.py ON_DATE --batch 5000

回填表达式取自 ontology 注册表（date_field 优先，缺省 date()）。
失败退出码 1；Neo4j 不可达不得静默成功（与迁移器同哲学）。
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB = os.path.join(os.path.dirname(_HERE), "quant_backtest")
for _p in (_HERE, _QB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ontology as O  # noqa: E402


def _connect():
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[backfill] neo4j driver 未安装", file=sys.stderr)
        return None
    uri = os.environ.get("KG_NEO4J_URI") or os.environ.get("NEO4J_URI")
    user = os.environ.get("KG_NEO4J_USER") or os.environ.get("NEO4J_USER") or "neo4j"
    pw = os.environ.get("KG_NEO4J_PASSWORD") or os.environ.get("NEO4J_PASSWORD")
    if not uri or not pw:
        try:
            import kg_schema as S

            uri, user, pw = S.NEO4J_URI, S.NEO4J_USER, S.NEO4J_PASSWORD
        except Exception:  # noqa: BLE001
            pass
    if not uri or not pw:
        print("[backfill] Neo4j 凭据未配置", file=sys.stderr)
        return None
    try:
        drv = GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=5)
        drv.verify_connectivity()
        return drv
    except Exception as exc:  # noqa: BLE001
        print("[backfill] Neo4j 连接失败: %s" % exc, file=sys.stderr)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="大边型 valid_from 批量化回填")
    ap.add_argument("rel", help="边类型（须为双时态时变边）")
    ap.add_argument("--batch", type=int, default=2000, help="每事务行数")
    a = ap.parse_args(argv)

    if a.rel not in O.bitemporal_edge_types():
        print("[backfill] %s 不在时变边注册表中 %s" % (a.rel, O.bitemporal_edge_types()),
              file=sys.stderr)
        return 1
    df = (O.EDGE_TYPES.get(a.rel) or {}).get("date_field")
    fill = ("coalesce(r.`%s`, date())" % df) if df else "date()"

    drv = _connect()
    if drv is None:
        print("RESULT: FAIL（Neo4j 不可达）")
        return 1

    stmt = (
        "MATCH ()-[r:`%s`]->() WHERE r.valid_from IS NULL "
        "CALL (r) { SET r.valid_from = %s } IN TRANSACTIONS OF %d ROWS "
        "RETURN count(r) AS done;" % (a.rel, fill, a.batch)
    )
    try:
        with drv.session() as s:
            rec = s.run(stmt).single()
            done = rec["done"] if rec else 0
        print("RESULT: APPLY OK %s backfilled=%d (batch=%d)" % (a.rel, done, a.batch))
        return 0
    except Exception as exc:  # noqa: BLE001
        print("[backfill] FAIL %s: %s" % (a.rel, str(exc)[:200]), file=sys.stderr)
        print("RESULT: FAIL")
        return 1
    finally:
        drv.close()


if __name__ == "__main__":
    sys.exit(main())
