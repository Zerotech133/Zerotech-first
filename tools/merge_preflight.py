#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并前预检（pre-merge-commit 钩子用，2026-10-28 · P13）。

在 git 生成 merge commit 之前运行：
  - 计算 HEAD 与 MERGE_HEAD 的改动文件；
  - 纯文档/配置变更 → 直接放行；
  - 否则跑 `tools/test_gate.ps1 -Suite fast`，新增失败 → exit 1（git 中止合并）。

环境变量：
  DEVTOOL_MERGE_GATE=0  → 显式跳过（回滚/紧急，须在回单注明"绕过质量闸"）
  DEVTOOL_MERGE_SUITE   → 覆盖套件（默认 fast）

退出码：0=放行，1=拒绝合并，2=预检自身异常（按拒绝处理）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
DOC_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".csv", ".html", ".css", ".js")
DOC_PREFIX = ("docs/", "deliverables/", "portal/static/", "agent_platform/skills/")
DEPS_FILES = ("uv.lock", "pyproject.toml")
DEPS_PREFIX = ("requirements/",)
DEP_AGE_HINT_LIMIT = 40
DEP_AGE_HINT_TIMEOUT = 120


def changed_files() -> list[str]:
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                        "diff", "--name-only", "HEAD", "MERGE_HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        # 兜底：暂存区 vs HEAD
        r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                            "diff", "--name-only", "--cached", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for x in (r.stdout or "").splitlines():
        x = x.strip().replace("\\", "/")
        if len(x) >= 2 and x[0] == '"' and x[-1] == '"':
            x = x[1:-1]
        if x:
            out.append(x)
    return out


def is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    return all(f.endswith(DOC_SUFFIX) and f.startswith(DOC_PREFIX) for f in files)


def deps_changed(files: list[str]) -> bool:
    for f in files or []:
        name = f.replace("\\", "/")
        if name in DEPS_FILES or any(name.startswith(p) for p in DEPS_PREFIX):
            return True
    return False


def dep_age_hint(files: list[str], runner=None):
    """依赖文件变更时打印年龄 warn；**永不阻断合并**（warn-only）。"""
    if not deps_changed(files):
        return None
    if os.environ.get("DEVTOOL_DEP_AGE_HINT", "1") == "0":
        print("[merge-gate] dep-age hint：DEVTOOL_DEP_AGE_HINT=0 → 跳过（warn-only）")
        return {"skipped": True}
    cmd = [sys.executable, "-X", "utf8", str(HERE / "dep_age_check.py"),
           "--limit", str(DEP_AGE_HINT_LIMIT), "--json"]
    try:
        run = runner or subprocess.run
        proc = run(cmd, cwd=str(REPO), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=DEP_AGE_HINT_TIMEOUT)
        payload = json.loads((proc.stdout or "").strip())
        fresh = payload.get("fresh") or []
        out = {"checked": payload.get("checked", 0),
               "fresh_count": payload.get("fresh_count", len(fresh)),
               "error_count": payload.get("error_count", 0),
               "fresh_top": [(x.get("name"), x.get("version"), x.get("age_days"))
                             for x in fresh[:5]]}
        print("[merge-gate] dep-age hint：checked=%d fresh=%d errors=%d（仅提示，不阻断）" % (
            out["checked"], out["fresh_count"], out["error_count"]))
        for name, ver, age in out["fresh_top"]:
            print("  DEP-FRESH %s==%s age=%sd" % (name, ver, age))
        return out
    except Exception as exc:  # noqa: BLE001
        print("[merge-gate] dep-age hint 跳过（%s: %s；不阻断）" % (
            type(exc).__name__, str(exc)[:120]))
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


def main() -> int:
    # 2026-10-31 P110：主仓 core.bare 自愈守卫（hook 直连 git merge 前）。
    try:
        sys.path.insert(0, str(HERE))
        from git_worktree_guard import ensure_worktree
        if not ensure_worktree(REPO):
            print("preflight: 主仓 core.bare 异常且修复失败 → 拒绝合并")
            return 1
    except Exception as exc:  # noqa: BLE001
        print("preflight: core.bare 守卫不可用（跳过）：%s" % exc)
    # 2026-10-30 P41：devtask 内联质量闸已真跑并通过（同一进程链），跳过重复测试。
    if os.environ.get("DEVTOOL_PREFLIGHT_DONE") == "1":
        print("preflight: 内联质量闸已跑（跳过重复测试）")
        return 0
    # 2026-10-29 合并串行锁：直连 git merge 的 hook 也需持锁（父进程已持锁则复用）
    if not os.environ.get("AIOS_MERGE_LOCK_TOKEN"):
        try:
            import sys as _sys
            _tools = os.path.dirname(os.path.abspath(__file__))
            if _tools not in _sys.path:
                _sys.path.insert(0, _tools)
            from merge_lock import acquire as _ml_acquire, release as _ml_release, ENV_TOKEN as _ML_ENV
            _tok = _ml_acquire(cmd="pre-merge-commit", timeout=0)
            if not _tok:
                print("[merge-gate] merge lock busy，拒绝并发合并")
                return 1
            os.environ[_ML_ENV] = _tok
            import atexit as _atexit
            _atexit.register(_ml_release, _tok)
        except ImportError:
            pass
    if os.environ.get("DEVTOOL_MERGE_GATE") == "0":
        print("[merge-gate] DEVTOOL_MERGE_GATE=0 → 显式跳过（回滚/紧急）")
        return 0
    suite = os.environ.get("DEVTOOL_MERGE_SUITE", "fast")
    try:
        files = changed_files()
    except Exception as exc:  # 预检异常按拒绝处理
        print("[merge-gate] 预检异常：%s" % exc)
        return 2
    docs = is_docs_only(files)
    print("[merge-gate] 合并改动 %d 文件；docs_only=%s；套件=%s" % (len(files), docs, suite))
    if docs:
        print("[merge-gate] 纯文档/配置变更 → 放行（不跑测试）")
        return 0
    log = TMP / ("merge_hook_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    print("[merge-gate] 跑测试门：test_gate -Suite %s（日志 %s）" % (suite, log))
    with log.open("w", encoding="utf-8") as f:
        rc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(HERE / "test_gate.ps1"), "-Suite", suite],
            cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT,
        ).returncode
    if rc != 0:
        print("[merge-gate] FAIL：测试门未过（rc=%d），拒绝合并。日志：%s" % (rc, log))
        try:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            for ln in tail:
                print("  |", ln)
        except Exception:
            pass
        return 1
    print("[merge-gate] PASS：测试门通过，放行合并")
    dep_age_hint(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
