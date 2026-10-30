# -*- coding: utf-8 -*-
"""OH 工单闭环：worktree 自动准备 / diff / 确认提交。

安全边界：
- 只接受 doing/review 工单；
- worktree 固定在 ASCII junction 根 C:\\legion-worktrees；
- 不自动 push，不自动 merge；
- 提交只在 task/<TID> 分支发生。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
WORKTREE_ROOT = Path(r"C:\legion-worktrees")
TASKS_FILE = REPO / ".worktrees" / "tasks.json"
TASK_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,80}$")
ALLOWED_STATUS = {"doing", "review"}


def _run(args: list[str], cwd: Path | None = None, timeout: int = 60) -> dict:
    try:
        p = subprocess.run(
            args, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return {"ok": p.returncode == 0, "rc": p.returncode,
                "out": (p.stdout or "").strip(), "err": (p.stderr or "").strip()}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "rc": -1, "out": "",
                "err": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _tasks() -> dict:
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _status(task: str) -> str:
    row = _tasks().get(task)
    return str(row.get("status") or "") if isinstance(row, dict) else ""


def list_tasks() -> list[dict]:
    out = []
    for tid, row in sorted(_tasks().items()):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        if status not in ALLOWED_STATUS:
            continue
        path = WORKTREE_ROOT / tid
        out.append({"task": tid, "status": status,
                    "title": str(row.get("title") or "")[:120],
                    "worktree": str(path), "exists": path.is_dir()})
    return out


def _check_task(task: str) -> tuple[Path | None, dict | None]:
    task = str(task or "").strip()
    if not TASK_RE.match(task):
        return None, {"ok": False, "error": "bad_task_id"}
    status = _status(task)
    if status not in ALLOWED_STATUS:
        return None, {"ok": False, "error": "task_not_eligible",
                      "detail": "status=%s" % (status or "missing")}
    return WORKTREE_ROOT / task, None


def ensure_worktree(task: str) -> dict:
    """存在则返回；不存在则从 task/<TID> 分支创建，分支不存在时从 master 建。"""
    path, error = _check_task(task)
    if error:
        return error
    if path.is_dir():
        return {"ok": True, "task": task, "worktree": str(path), "created": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = "task/" + task
    has_branch = _run(["git", "-C", str(REPO), "rev-parse", "--verify",
                       "refs/heads/" + branch])["ok"]
    if has_branch:
        r = _run(["git", "-C", str(REPO), "worktree", "add", str(path), branch], timeout=120)
    else:
        r = _run(["git", "-C", str(REPO), "worktree", "add", "-b", branch,
                  str(path), "master"], timeout=120)
    if not r["ok"]:
        return {"ok": False, "error": "worktree_create_failed",
                "detail": (r["err"] or r["out"])[:500]}
    return {"ok": True, "task": task, "worktree": str(path), "created": True,
            "branch": branch}


def diff(task: str, max_chars: int = 60000) -> dict:
    ready = ensure_worktree(task)
    if not ready.get("ok"):
        return ready
    wt = Path(ready["worktree"])
    st = _run(["git", "-C", str(wt), "status", "--porcelain"])
    stat = _run(["git", "-C", str(wt), "diff", "--stat"])
    body = _run(["git", "-C", str(wt), "diff", "--no-ext-diff", "--unified=3"])
    untracked = [line[3:] for line in st["out"].splitlines()
                 if line.startswith("?? ")]
    files = [line[3:] for line in st["out"].splitlines() if line.strip()]
    text = body["out"]
    # untracked 不在 git diff 里；把新文件内容作为 synthetic diff 展示。
    for rel in untracked[:20]:
        p = wt / rel
        try:
            if not p.is_file() or p.stat().st_size > 200_000:
                continue
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        text += "\n=== new file: %s ===\n" % rel
        lines = content.splitlines()
        text += "\n".join("+" + line for line in lines[:200])
        if len(lines) > 200:
            text += "\n+...（新文件已截断）"
    return {
        "ok": True,
        "task": task,
        "worktree": str(wt),
        "branch": _run(["git", "-C", str(wt), "branch", "--show-current"])["out"],
        "files": files,
        "untracked": untracked,
        "stat": stat["out"][:6000],
        "diff": text[:max_chars],
        "diff_truncated": len(text) > max_chars,
        "has_changes": bool(st["out"].strip()),
    }


def commit(task: str, message: str = "") -> dict:
    ready = ensure_worktree(task)
    if not ready.get("ok"):
        return ready
    wt = Path(ready["worktree"])
    st = _run(["git", "-C", str(wt), "status", "--porcelain"])
    if not st["out"].strip():
        return {"ok": False, "error": "no_changes"}
    msg = str(message or "").strip() or ("%s OH 执行结果" % task)
    msg = msg[:200]
    add = _run(["git", "-C", str(wt), "add", "-A", "--", "."])
    if not add["ok"]:
        return {"ok": False, "error": "git_add_failed",
                "detail": add["err"][:400]}
    r = _run(["git", "-C", str(wt), "commit", "-m", msg], timeout=120)
    if not r["ok"]:
        return {"ok": False, "error": "git_commit_failed",
                "detail": (r["err"] or r["out"])[:500]}
    sha = _run(["git", "-C", str(wt), "rev-parse", "--short", "HEAD"])["out"]
    return {"ok": True, "task": task, "commit": sha, "message": msg,
            "worktree": str(wt)}


# ── 工单执行锁（OH 并发保护；锁文件放 worktree 根外，避免被 git add）──
LOCK_DIR = WORKTREE_ROOT / ".oh-locks"
LOCK_TTL_S = 30 * 60


def _lock_path(task: str) -> Path:
    return LOCK_DIR / (task + ".lock")


def _lock_info(task: str) -> dict:
    try:
        data = json.loads(_lock_path(task).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"locked": False}
    ts = float(data.get("ts") or 0)
    age = time.time() - ts
    return {"locked": age <= LOCK_TTL_S, "stale": age > LOCK_TTL_S,
            "owner": str(data.get("owner") or ""), "age_s": age}


def acquire_oh_lock(task: str, owner: str) -> dict:
    if not TASK_RE.match(str(task or "")):
        return {"ok": False, "error": "bad_task_id"}
    owner = str(owner or "").strip()
    if not owner:
        return {"ok": False, "error": "owner_required"}
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = _lock_path(task)
    for _ in range(3):
        info = _lock_info(task)
        if info.get("locked"):
            if info.get("owner") == owner:
                return {"ok": True, "lock": info, "reused": True}
            return {"ok": False, "error": "oh_lock_busy",
                    "detail": "owner=%s age=%.0fs" % (info.get("owner"), info.get("age_s") or 0)}
        rec = {"owner": owner, "pid": os.getpid(), "ts": time.time(),
               "task": task}
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # 另一请求刚建锁；若有效则拒绝，否则清掉过期锁重试
            race = _lock_info(task)
            if race.get("locked"):
                return {"ok": False, "error": "oh_lock_busy",
                        "detail": "owner=%s" % race.get("owner")}
            try:
                path.unlink()
            except OSError:
                pass
            continue
        except OSError as exc:
            return {"ok": False, "error": "oh_lock_io",
                    "detail": type(exc).__name__}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        return {"ok": True, "lock": rec}
    return {"ok": False, "error": "oh_lock_race"}


def release_oh_lock(task: str, owner: str) -> dict:
    path = _lock_path(task)
    info = _lock_info(task)
    if not info.get("locked"):
        return {"ok": True, "released": False}
    if info.get("owner") != owner:
        return {"ok": False, "error": "oh_lock_owner_mismatch"}
    try:
        path.unlink()
        return {"ok": True, "released": True}
    except OSError as exc:
        return {"ok": False, "error": "oh_lock_io",
                "detail": type(exc).__name__}


def oh_lock_info(task: str) -> dict:
    return _lock_info(task)
