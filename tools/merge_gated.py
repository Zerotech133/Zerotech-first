#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并入口（兼容层）——质量闸已内联 `devtask.py merge`（P0#1 收尾，2026-10-29 P37）。

现状：
  - `python tools/devtask.py merge <TID>` 已内置质量闸：纯文档/配置变更自动跳过，
    其余跑 `tools/test_gate.ps1 -Suite fast`，新增失败即拒绝；
  - 本脚本保留为**兼容入口/预检器**：`--dry-run` 打印改动分类后退出，不合并；
    正常调用直接转发 devtask merge（不重复跑测试）。
绕过（回滚/紧急）：`DEVTOOL_MERGE_GATE=0`（须在回单注明"绕过质量闸"）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
LOG = TMP / ("merge_gated_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
DOC_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".csv", ".html", ".css", ".js")
DOC_PREFIX = ("docs/", "deliverables/", "portal/static/", "agent_platform/skills/")


def _clean_path(x: str) -> str:
    x = x.strip().replace("\\", "/")
    if len(x) >= 2 and x[0] == '"' and x[-1] == '"':
        x = x[1:-1]
    return x


def branch_changed_files(tid: str) -> list[str]:
    """任务分支相对 master 的改动文件列表。"""
    branch = "task/%s" % tid
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                        "merge-base", "master", branch],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    base = (r.stdout or "").strip()
    if not base:
        return []
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                        "diff", "--name-only", base, branch],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [_clean_path(x) for x in (r.stdout or "").splitlines() if x.strip()]


def is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    for f in files:
        if not f.endswith(DOC_SUFFIX):
            return False
        # 2026-10-30 审计修复：非白名单前缀不得跳过测试（防 docs_only 误判绕过质量闸）
        if not f.startswith(DOC_PREFIX):
            return False
    return True


def run(cmd: list[str], timeout: int = 3600) -> int:
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n$ %s\n" % " ".join(cmd))
        f.flush()
        p = subprocess.run(cmd, cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode


def emit(kind: str, task: str, detail: str) -> None:
    env = dict(os.environ, DEVTOOL_AGENT="GUANYU", DEVTOOL_IDE="DSH-GUANYU", DEVTOOL_MODEL="deepseek-flash")
    subprocess.run([sys.executable, str(HERE / "events.py"), "emit", "--kind", kind,
                    "--task", "MERGE_%s" % task, "--agent", "GUANYU", "--ide", "DSH-GUANYU",
                    "--model", "deepseek-flash", "--detail", detail],
                   cwd=str(REPO), env=env, capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并入口（兼容层；质量闸已内联 devtask merge）")
    ap.add_argument("tid")
    ap.add_argument("--suite", default="fast", help="（兼容参数，已忽略：内联门固定 fast）")
    ap.add_argument("--skip-tests", action="store_true", help="显式绕过（仅回滚/紧急）")
    ap.add_argument("--dry-run", action="store_true", help="只打印改动分类，不合并")
    args = ap.parse_args(argv)

    files = branch_changed_files(args.tid)
    docs_only = is_docs_only(files)
    print("MERGE_GATE task=%s files=%d docs_only=%s（质量闸已内联 devtask merge）"
          % (args.tid, len(files), docs_only))
    if args.dry_run:
        print("MERGE_GATE DRY-RUN：不执行合并（真实闸门在 devtask merge 内）")
        return 0
    if args.skip_tests:
        print("MERGE_GATE 警告：--skip-tests → 等效 DEVTOOL_MERGE_GATE=0（仅回滚/紧急）")
        os.environ["DEVTOOL_MERGE_GATE"] = "0"
    rc = run([sys.executable, str(HERE / "devtask.py"), "merge", args.tid])
    if rc == 0:
        emit("merge_done", args.tid, "merge_gated 兼容入口转发成功")
        print("MERGE_GATE OK：%s 已合并（闸门在 devtask 内）" % args.tid)
        return 0
    print("MERGE_GATE 合并失败（rc=%d）。日志：%s" % (rc, LOG))
    return 1


if __name__ == "__main__":
    sys.exit(main())
