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
import urllib.error
import urllib.request
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

# P0-2：写/执行角色门（identity roles 来自 8091；本地无 identity 时给 LOCAL_DEVELOPER）
LOCAL_ROLE = "LOCAL_DEVELOPER"
DEFAULT_WRITE_ROLES = frozenset({
    "LOCAL_DEVELOPER", "DEVELOPER", "EDITOR", "OWNER", "ADMIN",
    "ARCH1", "G1", "G2", "LEAD", "MAINTAINER",
})
ROLES_ENV = "IDE_WRITE_ROLES"


def write_roles(env: Optional[dict] = None) -> set:
    env = os.environ if env is None else env
    roles = set(DEFAULT_WRITE_ROLES)
    for x in str(env.get(ROLES_ENV) or "").split(","):
        x = x.strip().upper()
        if x:
            roles.add(x)
    return roles


def _norm_roles(roles) -> list:
    out = []
    for r in roles or []:
        r = str(r).strip().upper()
        if r and r not in out:
            out.append(r)
    return out


LOCAL_TOKEN_ENV = "IDE_LOCAL_TOKEN"
LOCAL_TOKEN_FILE_ENV = "IDE_LOCAL_TOKEN_FILE"


def local_token() -> str:
    """A1 本地准入 token：未配置返回空串（默认关，不改变现状）。"""
    v = str(os.environ.get(LOCAL_TOKEN_ENV) or "").strip().lstrip("\ufeff")
    if v:
        return v
    path = str(os.environ.get(LOCAL_TOKEN_FILE_ENV) or "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8-sig").strip()
        except OSError:
            return ""
    return ""


_CAP_MOD = None


def _cap():
    global _CAP_MOD
    if _CAP_MOD is not None:
        return _CAP_MOD
    import importlib.util
    p = Path(__file__).resolve().parent / "cap_token.py"
    spec = importlib.util.spec_from_file_location("legion_cap_token", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CAP_MOD = mod
    return mod


def check_cap(headers: dict, scope: str, task: str = ""):
    """Phase A：local_fallback 会话的写/执行要求能力令牌（cap key 未配置则不启用）。"""
    info = session_info(headers)
    if not info or str(info.get("role_source") or "") != "local_fallback":
        return None
    cap = _cap()
    if not cap.enabled():
        return None
    token = str(headers.get("X-ZB-Cap") or "").strip()
    if not token:
        token = str(parse_cookies(headers.get("Cookie") or "").get("zbcap") or "").strip()
    res = cap.verify(token, required_scope=str(scope or ""), task=str(task or ""))
    if res.get("ok"):
        return None
    return err(res.get("error") or "cap_invalid",
               "写/执行需要有效能力令牌（scope=%s task=%s）" % (scope, task),
               res.get("detail"))


def _local_token_denied(headers: dict, info: dict):
    """仅对 local_fallback（本机无门户 identity）会话强制 token；有 identity 的走 roles。"""
    tok = local_token()
    if not tok:
        return None
    if str((info or {}).get("role_source") or "") != "local_fallback":
        return None
    presented = str(headers.get("X-IDE-Token") or "").strip()
    if not presented:
        presented = str(parse_cookies(headers.get("Cookie") or "").get("ide_local_token") or "").strip()
    if presented and secrets.compare_digest(presented, tok):
        return None
    return err("local_token_required", "本机直连已启用本地准入：写/执行需带 X-IDE-Token（或 /rw?token=... 种 cookie）")


def _role_denied(info: dict, required_roles=None):
    if not required_roles:
        return None
    req = {str(r).strip().upper() for r in required_roles if str(r).strip()}
    have = set(_norm_roles((info or {}).get("roles") or []))
    if not (have & req):
        return err("role_forbidden", "当前会话角色无权写/执行（roles=%s）" % ",".join(sorted(have) or ["-"]),
                   {"roles": sorted(have), "required": sorted(req)})
    return None

_SESSION_TOKEN = secrets.token_urlsafe(24)  # 兼容旧单 token 测试/调用
_SESSION_TTL_S = 3600
_SESSIONS: dict[str, dict] = {}
_SESSIONS_LOADED = False
_STATE_ENV = "LEGION_IDE_STATE_DIR"


def rw_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(RW_ENV) or "").strip().lower() in RW_ON_VALUES


def session_token() -> str:
    return _SESSION_TOKEN


def _state_dir() -> Optional[Path]:
    raw = str(os.environ.get(_STATE_ENV) or "").strip()
    return Path(raw) if raw else None


def _sessions_file() -> Optional[Path]:
    d = _state_dir()
    return (d / "sessions.json") if d else None


