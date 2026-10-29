# -*- coding: utf-8 -*-
"""zhuge_tool_router — L5 只读工具自动编排。

规则：
- 问题含 URL → web_fetch（SSRF 防护、失败显式记录）；
- 命中任务/组织/健康/建议/文档意图 → 调 MCP 只读工具；
- 每次最多 2 个工具；只读、零执行、失败 fail-open；
- pytest 环境由调用方跳过，避免测试触网。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

URL_RE = re.compile(r"https?://[^\s<>\"]+")
AGENT_RE = re.compile(r"\b([A-Z]{1,3}\d{1,2})\b")
INTENTS = (
    ("tasks", ("任务", "工单", "派单", "task", "ticket")),
    ("org", ("组织", "席位", "谁负责", "agent", "编制")),
    ("health", ("健康", "服务", "端口", "挂了吗", "异常")),
    ("brief", ("建议", "今天先", "优先级", "brief", "该做什么")),
    ("docs", ("文档", "查一下", "规范", "判例", "ADR", "docs")),
)


def detect(question: str) -> dict:
    q = str(question or "")
    urls = [u.rstrip(".,;!?，。；！？") for u in URL_RE.findall(q)]
    intents = [name for name, words in INTENTS if any(w in q for w in words)]
    agent = (AGENT_RE.search(q) or [None])[0]
    return {"urls": urls[:2], "intents": intents[:2], "agent": agent}


def _summarize(tool: str, payload: dict) -> str:
    if not isinstance(payload, dict):
        return "%s：返回不可读" % tool
    if payload.get("ok") is False:
        return "%s：失败 %s" % (tool, str(payload.get("error") or "")[:160])
    if tool == "tasks":
        items = payload.get("items") or []
        return "任务：%d 条；%s" % (
            len(items), "；".join("%s[%s]" % (i.get("task_id"), i.get("status")) for i in items[:5]))
    if tool == "org":
        agents = payload.get("agents") or []
        return "组织：%d 席；%s" % (
            len(agents), "；".join("%s/%s" % (a.get("agent_id"), a.get("llm_binding")) for a in agents[:5]))
    if tool == "health":
        svcs = payload.get("services") or []
        return "服务：%d 项；%s" % (
            len(svcs), "；".join("%s=%s" % (s.get("service"), "ok" if s.get("ok") else "fail") for s in svcs[:6]))
    if tool == "brief":
        b = payload.get("brief") or {}
        sug = b.get("suggestions") or []
        return "建议：engine=%s suggestions=%d；%s" % (
            b.get("engine"), len(sug), "；".join(str(s.get("id")) for s in sug[:4]))
    if tool == "docs":
        rows = payload.get("results") or []
        return "文档：%d 条；%s" % (
            len(rows), "；".join(str(r.get("path")) for r in rows[:4]))
    return "%s：ok" % tool


def _mcp_call(tool: str, question: str, agent: str | None) -> dict:
    import zhuge_mcp_server as m

    if tool == "tasks":
        return m.tasks_read(limit=3)
    if tool == "org":
        return m.org_read(agent_id=agent or "")
    if tool == "health":
        return m.health_read(timeout=2.0)
    if tool == "brief":
        return m.brief_read(timeout=8.0)
    if tool == "docs":
        return m.docs_search(question[:60], limit=3)
    return {"ok": False, "error": "unknown_tool"}


def route(question: str, fetch_url=None, max_tools: int = 2) -> dict:
    """返回 {tools, context, errors, sources}；全程只读，异常不抛出。"""
    q = str(question or "")
    spec = detect(q)
    tools, context, errors, sources = [], [], [], []

    if spec["urls"]:
        if fetch_url is None:
            try:
                import zhuge_evidence

                fetch_url = zhuge_evidence.fetch_url
            except Exception as exc:  # noqa: BLE001
                errors.append("web_fetch: %s" % type(exc).__name__)
        if fetch_url is not None:
            for url in spec["urls"]:
                try:
                    data = fetch_url(url, max_chars=1200)
                    if data.get("ok"):
                        context.append(
                            "联网取证 %s《%s》抓取=%s 来源等级=%s：%s" % (
                                data.get("final_url") or url, data.get("title") or "",
                                data.get("fetched_at") or "?", 
                                (data.get("source_tier") or {}).get("label", "unknown"),
                                str(data.get("text") or "")[:700]))
                        sources.append({"url": data.get("final_url") or url,
                                        "title": data.get("title"), "at": data.get("fetched_at")})
                    else:
                        errors.append("web_fetch %s: %s" % (url, str(data.get("error") or "")[:120]))
                    tools.append({"tool": "web_fetch", "ok": bool(data.get("ok"))})
                except Exception as exc:  # noqa: BLE001
                    errors.append("web_fetch %s: %s" % (url, type(exc).__name__))
                    tools.append({"tool": "web_fetch", "ok": False})
                if len(tools) >= max_tools:
                    break

    for tool in spec["intents"]:
        if len(tools) >= max_tools:
            break
        try:
            payload = _mcp_call(tool, q, spec.get("agent"))
            ok = bool(payload.get("ok"))
            tools.append({"tool": tool, "ok": ok})
            context.append(_summarize(tool, payload))
            if not ok:
                errors.append("%s: %s" % (tool, str(payload.get("error") or "")[:120]))
        except Exception as exc:  # noqa: BLE001
            tools.append({"tool": tool, "ok": False})
            errors.append("%s: %s" % (tool, type(exc).__name__))

    return {"tools": tools, "context": "\n".join(context)[:3500],
            "errors": errors[:6], "sources": sources[:2]}
