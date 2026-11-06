#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C4 双时态迁移 CLI：索引 DDL + 存量回填（默认 dry-run，--apply 才写图）。

用法：
    python kg_migrate_bitemporal.py            # dry-run：打印将执行的语句
    python kg_migrate_bitemporal.py --apply    # 实际执行（幂等，可重跑）
    python kg_migrate_bitemporal.py --ddl-only # 只出 DDL 文本（给 DBA 审）

设计要点：
- DDL 全部 IF NOT EXISTS，幂等；回填只补缺失字段（WHERE ... IS NULL），
  不覆盖任何已有值，重跑无副作用。
- Neo4j 不可达 -> 退出码 1（迁移器不得静默成功，与 C2 门禁同哲学）。
- 凭据走 env（KG_NEO4J_* / NEO4J_*），与 kg_cq 同约定，不落盘不打印。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology as O  # noqa: E402


def _connect_neo4j():
    """与 kg_cq 同约定：解析凭据并连接；失败返回 (None, None)。"""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[migrate] neo4j driver 未安装", file=sys.stderr)
        return None, None
    uri = os.environ.get("KG_NEO4J_URI") or os.environ.get("NEO4J_URI")
    user = os.environ.get("KG_NEO4J_USER") or os.environ.get("NEO4J_USER") or "neo4j"
    pw = os.environ.get("KG_NEO4J_PASSWORD") or os.environ.get("NEO4J_PASSWORD")
    db = os.environ.get("KG_NEO4J_DB") or os.environ.get("NEO4J_DB") or "neo4j"
    if not uri or not pw:
        print("[migrate] Neo4j 凭据未配置（KG_NEO4J_URI/PASSWORD）", file=sys.stderr)
        return None, None
    try:
        drv = GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=5)
        drv.verify_connectivity()
        return drv, db
    except Exception as exc:  # noqa: BLE001
        print("[migrate] Neo4j 连接失败: %s" % exc, file=sys.stderr)
        return None, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="C4 双时态迁移（索引+回填）")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    ap.add_argument("--ddl-only", action="store_true", help="只打印 DDL 文本")
    ap.add_argument("--skip-backfill", action="store_true",
                    help="只建索引，不回填存量字段")
    a = ap.parse_args(argv)

    ddl = O.bitemporal_ddl()
    backfill = [] if a.skip_backfill else O.bitemporal_backfill_cypher()

    if a.ddl_only:
        for s in ddl:
            print(s)
        return 0

    print("=== C4 双时态迁移（TBox v%s）===" % O.TBOX_VERSION)
    print("时变边域: %s" % "/".join(O.BITEMPORAL_EDGE_DOMAINS))
    print("DDL 语句: %d 条（IF NOT EXISTS，幂等）" % len(ddl))
    print("回填语句: %d 条（只补缺失，不覆盖）" % len(backfill))

    drv, db = _connect_neo4j()
    if drv is None:
        print("RESULT: FAIL（Neo4j 不可达，迁移不得静默成功）")
        return 1

    ok = fail = 0
    t0 = time.time()
    with drv.session(database=db) as s:
        for stmt in ddl:
            try:
                s.run(stmt).consume()
                ok += 1
                if not a.apply:
                    pass
            except Exception as exc:  # noqa: BLE001
                fail += 1
                print("  [DDL FAIL] %s\n    %s" % (stmt[:80], str(exc)[:120]))
        if a.apply:
            for desc, stmt in backfill:
                try:
                    s.run(stmt).consume()
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    fail += 1
                    print("  [BACKFILL FAIL] %s\n    %s" % (desc, str(exc)[:120]))
    drv.close()

    mode = "APPLY" if a.apply else "DRY-RUN（未回填，仅 DDL 探测；加 --apply 落地）"
    print("执行 %s: ok=%d fail=%d 用时 %.1fs"
          % (mode, ok, fail, time.time() - t0))
    if fail:
        print("RESULT: FAIL（%d 条失败，逐条见上）" % fail)
        return 1
    if not a.apply:
        print("RESULT: DRY-RUN OK（加 --apply 执行回填）")
    else:
        print("RESULT: APPLY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
