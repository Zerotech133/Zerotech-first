# -*- coding: utf-8 -*-
"""api_kb_qa（知识库问答 HTTP handler 预接线）契约测试 —— 不调真实 LLM、不启动/重启服务。

覆盖：
  1. 参数校验：q 必填/长度、topk 类型与范围、mode 白名单、method/path 错误；
  2. 默认检索模式：retrieval_only=True、返回信封 schema、meta 字段；
  3. 生成模式：显式 mode=generate 才调 LLM（retrieval_only=False、超时常量）；
  4. 错误码：INVALID_ARGUMENT / UPSTREAM_UNAVAILABLE（异常/超时）/ NOT_READY（kb_qa 加载失败）；
  5. 限流：滑动窗口超限 → 429 RATE_LIMITED；缓存：同问题命中、cache=0 绕过；
  6. 预接线约束：WIRED=False、未注册 portal_api、路由自描述。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HANDLER = REPO / "portal" / "api_kb_qa.py"


def _load_api():
    spec = importlib.util.spec_from_file_location("api_kb_qa_under_test", HANDLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load_api()


@pytest.fixture(autouse=True)
def _reset_state():
    CALLS.clear()
    api.reset_rate_limit()
    api.reset_cache()
    yield
    CALLS.clear()
    api.reset_rate_limit()
    api.reset_cache()


CALLS: list[dict] = []


def mock_ask(question, topk=8, timeout=300, retrieval_only=False, **_kw):
    CALLS.append({"question": question, "topk": topk, "timeout": timeout,
                  "retrieval_only": retrieval_only})
    if retrieval_only:
        return {"question": question, "answer": None, "citations": [],
                "confidence": {"level": "n/a", "reasons": ["仅检索模式"]},
                "retrieved": [{"rank": 1, "unit_id": "41692ee59293", "kind": "unit"}],
                "citation_check": {}}
    return {"question": question, "answer": "M9 覆铜板提升价值量 [41692ee59293] p.2-14。",
            "citations": [{"unit_id": "41692ee59293", "book": "rp_X", "chapter": "图表目录",
                           "page": 2, "text": "M9 覆铜板…"}],
            "confidence": {"level": "high", "reasons": ["1 条可核验引用"]},
            "retrieved": [{"rank": 1, "unit_id": "41692ee59293", "kind": "unit"}],
            "citation_check": {"hallucinated_citations": [], "citation_missing": False}}


def qmap(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


# ---------------------------------------------------------------------------
# 1. 参数校验
# ---------------------------------------------------------------------------
def test_invalid_argument_errors():
    cases = [
        ("GET", "/api/kb/qa", {}, None, "q 不能为空"),
        ("GET", "/api/kb/qa", qmap(q="   "), None, "q 不能为空"),
        ("GET", "/api/kb/qa", qmap(q="x" * (api.MAX_QUESTION_CHARS + 1)), None, "q 过长"),
        ("GET", "/api/kb/qa", qmap(q="问题", topk="abc"), None, "topk"),
        ("GET", "/api/kb/qa", qmap(q="问题", topk="0"), None, "topk 超出范围"),
        ("GET", "/api/kb/qa", qmap(q="问题", topk=str(api.MAX_TOPK + 1)), None, "topk 超出范围"),
        ("GET", "/api/kb/qa", qmap(q="问题", mode="chat"), None, "mode"),
    ]
    for method, path, query, body, needle in cases:
        status, env = api.handle(method, path, query, body, ask_fn=mock_ask)
        assert status == 400, (query, status)
        assert env["ok"] is False
        assert env["error"]["code"] == "INVALID_ARGUMENT"
        assert needle in env["error"]["message"], env["error"]
    assert CALLS == []


def test_method_and_path_errors():
    status, env = api.handle("POST", "/api/kb/qa", qmap(q="问题"), None, ask_fn=mock_ask)
    assert status == 405 and env["error"]["code"] == "METHOD_NOT_ALLOWED"
    status, env = api.handle("GET", "/api/kb/other", qmap(q="问题"), None, ask_fn=mock_ask)
    assert status == 404 and env["error"]["code"] == "NOT_FOUND"
    # 允许尾斜杠
    status, env = api.handle("GET", "/api/kb/qa/", qmap(q="问题"), None, ask_fn=mock_ask)
    assert status == 200, env


def test_parse_topk_pure():
    assert api._parse_topk(None) == (api.DEFAULT_TOPK, None)
    assert api._parse_topk("5") == (5, None)
    topk, err = api._parse_topk("99")
    assert topk is None and "范围" in str(err)


# ---------------------------------------------------------------------------
# 2. 默认检索模式 + 3. 生成模式
# ---------------------------------------------------------------------------
def test_default_retrieval_mode_schema():
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板对 PCB 价值量影响", topk="5"),
                             ask_fn=mock_ask)
    assert status == 200 and env["ok"] is True
    assert env["api_version"] == "1.0" and env["request_id"] and env["generated_at"]
    assert env["error"] is None
    data = env["data"]
    for key in ("question", "answer", "citations", "confidence", "retrieved",
                "citation_check", "mode", "topk"):
        assert key in data
    assert data["mode"] == "retrieval" and data["topk"] == 5
    assert CALLS[-1]["retrieval_only"] is True
    assert CALLS[-1]["timeout"] == api.RETRIEVAL_TIMEOUT_SECONDS
    assert env["meta"]["mode"] == "retrieval" and env["meta"]["cache_hit"] is False
    assert env["meta"]["llm_called"] is False
    assert env["meta"]["rate_limit"]["limit"] == api.RATE_LIMIT["retrieval"]


def test_generate_mode_requires_explicit_param():
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", mode="generate"),
                             ask_fn=mock_ask)
    assert status == 200, env
    assert CALLS[-1]["retrieval_only"] is False
    assert CALLS[-1]["timeout"] == api.GENERATE_TIMEOUT_SECONDS
    assert env["data"]["answer"] and env["data"]["citations"]
    assert env["meta"]["llm_called"] is True
    assert env["meta"]["timeout_seconds"] == api.GENERATE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 4. 错误码：UPSTREAM_UNAVAILABLE / NOT_READY
# ---------------------------------------------------------------------------
def test_upstream_unavailable_on_ask_error_or_timeout():
    def raising_ask(*a, **k):
        raise RuntimeError("Ollama 被占满（测试注入）")

    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题", mode="generate"),
                             ask_fn=raising_ask)
    assert status == 503 and env["error"]["code"] == "UPSTREAM_UNAVAILABLE"

    def timeout_ask(*a, **k):
        raise TimeoutError("timed out")

    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题2", mode="generate"),
                             ask_fn=timeout_ask)
    assert status == 503 and env["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert "timed out" in env["error"]["message"]


def test_not_ready_when_kb_qa_unavailable(monkeypatch):
    def broken_load():
        raise api.QaUnavailable("模拟 kb_qa 加载失败")

    monkeypatch.setattr(api, "_load_qa", broken_load)
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题"))
    assert status == 503 and env["error"]["code"] == "NOT_READY"
    assert env["meta"]["mode"] == "retrieval"


# ---------------------------------------------------------------------------
# 5. 限流 / 缓存
# ---------------------------------------------------------------------------
def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setitem(api.RATE_LIMIT, "retrieval", 1)
    s1, _ = api.handle("GET", "/api/kb/qa", qmap(q="问题A"), ask_fn=mock_ask)
    s2, env2 = api.handle("GET", "/api/kb/qa", qmap(q="问题B"), ask_fn=mock_ask)
    assert s1 == 200 and s2 == 429
    assert env2["error"]["code"] == "RATE_LIMITED"
    assert env2["meta"]["rate_limit"]["limit"] == 1
    # generate 通道独立计数
    s3, _ = api.handle("GET", "/api/kb/qa", qmap(q="问题C", mode="generate"), ask_fn=mock_ask)
    assert s3 == 200


def test_check_rate_pure_window():
    allowed, info = api.check_rate("generate", now=1000.0)
    assert allowed is True and info["limit"] == api.RATE_LIMIT["generate"]
    for i in range(api.RATE_LIMIT["generate"] - 1):
        api.check_rate("generate", now=1000.0 + i * 0.01)
    denied, info2 = api.check_rate("generate", now=1001.0)
    assert denied is False and info2["remaining"] == 0
    # 窗口外自动释放
    ok, _ = api.check_rate("generate", now=1000.0 + api.RATE_LIMIT_WINDOW_SECONDS + 5)
    assert ok is True


def test_cache_hit_and_bypass():
    q = qmap(q="M9 覆铜板价值量")
    s1, _ = api.handle("GET", "/api/kb/qa", q, ask_fn=mock_ask)
    s2, env2 = api.handle("GET", "/api/kb/qa", q, ask_fn=mock_ask)
    assert s1 == s2 == 200
    assert env2["meta"]["cache_hit"] is True
    assert len(CALLS) == 1, "同问题第二次应命中缓存，不重复调用"
    s3, env3 = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", cache="0"),
                          ask_fn=mock_ask)
    assert s3 == 200 and env3["meta"]["cache_hit"] is False
    assert len(CALLS) == 2, "cache=0 应绕过缓存"
    s4, _ = api.handle("GET", "/api/kb/qa", qmap(q="另一个问题"), ask_fn=mock_ask)
    assert s4 == 200 and len(CALLS) == 3


# ---------------------------------------------------------------------------
# 6. 预接线约束 + 路由自描述
# ---------------------------------------------------------------------------
def test_not_wired_and_route_describe():
    assert api.WIRED is False
    doc = api.__doc__ or ""
    assert "未接线" in doc and "8091" in doc
    routes = api.describe_routes()
    assert routes["wired"] is False
    assert routes["routes"][0]["path"] == "/api/kb/qa"
    assert set(routes["routes"][0]["errors"]) >= {"INVALID_ARGUMENT", "UPSTREAM_UNAVAILABLE", "NOT_READY"}
    assert routes["limits"]["rate_limit_per_minute"]["generate"] < \
        routes["limits"]["rate_limit_per_minute"]["retrieval"]
    # 确认未注册进运行中的 portal_api（预接线，不重启任何服务）
    portal_api = (REPO / "portal" / "portal_api.py")
    assert portal_api.exists()
    assert "api_kb_qa" not in portal_api.read_text(encoding="utf-8", errors="replace")


def test_handle_returns_json_serializable():
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", mode="generate"),
                             ask_fn=mock_ask)
    assert status == 200
    text = json.dumps(env, ensure_ascii=False)
    assert "citations" in text and "confidence" in text
