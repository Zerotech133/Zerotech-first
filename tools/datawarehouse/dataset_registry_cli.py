# -*- coding: utf-8 -*-
"""统一取数入口 CLI（WP-I3 骨架）：dataset_id 一码查/取。

用法：
  python tools/datawarehouse/dataset_registry_cli.py stats
  python tools/datawarehouse/dataset_registry_cli.py list --domain 学习派生 --limit 20
  python tools/datawarehouse/dataset_registry_cli.py search --q 雪球
  python tools/datawarehouse/dataset_registry_cli.py get --id yt_learning_db
  python tools/datawarehouse/dataset_registry_cli.py sample --id xueqiu_info_xq_status_classified --n 3
  python tools/datawarehouse/dataset_registry_cli.py emit --out agent_platform/catalogs/dataset_registry_v1.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset_registry import DatasetRegistry  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据仓统一取数入口（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("list")
    p.add_argument("--domain")
    p.add_argument("--q")
    p.add_argument("--registered", choices=["0", "1"])
    p.add_argument("--exposed", choices=["0", "1"])
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("search")
    p.add_argument("--q", required=True)
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("get")
    p.add_argument("--id", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--id", required=True)
    p.add_argument("--n", type=int, default=3)
    p = sub.add_parser("emit")
    p.add_argument("--out", default="agent_platform/catalogs/dataset_registry_v1.yaml")
    ap.add_argument("--json", action="store_true", default=False, help="JSON 输出")
    args = ap.parse_args(argv)
    reg = DatasetRegistry()
    if args.cmd == "stats":
        out = reg.stats()
    elif args.cmd == "list":
        out = reg.list(domain=args.domain, q=args.q,
                       registered=None if args.registered is None else args.registered == "1",
                       exposed=None if args.exposed is None else args.exposed == "1",
                       limit=args.limit)
    elif args.cmd == "search":
        out = reg.list(q=args.q, limit=args.limit)
    elif args.cmd == "get":
        out = reg.get(args.id)
        if out is None:
            print(json.dumps({"error": "dataset_id 不存在", "id": args.id}, ensure_ascii=False))
            return 2
    elif args.cmd == "sample":
        out = reg.sample(args.id, n=args.n)
    elif args.cmd == "emit":
        p = reg.emit_yaml(args.out)
        out = {"emitted": str(p)}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
