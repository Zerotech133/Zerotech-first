#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 /os-board、量化/情报域页注入通用页面工具（搜索/折叠）。

末尾追加，幂等，可 --revert；不重写业务。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINK = '<link rel="stylesheet" href="/static/os_page_tools.css" data-os-page-tools="1">'
SCRIPT = '<script defer src="/static/os_page_tools.js" data-os-page-tools="1"></script>'
LINK_MARK = "<!-- OS-PAGE-TOOLS:style -->"
SCRIPT_MARK = "<!-- OS-PAGE-TOOLS:script -->"

TARGETS = (
    "portal/os.html",
    "portal/quant.html",
    "portal/quant_drill.html",
    "portal/quant_paper.html",
    "portal/quant_v2_demo.html",
    "portal/intel_workbench.html",
    "portal/news_intel.html",
    "portal/xq_intel.html",
    "portal/intel_dossier_skeleton.html",
)


def strip(text: str) -> str:
    import re
    text = re.sub(r"\s*<!--\s*OS-PAGE-TOOLS:[^>]*-->\s*", "", text, flags=re.I)
    text = re.sub(r"\s*<link[^>]+os_page_tools\.css[^>]*>\s*", "", text, flags=re.I)
    text = re.sub(r"\s*<script[^>]+os_page_tools\.js[^>]*>\s*</script>\s*", "", text, flags=re.I)
    return text


def inject(text: str) -> str:
    text = strip(text)
    block = "\n%s\n%s\n%s\n%s\n" % (LINK_MARK, LINK, SCRIPT_MARK, SCRIPT)
    body = text.lower().rfind("</body>")
    if body >= 0:
        return text[:body] + block + text[body:]
    return text.rstrip() + "\n" + block


def run(check: bool, revert: bool = False):
    changed = []
    missing = []
    errors = []
    for rel in TARGETS:
        p = REPO / rel
        if not p.is_file():
            errors.append({"file": rel, "error": "missing"})
            continue
        raw = p.read_text(encoding="utf-8")
        new = strip(raw) if revert else inject(raw)
        if new == raw:
            continue
        if check:
            missing.append(rel)
        else:
            p.write_text(new, encoding="utf-8", newline="\n")
            changed.append(rel)
    return {"ok": not errors and (not check or not missing), "targets": len(TARGETS),
            "missing": len(missing), "changed": len(changed), "errors": errors,
            "missing_files": missing, "changed_files": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="注入 OS 页面工具（搜索/折叠）")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = run(check=not args.apply, revert=args.revert)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[inject_os_tools] targets=%d missing=%d changed=%d errors=%d"
              % (out["targets"], out["missing"], out["changed"], len(out["errors"])))
        for f in out["missing_files"]:
            print("  -", f)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
