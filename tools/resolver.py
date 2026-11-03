#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合引用解析器 v1（P1/W1 缺口的独立工具）。

复用 composition.py 的注册表与 resolve_ref 规则，不改运行时、不写文件。
支持：agent / skill / strategy / dataset / page / job（以及 ext/service/interface 软校验）。
用法：
  python -X utf8 tools/resolver.py --all
  python -X utf8 tools/resolver.py --all --strict --json
  python -X utf8 tools/resolver.py --manifest compositions/acad1-legion/manifest.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import composition as C  # noqa: E402

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None


def _manifest(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("缺少 PyYAML")
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError("manifest 顶层不是对象")
    return d


def refs_of(manifest: dict) -> list[str]:
    out = []
    spec = manifest.get("spec") or {}
    for key, label in C.REF_GROUPS:
        if key == "data.requires":
            vals = (spec.get("data") or {}).get("requires") or []
        else:
            vals = spec.get(key) or []
        for item in vals:
            if isinstance(item, dict) and item.get("ref"):
                out.append((label, str(item["ref"])))
            elif isinstance(item, str):
                out.append((label, item))
    return out


def check_manifest(path: Path, regs: dict | None = None) -> dict:
    regs = regs or C.load_registries()
    try:
        m = _manifest(path)
    except Exception as e:  # noqa: BLE001
        return {"manifest": str(path), "ok": False, "error": type(e).__name__ + ":" + str(e)[:200], "refs": []}
    rows, errors, warnings = [], [], []
    for label, ref in refs_of(m):
        errors, warnings = [], []
        C.resolve_ref(ref, regs, errors, warnings)
        status = "error" if errors else ("warn" if warnings else "ok")
        rows.append({"group": label, "ref": ref, "status": status,
                     "errors": errors, "warnings": warnings})
    return {"manifest": str(path), "id": (m.get("metadata") or {}).get("id"),
            "ok": not any(x["status"] == "error" for x in rows),
            "errors": sum(1 for x in rows if x["status"] == "error"),
            "warnings": sum(1 for x in rows if x["status"] == "warn"),
            "refs": rows}


def check_all(strict: bool = False) -> dict:
    regs = C.load_registries()
    files = sorted((ROOT / "compositions").glob("*/manifest.yaml"))
    rows = [check_manifest(p, regs) for p in files]
    total = sum(len(r.get("refs") or []) for r in rows)
    errors = sum(r.get("errors", 0) for r in rows)
    warnings = sum(r.get("warnings", 0) for r in rows)
    return {"schema": "zerobound/resolver/v1", "manifests": rows,
            "summary": {"manifests": len(rows), "refs": total, "errors": errors,
                        "warnings": warnings, "strict": strict,
                        "ok": errors == 0 and (not strict or warnings == 0)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合引用解析器（只读）")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.manifest:
            res = check_manifest(Path(a.manifest))
        elif a.all:
            res = check_all(a.strict)
        else:
            ap.error("需要 --manifest 或 --all")
    except Exception as e:  # noqa: BLE001
        print("resolver_error: %s" % str(e)[:300], file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if "summary" in res:
            s = res["summary"]
            print("manifests=%d refs=%d errors=%d warnings=%d ok=%s"
                  % (s["manifests"], s["refs"], s["errors"], s["warnings"], s["ok"]))
            for m in res["manifests"]:
                print("  %-24s errors=%d warnings=%d ok=%s" % (m.get("id"), m.get("errors", 0), m.get("warnings", 0), m.get("ok")))
        else:
            print("%s ok=%s errors=%d warnings=%d" % (res.get("manifest"), res.get("ok"), res.get("errors", 0), res.get("warnings", 0)))
            for x in res.get("refs") or []:
                print("  [%s] %s %s %s" % (x["status"], x["group"], x["ref"], "; ".join(x["errors"] + x["warnings"])))
    if res.get("summary"):
        return 0 if res["summary"]["ok"] else 1
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
