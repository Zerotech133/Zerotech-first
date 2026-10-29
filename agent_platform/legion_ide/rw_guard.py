# -*- coding: utf-8 -*-
"""IDE 可写/可执行 · 作用域 / 工单锁 / 会话 / 审计（R0 骨架，D1/D3/D4=A）。

约束（设计单 v1）：
  · 只允许 `task/<TID>` worktree 内的受控目录（agent_platform/ops/docs/tools/portal）；
  · 禁 master、禁 .git/_vault/node_modules 等；禁密钥/二进制后缀；
  · 写/执行前必须持有工单级排他锁（.ide.lock，15min TTL）；DSH 会话遵守 advisory；
  · 所有写/执行落 dev_events（code_action / code_observation）；
  · 默认 `IDE_RW=off`，开启后仍需同源 + 会话 cookie。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional
import logging

RW_ENV = "IDE_RW"
RW_ON_VALUES = ("1", "true", "yes", "on")
LOCK_TTL_S = 15 * 60
MAX_FILE_BYTES = 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024

ALLOWED_TOP = ("agent_platform", "ops", "docs", "tools", "portal")
BLOCKED_PARTS = {
    ".git",
    "_vault",
    "node_modules",
    "__pycache__",
    ".worktrees",
    ".venv",
    "venv",
    ".pytest_cache",
}
BLOCKED_SUFFIX = {
    ".exe",
    ".dll",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".sys",
    ".bin",
    ".so",
    ".dylib",
}
TASK_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,80}$")

_SESSION_TOKEN = secrets.token_urlsafe(24)


def rw_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(RW_ENV) or "").strip().lower() in RW_ON_VALUES


def session_token() -> str:
    return _SESSION_TOKEN


def err(code: str, message: str, detail=None) -> dict:
    out = {"ok": False, "error": code, "message": message}
    if detail is not None:
        out["detail"] = detail
    return out


def parse_cookies(header: str) -> dict:
    out = {}
    for part in str(header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check_access(headers: dict, token=None) -> Optional[dict]:
    """同源 + 会话 cookie 校验；返回 None=通过，否则错误 dict。"""
    token = token or _SESSION_TOKEN
    origin = str(headers.get("Origin") or "").strip()
    if origin:
        m = re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", origin)
        if not m:
            return err(
                "bad_origin", "只接受本机同源请求（Origin 非 127.0.0.1/localhost）"
            )
    cookie = parse_cookies(headers.get("Cookie") or "")
    if cookie.get("ide_session") != token:
        return err("no_session", "缺少有效 IDE 会话（请从 /rw 页面进入）")
    return None


def _tasks_file(repo: Path) -> Path:
    return Path(repo) / ".worktrees" / "tasks.json"


def _load_tasks(repo: Path) -> dict:
    try:
        data = json.loads(_tasks_file(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def task_status(repo: Path, task: str) -> Optional[str]:
    row = _load_tasks(repo).get(task)
    if isinstance(row, dict):
        return str(row.get("status") or "")
    return None


def list_scope(repo: Path) -> list:
    """可写范围：仅 doing/review 的 task worktree（目录存在）。"""
    repo = Path(repo)
    root = repo / ".worktrees"
    out = []
    if not root.is_dir():
        return out
    tasks = _load_tasks(repo)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not TASK_RE.match(d.name):
            continue
        row = tasks.get(d.name) if isinstance(tasks.get(d.name), dict) else {}
        status = str(row.get("status") or "unknown")
        if status not in ("doing", "review"):
            continue
        out.append(
            {
                "task": d.name,
                "status": status,
                "path": str(d),
                "branch": str(row.get("branch") or ""),
                "owner": str(row.get("owner") or ""),
            }
        )
    return out


def resolve_worktree(repo: Path, task: str):
    """返回 (worktree_path, None) 或 (None, error)。"""
    task = str(task or "").strip()
    if not TASK_RE.match(task):
        return None, err("bad_task", "非法工单号（仅允许字母/数字/_.-，3-80 位）")
    wt = Path(repo) / ".worktrees" / task
    if not wt.is_dir():
        return None, err("worktree_missing", "worktree 不存在：%s" % task)
    status = task_status(repo, task)
    if status not in ("doing", "review"):
        return None, err(
            "task_not_active",
            "工单状态 %r 不允许写入（需 doing/review）" % (status or "unknown"),
        )
    return wt, None


def safe_path(worktree: Path, rel: str):
    """作用域内路径解析：返回 (path, None) 或 (None, error)。"""
    rel = str(rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None, err("bad_path", "必须使用 worktree 内相对路径")
    wt = Path(worktree).resolve()
    cand = (wt / rel).resolve()
    if not cand.is_relative_to(wt):
        return None, err("escape", "路径越出 worktree：%s" % rel)
    parts = cand.relative_to(wt).parts
    if len(parts) < 2 or parts[0] not in ALLOWED_TOP:
        return None, err("scope_denied", "只允许写受控目录：%s" % "/".join(ALLOWED_TOP))
    low = [p.lower() for p in parts]
    if any(p in BLOCKED_PARTS for p in low):
        return None, err("blocked_path", "路径含禁止目录：%s" % rel)
    if cand.suffix.lower() in BLOCKED_SUFFIX:
        return None, err("blocked_type", "禁止写入该类型文件：%s" % cand.suffix)
    if cand.is_dir():
        return None, err("is_dir", "目标是目录：%s" % rel)
    return cand, None


def _lock_path(worktree: Path) -> Path:
    return Path(worktree) / ".ide.lock"


def lock_info(worktree: Path) -> dict:
    """读锁状态；过期锁视为空闲。"""
    p = _lock_path(worktree)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"locked": False}
    age = time.time() - float(data.get("ts") or 0)
    if age > LOCK_TTL_S:
        return {"locked": False, "stale": True, "owner": data.get("owner")}
    data["locked"] = True
    data["age_s"] = round(age, 1)
    return data


def acquire_lock(worktree: Path, owner: str) -> dict:
    """原子抢锁（O_CREAT|O_EXCL）；只清理明确的过期锁，避免 read-then-write 竞态。"""
    worktree = Path(worktree)
    owner = str(owner or "").strip()
    if not owner:
        return err("owner_required", "缺少 owner 标识")
    lock = _lock_path(worktree)
    for _attempt in range(3):
        info = lock_info(worktree)
        if info.get("locked"):
            if info.get("owner") == owner:
                return {"ok": True, "lock": info, "reused": True}
            return err(
                "locked",
                "工单已被占用（%s，%.0fs 前）"
                % (info.get("owner"), info.get("age_s") or 0),
                info,
            )
        rec = {
            "owner": owner,
            "pid": os.getpid(),
            "ts": time.time(),
            "expires_in_s": LOCK_TTL_S,
        }
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # 另一端刚建锁：重新判活，避免误删有效锁
            race = lock_info(worktree)
            if race.get("locked"):
                if race.get("owner") == owner:
                    return {"ok": True, "lock": race, "reused": True}
                return err("locked", "工单已被占用（%s）" % race.get("owner"), race)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                return err("lock_io", "锁文件不可清理：%s" % type(exc).__name__)
            continue
        except OSError as exc:
            return err("lock_io", "锁文件不可创建：%s" % type(exc).__name__)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            try:
                lock.unlink()
            except OSError:
                pass
            return err("lock_io", "锁写入失败：%s" % type(exc).__name__)
        return {"ok": True, "lock": rec}
    return err("lock_race", "抢锁冲突，请重试")


def release_lock(worktree: Path, owner: str) -> dict:
    worktree = Path(worktree)
    info = lock_info(worktree)
    if not info.get("locked"):
        return {"ok": True, "released": False}
    if info.get("owner") != owner:
        return err("lock_owner_mismatch", "锁不属于当前会话（%s）" % info.get("owner"))
    try:
        _lock_path(worktree).unlink()
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_ide/rw_guard.py", exc)
    return {"ok": True, "released": True}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(repo: Path, kind: str, task: str, agent: str, detail: str) -> dict:
    """落 dev_events（复用 tools/events.py）；失败降级追加本地 jsonl（不挡业务）。"""
    try:
        tools = str(Path(repo) / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events as E  # noqa: WPS433

        res = E.emit(
            kind=kind,
            task=task,
            agent=agent or "IDE",
            detail=detail[:300],
            ide="军团独立IDE",
            model="",
        )
        if isinstance(res, dict) and res.get("ok"):
            return {"ok": True, "via": "events"}
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_ide/rw_guard.py", exc)
    try:
        fb = Path(repo) / "agent_platform" / "data" / "legion_ide_audit.jsonl"
        fb.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "task": task,
            "agent": agent,
            "detail": detail[:300],
        }
        with fb.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "via": "fallback"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:120]}
