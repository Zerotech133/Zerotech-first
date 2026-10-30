# -*- coding: utf-8 -*-
"""Hermes orchestrator：A0/席位先规划，OH 只执行。

流程：
  user message
    → load agent prompt (bundle)
    → Hermes /api/chat?agent=A0 生成严格 JSON 计划
    → need_execute=false：直接回答，不启动 OH
    → need_execute=true ：把「A0 计划 + 用户任务」交给 OH 执行

边界：本模块只做编排，不直接跑命令；执行仍在 OH sandbox。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "portal" / "agent_ide_bundle.json"
TASKS_FILE = REPO / ".worktrees" / "tasks.json"
RUN_DIR = Path(os.environ.get(
    "LEGION_RUN_DIR", r"C:\项目\_vault\legion_ide\runs"))
HERMES_CHAT_URL = os.environ.get(
    "HERMES_ORCH_CHAT_URL", "http://127.0.0.1:8796/api/chat")
PLAN_SYSTEM = (
    "你是 Hermes 的编排层。先判断用户任务是否需要改动仓库/执行命令。\n"
    "需要执行时给出可核对的短计划；不需要执行时直接回答。\n"
    "只输出严格 JSON，不要 markdown 代码围栏：\n"
    '{"need_execute": true|false, "plan": "不超过 120 字的计划或回答"}'
)


def load_agent(agent: str) -> dict:
    try:
        data = json.loads(BUNDLE.read_text(encoding="utf-8"))
        row = (data.get("agents") or {}).get(str(agent or "").strip())
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError):
        return {}


def _task_row(task: str) -> dict:
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        row = data.get(str(task or "").strip())
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError):
        return {}


def task_context(task: str) -> str:
    """给 A0 的最小工单上下文；只读，不塞整张 tasks.json。"""
    task = str(task or "").strip()
    if not task:
        return ""
    row = _task_row(task)
    if not row:
        return "工单：%s（台账未找到，按用户输入执行）" % task
    parts = [
        "工单：%s" % task,
        "状态：%s" % (row.get("status") or "unknown"),
        "项目：%s" % (row.get("project") or "-"),
        "owner：%s" % (row.get("owner") or "-"),
        "描述：%s" % str(row.get("desc") or "")[:800],
    ]
    return "\n".join(parts)


def list_tasks(limit: int = 100) -> list:
    """Hermes 主聊天 task 选择器数据：doing/review。"""
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for tid, row in (data or {}).items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        if status not in ("doing", "review", "in_progress"):
            continue
        out.append({
            "task": str(tid),
            "desc": str(row.get("desc") or "")[:120],
            "status": status,
            "project": str(row.get("project") or ""),
            "worktree": str(REPO / ".worktrees" / str(tid)),
        })
    out.sort(key=lambda x: x["task"], reverse=True)
    return out[:max(1, min(int(limit or 100), 300))]


def _event(run_id: str, etype: str, payload: dict | None = None) -> None:
    """append-only run 事件；失败不影响主流程。"""
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_id": run_id,
            "source": "hermes_orchestrator",
            "type": etype,
            "payload": payload or {},
        }
        with open(RUN_DIR / (run_id + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _extract_json(text: str) -> dict:
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def parse_plan(text: str) -> dict:
    obj = _extract_json(text)
    need = obj.get("need_execute")
    if isinstance(need, str):
        need = need.strip().lower() in ("1", "true", "yes", "on")
    plan = str(obj.get("plan") or "").strip()
    if not plan:
        # 模型没按 JSON 输出：按“可执行计划”处理，保留原文，避免丢任务。
        plan = str(text or "").strip()[:2000]
        need = True
    return {"need_execute": bool(need), "plan": plan[:2000]}


def ask_hermes(agent: str, prompt: str, user: str, timeout: int = 90,
               context: str = "") -> dict:
    system = str(prompt or "") + "\n\n" + PLAN_SYSTEM
    if context:
        system += "\n\n当前工单上下文（只读，勿改）：\n" + str(context)[:4000]
    body = json.dumps({
        "agent": agent,
        "system": system[:16000],
        "user": str(user or "")[:8000],
    }).encode("utf-8")
    req = urllib.request.Request(
        HERMES_CHAT_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=max(5, int(timeout))) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"ok": False, "error": "hermes_chat_unavailable",
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def orchestrate(message: str, task: str = "", model: str = "",
                agent: str = "") -> dict:
    message = str(message or "").strip()
    task = str(task or "").strip()
    agent = str(agent or "").strip()
    if not message:
        return {"ok": False, "error": "empty_message"}
    if not agent:
        return {"ok": False, "error": "agent_required"}
    meta = load_agent(agent)
    prompt = str(meta.get("prompt") or "")
    if not prompt:
        return {"ok": False, "error": "agent_prompt_missing", "agent": agent}
    run_id = "run-" + uuid.uuid4().hex[:12]
    ctx = task_context(task)
    _event(run_id, "orchestrate_start", {
        "agent": agent, "task": task, "model": model,
        "worktree": str(REPO / ".worktrees" / task) if task else "",
        "message": message[:500],
    })
    env = ask_hermes(agent, prompt, message, context=ctx)
    if not env.get("ok"):
        _event(run_id, "hermes_failed", {"detail": env})
        return {"ok": False, "error": "hermes_chat_failed",
                "agent": agent, "run_id": run_id, "task": task, "detail": env}
    data = env.get("data") or {}
    text = str(data.get("text") or env.get("text") or "")
    plan = parse_plan(text)
    base = {
        "ok": True,
        "run_id": run_id,
        "task": task,
        "context": ctx,
        "agent": agent,
        "agent_name": meta.get("cn_name") or agent,
        "binding": env.get("binding") or data.get("binding_resolved") or "",
        "plan": plan["plan"],
        "need_execute": plan["need_execute"],
    }
    _event(run_id, "hermes_plan", {
        "binding": base["binding"], "need_execute": plan["need_execute"],
        "plan": plan["plan"],
    })
    if not plan["need_execute"]:
        base["mode"] = "answer"
        _event(run_id, "run_end", {"mode": "answer"})
        return base
    from legion_command import openhands_engine as OH
    composed = (
        "A0/席位计划（Hermes 编排层）：\n%s\n\n工单：%s\n\n用户原始任务：\n%s"
        % (plan["plan"], task or "（未指定）", message)
    )
    res = OH.start(
        composed, task=task,
        model=str(model or meta.get("llm") or "deepseek-flash"),
        agent=agent,
    )
    base.update(res)
    base["mode"] = "executed" if res.get("ok") else "execute_failed"
    _event(run_id, "oh_start", {
        "ok": bool(res.get("ok")), "cid": res.get("conversation_id"),
        "worktree": res.get("worktree"), "error": res.get("error"),
        "detail": res.get("detail"),
    })
    _event(run_id, "run_end", {"mode": base["mode"]})
    return base
