# -*- coding: utf-8 -*-
"""0 元 MVP 数据源健康检查：只读配置/包/HTTP，不写库。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "data_sources.yaml"


def check_one(src: dict, online: bool) -> dict:
    out = {"id": src["id"], "name": src.get("name", ""), "kind": src.get("kind"),
           "markets": src.get("markets", []), "ok": False, "warning": False, "detail": ""}
    kind = src.get("kind")
    if kind == "local":
        p = Path(src.get("path", ""))
        out["ok"] = p.exists()
        out["detail"] = "path exists" if out["ok"] else "path missing"
    elif kind == "python":
        pkg = src.get("package", "")
        found = importlib.util.find_spec(pkg) is not None if pkg else False
        out["ok"] = found or not src.get("required", True)
        out["warning"] = not found
        out["detail"] = "importable" if found else ("optional package missing" if not src.get("required", True) else "package missing")
    elif kind == "http":
        if not online:
            out["ok"] = True
            out["detail"] = "offline (not probed)"
        else:
            try:
                req = urllib.request.Request(src["url"], headers={"User-Agent": "zhice-data-health/1"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    out["ok"] = 200 <= r.status < 400
                    out["detail"] = "HTTP %s" % r.status
            except Exception as e:  # noqa: BLE001
                out["detail"] = "ERR %s" % str(e)[:120]
    else:
        out["detail"] = "unknown kind"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--json", default=r"C:\Temp\data_source_health.json")
    args = ap.parse_args()
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    results = [check_one(s, args.online) for s in cfg.get("sources", [])]
    payload = {"online": args.online, "total": len(results),
               "ok": sum(1 for r in results if r["ok"]),
               "warnings": sum(1 for r in results if r.get("warning")), "results": results}
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print("%-14s %-5s %-6s %s" % (r["id"], "OK" if r["ok"] else "FAIL", r["kind"], r["detail"]))
    print("saved", args.json)
    return 0 if all(r["ok"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
