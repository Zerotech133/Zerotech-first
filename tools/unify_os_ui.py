#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 OS 统一主题/下钻层注入全站 HTML（幂等，可回滚）。

默认 --check 只报告；--apply 才写盘。
只改 <head>/<body> 两处标记，不重写页面业务结构。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINK = '<link rel="stylesheet" href="/static/os_theme.css" data-os-unify="1">'
SCRIPT = '<script defer src="/static/os_drill.js" data-os-unify="1"></script>'
LINK_MARK = "<!-- OS-UI-UNIFY:theme -->"
SCRIPT_MARK = "<!-- OS-UI-UNIFY:drill -->"
MARKERS = (LINK_MARK, SCRIPT_MARK)

DEFAULT_DIRS = (
    "portal",
    "portal/m",
    "portal/picks_public",
    "portal/protos",
    "portal/czsc-eval",
    "portal/chanlun-learn-report",
)


def _strip_old(text: str) -> str:
    # 删掉注入块及其包裹换行，尽量恢复原始字节，避免 secret-scan 基线行号漂移。
    text = re.sub(
        r"\s*<!--\s*OS-UI-UNIFY:theme\s*-->\s*<link[^>]+os_theme\.css[^>]*>\s*",
        "", text, flags=re.I)
    text = re.sub(
        r"\s*<!--\s*OS-UI-UNIFY:drill\s*-->\s*<script[^>]+os_drill\.js[^>]*>\s*</script>\s*",
        "", text, flags=re.I)
    return text


def inject(text: str) -> str:
    text = _strip_old(text)
    # 关键：只在文件末尾追加，不往 <head> 插，避免整体行号偏移触发
    # secret-scan 基线误报（os_council 等 JS 敏感行号）。
    block = "\n%s\n%s\n%s\n%s\n" % (LINK_MARK, LINK, SCRIPT_MARK, SCRIPT)
    body = re.search(r"</body\s*>", text, flags=re.I)
    if body:
        text = text[:body.start()] + block + text[body.start():]
    else:
        text = text.rstrip() + "\n" + block
    return text


def targets(dirs=DEFAULT_DIRS):
    out = []
    for d in dirs:
        base = REPO / d
        if not base.is_dir():
            continue
        out.extend(sorted(base.glob("*.html")))
    # 去重、排除历史快照/测试产物目录
    seen = set()
    pages = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        if any(part in ("data", "work", "tests", "__pycache__") for part in p.parts):
            continue
        pages.append(p)
    return pages


def process(check: bool, dirs=DEFAULT_DIRS, revert: bool = False) -> dict:
    pages = targets(dirs)
    missing = []
    changed = []
    errors = []
    for p in pages:
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"file": str(p), "error": str(exc)})
            continue
        if revert:
            new = _strip_old(raw)
            if new != raw:
                if check:
                    missing.append(str(p.relative_to(REPO)))
                else:
                    try:
                        p.write_text(new, encoding="utf-8", newline="\n")
                        changed.append(str(p.relative_to(REPO)))
                    except OSError as exc:
                        errors.append({"file": str(p), "error": str(exc)})
            continue
        need = LINK not in raw or SCRIPT not in raw or LINK_MARK not in raw or SCRIPT_MARK not in raw
        if not need:
            continue
        missing.append(str(p.relative_to(REPO)))
        if check:
            continue
        try:
            p.write_text(inject(raw), encoding="utf-8", newline="\n")
            changed.append(str(p.relative_to(REPO)))
        except (OSError, ValueError) as exc:
            errors.append({"file": str(p), "error": str(exc)})
    return {
        "ok": not errors and (not check or not missing),
        "mode": "revert" if revert else ("check" if check else "apply"),
        "targets": len(pages),
        "missing": len(missing),
        "changed": len(changed),
        "missing_files": missing[:40],
        "changed_files": changed[:40],
        "errors": errors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OS 全站主题/下钻注入")
    ap.add_argument("--check", action="store_true", help="只检查（默认）")
    ap.add_argument("--apply", action="store_true", help="写盘")
    ap.add_argument("--revert", action="store_true", help="移除注入标记（回滚）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dir", action="append", default=None, help="限定目录（可重复）")
    args = ap.parse_args(argv)
    check = not args.apply
    dirs = tuple(args.dir) if args.dir else DEFAULT_DIRS
    out = process(check=check, dirs=dirs, revert=args.revert)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[unify_os_ui] targets=%d missing=%d changed=%d errors=%d"
              % (out["targets"], out["missing"], out["changed"], len(out["errors"])))
        for f in out["missing_files"]:
            print("  -", f)
        for e in out["errors"][:5]:
            print("  !", e["file"], e["error"])
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
