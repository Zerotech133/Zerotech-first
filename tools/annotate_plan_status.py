# -*- coding: utf-8 -*-
"""给 docs/plans 计划补一行权威 STATUS（有则跳过）。

- 只处理 docs/plans/**/*.md，排除 README 与 PLAN_REGISTER 生成物；
- 在首个 # 标题后插入 `> STATUS: ...`；
- 已存在 STATUS/状态 行或 PLAN_STATUS 标记的不动；
- 默认 dry-run；--apply 才写入。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "docs" / "plans"
SKIP_NAMES = {"README.md"}
STATUS_RE = re.compile(r"(?im)^\s*(?:status|状态)\s*[:：]|<!--\s*PLAN_STATUS")
HEAD_RE = re.compile(r"^#\s+.*$", re.M)


def classify(text: str) -> str:
    # 只看头部/标题语义，避免正文提到 superseded 就误判（2026-10-31 已踩过）
    head = text[:1500]
    if "STATUS: superseded" in head or "已废弃" in head or "已被取代" in head:
        return "superseded（见 PLAN_REGISTER_ALL_20261031）"
    if "待批" in head or "待拍板" in head:
        return "pending_approval（owner: TBD；2026-11-07 前拍板）"
    if "冻结" in head:
        return "frozen（只读保留）"
    return "unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    changed, skipped = [], []
    for p in sorted(PLANS.rglob("*.md")):
        if p.name in SKIP_NAMES or p.name.startswith("PLAN_REGISTER"):
            skipped.append(str(p.relative_to(ROOT)))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if STATUS_RE.search(text[:6000]):
            skipped.append(str(p.relative_to(ROOT)))
            continue
        status = classify(text)
        line = "> STATUS: " + status + "\n\n"
        m = HEAD_RE.search(text)
        if m:
            new = text[:m.end()] + "\n" + line + text[m.end():]
        else:
            new = line + text
        if args.apply:
            p.write_text(new, encoding="utf-8")
        changed.append({"path": str(p.relative_to(ROOT)), "status": status})
    out = {"ok": True, "apply": args.apply, "changed": len(changed),
           "skipped": len(skipped), "files": changed[:50]}
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else
          "changed=%d skipped=%d apply=%s" % (len(changed), len(skipped), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
