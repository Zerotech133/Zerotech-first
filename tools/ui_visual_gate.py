#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""组件视觉基线 warn-only 门。

默认 --check 只读；--warn-only 永远 rc=0（合并门用）。
检查：
1. UI Kit 资产/示例页/基线截图存在；
2. manifest 中的 sha256 与当前文件一致；
3. 分支 diff 若改动了 UI Kit，则提示需要更新视觉基线。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "docs" / "visual_baseline" / "2026-10-30_ui_kit" / "manifest.json"
ASSETS = (
    "portal/static/ui/ui.css",
    "portal/static/ui/ui.js",
    "portal/ui_kit.html",
)
SHOTS = (
    "docs/visual_baseline/2026-10-30_ui_kit/ui_kit_1440.png",
    "docs/visual_baseline/2026-10-30_ui_kit/ui_kit_390.png",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def changed_ui(branch: str | None) -> list[str]:
    if not branch:
        return []
    try:
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only",
                            "master...%s" % branch],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [x.strip() for x in r.stdout.splitlines()
            if x.strip() in ASSETS or x.strip() in SHOTS]


def check(branch: str | None = None) -> dict:
    out = {"ok": True, "warnings": [], "errors": [], "changed": changed_ui(branch),
           "manifest": str(MANIFEST.relative_to(REPO))}
    if not MANIFEST.is_file():
        out["warnings"].append("缺少组件视觉基线 manifest")
        return out
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["warnings"].append("manifest 不可读：%s" % exc)
        return out
    expect = data.get("files") or {}
    for rel in ASSETS + SHOTS:
        p = REPO / rel
        if not p.is_file():
            out["warnings"].append("基线文件缺失：%s" % rel)
            continue
        got = sha256(p)
        if expect.get(rel) and expect[rel] != got:
            out["warnings"].append("基线与文件不一致：%s" % rel)
    if out["changed"] and not out["warnings"]:
        # 改动 UI Kit 但 manifest 已同步（hash 对得上）时不告警；只有 hash 不一致才告警
        pass
    out["ok"] = not out["errors"] and not out["warnings"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组件视觉基线 warn-only 门")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--warn-only", action="store_true")
    ap.add_argument("--branch", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = check(args.branch or None)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[ui_visual_gate] ok=%s changed=%s warnings=%d"
              % (out["ok"], len(out["changed"]), len(out["warnings"])))
        for w in out["warnings"][:8]:
            print("  ⚠", w)
    if args.warn_only:
        return 0
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
