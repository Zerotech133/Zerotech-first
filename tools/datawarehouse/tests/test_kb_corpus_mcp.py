# -*- coding: utf-8 -*-
"""MCP-D kb-corpus 测试：stdio JSON-RPC（子进程）+ 只读/白名单/错误路径。

覆盖：
  * initialize / tools/list 契约（5 工具，serverInfo=kb-corpus）
  * kb_status / kb_books 走 kb_read_api.py 真检索
  * kb_search 走 kb_read_api.py search（真命中 + top 上限）
  * yt_tables 表清单/行数（sqlite mode=ro）
  * yt_query 白名单 + limit≤20 + 文本截断
  * 缺失库文件 fail-visible（isError=true，不静默）
  * 只读实证：调用前后 yt_learning.db 的 size/mtime 不变
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "agent_platform" / "tools" / "kb_corpus_mcp.py"
YT_DB = Path(r"C:/项目/知识库/00_数据仓/yt_learning.db")

# 2026-11-08（K-CI-20261108-KBSKIP）：本文件 6 例全部围绕 yt_learning.db 展开
# （kb_status/kb_search/yt_tables/yt_query/只读实证/selftest）。该库是本地知识库
# 数据资产（C:/项目/知识库/... 走 .gitignore 之外的绝对路径），干净 runner 上
# 必然不存在——不 skip 就是 6 条「云端必红」。与 pyarrow 缺库时 importorskip 同口径：
# 缺资产 → 明确 skip 并留原因，不静默、也不冒充通过。本机有库时照常真跑。
if not YT_DB.exists():
    pytest.skip(
        "yt_learning.db 不在本机（%s 属本地知识库数据资产，未入库）" % YT_DB,
        allow_module_level=True,
    )


def _call(msgs, env=None, timeout=120):
    """起一个 stdio MCP 子进程，喂 msgs，返回逐行 JSON 响应。"""
    inp = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT)], input=inp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=run_env, cwd=str(REPO))
    assert r.returncode == 0, "rc=%s stderr=%s" % (r.returncode, (r.stderr or "")[-500:])
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert lines, "MCP 无响应: stderr=%s" % (r.stderr or "")[-500:]
    return lines


def _call_tool(name, arguments, env=None):
    lines = _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}},
    ], env=env)
    return lines[-1]["result"]


def _body(result):
    return json.loads(result["content"][0]["text"])


def test_initialize_and_tools_list():
    lines = _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert lines[0]["result"]["serverInfo"]["name"] == "kb-corpus"
    tools = lines[1]["result"]["tools"]
    assert [t["name"] for t in tools] == ["kb_status", "kb_search", "kb_books", "yt_tables", "yt_query"]
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
    yt_query = [t for t in tools if t["name"] == "yt_query"][0]
    # 安全：表名走 enum 白名单，limit ≤ 20
    assert yt_query["inputSchema"]["properties"]["limit"]["maximum"] == 20
    assert "dim_topic" in yt_query["inputSchema"]["properties"]["table"]["enum"]


def test_kb_status_and_books_via_read_api():
    status = _body(_call_tool("kb_status", {}))
    assert status["ok"] is True and status["meta"]["read_only"] is True
    assert status["data"]["docs"] >= 1
    assert status["data"]["units"] >= 1
    assert status["data"]["sources"]

    books = _body(_call_tool("kb_books", {}))
    assert books["ok"] is True
    assert books["total"] >= 1
    assert sum(v["books"] for v in books["by_source"].values()) == books["total"]


def test_kb_search_returns_hits_and_respects_top():
    body = _body(_call_tool("kb_search", {"q": "策略", "top": 3}))
    assert body["ok"] is True
    assert body["total_hits"] >= 1
    assert 1 <= len(body["items"]) <= 3
    item = body["items"][0]
    assert item["id"] and item["claim"] and "score" in item  # 带稳定 id + 论断 + 评分


def test_yt_tables_readonly_inventory():
    body = _body(_call_tool("yt_tables", {}))
    assert body["ok"] is True
    assert body["total_tables"] >= 9
    rows = {t["name"]: t["rows"] for t in body["tables"]}
    assert rows["dim_channel"] == 100
    assert rows["dim_topic"] >= 1
    assert rows["fact_comment"] >= 1000
    assert all(t["whitelisted"] for t in body["tables"] if t["name"] in body["whitelist"])


def test_yt_query_whitelist_limit_and_text_cap():
    body = _body(_call_tool("yt_query", {"table": "dim_topic", "limit": 3}))
    assert body["ok"] is True and body["limit"] == 3 and len(body["rows"]) <= 3
    assert "topic" in body["columns"]

    # limit > 20 被压到 20（不越界读）
    capped = _body(_call_tool("yt_query", {"table": "meta_faq_build", "limit": 999}))
    assert capped["requested_limit"] == 999 and capped["limit"] == 20
    assert len(capped["rows"]) <= 20

    # 非白名单/注入表名 → isError
    for bad in ("sqlite_master", "dim_topic; DROP TABLE dim_topic", "no_such_table"):
        res = _call_tool("yt_query", {"table": bad})
        assert res["isError"] is True, bad
        assert "白名单" in res["content"][0]["text"] or "failed" in res["content"][0]["text"]


def test_missing_yt_db_is_fail_visible(tmp_path):
    missing = tmp_path / "no_such_yt_learning.db"
    res = _call_tool("yt_tables", {}, env={"KB_CORPUS_YT_DB": str(missing)})
    assert res["isError"] is True
    assert "缺失" in res["content"][0]["text"]

    res2 = _call_tool("yt_query", {"table": "dim_topic", "limit": 1},
                      env={"KB_CORPUS_YT_DB": str(missing)})
    assert res2["isError"] is True


def test_calls_do_not_modify_yt_db():
    before = (YT_DB.stat().st_size, YT_DB.stat().st_mtime_ns)
    _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "yt_tables", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "yt_query", "arguments": {"table": "fact_faq_semantic", "limit": 2}}},
    ])
    after = (YT_DB.stat().st_size, YT_DB.stat().st_mtime_ns)
    assert before == after  # 只读：库文件未被写（尺寸与 mtime 均不变）


def test_selftest_exit_zero():
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 0, (r.stdout or "")[-800:] + (r.stderr or "")[-400:]
    report = json.loads(r.stdout)
    assert report["ok"] is True
    assert report["tools"] == ["kb_status", "kb_search", "kb_books", "yt_tables", "yt_query"]
    assert report["checks"]["yt_tables"]["total_tables"] >= 9
