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
_REAL_LOAD_GUARD = api._load_guard
GUARD_CALLS: list[dict] = []


class _FakeGuard:
    """测试 guard：pass=True 通过标记；fail=True 抛错（fail-open 用例）；否则回退。"""

    def __init__(self, pass_=True, fail=False):
        self.pass_ = pass_
        self.fail = fail

    def load_kb_ids(self):
        if self.fail:
            raise RuntimeError("guard 知识库 id 加载失败（测试注入）")
        return {"u1", "u2"}, {"b1"}

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback", marker_mode=None):
        if self.fail:
            raise RuntimeError("guard 应用失败（测试注入）")
        GUARD_CALLS.append({"on_fail": on_fail, "n_unit_ids": len(unit_ids)})
        out = dict(result)
        if self.pass_:
            out.update({"guard_passed": True, "guard_issues": [], "guard_action": "pass",
                        "citation_valid": ["u1", "u2"], "citation_invalid": [],
                        "citation_total": 2, "citation_valid_count": 2,
                        "citation_invalid_count": 0, "support_rate": 1.0,
                        "inference_ratio": 0.0, "distinct_cited_units": 2})
        else:
            out.update({"guard_passed": False, "guard_issues": ["citation_invalid"],
                        "guard_action": "fallback",
                        "citation_valid": ["u1"], "citation_invalid": ["bad"],
                        "citation_total": 2, "citation_valid_count": 1,
                        "citation_invalid_count": 1, "support_rate": 0.5,
                        "inference_ratio": 0.2, "distinct_cited_units": 1,
                        "answer": "（质量守卫回退）可核验摘录", "citations": [],
                        "fallback": True, "refusal": False})
        return out


_PASS_GUARD = _FakeGuard(True)
# 默认把懒加载 guard 替换为 pass-through fake：既有 multi 用例语义不变（guard 用例显式注入）
api._load_guard = lambda: _PASS_GUARD


@pytest.fixture(autouse=True)
def _reset_state():
    CALLS.clear()
    GUARD_CALLS.clear()
    api.reset_rate_limit()
    api.reset_cache()
    api.reset_guard_ids_cache()
    yield
    CALLS.clear()
    GUARD_CALLS.clear()
    api.reset_rate_limit()
    api.reset_cache()
    api.reset_guard_ids_cache()


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


# ---------------------------------------------------------------------------
# 8. guard 接入（wave20 CB：multi 默认 guard=1 + fallback；guard=0 灰度绕过）
# ---------------------------------------------------------------------------
def test_guard_default_on_multi():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi"),
                             ask_fn=mock_ask, multi_fn=mock_multi,
                             guard_impl=_PASS_GUARD)
    assert status == 200
    g = env["meta"]["guard"]
    assert g["enabled"] is True and g["applicable"] is True and g["applied"] is True
    assert g["on_fail"] == "fallback"
    assert g["guard_passed"] is True and g["guard_action"] == "pass"
    for key in ("guard_issues", "citation_valid", "citation_invalid",
                "citation_total", "support_rate", "inference_ratio"):
        assert key in g, f"meta.guard 缺字段 {key}"
    assert GUARD_CALLS and GUARD_CALLS[-1]["on_fail"] == "fallback"


def test_guard_zero_bypasses():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi", guard="0"),
                             ask_fn=mock_ask, multi_fn=mock_multi,
                             guard_impl=_PASS_GUARD)
    assert status == 200
    g = env["meta"]["guard"]
    assert g["enabled"] is False and g["applied"] is False
    assert GUARD_CALLS == [], "guard=0 不应调用 guard"


def test_guard_fallback_meta_and_answer():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi"),
                             ask_fn=mock_ask, multi_fn=mock_multi,
                             guard_impl=_FakeGuard(pass_=False))
    assert status == 200
    g = env["meta"]["guard"]
    assert g["guard_passed"] is False and g["guard_action"] == "fallback"
    assert g["citation_invalid"] == ["bad"] and g["guard_issues"]
    assert env["data"]["answer"].startswith("（质量守卫回退）")
    assert env["meta"]["llm_called"] is True, "LLM 已调用（guard 只是返回前加工）"


def test_guard_unavailable_fail_open():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi"),
                             ask_fn=mock_ask, multi_fn=mock_multi,
                             guard_impl=_FakeGuard(fail=True))
    assert status == 200, "guard 异常应 fail-open，不阻断问答"
    g = env["meta"]["guard"]
    assert g["applied"] is False and "error" in g
    assert env["data"]["answer"], "fail-open 时保留未守卫答案"


def test_guard_invalid_value_400():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="问题", mode="multi", guard="2"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 400 and env["error"]["code"] == "INVALID_ARGUMENT"
    assert "guard" in env["error"]["message"]


