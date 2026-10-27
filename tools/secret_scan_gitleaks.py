# -*- coding: utf-8 -*-
"""gitleaks 包装器：调用本机 gitleaks 扫描指定目录（默认主仓），输出摘要。

用途：作为自研 scan_secrets 的第二道（真实凭据面），merge 门 warn-only。
用法：
  python tools/secret_scan_gitleaks.py            # 扫主仓（默认）
  python tools/secret_scan_gitleaks.py --path tools --json
退出码：0=无泄露；1=有泄露；2=gitleaks 不存在/执行失败。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / ".gitleaks.toml"
REPORT = Path(os.environ.get("GITLEAKS_REPORT", r"C:\Temp\gitleaks_report.json"))


def find_gitleaks() -> str | None:
    exe = shutil.which("gitleaks")
    if exe:
        return exe
    # winget 安装位置（PATH 未刷新时兜底）
    patterns = [
        str(Path(os.environ.get("LOCALAPPDATA", "")) /
            "Microsoft/WinGet/Packages/**/gitleaks.exe"),
    ]
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return None


def _materialize_diff(diff_spec: str, base: Path) -> Path | None:
    """把 master...branch 的变更文件复制到临时目录（保留相对路径），返回扫描根。"""
    import shutil
    import tempfile
    r = subprocess.run(["git", "-C", str(base), "diff", "--name-only", diff_spec],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    rels = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if not rels:
        return None
    out = Path(tempfile.mkdtemp(prefix="gitleaks_diff_"))
    for rel in rels:
        src = base / rel
        if not src.is_file():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return out


def scan(path: Path, report: Path) -> tuple[int, int, list[dict]]:
    exe = find_gitleaks()
    if not exe:
        return 2, 0, [{"error": "gitleaks_not_found"}]
    cmd = [exe, "dir", str(path), "--no-banner", "-f", "json", "-r", str(report)]
    if CONFIG.exists():
        cmd += ["--config", str(CONFIG)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    if not report.exists():
        return 2, 0, [{"error": "report_missing", "stderr": (proc.stderr or "")[-300:]}]
    try:
        data = json.loads(report.read_text(encoding="utf-8", errors="replace") or "[]")
    except ValueError:
        return 2, 0, [{"error": "report_unparsable"}]
    findings = data if isinstance(data, list) else []
    return (0 if not findings else 1), len(data), findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="gitleaks 包装器（第二道凭据扫描）")
    ap.add_argument("--path", default=str(REPO))
    ap.add_argument("--diff", default="", help="只扫分支变更文件：master...<branch>")
    ap.add_argument("--worktree", default="", help="diff 模式的文件根（worktree 路径）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    target = Path(args.path)
    if args.diff:
        target = _materialize_diff(args.diff, Path(args.worktree) if args.worktree else Path(REPO))
        if target is None:
            if args.json:
                print(json.dumps({"tool": "secret_scan_gitleaks", "code": 0, "findings": 0,
                                  "mode": "diff", "note": "no_changed_files"}, ensure_ascii=False))
            return 0
    rc, n, findings = scan(target, REPORT)
    if args.json:
        print(json.dumps({
            "tool": "secret_scan_gitleaks",
            "code": rc,
            "path": args.path,
            "findings": n,
            "sample": [
                {"rule": f.get("RuleID"), "file": f.get("File"), "line": f.get("StartLine")}
                for f in findings[:10]
            ],
        }, ensure_ascii=False, indent=1))
    elif not args.quiet:
        print("gitleaks scan: path=%s findings=%d rc=%d" % (args.path, n, rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
