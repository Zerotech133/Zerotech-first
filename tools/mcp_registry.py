#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""私有 MCP Registry v1 只读校验/清单。

用法：
  python -X utf8 tools/mcp_registry.py --check
  python -X utf8 tools/mcp_registry.py --json
  python -X utf8 tools/mcp_registry.py --list
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "platform" / "mcp" / "registry.yaml"
REQUIRED = ("id", "transport", "owner", "status", "capabilities")
ALLOWED = {"stdio", "streamable_http", "sse"}


def load() -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    return yaml.safe_load(REG.read_text(encoding="utf-8")) or {}


def validate(doc: dict | None = None) -> dict:
    d = doc if doc is not None else load()
    servers = d.get("servers") or []
    errors = []
    ids = []
    for i, s in enumerate(servers):
        if not isinstance(s, dict):
            errors.append(f"servers[{i}] not object")
            continue
        for k in REQUIRED:
            if not s.get(k):
                errors.append(f"servers[{i}] missing {k}")
        if s.get("transport") not in ALLOWED:
            errors.append(f"{s.get('id')}: transport not allowed: {s.get('transport')}")
        sid = s.get("id")
        if sid in ids:
            errors.append(f"duplicate id: {sid}")
        ids.append(sid)
    return {"ok": not errors, "schema": d.get("schema"), "version": d.get("version"),
            "servers": len(servers), "ids": ids, "errors": errors}


def reconcile(doc: dict | None = None, config_path: Path | None = None) -> dict:
    """与 ~/.hermes/config.yaml 的 mcp_servers 做运行时对账（只读）。"""
    d = doc if doc is not None else load()
    registered = {s.get("id") for s in (d.get("servers") or []) if isinstance(s, dict)}
    cfg_path = config_path or (Path.home() / ".hermes" / "config.yaml")
    configured = set()
    error = None
    try:
        if yaml is not None and cfg_path.is_file():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) or {}
            configured = set((cfg.get("mcp_servers") or {}).keys())
        else:
            error = f"config unavailable: {cfg_path}"
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    missing_in_registry = sorted(configured - registered)
    missing_in_config = sorted(registered - configured)
    return {"ok": not error and not missing_in_registry and not missing_in_config,
            "registered": len(registered), "configured": len(configured),
            "matched": len(registered & configured),
            "missing_in_registry": missing_in_registry,
            "missing_in_config": missing_in_config,
            "error": error}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MCP registry 校验")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    args = ap.parse_args(argv)
    d = load()
    r = validate(d)
    if args.reconcile:
        rec = reconcile(d)
        print(json.dumps({"validate": r, "reconcile": rec}, ensure_ascii=False, indent=1))
        return 0 if r["ok"] and rec["ok"] else 1
    if args.list:
        for s in d.get("servers") or []:
            print(f"{s.get('id')}\t{s.get('transport')}\t{s.get('owner')}\t{s.get('status')}")
        return 0 if r["ok"] else 1
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(("PASS" if r["ok"] else "FAIL") + f": MCP servers={r['servers']}" + ("" if r["ok"] else " errors=" + "; ".join(r["errors"])))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
