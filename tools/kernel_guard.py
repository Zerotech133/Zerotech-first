#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内核守卫（P1 MVP）：内核契约文件改动必须带 ADR 号。

读取 platform/kernel_v1.yaml 的 artifacts（status=existing/frozen），
与改动文件集合比对；命中且未提供 ADR 号 → exit 1。

用法：
  python -X utf8 tools/kernel_guard.py --files a.py,b.md --adr ADR-015
  python -X utf8 tools/kernel_guard.py --staged --adr ADR-015
  python -X utf8 tools/kernel_guard.py --files a.py --commit-msg "feat: xxx ADR-015"
  python -X utf8 tools/kernel_guard.py --list
关联判定：--adr 优先；其次 --commit-msg 文本中出现 ADR-号（如 ADR-015）即视为关联。
退出码：0 允许；1 阻断（内核改动无 ADR）；2 参数/环境错误。
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "platform" / "kernel_v1.yaml"
ADR_RE = re.compile(r"ADR-\d{3,}")


def _norm(p: str) -> str:
    """改动路径归一：反斜杠→/、去 ./；**仓内绝对路径转相对**（防绝对路径绕过门禁）。

    Windows/git 的输出通常已是相对路径；但手工/包装器传绝对路径时，若不归一，
    `match()` 对内核 artifact（相对路径）永不命中 → 内核改动被 fail-open 放行。
    仓外绝对路径保持原样（不可能命中仓内 artifact，不影响判定）。
    """
    p = (p or "").replace("\\", "/").strip()
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("/") or re.match(r"^[A-Za-z]:/", p):
        try:
            p = str(Path(p).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
        except (ValueError, OSError):
            pass
    return p


def load_kernel_artifacts() -> list[dict]:
    if not KERNEL.exists() or yaml is None:
        return []
    data = yaml.safe_load(KERNEL.read_text(encoding="utf-8")) or {}
    out = []
    out.append({"path": "platform/kernel_v1.yaml", "kind": "kernel-manifest", "contract": "KERNEL"})
    for k, v in (data.get("contracts") or {}).items():
        for a in v.get("artifacts", []):
            if a.get("status") in ("existing", "frozen"):
                out.append({"path": _norm(a.get("path", "")), "kind": a.get("kind", ""), "contract": k})
    return out


def changed_files(staged: bool, files_arg: str | None) -> list[str]:
    if files_arg:
        return [_norm(x) for x in files_arg.split(",") if x.strip()]
    args = ["git", "diff", "--name-only", "--cached"] if staged else ["git", "diff", "--name-only"]
    try:
        p = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        return [_norm(x) for x in (p.stdout or "").splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def match(art: str, changed: str) -> bool:
    if not art:
        return False
    if "*" in art or "?" in art:
        return fnmatch.fnmatch(changed, art)
    return changed == art or changed.startswith(art.rstrip("/") + "/")


def main(argv=None):
    ap = argparse.ArgumentParser(description="内核守卫（P1 MVP）")
    ap.add_argument("--files", default=None, help="逗号分隔的改动文件（缺省用 git diff）")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--adr", default="", help="本次改动关联的 ADR 号，如 ADR-015")
    ap.add_argument("--commit-msg", default="",
                    help="提交信息全文；其中出现 ADR-号也视为已关联（--adr 优先）")
    ap.add_argument("--list", action="store_true", help="列出受保护的内核 artifact")
    args = ap.parse_args(argv)

    arts = load_kernel_artifacts()
    if args.list:
        for a in arts:
            print(f"{a['contract']}\t{a['kind']}\t{a['path']}")
        return 0
    if not arts:
        print("[kernel_guard] WARN: kernel_v1.yaml 缺失或无法解析，放行（fail-open）")
        return 0
    changed = changed_files(args.staged, args.files)
    hits = []
    for c in changed:
        for a in arts:
            if match(a["path"], c):
                hits.append((c, a)); break
    if not hits:
        print(f"[kernel_guard] OK: {len(changed)} 个改动，无内核契约命中")
        return 0
    adr = ADR_RE.search(args.adr or "")
    src = "--adr"
    if not adr:
        adr = ADR_RE.search(args.commit_msg or "")
        src = "--commit-msg"
    if not adr:
        print(f"[kernel_guard] BLOCK: {len(hits)} 个内核契约文件被改动，但未提供 ADR 号")
        for c, a in hits:
            print(f"  - {c}  ({a['contract']}/{a['kind']})")
        print("  解决：--adr ADR-0xx，或 --commit-msg 传入带 ADR 号的提交信息")
        return 1
    print(f"[kernel_guard] OK: {len(hits)} 个内核契约改动，已关联 {adr.group(0)}（来源 {src}）")
    for c, a in hits:
        print(f"  - {c}  ({a['contract']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