def test_guard_cache_key_includes_flag():
    base_q = {"q": ["M9 覆铜板价值量"], "mode": ["multi"]}
    s1, _ = api.handle("GET", "/api/kb/qa", base_q, ask_fn=mock_ask,
                       multi_fn=mock_multi, guard_impl=_PASS_GUARD)
    s2, env2 = api.handle("GET", "/api/kb/qa", base_q, ask_fn=mock_ask,
                          multi_fn=mock_multi, guard_impl=_PASS_GUARD)
    assert s1 == s2 == 200 and env2["meta"]["cache_hit"] is True
    n_calls = len(CALLS)
    s3, env3 = api.handle("GET", "/api/kb/qa",
                          {"q": ["M9 覆铜板价值量"], "mode": ["multi"], "guard": ["0"]},
                          ask_fn=mock_ask, multi_fn=mock_multi, guard_impl=_PASS_GUARD)
    assert s3 == 200 and env3["meta"]["cache_hit"] is False
    assert len(CALLS) == n_calls + 1
    assert env3["meta"]["guard"]["enabled"] is False


def test_real_guard_module_loads():
    mod = _REAL_LOAD_GUARD()
    assert hasattr(mod, "apply_guard") and hasattr(mod, "load_kb_ids")
    assert callable(mod.apply_guard)

# ---------------------------------------------------------------------------
# prompt / guard_marker 灰度参数（task-95）：默认 v1 + strict，可切换且缓存键区分
# ---------------------------------------------------------------------------
def test_prompt_and_guard_marker_defaults_and_switch():
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="M9 覆铜板价值量", mode="multi"),
                             ask_fn=mock_ask, multi_fn=mock_multi,
                             guard_impl=_PASS_GUARD)
    assert status == 200
    assert env["meta"]["prompt_version"] == "v1"
    assert env["meta"]["guard_marker"] == "strict"
    assert env["meta"]["guard"]["marker_mode"] == "strict_line"

    seen, guard_seen = {}, {}

    def rec_multi(question, k=8, timeout=300, **kw):
        seen.update(kw)
        return mock_multi(question, k=k, timeout=timeout)

    def rec_apply(result, unit_ids, book_ids, on_fail="fallback", **kw):
        guard_seen.update(kw)
        return _PASS_GUARD.apply_guard(result, unit_ids, book_ids, on_fail=on_fail)

    class _G:
        def load_kb_ids(self):
            return {"u1", "u2"}, {"b1"}

        def apply_guard(self, result, unit_ids, book_ids, **kw):
            return rec_apply(result, unit_ids, book_ids, **kw)

    api.reset_cache()
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="另一个问题", mode="multi", prompt="v4",
                                  guard_marker="segment"),
                             ask_fn=mock_ask, multi_fn=rec_multi, guard_impl=_G())
    assert status == 200
    assert seen.get("prompt_version") == "v4"
    assert guard_seen.get("marker_mode") == "segment"
    assert env["meta"]["prompt_version"] == "v4"
    assert env["meta"]["guard_marker"] == "segment"


def test_citation_retry_param_default_and_switch():
    """wave38：citation_retry 默认开；=0 可回滚；非法 400。"""
    seen = {}

    def rec_multi(question, k=8, timeout=300, **kw):
        seen.update(kw)
        return mock_multi(question, k=k, timeout=timeout)

    api.reset_cache()
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="retry 默认", mode="multi"),
                             ask_fn=mock_ask, multi_fn=rec_multi)
    assert status == 200
    assert env["meta"]["citation_retry"] is True
    assert seen.get("citation_retry") is True

    seen.clear()
    status, env = api.handle("GET", "/api/kb/qa", qmap(q="retry 默认", mode="multi"),
                             ask_fn=mock_ask, multi_fn=rec_multi)
    assert status == 200 and env["meta"]["cache_hit"] is True  # 同参命中缓存

    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="retry 关闭", mode="multi", citation_retry="0"),
                             ask_fn=mock_ask, multi_fn=rec_multi)
    assert status == 200 and env["meta"]["citation_retry"] is False
    status, env = api.handle("GET", "/api/kb/qa",
                             qmap(q="retry 非法", mode="multi", citation_retry="2"),
                             ask_fn=mock_ask, multi_fn=mock_multi)
    assert status == 400 and env["error"]["code"] == "INVALID_ARGUMENT"


def test_prompt_guard_marker_invalid_and_cache_key():
    for query in (qmap(q="问题", mode="multi", prompt="v9"),
                  qmap(q="问题", mode="multi", guard_marker="full")):
        status, env = api.handle("GET", "/api/kb/qa", query,
                                 ask_fn=mock_ask, multi_fn=mock_multi)
        assert status == 400 and env["error"]["code"] == "INVALID_ARGUMENT"
    # 缓存键包含 prompt/guard_marker：同问题不同灰度参数 → 不命中
    base_q = {"q": ["M9 覆铜板价值量"], "mode": ["multi"]}
    s1, _ = api.handle("GET", "/api/kb/qa", base_q, ask_fn=mock_ask, multi_fn=mock_multi,
                       guard_impl=_PASS_GUARD)
    s2, env2 = api.handle("GET", "/api/kb/qa",
                          {"q": ["M9 覆铜板价值量"], "mode": ["multi"], "prompt": ["v4"]},
                          ask_fn=mock_ask, multi_fn=mock_multi, guard_impl=_PASS_GUARD)
    assert s1 == s2 == 200 and env2["meta"]["cache_hit"] is False
