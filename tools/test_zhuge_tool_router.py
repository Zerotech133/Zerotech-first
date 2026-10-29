# -*- coding: utf-8 -*-
"""L5 只读工具编排：URL 取证 / MCP 意图 / 无意图不调用。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load():
    spec = importlib.util.spec_from_file_location("_tool_router_test", str(TOOLS / "zhuge_tool_router.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_detect_url_and_intent():
    mod = _load()
    d = mod.detect("请查 https://example.com/x 并看任务 A1A 健康")
    assert d["urls"] == ["https://example.com/x"]
    assert "tasks" in d["intents"] and "health" in d["intents"]


def test_route_url_fetch():
    mod = _load()

    def fake_fetch(url, max_chars=1200):
        return {"ok": True, "final_url": url, "title": "示例", "fetched_at": "2026-10-29",
                "source_tier": {"label": "unknown"}, "text": "正文"}

    out = mod.route("看 https://example.com/x", fetch_url=fake_fetch)
    assert out["tools"] == [{"tool": "web_fetch", "ok": True}]
    assert "联网取证" in out["context"] and out["sources"]


def test_route_intent_without_network(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_mcp_call", lambda tool, q, agent: {
        "ok": True, "items": [{"task_id": "T-1", "status": "doing"}]})
    out = mod.route("查看任务进度")
    assert out["tools"] == [{"tool": "tasks", "ok": True}]
    assert "任务：1 条" in out["context"]


def test_route_benign_no_tools():
    mod = _load()
    out = mod.route("今天天气怎么样")
    assert out["tools"] == [] and out["context"] == ""
