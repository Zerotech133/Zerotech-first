#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并入口（兼容层）——质量闸已内联 `devtask.py merge`（P0#1 收尾，2026-10-29 P37）。

现状：
  - `python tools/devtask.py merge <TID>` 已内置质量闸：纯文档/配置变更自动跳过，
    其余跑 `tools/test_gate.ps1 -Suite fast`，新增失败即拒绝；
  - 本脚本保留为**兼容入口/预检器**：`--dry-run` 打印改动分类 + H5 钉扎校验后退出；
    正常调用直接转发 devtask merge（不重复跑测试）。
绕过（回滚/紧急）：`DEVTOOL_MERGE_GATE=0`（须在回单注明"绕过质量闸"）。

2026-10-31 H5/A9（K-ORG-20261031-MERGEGATE，审计 v2）：
  - H5-a **master 钉扎**：待合并分支不得修改 `tools/test_gate.ps1`（否则拒绝），
    `tools/test_baseline.json` 仅允许 ratchet（known/env/flaky 只减不增）；
  - A9 `docs_only` 去掉 `.js`：`portal/static/*.js` 是前端代码，不再免测（html/css 保留）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
LOG = TMP / ("merge_gated_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
# A9（2026-10-31）：.js 是前端代码，不能像文档/样式一样免测；html/css 保留为纯静态资产
DOC_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".csv", ".html", ".css")
DOC_PREFIX = ("docs/", "deliverables/", "portal/static/", "agent_platform/skills/")

# H5-a（2026-10-31）：质量闸脚本/baseline 必须与 master 钉扎一致（分支自证不可信）
GATE_PIN_GATE = "tools/test_gate.ps1"
GATE_PIN_BASELINE = "tools/test_baseline.json"
BASELINE_LISTS = ("known_failures", "env_failures", "flaky")


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


# ---------------------------------------------------------------- H5-a 钉扎

def _git_show(repo, ref: str, rel: str):
    """→ blob bytes 或 None（读取失败/文件不存在）。"""
    r = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (ref, rel)],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def baseline_ratchet_violations(master_payload, branch_payload) -> list[str]:
    """baseline 只允许 ratchet：known/env/flaky 清单只减不增，且不新增 suite 键。"""
    if not isinstance(master_payload, dict) or not isinstance(branch_payload, dict):
        return ["baseline 顶层不是 JSON 对象"]
    bad = []
    for suite, bent in branch_payload.items():
        if str(suite).startswith("_"):
            continue
        if not isinstance(bent, dict):
            bad.append("%s: 条目不是对象" % suite)
            continue
        ment = master_payload.get(suite)
        if not isinstance(ment, dict):
            bad.append("%s: 新增 suite 键（master 无）" % suite)
            continue
        for key in BASELINE_LISTS:
            bset = {str(x) for x in (bent.get(key) or [])}
            mset = {str(x) for x in (ment.get(key) or [])}
            added = sorted(bset - mset)
            if added:
                bad.append("%s.%s 新增 %d 条：%s"
                           % (suite, key, len(added), "；".join(added[:3])))
    return bad


def gate_pin_check(tid: str, repo=None):
    """H5-a：gate/baseline master 钉扎校验 → (ok, detail)。

    - `tools/test_gate.ps1` 必须与 master 完全一致（分支改 gate = 默认拒绝，
      质量闸脚本变更需独立审批后由 Lead 手动合并；分支落后 master 同样拒绝，先 rebase）；
    - `tools/test_baseline.json` 未改则通过；有改则仅允许 ratchet（只减 known）。
    """
    repo = Path(repo) if repo else REPO
    branch = "task/%s" % tid
    master_gate = _git_show(repo, "master", GATE_PIN_GATE)
    branch_gate = _git_show(repo, branch, GATE_PIN_GATE)
    if master_gate is None or branch_gate is None:
        return False, ("H5 钉扎：无法读取 tools/test_gate.ps1（master/分支缺失）"
                       "→ 拒绝（fail-closed）")
    if master_gate != branch_gate:
        return False, ("H5 钉扎：分支修改了 tools/test_gate.ps1（与 master 不一致）"
                       "→ 默认拒绝；质量闸脚本变更需独立审批后由 Lead 手动合并，"
                       "或先 rebase master 再合并")
    master_raw = _git_show(repo, "master", GATE_PIN_BASELINE)
    branch_raw = _git_show(repo, branch, GATE_PIN_BASELINE)
    if master_raw is None or branch_raw is None:
        return False, ("H5 钉扎：无法读取 tools/test_baseline.json（master/分支缺失）"
                       "→ 拒绝（fail-closed）")
    if branch_raw == master_raw:
        return True, "H5 钉扎：test_gate.ps1 / test_baseline.json 与 master 一致"
    try:
        master_payload = json.loads(master_raw.decode("utf-8"))
        branch_payload = json.loads(branch_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return False, "H5 钉扎：baseline JSON 解析失败（%s）→ 拒绝" % exc
    bad = baseline_ratchet_violations(master_payload, branch_payload)
    if bad:
        return False, ("H5 钉扎：baseline 非 ratchet（%s）→ 拒绝："
                       "分支不得新增 known/env/flaky 条目（防止把新失败登记为 known）"
                       % "；".join(bad[:5]))
    return True, "H5 钉扎：baseline 仅 ratchet 减少 known（允许）"


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
    ap.add_argument("--dry-run", action="store_true", help="只打印改动分类/H5 钉扎，不合并")
    args = ap.parse_args(argv)

    files = branch_changed_files(args.tid)
    docs_only = is_docs_only(files)
    print("MERGE_GATE task=%s files=%d docs_only=%s（质量闸已内联 devtask merge）"
          % (args.tid, len(files), docs_only))
    try:
        pin_ok, pin_detail = gate_pin_check(args.tid)
    except Exception as exc:  # noqa: BLE001 —— 钉扎异常按拒绝（fail-visible）
        pin_ok, pin_detail = False, "H5 钉扎校验异常：%r" % exc
    print("MERGE_GATE PIN_GATE task=%s -> %s：%s"
          % (args.tid, "OK" if pin_ok else "REJECT", pin_detail))
    if args.dry_run:
        print("MERGE_GATE DRY-RUN：不执行合并（真实闸门在 devtask merge 内；pin_ok=%s）"
              % pin_ok)
        return 0 if pin_ok else 1
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
