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
import time
import urllib.error
import urllib.request
from pathlib import Path

# 方案 A 第二阶段：默认走 Docker sandbox（Linux 容器内执行工具，绕开 Windows
# FileEditor 挂起；worktree 通过 C:\legion-worktrees -> /workspaces 绑定挂载）。
BASE_URL = os.environ.get("OPENHANDS_SANDBOX_URL", "http://127.0.0.1:8771").rstrip("/")
HOST_BASE_URL = os.environ.get("OPENHANDS_HOST_URL", "http://127.0.0.1:8770").rstrip("/")
KEY_FILE = Path(os.environ.get("OPENHANDS_KEY_FILE", r"C:\项目\legion-openhands\.session_key"))
# 容器内访问宿主机网关；容器外测试可覆盖为 127.0.0.1。
LEGION_GATEWAY = os.environ.get(
    "OPENHANDS_GATEWAY_URL", "http://host.docker.internal:8797/v1"
)
REPO_ROOT = Path(os.environ.get("OPENHANDS_REPO", r"C:\项目\老田的AI助手")).resolve()
WORKTREE_ROOT = Path(os.environ.get("OPENHANDS_WORKTREE_ROOT", r"C:\legion-worktrees"))
CONTAINER_WORKTREE_ROOT = "/workspaces"
DEFAULT_HOST_WORKDIR = Path(r"C:\legion-worktrees\_default")
DEFAULT_CONTAINER_WORKDIR = CONTAINER_WORKTREE_ROOT + "/_default"
LOCAL_ROOTS = (WORKTREE_ROOT, Path(r"C:\legion-oh-worktrees"))
DEFAULT_TIMEOUT = 600
TASK_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,80}$")


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
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
        host = (WORKTREE_ROOT / task).absolute()
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


def _llm(model: str) -> dict:
    alias = str(model or "deepseek-flash").strip()
    if alias.startswith("openhands_"):
        alias = alias[len("openhands_"):]
    if "/" not in alias:
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
    source = str(e.get("source") or "")
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
            out.append({"kind": e.get("kind"), "source": e.get("source"),
                        "summary": summary, "id": e.get("id")})
    return out[-limit:]


def health() -> dict:
    _r, data = _req("/health", timeout=5)
    if data.get("status") == "ok":
        return {"ok": True, "base_url": BASE_URL, "mode": "docker-sandbox",
                "worktree_root": str(WORKTREE_ROOT),
                "container_root": CONTAINER_WORKTREE_ROOT}
    return {"ok": False, "base_url": BASE_URL,
            **{k: v for k, v in (data or {}).items() if k != "ok"}}


def chat(message: str, conversation_id: str = "", timeout: int = DEFAULT_TIMEOUT,
         task: str = "", worktree: str = "", model: str = "deepseek-flash") -> dict:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "error": "empty_message"}
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
