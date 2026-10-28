#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并质量闸（P0 #1，2026-10-28）：merge 前强制跑测试。

背景：devtask.py merge 只跑 5 道静态门，不跑 pytest；本闸在合并前跑
`tools/test_gate.ps1 -Suite fast`（portal + k_stock，三分类基线），
新增失败即拒绝合并。文档/配置类纯变更自动跳过（避免无意义等待）。

用法：
  python tools/merge_gated.py <TID> [--suite fast] [--skip-tests] [--dry-run]
退出码：0=已合并（或 dry-run 通过）；1=测试门未过/合并失败。
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

# 只有这些"纯文档/纯配置"后缀才允许跳过测试
DOC_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".csv", ".html", ".css", ".js")
DOC_PREFIX = ("docs/", "deliverables/", "portal/static/", "agent_platform/skills/")


def run(cmd: list[str], timeout: int = 3600) -> int:
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n$ %s\n" % " ".join(cmd))
        f.flush()
        p = subprocess.run(cmd, cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode


def branch_changed_files(tid: str) -> list[str]:
    """任务分支相对 master 的改动文件列表。"""
    branch = "task/%s" % tid
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false", "merge-base", "master", branch],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    base = (r.stdout or "").strip()
    if not base:
        return []
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false", "diff", "--name-only", base, branch],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [_clean_path(x) for x in (r.stdout or "").splitlines() if x.strip()]

def _clean_path(x: str) -> str:
    x = x.strip().replace("\\", "/")
    if len(x) >= 2 and x[0] == '"' and x[-1] == '"':
        x = x[1:-1]
    return x


def is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    for f in files:
        if not f.endswith(DOC_SUFFIX):
            return False
        if not f.startswith(DOC_PREFIX) and "/" not in f:
            return False
    return True


def emit(kind: str, task: str, detail: str) -> None:
    env = dict(os.environ, DEVTOOL_AGENT="GUANYU", DEVTOOL_IDE="DSH-GUANYU", DEVTOOL_MODEL="deepseek-flash")
    subprocess.run([sys.executable, str(HERE / "events.py"), "emit", "--kind", kind,
                    "--task", "MERGE_%s" % task, "--agent", "GUANYU", "--ide", "DSH-GUANYU",
                    "--model", "deepseek-flash", "--detail", detail],
                   cwd=str(REPO), env=env, capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并质量闸：merge 前跑 fast 测试")
    ap.add_argument("tid")
    ap.add_argument("--suite", default="fast", help="test_gate 套件（默认 fast，也可 all/portal/kstock）")
    ap.add_argument("--skip-tests", action="store_true", help="显式跳过（仅限回滚/紧急，需在提交信息注明）")
    ap.add_argument("--dry-run", action="store_true", help="只跑测试不合并")
    args = ap.parse_args(argv)

    files = branch_changed_files(args.tid)
    docs_only = is_docs_only(files)
    print("MERGE_GATE task=%s files=%d docs_only=%s log=%s" % (args.tid, len(files), docs_only, LOG))
    LOG.write_text("## merge_gated %s task=%s files=%d docs_only=%s\n"
                   % (time.strftime("%Y-%m-%dT%H:%M:%S"), args.tid, len(files), docs_only),
                   encoding="utf-8")

    skip = args.skip_tests or docs_only
    if skip:
        why = "--skip-tests" if args.skip_tests else "纯文档/配置变更"
        print("MERGE_GATE 跳过测试：%s" % why)
    else:
        print("MERGE_GATE 跑测试：test_gate -Suite %s（portal+k_stock，约 4~5 分钟）..." % args.suite)
        rc = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(HERE / "test_gate.ps1"), "-Suite", args.suite])
        if rc != 0:
            tail = LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:]
            print("MERGE_GATE FAIL：测试门未过（rc=%d），拒绝合并。日志：%s" % (rc, LOG))
            for ln in tail:
                print("  |", ln)
            emit("guard_fail", args.tid, "merge_gated 测试门未过 rc=%d" % rc)
            return 1
        print("MERGE_GATE PASS：测试门通过")

    if args.dry_run:
        print("MERGE_GATE DRY-RUN：不执行合并")
        return 0

    rc = run([sys.executable, str(HERE / "devtask.py"), "merge", args.tid])
    if rc != 0:
        print("MERGE_GATE 合并失败（rc=%d）。日志：%s" % (rc, LOG))
        emit("guard_fail", args.tid, "devtask merge 失败 rc=%d" % rc)
        return 1
    emit("merge_done", args.tid, "merge_gated 合并成功（tests=%s）" % ("skipped" if skip else args.suite))
    print("MERGE_GATE OK：%s 已合并" % args.tid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
