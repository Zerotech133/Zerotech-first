# -*- coding: utf-8 -*-
"""task_ledger_reconcile.py — 任务账本对账工具（W2，2026-10-31，工单 K-ORG-20261031-LEDGERRECON）。

把「open 单里哪些分支已合并、哪些真在制、哪些无 worktree」变成可复跑工具。

只读（默认）：
  · 读主工作树 `.worktrees/tasks.json` 的 doing/review 单；
  · 对每单：branch（缺省 ``task/<tid>``）是否存在（git rev-parse）、是否已并入 HEAD
    （git merge-base --is-ancestor）、worktree 路径是否存在；
  · 分类：merged / unmerged / no_branch / no_worktree（tasks.json 坏 → bad_json）；
  · 输出 JSON 或人类摘要；exit 0 = 无不一致，1 = 有需处置。

``--apply``（默认 dry-run，仅显式传 --apply 才写）：
  · 只关闭「分类=merged 且 status=doing 且 owner ∈ --owners 白名单（默认 DSH-Lead）」的单；
  · 写前备份 `tasks.json.bak-<ts>`，CAS 校验（加载后文件又被改 → 放弃，不覆盖并发写），
    原子替换（tmp + fsync + os.replace，格式同 devtask _save：ensure_ascii=False indent=1）；
  · 绝不关闭 unmerged/非白名单；不动 review、其他 owner、worktree、git 历史。
  · ⚠ merged = 「分支是 HEAD 祖先」；刚建单、尚无独有提交的分支也会命中（无可合并提交），
    --apply 前必须人工核对 plan（尤其本工具工单自身分支），不盲目批量关。

用法：
  python tools/task_ledger_reconcile.py                       # 人类摘要（只读）
  python tools/task_ledger_reconcile.py --json                # 机器可读（只读）
  python tools/task_ledger_reconcile.py --apply               # 关闭白名单 merged doing 单
  python tools/task_ledger_reconcile.py --apply --owners DSH-Lead,ZHAOYUN
  python tools/task_ledger_reconcile.py --tasks <path> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:  # 统一主工作树根（linked worktree 回落主仓）
    from _repo_paths import repo_root  # noqa: E402
except Exception:  # pragma: no cover - 极端环境下退化为脚本相对根
    def repo_root(start=None):  # type: ignore
        return Path(start or __file__).resolve().parents[1]

REPO = Path(repo_root(Path(__file__)))
WT_DIR = REPO / ".worktrees"
DEFAULT_TASKS = WT_DIR / "tasks.json"
DEFAULT_OWNERS = ("DSH-Lead",)
STATUS_IN_SCOPE = ("doing", "review")
CLASSES = ("merged", "unmerged", "no_branch", "no_worktree")
NEEDS_ACTION_CLASSES = ("merged", "no_branch", "no_worktree")
SCHEMA = "task_ledger_reconcile/v1"


# ---------------------------------------------------------------- 基础

def _default_runner(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout or "", r.stderr or ""


def _git(repo, *args, runner=None):
    """→ (rc, stdout, stderr)；runner 可注入（测试用 fake git）。"""
    run = runner or _default_runner
    return run(["git", "-C", str(repo), *args])


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _load_tasks(path: Path):
    """→ (tasks dict|None, error str|None)。"""
    if not path.is_file():
        return None, "tasks.json 不存在：%s" % path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "tasks.json 解析失败：%s: %s" % (type(exc).__name__, exc)
    if not isinstance(data, dict):
        return None, "tasks.json 顶层不是对象（实为 %s）" % type(data).__name__
    return data, None


def _worktree_exists(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if not p.is_dir():
        return False
    return (p / ".git").exists() or (p / ".git").is_dir()


# ---------------------------------------------------------------- 分类

def classify_entry(tid: str, meta: dict, repo: Path, runner=None) -> dict:
    """单张 doing/review 单的只读分类（不写任何东西）。"""
    meta = meta if isinstance(meta, dict) else {}
    status = str(meta.get("status") or "")
    owner = str(meta.get("owner") or "")
    branch = str(meta.get("branch") or ("task/%s" % tid)).strip()
    wt_raw = str(meta.get("worktree") or "").strip()
    wt_path = wt_raw or str(Path(repo) / ".worktrees" / tid)
    item = {
        "tid": tid, "status": status, "owner": owner, "branch": branch,
        "worktree": wt_path, "worktree_exists": _worktree_exists(wt_path),
        "merged": None, "classification": "", "detail": "",
    }
    rc, _out, err = _git(repo, "rev-parse", "--verify", "--quiet",
                         "refs/heads/%s" % branch, runner=runner)
    if rc != 0:
        item["classification"] = "no_branch"
        item["detail"] = "分支不存在（%s）" % (err.strip()[:120] or "rev-parse rc=%d" % rc)
        return item
    rc, _out, err = _git(repo, "merge-base", "--is-ancestor",
                         "refs/heads/%s" % branch, "HEAD", runner=runner)
    if rc == 0:
        item["merged"] = True
        item["classification"] = "merged"
        item["detail"] = "分支已并入 HEAD（可安全关闭）"
        return item
    item["merged"] = False
    if rc != 1:
        item["classification"] = "unmerged"
        item["detail"] = "merge-base 异常（rc=%d，保守不关闭）：%s" % (rc, err.strip()[:120])
        return item
    if not item["worktree_exists"]:
        item["classification"] = "no_worktree"
        item["detail"] = "分支未合并且 worktree 不存在：%s" % wt_path
        return item
    item["classification"] = "unmerged"
    item["detail"] = "真在制（分支未合并、worktree 存在）"
    return item


def reconcile(tasks: dict, repo: Path, runner=None) -> dict:
    """→ 对账结果 dict（只读）。"""
    counts = {c: 0 for c in CLASSES}
    by_status = {s: 0 for s in STATUS_IN_SCOPE}
    items = []
    for tid, meta in tasks.items():
        if not isinstance(meta, dict):
            continue
        status = str(meta.get("status") or "")
        if status not in STATUS_IN_SCOPE:
            continue
        by_status[status] += 1
        item = classify_entry(str(tid), meta, repo, runner=runner)
        counts[item["classification"]] += 1
        items.append(item)
    items.sort(key=lambda x: (x["classification"], x["status"], x["tid"]))
    needs = [it for it in items if it["classification"] in NEEDS_ACTION_CLASSES]
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tasks_path": str(DEFAULT_TASKS),
        "repo": str(repo),
        "scanned_status": list(STATUS_IN_SCOPE),
        "by_status": by_status,
        "counts": counts,
        "needs_action": len(needs),
        "items": items,
    }


# ---------------------------------------------------------------- apply

def apply_merged_doing(result: dict, tasks: dict, tasks_path: Path,
                       owners, backup_dir: Path | None = None,
                       runner=None) -> dict:
    """关闭「merged + doing + owner 白名单」单；备份 + CAS + 原子写。绝不关闭其它单。"""
    whitelist = {str(o).strip() for o in owners if str(o).strip()}
    applicable = [
        it for it in result.get("items") or []
        if it["classification"] == "merged" and it["status"] == "doing"
        and it["owner"] in whitelist
    ]
    out = {"dry_run": False, "owners": sorted(whitelist),
           "applicable": [it["tid"] for it in applicable],
           "applied": [], "refused": [], "backup": "", "error": None}
    if not applicable:
        out["dry_run"] = True
        out["detail"] = "无可安全关闭单（merged+doing+白名单）"
        return out
    before_sha = _sha256_file(tasks_path)
    for it in result.get("items") or []:
        if it["classification"] == "merged" and it["status"] == "doing" \
                and it["owner"] not in whitelist:
            out["refused"].append("%s（owner=%s 不在白名单）" % (it["tid"], it["owner"]))
    # CAS：加载后文件又被并发写 → 放弃（不覆盖他人改动）
    if _sha256_file(tasks_path) != before_sha:
        out["error"] = "tasks.json 在加载后被并发修改，放弃写入（不覆盖他人改动）"
        return out
    backup = tasks_path.with_name(tasks_path.name + ".bak-" +
                                  time.strftime("%Y%m%d_%H%M%S"))
    try:
        shutil.copy2(tasks_path, backup)
    except OSError as exc:
        out["error"] = "备份失败：%s" % exc
        return out
    out["backup"] = str(backup)
    ts = time.strftime("%F %T")
    updated = dict(tasks)
    for it in applicable:
        meta = dict(updated.get(it["tid"]) or {})
        meta["status"] = "done"
        meta["merged"] = meta.get("merged") or ts
        note = str(meta.get("note") or "")
        add = ("%s 账本对账自动关闭（branch %s 已并入 HEAD；owner 白名单）"
               % (ts, it["branch"]))
        meta["note"] = (note + "；" + add).strip("；") if note else add
        updated[it["tid"]] = meta
        out["applied"].append(it["tid"])
    # 备份后再做一次 CAS 终检（备份与写之间仍可能被并发改）
    if _sha256_file(tasks_path) != before_sha:
        out["error"] = "tasks.json 在备份后被并发修改，放弃写入（备份已留证）"
        out["applied"] = []
        return out
    try:
        fd, tmp = tempfile.mkstemp(prefix="tasks.ledger.", suffix=".tmp",
                                   dir=str(tasks_path.parent))
    except OSError as exc:
        out["error"] = "临时文件创建失败：%s" % exc
        return out
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(updated, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, tasks_path)
    except OSError as exc:
        out["error"] = "原子写失败：%s" % exc
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return out
    return out


# ---------------------------------------------------------------- 渲染/CLI

def render_human(result: dict) -> str:
    c = result["counts"]
    lines = [
        "任务账本对账（只读）：%s" % result["tasks_path"],
        "  doing=%d review=%d → merged=%d unmerged=%d no_branch=%d no_worktree=%d"
        % (result["by_status"].get("doing", 0), result["by_status"].get("review", 0),
           c["merged"], c["unmerged"], c["no_branch"], c["no_worktree"]),
        "  需处置 %d 单（merged/no_branch/no_worktree）" % result["needs_action"],
    ]
    for it in result["items"]:
        if it["classification"] == "unmerged":
            continue
        lines.append("  [%s] %s (%s, owner=%s)：%s"
                     % (it["classification"], it["tid"], it["status"],
                        it["owner"] or "?", it["detail"]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="任务账本对账（默认只读；--apply 才写且仅白名单 merged doing）")
    ap.add_argument("--tasks", default=str(DEFAULT_TASKS),
                    help="tasks.json 路径（默认主工作树 .worktrees/tasks.json）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--apply", action="store_true",
                    help="关闭 merged+doing+owner 白名单单（默认 dry-run，不写）")
    ap.add_argument("--owners", default=",".join(DEFAULT_OWNERS),
                    help="apply 白名单 owner，逗号分隔（默认 DSH-Lead）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    tasks_path = Path(args.tasks)
    tasks, err = _load_tasks(tasks_path)
    if err:
        payload = {"schema": SCHEMA, "ok": False, "error": err,
                   "tasks_path": str(tasks_path), "classification": "bad_json"}
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 1
    result = reconcile(tasks, REPO)
    result["tasks_path"] = str(tasks_path)
    owners = [x for x in str(args.owners or "").split(",") if x.strip()]
    if args.apply:
        applied = apply_merged_doing(result, tasks, tasks_path, owners)
        result["apply"] = applied
        result["needs_action_after"] = max(
            0, result["needs_action"] - len(applied.get("applied") or []))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(render_human(result))
            print("  [--apply] 白名单=%s 可关闭=%d 已关闭=%d 拒绝=%d 备份=%s"
                  % (",".join(owners), len(applied["applicable"]),
                     len(applied["applied"]), len(applied["refused"]),
                     applied["backup"] or "-"))
            if applied.get("error"):
                print("  [--apply] 失败：%s" % applied["error"])
        if applied.get("error"):
            return 1
        return 0 if result["needs_action_after"] == 0 else 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(render_human(result))
    return 0 if result["needs_action"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