def _token_key(token: str) -> str:
    """落盘只存 token 的 sha256，不写原始 cookie。"""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _load_sessions() -> None:
    global _SESSIONS_LOADED
    if _SESSIONS_LOADED:
        return
    _SESSIONS_LOADED = True
    p = _sessions_file()
    if not p or not p.is_file():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for key, rec in (data.get("sessions") or {}).items():
            if isinstance(rec, dict) and rec.get("owner"):
                _SESSIONS[str(key)] = {
                    "owner": str(rec["owner"]),
                    "roles": list(rec.get("roles") or []),
                    "created": float(rec.get("created") or 0),
                }
    except (OSError, ValueError, TypeError):
        return


def _save_sessions() -> None:
    p = _sessions_file()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"sessions": _SESSIONS}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _prune_sessions(now: float | None = None) -> None:
    now = time.time() if now is None else now
    stale = [k for k, v in _SESSIONS.items()
             if now - float(v.get("created") or 0) > _SESSION_TTL_S]
    for tok in stale:
        _SESSIONS.pop(tok, None)
    if stale:
        _save_sessions()


def auth_identity(headers: dict) -> dict:
    """从 Phase A auth cookie 解析 sub + identity roles。"""
    cookie = parse_cookies(headers.get("Cookie") or "")
    raw = cookie.get("zb_session") or ""
    if not raw:
        return {}
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8091/api/bff/identity/me",
            headers={"Cookie": "zb_session=" + raw},
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        sess = data.get("session") or data
        ic = sess.get("identity_context") or data.get("identity_context") or {}
        roles = sess.get("roles") or ic.get("roles") or []
        return {
            "sub": str(sess.get("sub") or "").strip(),
            "roles": [str(r) for r in roles if r],
        }
    except (OSError, ValueError, urllib.error.URLError):
        return {}


def auth_owner(headers: dict) -> str:
    return str(auth_identity(headers).get("sub") or "")


def new_session(owner: str = "", headers: dict | None = None) -> tuple[str, str]:
    """下发独立会话：token 只进 HttpOnly cookie，owner 绑定登录 sub。"""
    _load_sessions()
    _prune_sessions()
    token = secrets.token_urlsafe(24)
    identity = auth_identity(headers) if headers and not owner else {}
    owner = str(owner or identity.get("sub") or "").strip()
    roles = _norm_roles(identity.get("roles") or [])
    if identity and not roles:
        role_source = "identity_no_roles"
    elif identity:
        role_source = "identity"
    else:
        # 本机直接 /rw（无门户 identity）：显式本地开发者角色，仍受同源+cookie约束
        roles = [LOCAL_ROLE]
        role_source = "local_fallback"
    owner = owner or ("lde-" + token[:10])
    _SESSIONS[_token_key(token)] = {
        "owner": owner, "roles": roles, "role_source": role_source,
        "created": time.time(),
    }
    _save_sessions()
    return token, owner


def session_info(headers: dict) -> Optional[dict]:
    _load_sessions()
    cookie = parse_cookies(headers.get("Cookie") or "")
    token = cookie.get("ide_session") or ""
    key = _token_key(token)
    info = _SESSIONS.get(key)
    if not info:
        return None
    if time.time() - float(info.get("created") or 0) > _SESSION_TTL_S:
        _SESSIONS.pop(key, None)
        _save_sessions()
        return None
    return {"token": token, "roles": list(info.get("roles") or []), **info}


def session_owner(headers: dict) -> str:
    info = session_info(headers)
    return str(info.get("owner") or "") if info else ""


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


def check_access(headers: dict, token=None, required_roles=None) -> Optional[dict]:
    """同源 + 服务端会话校验；required_roles 非空时强制角色门。"""
    origin = str(headers.get("Origin") or "").strip()
    if origin:
        m = re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", origin)
        if not m:
            return err(
                "bad_origin", "只接受本机同源请求（Origin 非 127.0.0.1/localhost）"
            )
    cookie = parse_cookies(headers.get("Cookie") or "")
    if token is not None:
        if cookie.get("ide_session") != token:
            return err("no_session", "缺少有效 IDE 会话（请从 /rw 页面进入）")
        info = _SESSIONS.get(_token_key(token)) or {}
        denied = _role_denied(info, required_roles)
        return denied or _local_token_denied(headers, info)
    info = session_info(headers)
    if not info:
        return err("no_session", "缺少有效 IDE 会话（请从 /rw 页面进入）")
    denied = _role_denied(info, required_roles)
    return denied or _local_token_denied(headers, info)


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
