#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PageShell v2 生成器：统一壳资产注入 + 新页模板。

用法：
  python tools/page_shell_gen.py --check --pages portal/os_entry_v9_prod.html portal/os.html
  python tools/page_shell_gen.py --apply --all
  python tools/page_shell_gen.py --new customer --title "客户" --eyebrow "DOMAIN" --lead "..."
  python tools/page_shell_gen.py --revert --all

设计：
- 只追加缺失的 canonical 资产，不改现有 head/footer；
- 末尾追加，避免 secret-scan 基线行号漂移；
- 已有同名/同路径资产不重复；
- 新页从 portal/_templates/page_shell_v1.html 生成。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "portal" / "_templates" / "page_shell_v1.html"
CSS = (
    "/static/tokens.css",
    "/static/os_theme.css",
    "/static/ui/ui.css",
)
SCRIPTS = (
    "/static/nav.js",
    "/static/ui/ui.js",
)
MARK = "<!-- PAGE-SHELL-GEN:v2 -->"


def pages_all():
    root = REPO / "portal"
    out = []
    for sub in ("", "m", "picks_public", "protos", "czsc-eval", "chanlun-learn-report"):
        base = root / sub
        if base.is_dir():
            out.extend(sorted(base.glob("*.html")))
    out = [p for p in out if "dw.html" not in p.name]
    return out


def _has(text: str, asset: str) -> bool:
    return asset in text


def inject(text: str) -> str:
    add = []
    for asset in CSS:
        if not _has(text, asset):
            add.append('<link rel="stylesheet" href="%s" data-page-shell="1">' % asset)
    for asset in SCRIPTS:
        if not _has(text, asset):
            add.append('<script defer src="%s" data-page-shell="1"></script>' % asset)
    if not add:
        return text
    block = "\n%s\n%s\n" % (MARK, "\n".join(add))
    body = text.lower().rfind("</body>")
    if body >= 0:
        return text[:body] + block + text[body:]
    return text.rstrip() + "\n" + block


def revert(text: str) -> str:
    text = re.sub(r"\n?%s\n?(?:<link[^>]*data-page-shell=\"1\"[^>]*>\n?|<script[^>]*data-page-shell=\"1\"[^>]*></script>\n?)+" % re.escape(MARK), "", text)
    return text


def process(pages, check: bool, do_revert: bool = False):
    changed, missing, errors, dup = [], [], [], []
    for p in pages:
        if not p.is_file():
            errors.append({"file": str(p), "error": "missing"})
            continue
        raw = p.read_text(encoding="utf-8")
        new = revert(raw) if do_revert else inject(raw)
        # 重复 canonical 资产检测
        for asset in CSS + SCRIPTS:
            if raw.count(asset) > 1 and not do_revert:
                dup.append({"file": str(p.relative_to(REPO)), "asset": asset,
                            "count": raw.count(asset)})
        if new == raw:
            continue
        if check:
            missing.append(str(p.relative_to(REPO)))
        else:
            p.write_text(new, encoding="utf-8", newline="\n")
            changed.append(str(p.relative_to(REPO)))
    return {"ok": not errors and (not check or not missing),
            "targets": len(pages), "missing": len(missing), "changed": len(changed),
            "duplicates": len(dup), "errors": errors,
            "missing_files": missing, "changed_files": changed,
            "duplicate_items": dup[:20]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PageShell v2 生成器")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--new", default="")
    ap.add_argument("--title", default="新页面")
    ap.add_argument("--eyebrow", default="ZEROBOUND · PAGE")
    ap.add_argument("--lead", default="页面说明待补充。")
    args = ap.parse_args(argv)

    if args.new:
        name = re.sub(r"[^a-zA-Z0-9_-]", "-", args.new).strip("-") or "new-page"
        tpl = TEMPLATE.read_text(encoding="utf-8")
        html = (tpl.replace("{{TITLE}}", args.title)
                   .replace("{{EYEBROW}}", args.eyebrow)
                   .replace("{{LEAD}}", args.lead)
                   .replace("{{CONTENT}}", '<section class="ui-panel"><h2>内容</h2><p>待填。</p></section>'))
        out = REPO / "portal" / (name + ".html")
        out.write_text(html, encoding="utf-8", newline="\n")
        print("created", out)
        return 0

    pages = ([REPO / p for p in args.pages] if args.pages else
             (pages_all() if args.all else
              [REPO / "portal" / "os_entry_v9_prod.html", REPO / "portal" / "os.html"]))
    out = process(pages, check=not args.apply, do_revert=args.revert)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[page_shell_gen] targets=%d missing=%d changed=%d dup=%d errors=%d"
              % (out["targets"], out["missing"], out["changed"], out["duplicates"], len(out["errors"])))
        for f in out["missing_files"]:
            print("  -", f)
        for d in out["duplicate_items"]:
            print("  ! duplicate", d)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
