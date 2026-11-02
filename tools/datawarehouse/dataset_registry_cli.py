# -*- coding: utf-8 -*-
"""统一取数入口 CLI（WP-I3 骨架）：dataset_id 一码查/取。

用法：
  python tools/datawarehouse/dataset_registry_cli.py stats
  python tools/datawarehouse/dataset_registry_cli.py stats-v2          # 合并口径（DWH-103；PG 未就绪 rc=2，fail-visible）
  python tools/datawarehouse/dataset_registry_cli.py list --domain 学习派生 --limit 20
  python tools/datawarehouse/dataset_registry_cli.py list --kind pg_table --limit 20
  python tools/datawarehouse/dataset_registry_cli.py search --q 雪球
  python tools/datawarehouse/dataset_registry_cli.py get --id yt_learning_db
  python tools/datawarehouse/dataset_registry_cli.py sample --id xueqiu_info_xq_status_classified --n 3
  python tools/datawarehouse/dataset_registry_cli.py resolve-ref --ref dataset/pg/v2_acad1_pool
  python tools/datawarehouse/dataset_registry_cli.py emit --out agent_platform/catalogs/dataset_registry_v1.yaml

退出码：0 ok；1 用法/内部错；2 目标不存在或连接器/契约产物 fail-visible（不假绿）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset_registry import (  # noqa: E402
    ConnectorUnavailable,
    ContractArtifactUnavailable,
    DatasetRegistry,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据仓统一取数入口（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    sub.add_parser("stats-v2")
    p = sub.add_parser("list")
    p.add_argument("--domain")
    p.add_argument("--q")
    p.add_argument("--registered", choices=["0", "1"])
    p.add_argument("--exposed", choices=["0", "1"])
    p.add_argument("--kind", choices=["all", "pg_table", "contract_artifact"])
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("search")
    p.add_argument("--q", required=True)
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("get")
    p.add_argument("--id", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--id", required=True)
    p.add_argument("--n", type=int, default=3)
    p = sub.add_parser("resolve-ref")
    p.add_argument("--ref", required=True)
    p = sub.add_parser("emit")
    p.add_argument("--out", default="agent_platform/catalogs/dataset_registry_v1.yaml")
    ap.add_argument("--json", action="store_true", default=False, help="JSON 输出")
    args = ap.parse_args(argv)
    reg = DatasetRegistry()
    rc = 0
    if args.cmd == "stats":
        out = reg.stats()
    elif args.cmd == "stats-v2":
        out = reg.stats_v2()
        # ADR-019 D2：连接器不可用/缺 DSN → exit 2（fail-visible，不假绿）；显式 disabled 不算失败
        rc = 0 if out.get("connector_status") in ("ok", "disabled") else 2
    elif args.cmd == "list":
        out = reg.list(domain=args.domain, q=args.q,
                       registered=None if args.registered is None else args.registered == "1",
                       exposed=None if args.exposed is None else args.exposed == "1",
                       limit=args.limit, kinds=args.kind)
    elif args.cmd == "search":
        out = reg.list(q=args.q, limit=args.limit)
    elif args.cmd == "get":
        out = reg.get(args.id)
        if out is None:
            print(json.dumps({"error": "dataset_id 不存在", "id": args.id}, ensure_ascii=False))
            return 2
    elif args.cmd == "resolve-ref":
        out = reg.resolve_ref(args.ref)
        if out is None:
            print(json.dumps({"error": "ref 未登记", "ref": args.ref}, ensure_ascii=False))
            return 2
    elif args.cmd == "sample":
        try:
            out = reg.sample(args.id, n=args.n)
        except (ConnectorUnavailable, ContractArtifactUnavailable, PermissionError) as exc:
            print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc))[:240],
                              "fail_visible": True, "id": args.id},
                             ensure_ascii=False, indent=1))
            return 2
    elif args.cmd == "emit":
        p = reg.emit_yaml(args.out)
        out = {"emitted": str(p)}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main())
