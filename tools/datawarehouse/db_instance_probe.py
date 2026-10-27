# -*- coding: utf-8 -*-
"""数据库实例只读探活：TCP 端口（不连库、不用凭据）。

用法：python tools/datawarehouse/db_instance_probe.py --json
退出码：0=全部在听 / 1=存在离线（按 expected 判定）
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

INSTANCES = [
    (5442, "quant-pg (quant_meta/quant_system)"), (5446, "zhice-db (zhice_os_v2)"),
    (5455, "docker-mysql-1 (rag_flow)"),
    (3306, "openmetadata_mysql"), (3307, "quant-cainiao-mysql"),
    (6379, "docker-redis-1"),
    (7474, "neo4j-kg HTTP"), (7687, "neo4j-kg Bolt"),
    (17687, "zerobound-neo4j-independent"),
    (11434, "Ollama"), (8091, "portal-api"), (8734, "人类入口"),
]


def probe(port: int, timeout: float = 1.2) -> dict:
    t0 = time.time()
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return {"port": port, "up": True, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"port": port, "up": False, "err": type(e).__name__}


def collect() -> dict:
    rows = []
    for port, name in INSTANCES:
        r = probe(port)
        r["name"] = name
        rows.append(r)
    down = [r for r in rows if not r["up"]]
    return {"tool": "db_instance_probe", "code": common.EXIT_WARN if down else common.EXIT_OK,
            "checked": len(rows), "down": [(r["port"], r["name"]) for r in down], "instances": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据库实例探活（只读）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return common.emit(collect(), as_json=a.json, title="db_instance_probe")


if __name__ == "__main__":
    raise SystemExit(main())
