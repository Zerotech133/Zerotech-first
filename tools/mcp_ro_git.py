# -*- coding: utf-8 -*-
"""MCP 只读 git server：status / log / diff（固定参数，无 shell，仓库白名单）。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_platform.mcp.server import serve_stdio  # noqa: E402

GIT_REPO = REPO


def _git(args: list[str], timeout: int = 30) -> dict:
    try:
        r = subprocess.run(["git", "-C", str(GIT_REPO)] + args,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return {"ok": r.returncode == 0, "rc": r.returncode,
                "stdout": (r.stdout or "")[:200000],
                "stderr": (r.stderr or "")[:2000]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def git_status():
    return dict(_git(["status", "--short", "--branch"]), repo=str(GIT_REPO))


def git_log(limit: int = 10):
    limit = max(1, min(int(limit or 10), 50))
    return dict(_git(["log", "--oneline", "-n", str(limit)]), repo=str(GIT_REPO))


def git_diff(path: str = "", max_lines: int = 200):
    if path and (".." in path or path.startswith("/") or path.startswith("\\")):
        return {"ok": False, "error": "bad_path"}
    args = ["diff", "--stat"] if not path else ["diff", "--", path]
    rep = _git(args if not path else ["diff", "--", path])
    if rep.get("ok") and not path:
        rep["ok"] = True
    lines = rep.get("stdout", "").splitlines()
    max_lines = max(1, min(int(max_lines or 200), 1000))
    rep["stdout"] = "\n".join(lines[:max_lines])
    rep["truncated"] = len(lines) > max_lines
    return dict(rep, repo=str(GIT_REPO))


TOOLS = {
    "git_status": {"description": "只读 git status（短格式）",
                   "inputSchema": {"type": "object", "properties": {}},
                   "handler": git_status},
    "git_log": {"description": "只读 git log --oneline",
                "inputSchema": {"type": "object", "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                "handler": git_log},
    "git_diff": {"description": "只读 git diff（可按路径，限行数）",
                 "inputSchema": {"type": "object", "properties": {
                     "path": {"type": "string"},
                     "max_lines": {"type": "integer"}}},
                 "handler": git_diff},
}


def main(argv=None) -> int:
    global GIT_REPO
    ap = argparse.ArgumentParser(description="MCP 只读 git server")
    ap.add_argument("--repo", default=str(REPO))
    args = ap.parse_args(argv)
    GIT_REPO = Path(args.repo).resolve()
    return serve_stdio(TOOLS, "zhaoyun-ro-git")


if __name__ == "__main__":
    raise SystemExit(main())
