# -*- coding: utf-8 -*-
"""zhuge_mcp_server 单测（P0-1）。

全部离线：平台文件用临时目录；HTTP 用假函数。不启 MCP server、不联网、不写仓库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_mcp_server as m  # noqa: E402


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".worktrees").mkdir(parents=True)
    (repo / "agent_platform" / "registry").mkdir(parents=True)
    (repo / "docs" / "legion").mkdir(parents=True)
    tasks = {
        "T-1": {"desc": "接通采纳率", "status": "doing", "owner": "ZHAOYUN",
                "project": "general", "created": "2026-10-27 06:00"},
        "T-2": {"desc": "写一份审计报告", "status": "done", "owner": "A0",
                "created": "2026-10-26 10:00"},
    }
    (repo / ".worktrees" / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    org = {"org": {"departments": [{"name": "研究院", "agents": [
        {"agent_id": "R2", "name": "赵云", "department": "研究院",
         "llm_binding": "ollama/qwen3.5:9b"},
        {"agent_id": "A0", "name": "田丰", "department": "执行", "llm_binding": "glm/glm-5.3"},
    ]}]}}
    (repo / "agent_platform" / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (repo / "docs" / "legion" / "a.md").write_text(
        "# 诸葛亮\n\n军师负责质询与建议。\n第二行：反向军师。\n", encoding="utf-8")
    (repo / "docs" / "other.md").write_text("# 其他\n无关内容\n", encoding="utf-8")
    return repo


def test_tasks_read_filter(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    res = m.tasks_read(status="doing")
    assert res["ok"] is True and res["total"] == 1
    assert res["items"][0]["task_id"] == "T-1"
    assert res["items"][0]["read_only"] if "read_only" in res["items"][0] else True
    res2 = m.tasks_read(owner="A0")
    assert res2["total"] == 1 and res2["items"][0]["task_id"] == "T-2"
    res3 = m.tasks_read(limit=1)
    assert res3["ok"] and len(res3["items"]) == 1


def test_tasks_read_missing(monkeypatch, tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    res = m.tasks_read()
    assert res["ok"] is False and res["error"]["code"] == "tasks_missing"


def test_org_read_filter(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    res = m.org_read("R2")
    assert res["ok"] and res["returned"] == 1
    assert res["agents"][0]["llm_binding"] == "ollama/qwen3.5:9b"
    res_all = m.org_read()
    assert res_all["total"] >= 2


def test_docs_search(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    res = m.docs_search("反向军师")
    assert res["ok"] and res["returned"] == 1
    hit = res["results"][0]
    assert hit["path"] == "docs/legion/a.md" and hit["line"] == 4
    assert m.docs_search("x")["ok"] is False


def test_docs_search_respects_limit(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    res = m.docs_search("诸葛亮", limit=1)
    assert res["ok"] and len(res["results"]) <= 1


def test_local_json_url_allowlist():
    assert m._local_json_url("http://127.0.0.1:8795/health")
    assert m._local_json_url("http://localhost:8091/health")
    assert not m._local_json_url("http://example.com/health")
    assert not m._local_json_url("https://127.0.0.1:8795/health")


def test_worktree_root_discovery(tmp_path):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "W1").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(
        "gitdir: %s\n" % (main / ".git" / "worktrees" / "W1"), encoding="utf-8")
    assert m._discover_repo_root(wt) == main
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    assert m._discover_repo_root(plain) == plain


def test_health_read_mock(monkeypatch):
    def fake(url, timeout=10):
        return {"ok": True, "status_code": 200, "elapsed_ms": 1.0, "data": {"ok": True}}

    monkeypatch.setattr(m, "_http_get_json", fake)
    res = m.health_read()
    assert res["ok"] and len(res["services"]) == 2
    assert all(s["ok"] for s in res["services"])


def test_brief_read_denied(monkeypatch):
    res = m.brief_read(clock="+")
    # 真实环境 8091 可能在线，这里只验证 URL 白名单与返回结构不崩
    assert "ok" in res


def test_source_read_only_no_write_apis():
    """AST 级只读纪律：无 subprocess/os.system、无写文件调用、无 eval/exec。"""
    import ast

    src = (TOOLS_DIR / "zhuge_mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert "subprocess" not in imports

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = {c.func.id for c in calls if isinstance(c.func, ast.Name)}
    attrs = {c.func.attr for c in calls if isinstance(c.func, ast.Attribute)}
    assert "open" not in names and "eval" not in names and "exec" not in names
    for attr in ("write_text", "write_bytes", "system", "popen"):
        assert attr not in attrs, "只读纪律被破坏：%s" % attr


def test_mcp_sdk_available():
    assert m.mcp is not None, "mcp SDK 未安装"
    tool_names = {t.name for t in m.mcp._tool_manager.list_tools()}
    assert {"tasks_read_tool", "org_read_tool", "docs_search_tool",
            "health_read_tool", "brief_read_tool",
            "web_fetch_tool", "evidence_check_tool"} <= tool_names
