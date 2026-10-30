# -*- coding: utf-8 -*-
"""方案 A：OpenHands Agent Server 适配层（原生会话 API）。

- 每个任务可指定 `.worktrees/<TID>` 作为 workspace
- 原生 conversation / run / events / final_response
- 模型按 IDE 请求映射到 OpenHands LLM profile
- 只通过 loopback HTTP 通信；session key 不写进仓库
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


# 方案 A 第二阶段：默认走 Docker sandbox（Linux 容器内执行工具，绕开 Windows
# FileEditor 挂起；worktree 通过 C:\legion-worktrees -> /workspaces 绑定挂载）。
def _resolve_sandbox_base() -> str:
    explicit = str(os.environ.get("OPENHANDS_SANDBOX_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    # 默认 loopback（旧）；若不可达，尝试 Docker 容器 IP（B2 去 host publish）
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
             SANDBOX_CONTAINER],
            capture_output=True, text=True, timeout=5,
        )
        ip = (r.stdout or "").strip()
        if r.returncode == 0 and ip:
            return "http://%s:8000" % ip
    except (OSError, subprocess.SubprocessError):
        pass
    return "http://127.0.0.1:8771"


BASE_URL = _resolve_sandbox_base()
HOST_BASE_URL = os.environ.get("OPENHANDS_HOST_URL", "http://127.0.0.1:8770").rstrip("/")
KEY_FILE = Path(os.environ.get("OPENHANDS_KEY_FILE", r"C:\项目\legion-openhands\.session_key"))
# 容器内访问宿主机网关；容器外测试可覆盖为 127.0.0.1。
LEGION_GATEWAY = os.environ.get(
    "OPENHANDS_GATEWAY_URL", "http://host.docker.internal:8796/v1"
)
REPO_ROOT = Path(os.environ.get("OPENHANDS_REPO", r"C:\项目\老田的AI助手")).resolve()
WORKTREE_ROOT = Path(os.environ.get("OPENHANDS_WORKTREE_ROOT", r"C:\legion-worktrees"))
CONTAINER_WORKTREE_ROOT = "/workspaces"
DEFAULT_HOST_WORKDIR = Path(r"C:\legion-worktrees\_default")
DEFAULT_CONTAINER_WORKDIR = CONTAINER_WORKTREE_ROOT + "/_default"
LOCAL_ROOTS = (WORKTREE_ROOT, Path(r"C:\legion-oh-worktrees"))
DEFAULT_TIMEOUT = 600
TASK_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,80}$")
SANDBOX_CONTAINER = os.environ.get("OH_SANDBOX_CONTAINER", "legion-openhands-sandbox")
SANDBOX_IMAGE = os.environ.get(
    "OH_SANDBOX_IMAGE", "ghcr.io/openhands/agent-server:latest-python"
)
SANDBOX_PORT = "8771"


def _wait_health(timeout: int = 60, base: str | None = None) -> bool:
    base = (base or BASE_URL).rstrip("/")
    deadline = time.time() + max(5, timeout)
    while time.time() < deadline:
        try:
            with safe_urlopen(base + "/health", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
                if data.get("status") == "ok":
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1)
    return False


def ensure_sandbox(timeout: int = 60) -> dict:
    """自愈：8771 不通时尝试 docker start / docker run，最后等 health。"""
    if _wait_health(2):
        return {"ok": True, "started": False, "container": SANDBOX_CONTAINER}
    key = _key()
    secret = ""
    try:
        secret = Path(r"C:\项目\legion-openhands\.secret_key").read_text(
            encoding="utf-8-sig").strip()
    except OSError:
        pass
    if not key:
        return {"ok": False, "error": "sandbox_key_missing"}
    run = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", SANDBOX_CONTAINER],
        capture_output=True, text=True, timeout=20,
    )
    if run.returncode == 0:
        if run.stdout.strip().lower() != "true":
            subprocess.run(["docker", "start", SANDBOX_CONTAINER],
                           capture_output=True, text=True, timeout=60)
    else:
        args = [
            "docker", "run", "-d", "--name", SANDBOX_CONTAINER,
            "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
            "-p", "127.0.0.1:%s:8000" % SANDBOX_PORT,
            "-v", r"C:\legion-worktrees:/workspaces",
            "-e", "OH_SESSION_API_KEYS_0=" + key,
        ]
        if secret:
            args += ["-e", "OH_SECRET_KEY=" + secret]
        args += [
            "-e", "OH_ENABLE_VNC=0", "-e", "OH_ENABLE_VSCODE=0",
            "-e", "OH_PRELOAD_TOOLS=0", SANDBOX_IMAGE,
        ]
        subprocess.run(args, capture_output=True, text=True, timeout=120)
    if _wait_health(timeout):
        return {"ok": True, "started": True, "container": SANDBOX_CONTAINER}
    return {"ok": False, "error": "sandbox_unhealthy",
            "container": SANDBOX_CONTAINER}


_WATCH_LOCK = threading.Lock()
_WATCH_STARTED = False


def _sandbox_watch_loop() -> None:
    while True:
        try:
            if not _wait_health(2):
                ensure_sandbox(timeout=60)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)


def start_watchdog() -> dict:
    """网关内健康守护：容器挂掉后最多 60s 自动拉起。"""
    global _WATCH_STARTED
    if os.environ.get("OH_SANDBOX_WATCH", "1") == "0":
        return {"ok": False, "watching": False, "reason": "disabled"}
    with _WATCH_LOCK:
        if _WATCH_STARTED:
            return {"ok": True, "watching": True, "started": False}
        t = threading.Thread(target=_sandbox_watch_loop, name="oh-sandbox-watch",
                             daemon=True)
        t.start()
        _WATCH_STARTED = True
        return {"ok": True, "watching": True, "started": True}


def _key() -> str:
    try:
        return KEY_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return os.environ.get("OPENHANDS_SESSION_KEY", "").strip()


def _req(path: str, method: str = "GET", body=None, timeout: int = 30):
    key = _key()
    if not key:
        return None, {"ok": False, "error": "openhands_key_missing"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, method=method,
        headers={
            "Authorization": "Bearer " + key,
            "X-Session-API-Key": key,
            "Content-Type": "application/json",
        },
    )
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            msg = e.read(700).decode("utf-8", "replace")
        except OSError:
            msg = ""
        return None, {"ok": False, "error": "openhands_http_%s" % e.code,
                      "detail": msg[:400]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, {"ok": False, "error": "openhands_unreachable",
                      "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _resolve_worktree(task: str = "", worktree: str = ""):
    """返回 (宿主 worktree, 容器内 working_dir, error)。

    Docker sandbox 只挂载 C:\\legion-worktrees -> /workspaces 与
    C:\\legion-oh-worktrees -> /workspaces-extra。
    """
    host = None
    container = None
    if worktree:
        try:
            host = Path(worktree).expanduser().absolute()
        except OSError:
            return None, None, {"ok": False, "error": "bad_worktree"}
    elif task:
        task = str(task or "").strip()
        if not TASK_RE.match(task):
            return None, None, {"ok": False, "error": "bad_task_id"}
        from legion_command import ide_worktree
        ready = ide_worktree.ensure_worktree(task)
        if not ready.get("ok"):
            return None, None, ready
        host = Path(ready["worktree"]).absolute()
    else:
        host = DEFAULT_HOST_WORKDIR.absolute()

    if host.is_relative_to(WORKTREE_ROOT.absolute()):
        rel = host.relative_to(WORKTREE_ROOT.absolute()).as_posix()
        container = CONTAINER_WORKTREE_ROOT + "/" + rel
    elif host.is_relative_to(Path(r"C:\legion-oh-worktrees").absolute()):
        rel = host.relative_to(Path(r"C:\legion-oh-worktrees").absolute()).as_posix()
        container = "/workspaces-extra/" + rel
    else:
        return None, None, {"ok": False, "error": "worktree_out_of_scope",
                            "detail": str(host)}
    if host.exists() and not host.is_dir():
        return None, None, {"ok": False, "error": "worktree_not_dir"}
    host.mkdir(parents=True, exist_ok=True)
    return host, container, None


_PROVIDER_PREFIXES = (
    "openai/", "anthropic/", "azure/", "ollama/", "google/", "gemini/",
    "bedrock/", "groq/", "mistral/", "cohere/", "together/", "fireworks/",
    "openrouter/",
)


def _llm(model: str) -> dict:
    alias = str(model or "deepseek-flash").strip()
    if alias.startswith("openhands_"):
        alias = alias[len("openhands_"):]
    # 平台模型别名（如 glm/glm-5.3）会被 OpenHands 当作 provider。
    # 对非真 provider 前缀，剥成网关实际模型名（glm-5.3），再走 openai/。
    if "/" in alias and not alias.startswith(_PROVIDER_PREFIXES):
        alias = alias.rsplit("/", 1)[-1]
    if not alias.startswith(_PROVIDER_PREFIXES):
        alias = "openai/" + alias
    return {"model": alias, "api_key": "legion-local", "base_url": LEGION_GATEWAY}


def _conversation(conversation_id: str) -> tuple[dict, dict | None]:
    _r, d = _req("/api/conversations/" + conversation_id, timeout=20)
    if not d or not d.get("id"):
        return {}, d or {"ok": False, "error": "conversation_missing"}
    return d, None


def _start(message: str, container_workdir: str, model: str,
           max_iterations: int = 60) -> tuple[str | None, dict | None]:
    body = {
        "workspace": {"working_dir": str(container_workdir), "kind": "LocalWorkspace"},
        "initial_message": {"role": "user", "content": [{"type": "text", "text": message}]},
        "agent_settings": {"llm": _llm(model)},
        "max_iterations": max(1, min(int(max_iterations or 60), 100)),
        "stuck_detection": True,
        # 所有 shell 命令逐条确认；文件编辑由 status() 自动放行，避免噪音。
        "confirmation_policy": {"kind": "AlwaysConfirm"},
        "security_analyzer": {"kind": "PolicyRailSecurityAnalyzer"},
    }
    _r, d = _req("/api/conversations", method="POST", body=body, timeout=120)
    if not d or not d.get("id"):
        return None, d or {"ok": False, "error": "conversation_create_failed"}
    return str(d["id"]), None


def _send(cid: str, message: str) -> dict | None:
    body = {"role": "user", "content": [{"type": "text", "text": message}], "run": True}
    _r, d = _req("/api/conversations/%s/events" % cid, method="POST", body=body, timeout=60)
    if _r is None:
        return d
    return None


def _run(cid: str) -> dict | None:
    _r, d = _req("/api/conversations/%s/run" % cid, method="POST", timeout=30)
    if _r is None:
        return d
    return None


def _wait(cid: str, timeout: int) -> tuple[dict, dict | None]:
    deadline = time.time() + max(10, int(timeout))
    last = {}
    terminal = {"finished", "error", "stuck", "cancelled", "waiting_for_confirmation"}
    while time.time() < deadline:
        d, error = _conversation(cid)
        if error:
            return {}, error
        last = d
        status = str(d.get("execution_status") or "")
        if status in terminal:
            return d, None
        time.sleep(1.0)
    return last, {"ok": False, "error": "openhands_timeout",
                  "detail": "execution_status=%s" % last.get("execution_status")}


def _final(cid: str) -> str:
    _r, d = _req("/api/conversations/%s/agent_final_response" % cid, timeout=30)
    if isinstance(d, dict):
        return str(d.get("response") or d.get("message") or "")
    return ""


def _summary(e: dict) -> str | None:
    kind = str(e.get("kind") or "")
    source = str(e.get("source") or "")  # noqa: F841 - 既有未使用变量（批3不改行为）
    if kind == "ActionEvent":
        action = e.get("action") or {}
        if not isinstance(action, dict):
            return "动作"
        ak = action.get("kind") or "Action"
        if "command" in action:
            return "%s: %s" % (ak, str(action.get("command"))[:220])
        if "path" in action:
            return "%s: %s" % (ak, str(action.get("path"))[:220])
        return ak
    if kind == "ObservationEvent":
        obs = e.get("observation") or {}
        if isinstance(obs, dict):
            text = obs.get("content") or obs.get("text") or obs.get("output") or ""
            if isinstance(text, list):
                text = " ".join(str(x.get("text") or x) for x in text if isinstance(x, dict))
            return ("观察: " + str(text)[:260]) if text else "观察结果"
        return "观察结果"
    if kind in ("MessageEvent", "AgentErrorEvent"):
        return str(e.get("message") or kind)[:260]
    return None


def events(cid: str, limit: int = 60) -> list[dict]:
    if not cid:
        return []
    _r, d = _req(
        "/api/conversations/%s/events/search?limit=%d" % (cid, max(1, min(int(limit), 200))),
        timeout=30,
    )
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for e in items:
        if not isinstance(e, dict):
            continue
        summary = _summary(e)
        if summary:
            action = e.get("action") if isinstance(e.get("action"), dict) else {}
            out.append({"kind": e.get("kind"), "source": e.get("source"),
                        "summary": summary, "id": e.get("id"),
                        "tool": str(e.get("tool_name") or ""),
                        "action_kind": str(action.get("kind") or ""),
                        "command": str(action.get("command") or "")})
    return out[-limit:]


def health() -> dict:
    watch = start_watchdog()
    _r, data = _req("/health", timeout=5)
    if data.get("status") == "ok":
        return {"ok": True, "base_url": BASE_URL, "mode": "docker-sandbox",
                "worktree_root": str(WORKTREE_ROOT),
                "container_root": CONTAINER_WORKTREE_ROOT,
                "watchdog": watch}
    return {"ok": False, "base_url": BASE_URL, "watchdog": watch,
            **{k: v for k, v in (data or {}).items() if k != "ok"}}


def chat(message: str, conversation_id: str = "", timeout: int = DEFAULT_TIMEOUT,
         task: str = "", worktree: str = "", model: str = "deepseek-flash") -> dict:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "error": "empty_message"}
    sandbox = ensure_sandbox()
    if not sandbox.get("ok"):
        return sandbox
    host_wd, container_wd, error = _resolve_worktree(task=task, worktree=worktree)
    if error:
        return error
    cid = str(conversation_id or "").strip()
    created = False
    if not cid:
        cid, error = _start(message, container_wd, model)
        if error:
            return error
        created = True
    else:
        err = _send(cid, message)
        if err:
            return err
    state_now, _err = _conversation(cid)
    status_now = str(state_now.get("execution_status") or "")
    if status_now not in ("running", "waiting_for_confirmation"):
        err = _run(cid)
        if err:
            return err
    started = time.time()
    state, error = _wait(cid, timeout)
    if error:
        return {**error, "conversation_id": cid, "worktree": str(host_wd),
                "container_worktree": container_wd}
    reply = _final(cid)
    evs = events(cid)
    status = str(state.get("execution_status") or "")
    return {
        "ok": status == "finished",
        "reply": reply or ("（OpenHands 状态：%s）" % status),
        "conversation_id": cid,
        "worktree": str(host_wd),
        "container_worktree": container_wd,
        "created": created,
        "status": status,
        "duration_ms": int((time.time() - started) * 1000),
        "engine": "openhands-sandbox",
        "model": _llm(model)["model"],
        "events": evs,
    }


# ── OHLIVE：异步 start / status / interrupt ─────────────────────────
_RUNS: dict[str, dict] = {}
TERMINAL = {"finished", "error", "stuck", "cancelled", "paused"}

# IDE Session 服务端持久化：跨浏览器 / PWA 共用一份历史。
SESSIONS_FILE = Path(os.environ.get(
    "LEGION_IDE_SESSIONS_FILE",
    str(Path(__file__).resolve().parents[2] / "work" / "legion_ide_sessions.json"),
))
_SESSION_LOCK = threading.Lock()
_SESSION_FILE_LOCK = SESSIONS_FILE.with_suffix(SESSIONS_FILE.suffix + ".lock")
_SESSION_SAVED_AT: dict[str, float] = {}


def _session_file_lock():
    """跨进程文件锁：多 8796 实例/多浏览器并发写 sessions 时防丢行。"""
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(_SESSION_FILE_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    if os.name == "nt":
        import msvcrt
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return fd
            except OSError:
                time.sleep(0.05)
        os.close(fd)
        return None
    return fd


def _session_file_unlock(fd) -> None:
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextmanager
def _session_file_guard():
    fd = _session_file_lock()
    try:
        yield
    finally:
        _session_file_unlock(fd)


def _session_rows() -> list[dict]:
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("sessions") if isinstance(data, dict) else data
    return list(rows) if isinstance(rows, list) else []


def save_session(record: dict) -> None:
    cid = str(record.get("cid") or "").strip()
    if not cid:
        return
    with _SESSION_LOCK, _session_file_guard():
        rows = _session_rows()
        prev = next((r for r in rows if str(r.get("cid")) == cid), {})
        row = dict(prev)
        row.update({k: v for k, v in record.items() if v is not None})
        row["cid"] = cid
        row["updated"] = float(row.get("updated") or time.time())
        if "title" in row:
            row["title"] = str(row["title"])[:300]
        if "reply" in row:
            row["reply"] = str(row["reply"])[:8000]
        row["status"] = str(row.get("status") or "running")[:40]
        rows = [r for r in rows if str(r.get("cid")) != cid]
        rows.insert(0, row)
        try:
            SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SESSIONS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sessions": rows[:50]}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, SESSIONS_FILE)
        except OSError:
            pass
        _SESSION_SAVED_AT[cid] = time.time()


def sessions(limit: int = 30) -> list[dict]:
    with _SESSION_LOCK, _session_file_guard():
        rows = _session_rows()
    try:
        limit = max(1, min(int(limit), 100))
    except ValueError:
        limit = 30
    return rows[:limit]


def delete_session(cid: str) -> bool:
    cid = str(cid or "").strip()
    if not cid:
        return False
    with _SESSION_LOCK, _session_file_guard():
        rows = _session_rows()
        kept = [r for r in rows if str(r.get("cid")) != cid]
        if len(kept) == len(rows):
            return False
        try:
            SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SESSIONS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sessions": kept}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, SESSIONS_FILE)
        except OSError:
            return False
    _SESSION_SAVED_AT.pop(cid, None)
    return True


def _release_run(cid: str) -> None:
    run = _RUNS.pop(cid, None)
    if not run:
        return
    task = run.get("task") or ""
    if task:
        from legion_command import ide_worktree
        ide_worktree.release_oh_lock(task, run.get("owner") or "")


def start(message: str, conversation_id: str = "", task: str = "",
          worktree: str = "", model: str = "deepseek-flash",
          agent: str = "") -> dict:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "error": "empty_message"}
    start_watchdog()
    task = str(task or "").strip()
    owner = "oh-" + uuid.uuid4().hex[:12]
    locked = False
    if task:
        from legion_command import ide_worktree
        lock = ide_worktree.acquire_oh_lock(task, owner)
        if not lock.get("ok"):
            return lock
        locked = True
    try:
        sandbox = ensure_sandbox()
        if not sandbox.get("ok"):
            raise RuntimeError(sandbox.get("error") or "sandbox_unhealthy")
        host_wd, container_wd, error = _resolve_worktree(task=task, worktree=worktree)
        if error:
            raise RuntimeError(error.get("error") or "worktree_error")
        cid = str(conversation_id or "").strip()
        if not cid:
            cid, error = _start(message, container_wd, model)
            if error:
                raise RuntimeError(error.get("error") or "conversation_create_failed")
        else:
            err = _send(cid, message)
            if err:
                raise RuntimeError(err.get("error") or "send_failed")
        state, _ = _conversation(cid)
        if str(state.get("execution_status") or "") not in ("running", "waiting_for_confirmation"):
            err = _run(cid)
            if err:
                raise RuntimeError(err.get("error") or "run_failed")
        _RUNS[cid] = {"task": task, "owner": owner, "host": str(host_wd),
                      "container": container_wd, "started": time.time(),
                      "agent": str(agent or ""), "model": str(model or "")}
        save_session({
            "cid": cid, "task": task, "agent": str(agent or ""),
            "model": str(model or ""), "title": message[:300],
            "status": "running", "reply": "",
        })
        return {"ok": True, "conversation_id": cid, "task": task,
                "worktree": str(host_wd), "container_worktree": container_wd,
                "model": _llm(model)["model"], "status": "running",
                "lock_owner": owner if locked else ""}
    except Exception as e:  # noqa: BLE001
        if locked:
            from legion_command import ide_worktree
            ide_worktree.release_oh_lock(task, owner)
        return {"ok": False, "error": "oh_start_failed",
                "detail": "%s: %s" % (type(e).__name__, str(e)[:300])}


_SAFE_HEADS = {"cat", "ls", "pwd", "head", "tail", "wc", "echo", "which", "type"}
_DANGER_TOKENS = ("rm ", "mv ", "cp ", "chmod", "chown", "curl ", "wget ",
                  "git commit", "git push", "pip install", "npm install",
                  ">", ">>", "del ", "format ", "shutdown", "taskkill")


def _safe_terminal(cmd: str) -> bool:
    s = str(cmd or "").strip().lower()
    if not s or any(tok in s for tok in _DANGER_TOKENS):
        return False
    parts = s.split()
    head = parts[0]
    if head in _SAFE_HEADS:
        return True
    if head == "git" and len(parts) > 1 and parts[1] in (
            "status", "diff", "log", "show", "branch"):
        return True
    return False


def status(cid: str) -> dict:
    cid = str(cid or "").strip()
    if not cid:
        return {"ok": False, "error": "conversation_id_required"}
    state, error = _conversation(cid)
    if error:
        return error
    status_now = str(state.get("execution_status") or "")
    evs = events(cid)
    run = _RUNS.get(cid) or {}
    reply = ""
    pending = ""
    if status_now in TERMINAL:
        reply = _final(cid)
        _release_run(cid)
    elif status_now == "waiting_for_confirmation":
        actions = [e for e in evs if e.get("kind") == "ActionEvent"]
        if actions:
            last = actions[-1]
            tool = str(last.get("tool") or "")
            cmd = str(last.get("command") or "")
            if tool in ("file_editor", "task_tracker") or (
                    tool == "terminal" and _safe_terminal(cmd)):
                # 文件编辑/只读 shell 自动批准；危险 shell 必须人工确认。
                auto = respond(cid, True, "auto approve safe action")
                if auto.get("ok"):
                    status_now = "running"
                pending = "" if auto.get("ok") else str(last.get("summary") or "")
            else:
                pending = str(last.get("summary") or "")
    now = time.time()
    if (status_now in TERMINAL or status_now == "waiting_for_confirmation"
            or now - _SESSION_SAVED_AT.get(cid, 0.0) >= 4.0):
        rec = {"cid": cid, "status": status_now or "running"}
        for key in ("task", "agent", "model"):
            if run.get(key):
                rec[key] = run[key]
        if reply:
            rec["reply"] = reply
        save_session(rec)
    return {"ok": True, "conversation_id": cid, "status": status_now,
            "events": evs, "reply": reply, "pending_action": pending,
            "task": run.get("task") or "",
            "worktree": run.get("host") or "",
            "terminal": status_now in TERMINAL}


def interrupt(cid: str) -> dict:
    cid = str(cid or "").strip()
    if not cid:
        return {"ok": False, "error": "conversation_id_required"}
    _r, d = _req("/api/conversations/%s/interrupt" % cid, method="POST", timeout=30)
    if _r is None:
        return d
    st = status(cid)
    return {"ok": True, "conversation_id": cid,
            "status": st.get("status") or "paused"}


def respond(cid: str, accept: bool, reason: str = "") -> dict:
    cid = str(cid or "").strip()
    if not cid:
        return {"ok": False, "error": "conversation_id_required"}
    body = {"accept": bool(accept),
            "reason": str(reason or ("用户批准" if accept else "用户拒绝"))[:300]}
    _r, d = _req(
        "/api/conversations/%s/events/respond_to_confirmation" % cid,
        method="POST", body=body, timeout=60,
    )
    if _r is None:
        return d
    st = status(cid)
    return {"ok": True, "conversation_id": cid, "accept": bool(accept),
            "status": st.get("status") or "running"}
