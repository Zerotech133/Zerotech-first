# -*- coding: utf-8 -*-
"""api_kb_qa（知识库问答 HTTP handler 预接线）契约测试 —— 不调真实 LLM、不启动/重启服务。

覆盖（wave18 BA：multi 灰度预接线）：
  1. 参数校验：q 必填/长度、topk 类型与范围、mode 白名单（retrieval/single/multi；generate 兼容别名）、
     method/path 错误；
  2. 默认检索模式：retrieval_only=True、信封 schema、meta 含 mode/k/clusters/llm_called；
  3. single 生成：显式 mode=single 才走单单元路径（retrieval_only=False、超时 120s）；
  4. multi 生成（生成默认）：调 kb_qa_multi.answer_multi、k 截断 5..8、meta clusters/k/llm_called；
     旧 mode=generate 兼容映射 multi；
  5. 错误码：INVALID_ARGUMENT / UPSTREAM_UNAVAILABLE（异常/超时）/ NOT_READY（模块加载失败）；
  6. 限流：各 mode 通道独立；缓存键含 mode（不同模式互不命中）、cache=0 绕过；
  7. 预接线约束：WIRED=False、未注册 portal_api、路由自描述。
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
    CALLS.append({"kind": "ask", "question": question, "topk": topk, "timeout": timeout,
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


def mock_multi(question, k=8, timeout=300, **_kw):
    CALLS.append({"kind": "multi", "question": question, "k": k, "timeout": timeout})
    return {"question": question, "k": max(5, min(8, k)),
            "clusters": [{"label": "M9 覆铜板", "book": "rp_X", "chapter": "图表目录",
                          "units": 2, "max_score": 0.9},
                         {"label": "PCB 价值量", "book": "rp_Y", "chapter": "前言",
                          "units": 1, "max_score": 0.8}],
            "cluster_count": 2,
            "answer": "多单元综述：M9 覆铜板提升 PCB 价值量 [41692ee59293][28e0346286a2]。",
            "citations": [{"unit_id": "41692ee59293", "book": "rp_X", "page": 2},
                          {"unit_id": "28e0346286a2", "book": "rp_Y", "page": 1}],
            "confidence": {"level": "high", "reasons": ["2 簇引用"]},
            "citation_check": {"hallucinated_ids": [], "citation_missing": False},
            "fallback": False, "refusal": False}


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
        ("GET", "/api/kb/qa", qmap(q="问题", mode="fast"), None, "mode"),
    ]
    for method, path, query, body, needle in cases:
        status, env = api.handle(method, path, query, body, ask_fn=mock_ask,
                                 multi_fn=mock_multi)
        assert status == 400, (query, status)
        assert env["ok"] is False
        assert env["error"]["code"] == "INVALID_ARGUMENT"
        assert needle in env["error"]["message"], env["error"]
    assert CALLS == []


def test_method_and_path_errors():
    status, env = api.handle("POST", "/api/kb/qa", qmap(q="问题"), None,
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 405 and env["error"]["code"] == "METHOD_NOT_ALLOWED"
    status, env = api.handle("GET", "/api/kb/other", qmap(q="问题"), None,
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 404 and env["error"]["code"] == "NOT_FOUND"
    # 允许尾斜杠
    status, env = api.handle("GET", "/api/kb/qa/", qmap(q="问题"), None,
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 200, env


def test_parse_topk_pure():
    assert api._parse_topk(None) == (api.DEFAULT_TOPK, None)
    assert api._parse_topk("5") == (5, None)
    topk, err = api._parse_topk("99")
    assert topk is None and "范围" in str(err)


# ---------------------------------------------------------------------------
# 2. 默认检索模式
# ---------------------------------------------------------------------------
def test_default_retrieval_mode_schema():
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板对 PCB 价值量影响", topk="5"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 200 and env["ok"] is True
    assert env["api_version"] == "1.0" and env["request_id"] and env["generated_at"]
    assert env["error"] is None
    data = env["data"]
    for key in ("question", "answer", "citations", "confidence", "retrieved",
                "citation_check", "mode", "topk"):
        assert key in data
    assert data["mode"] == "retrieval" and data["topk"] == 5
    assert CALLS[-1]["kind"] == "ask" and CALLS[-1]["retrieval_only"] is True
    assert CALLS[-1]["timeout"] == api.RETRIEVAL_TIMEOUT_SECONDS
    meta = env["meta"]
    assert meta["mode"] == "retrieval" and meta["cache_hit"] is False
    assert meta["llm_called"] is False and meta["k"] == 5 and meta["clusters"] == 0
    assert meta["rate_limit"]["limit"] == api.RATE_LIMIT["retrieval"]
    # 默认常量：HTTP 默认 retrieval、生成默认 multi
    assert api.DEFAULT_MODE == "retrieval" and api.DEFAULT_GENERATE_MODE == "multi"


# ---------------------------------------------------------------------------
# 3. single 生成（显式）
# ---------------------------------------------------------------------------
def test_single_mode_generation():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="single"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 200, env
    assert CALLS[-1]["kind"] == "ask" and CALLS[-1]["retrieval_only"] is False
    assert CALLS[-1]["timeout"] == api.GENERATE_TIMEOUT_SECONDS
    assert env["data"]["answer"] and env["data"]["citations"]
    assert env["data"]["mode"] == "single"
    assert env["meta"]["llm_called"] is True
    assert env["meta"]["mode"] == "single"
    assert env["meta"]["k"] == api.DEFAULT_TOPK and env["meta"]["clusters"] == 0
    assert env["meta"]["timeout_seconds"] == api.GENERATE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 4. multi 生成（生成默认）+ generate 兼容别名
# ---------------------------------------------------------------------------
def test_multi_mode_generation_default():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi", topk="8"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 200, env
    assert CALLS[-1]["kind"] == "multi"
    assert CALLS[-1]["k"] == 8 and CALLS[-1]["timeout"] == api.MULTI_TIMEOUT_SECONDS
    data = env["data"]
    assert data["mode"] == "multi" and len(data["clusters"]) == 2
    assert data["answer"] and data["citations"]
    meta = env["meta"]
    assert meta["mode"] == "multi" and meta["k"] == 8 and meta["clusters"] == 2
    assert meta["llm_called"] is True
    assert meta["timeout_seconds"] == api.MULTI_TIMEOUT_SECONDS
    assert meta["rate_limit"]["limit"] == api.RATE_LIMIT["multi"]
    # topk 超出 multi k 范围时截断到 5..8（topk=20 → k=8）
    api.reset_cache()
    status2, env2 = api.handle("GET", "/api/kb/qa",
                               qmap(q="另一个问题", mode="multi", topk="20"),
                               ask_fn=mock_ask, multi_fn=mock_multi)
    assert status2 == 200 and env2["meta"]["k"] == 8
    # 旧 mode=generate 兼容映射 multi
    api.reset_cache()
    status3, env3 = api.handle("GET", "/api/kb/qa",
                               qmap(q="第三个问题", mode="generate"),
                               ask_fn=mock_ask, multi_fn=mock_multi)
    assert status3 == 200 and env3["data"]["mode"] == "multi"
    assert env3["meta"].get("mode_alias") == "generate→multi"


def test_multi_no_evidence_llm_not_called():
    def no_evidence_multi(question, k=8, timeout=300, **_kw):
        return {"question": question, "k": k, "clusters": [], "cluster_count": 0,
                "answer": "知识库中没有足够依据回答该问题。", "citations": [],
                "confidence": {"level": "none", "reasons": ["检索无材料"]},
                "citation_check": {"citation_missing": True}, "fallback": False,
                "refusal": True}

    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="无材料问题", mode="multi"),
                             ask_fn=mock_ask, multi_fn=no_evidence_multi)
    assert status == 200
    assert env["meta"]["clusters"] == 0 and env["meta"]["llm_called"] is False
    assert env["data"]["refusal"] is True


# ---------------------------------------------------------------------------
# 5. 错误码：UPSTREAM_UNAVAILABLE / NOT_READY
# ---------------------------------------------------------------------------
def test_upstream_unavailable_on_ask_error_or_timeout():
    def raising_ask(*a, **k):
        raise RuntimeError("Ollama 被占满（测试注入）")

    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题", mode="single"),
                             ask_fn=raising_ask, multi_fn=mock_multi)
    assert status == 503 and env["error"]["code"] == "UPSTREAM_UNAVAILABLE"

    def timeout_ask(*a, **k):
        raise TimeoutError("timed out")

    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题2", mode="single"),
                             ask_fn=timeout_ask, multi_fn=mock_multi)
    assert status == 503 and env["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert "timed out" in env["error"]["message"]

    def raising_multi(*a, **k):
        raise RuntimeError("multi 上游不可用（测试注入）")

    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题3", mode="multi"),
                             ask_fn=mock_ask, multi_fn=raising_multi)
    assert status == 503 and env["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_not_ready_when_modules_unavailable(monkeypatch):
    def broken_load():
        raise api.QaUnavailable("模拟 kb_qa 加载失败")

    monkeypatch.setattr(api, "_load_qa", broken_load)
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题"))
    assert status == 503 and env["error"]["code"] == "NOT_READY"
    assert env["meta"]["mode"] == "retrieval"

    def broken_multi_load():
        raise api.QaUnavailable("模拟 kb_qa_multi 加载失败")

    monkeypatch.setattr(api, "_load_multi", broken_multi_load)
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="问题", mode="multi"))
    assert status == 503 and env["error"]["code"] == "NOT_READY"
    assert env["meta"]["mode"] == "multi"


# ---------------------------------------------------------------------------
# 6. 限流 / 缓存（缓存键含 mode）
# ---------------------------------------------------------------------------
def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setitem(api.RATE_LIMIT, "retrieval", 1)
    s1, _ = api.handle("GET", "/api/kb/qa", qmap(q="问题A"), ask_fn=mock_ask,
                       multi_fn=mock_multi)
    s2, env2 = api.handle("GET", "/api/kb/qa", qmap(q="问题B"), ask_fn=mock_ask,
                          multi_fn=mock_multi)
    assert s1 == 200 and s2 == 429
    assert env2["error"]["code"] == "RATE_LIMITED"
    assert env2["meta"]["rate_limit"]["limit"] == 1
    # single 通道独立计数
    s3, _ = api.handle("GET", "/api/kb/qa", qmap(q="问题C", mode="single"),
                       ask_fn=mock_ask, multi_fn=mock_multi)
    assert s3 == 200
    # multi 通道也独立
    s4, _ = api.handle("GET", "/api/kb/qa", qmap(q="问题D", mode="multi"),
                       ask_fn=mock_ask, multi_fn=mock_multi)
    assert s4 == 200


def test_check_rate_pure_window():
    allowed, info = api.check_rate("multi", now=1000.0)
    assert allowed is True and info["limit"] == api.RATE_LIMIT["multi"]
    for i in range(api.RATE_LIMIT["multi"] - 1):
        api.check_rate("multi", now=1000.0 + i * 0.01)
    denied, info2 = api.check_rate("multi", now=1001.0)
    assert denied is False and info2["remaining"] == 0
    ok, _ = api.check_rate("multi", now=1000.0 + api.RATE_LIMIT_WINDOW_SECONDS + 5)
    assert ok is True


def test_cache_key_includes_mode():
    q = qmap(q="M9 覆铜板价值量")
    s1, _ = api.handle("GET", "/api/kb/qa", q, ask_fn=mock_ask, multi_fn=mock_multi)
    s2, env2 = api.handle("GET", "/api/kb/qa", q, ask_fn=mock_ask, multi_fn=mock_multi)
    assert s1 == s2 == 200 and env2["meta"]["cache_hit"] is True
    assert len(CALLS) == 1, "同问题同 mode 第二次应命中缓存"
    # 不同 mode → 缓存键不同，各自执行
    s3, env3 = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", mode="single"),
                          ask_fn=mock_ask, multi_fn=mock_multi)
    assert s3 == 200 and env3["meta"]["cache_hit"] is False and len(CALLS) == 2
    s4, env4 = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", mode="multi"),
                          ask_fn=mock_ask, multi_fn=mock_multi)
    assert s4 == 200 and env4["meta"]["cache_hit"] is False and len(CALLS) == 3
    # cache=0 绕过
    s5, env5 = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", cache="0"),
                          ask_fn=mock_ask, multi_fn=mock_multi)
    assert s5 == 200 and env5["meta"]["cache_hit"] is False and len(CALLS) == 4


# ---------------------------------------------------------------------------
# 7. 预接线约束 + 路由自描述
# ---------------------------------------------------------------------------
def test_not_wired_and_route_describe():
    assert api.WIRED is False
    doc = api.__doc__ or ""
    assert "未接线" in doc and "8091" in doc
    routes = api.describe_routes()
    assert routes["wired"] is False
    assert routes["routes"][0]["path"] == "/api/kb/qa"
    assert set(routes["routes"][0]["errors"]) >= {"INVALID_ARGUMENT", "UPSTREAM_UNAVAILABLE", "NOT_READY"}
    params = routes["routes"][0]["params"]
    assert "retrieval" in params["mode"] and "multi" in params["mode"]
    limits = routes["limits"]
    assert limits["rate_limit_per_minute"]["single"] < \
        limits["rate_limit_per_minute"]["retrieval"]
    assert limits["rate_limit_per_minute"]["multi"] < \
        limits["rate_limit_per_minute"]["retrieval"]
    assert limits["multi_timeout_s"] > limits["single_timeout_s"] > limits["retrieval_timeout_s"]
    # 确认未注册进运行中的 portal_api（预接线，不重启任何服务）
    portal_api = (REPO / "portal" / "portal_api.py")
    assert portal_api.exists()
    assert "api_kb_qa" not in portal_api.read_text(encoding="utf-8", errors="replace")


def test_handle_returns_json_serializable():
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="M9 覆铜板价值量", mode="multi"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 200
    text = json.dumps(env, ensure_ascii=False)
    assert "citations" in text and "confidence" in text and "clusters" in text
