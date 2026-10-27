# -*- coding: utf-8 -*-
"""WO-LEGION-S21 军团门面测试：信封/LLM 门面/投递幂等/HTTP 端点。

承重纪律：先红后绿已在本文件编写过程中执行（envelope 校验各失败分支
在实现前先写用例确认失败形态）；MockLLM 注入保证不依赖外部 Ollama。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, llm_gateway  # noqa: E402
from gateway.envelope import (EnvelopeError, make_envelope,  # noqa: E402
                              validate_envelope)
from gateway.serve import build_server  # noqa: E402


# ---------------------------------------------------------------- 信封

def test_envelope_ok_shape():
    env = make_envelope(kind="llm_chat", ok=True, data={"text": "hi"})
    validate_envelope(env)
    assert env["schema_version"] == "1.0"
    assert env["degraded"] is False


def test_envelope_reject_missing_key():
    env = make_envelope(kind="x", ok=True)
    del env["ts"]
    with pytest.raises(EnvelopeError):
        validate_envelope(env)


def test_envelope_reject_ok_with_error():
    env = make_envelope(kind="x", ok=True,
                        error={"code": "c", "message": "m"})
    with pytest.raises(EnvelopeError):
        validate_envelope(env)


def test_envelope_reject_fail_without_error():
    env = make_envelope(kind="x", ok=False, data={"a": 1})
    with pytest.raises(EnvelopeError):
        validate_envelope(env)


def test_envelope_reject_bad_version_and_latency():
    env = make_envelope(kind="x", ok=True)
    env["schema_version"] = "9.9"
    with pytest.raises(EnvelopeError):
        validate_envelope(env)
    env2 = make_envelope(kind="x", ok=True, latency_ms=-1)
    with pytest.raises(EnvelopeError):
        validate_envelope(env2)


# ---------------------------------------------------------------- LLM 门面

def test_llm_chat_mock_roundtrip(monkeypatch):
    class StubLLM:
        def chat(self, system, user, model=None):
            return "ok", "normal"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: StubLLM())
    env = llm_gateway.chat(binding="ollama/qwen3.5:9b",
                           system="s", user="回声")
    validate_envelope(env)
    assert env["ok"] is True
    assert env["data"]["text"] == "ok"
    assert env["data"]["status"] == "normal"
    assert env["latency_ms"] >= 0


def test_llm_chat_degraded_passthrough(monkeypatch):
    class DegradedLLM:
        def chat(self, system, user, model=None):
            return "fallback text", "degraded"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: DegradedLLM())
    env = llm_gateway.chat(binding="glm/glm-5.3", system="", user="u")
    validate_envelope(env)
    assert env["ok"] is True and env["degraded"] is True
    assert env["data"]["status"] == "degraded"


def test_llm_chat_openclaw_metadata_passthrough(monkeypatch):
    """OpenClaw 载体元信息（carrier/relay/mode/agent）随信封透传，供 /llm/chat 核验。"""
    class OpenClawStub:
        last_meta = {"mode": "openclaw", "agent": "zhuge",
                     "carrier": "deepseek/deepseek-chat", "relay": True,
                     "requested_model": "gpt-6.0"}

        def chat(self, system, user, model=None):
            return "真身/中转答案", "normal"

    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: OpenClawStub())
    env = llm_gateway.chat(binding="openai/gpt-6.0", system="", user="u")
    validate_envelope(env)
    assert env["ok"] is True
    assert env["data"]["carrier"] == "deepseek/deepseek-chat"
    assert env["data"]["relay"] is True
    assert env["data"]["mode"] == "openclaw"
    assert env["data"]["agent"] == "zhuge"


def test_llm_chat_no_meta_keeps_legacy_data(monkeypatch):
    class PlainStub:
        def chat(self, system, user, model=None):
            return "ok", "normal"

    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: PlainStub())
    env = llm_gateway.chat(binding="ollama/qwen3.5:9b", system="", user="u")
    validate_envelope(env)
    assert "carrier" not in env["data"] and "relay" not in env["data"]


def test_llm_chat_error_no_throw(monkeypatch):
    from engine.llm import LLMError
    def boom(binding):
        raise RuntimeError("构造坏")
    monkeypatch.setattr(llm_gateway, "make_llm", boom)
    env = llm_gateway.chat(binding="nova/not-a-port")
    validate_envelope(env)
    assert env["ok"] is False
    assert env["error"]["code"] == "llm_error"

    class DeadLLM:
        def chat(self, system, user, model=None):
            raise LLMError("重试耗尽")
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: DeadLLM())
    env2 = llm_gateway.chat(binding="ollama/qwen3.5:9b", system="", user="x")
    validate_envelope(env2)
    assert env2["ok"] is False and "耗尽" in env2["error"]["message"]


def test_llm_chat_default_binding_used(monkeypatch):
    seen = {}
    class Spy:
        def chat(self, system, user, model=None):
            seen["model"] = model
            return "t", "normal"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: Spy())
    llm_gateway.chat(system="", user="u")
    # 默认档是 ollama/*：必须把**剥掉前缀后的真模型名**传下去（本地才有这个模型）。
    # 旧断言写死整串 DEFAULT_BINDING，等于把「前缀当模型名」的缺陷固化成契约。
    assert llm_gateway.DEFAULT_BINDING.startswith("ollama/")
    assert seen["model"] == llm_gateway.DEFAULT_BINDING.split("/", 1)[1]


def test_llm_chat_glm_does_not_repass_prefixed_model(monkeypatch):
    """glm/nova/openai 的模型已在 make_llm 构造时绑定，chat 不得再传整串 binding。

    2026-10-21 真实缺陷：`glm/glm-5.3` 整串被当模型名发往智谱 → HTTP 400，
    进而把 S38「glm-5.3-flash 模型名不存在」误判成模型能力问题。
    2026-10-27 B05：openai/（OpenClaw 载体）与 openai-direct/（旧直连）同样
    在构造时吃下模型，不得把整串 binding 当 model 传下去。
    """
    seen = {}
    class Spy:
        def __init__(self, binding):
            self.binding = binding
        def chat(self, system, user, model=None):
            seen[self.binding] = model
            return "t", "normal"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: Spy(binding))
    for b in ("glm/glm-5.3", "glm/glm-5.3-flash", "nova/8080",
              "openai/gpt-6.0", "openai-direct/gpt-6.0"):
        env = llm_gateway.chat(binding=b, system="", user="u")
        assert env["ok"] is True
        assert seen[b] is None, "%s 不应把整串 binding 当 model 传入（实测会 400）" % b


def test_gateway_prefix_owns_model_includes_openai_family():
    """B05：openai/（OpenClaw 载体）与 openai-direct/（旧直连）都在前缀清单内"""
    assert {"openai", "openai-direct"} <= set(llm_gateway._PREFIX_OWNS_MODEL)


def test_llm_chat_openai_delegates_openclaw(monkeypatch):
    """B05 集成：门面 openai/ → engine OpenClawLLM → OpenClaw 载体 run（同一轨道）"""
    from engine import openclaw_cli as oc_mod
    monkeypatch.setattr(oc_mod, "run",
                        lambda agent, prompt, model=None, timeout=None: ("好", None))
    monkeypatch.setattr(oc_mod, "agent_primary_model",
                        lambda agent=None: "deepseek/deepseek-chat")
    env = llm_gateway.chat(binding="openai/gpt-6.0", system="你是军师", user="给个结论")
    validate_envelope(env)
    assert env["ok"] is True
    assert env["data"]["text"] == "好" and env["data"]["status"] == "normal"


def test_llm_chat_openai_direct_key_missing_is_envelope_error(monkeypatch):
    """B05：openai-direct/ 旧直连 key 缺失 → 快速信封错误，不降档、不抛穿"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_GPT6_KEY", raising=False)
    env = llm_gateway.chat(binding="openai-direct/gpt-6.0", system="", user="u")
    validate_envelope(env)
    assert env["ok"] is False and env["error"]["code"] == "llm_error"
    assert "key" in env["error"]["message"]


def test_llm_chat_unknown_prefix_passes_verbatim(monkeypatch):
    """未知前缀（如 huihui_ai/xxx 这类真名带斜杠的本地模型）必须原样传，不许截断。"""
    seen = {}
    class Spy:
        def chat(self, system, user, model=None):
            seen["model"] = model
            return "t", "normal"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: Spy())
    llm_gateway.chat(binding="huihui_ai/qwen3-abliterated:9b", system="", user="u")
    assert seen["model"] == "huihui_ai/qwen3-abliterated:9b"


# ---------------------------------------------------------------- 投递门面

@pytest.fixture()
def store(tmp_path):
    inbox_gateway.set_store(tmp_path)
    yield tmp_path
    inbox_gateway._DEFAULT_STORE = None


def test_submit_and_receipt_roundtrip(store):
    env = inbox_gateway.submit("question", {"q": "今天该干嘛"},
                               source="feishu", idempotency_key="fs-001")
    validate_envelope(env)
    assert env["ok"] is True
    msg_id = env["data"]["id"]
    assert env["data"]["replayed"] is False

    rec = inbox_gateway.receipt(msg_id)
    validate_envelope(rec)
    assert rec["data"]["state"] == "inbox"
    assert rec["data"]["record"]["payload"]["q"] == "今天该干嘛"


def test_submit_idempotent_replay(store):
    e1 = inbox_gateway.submit("task_request", {"t": 1}, idempotency_key="dup-1")
    e2 = inbox_gateway.submit("task_request", {"t": 1}, idempotency_key="dup-1")
    assert e1["data"]["id"] == e2["data"]["id"]
    assert e2["data"]["replayed"] is True
    # 只落一份盘
    files = list((store / "inbox").glob("*.json"))
    assert len(files) == 1


def test_submit_replay_after_processed(store):
    e1 = inbox_gateway.submit("feedback", {"f": "x"}, idempotency_key="mv-1")
    msg_id = e1["data"]["id"]
    src = store / "inbox" / ("%s.json" % msg_id)
    dst = store / "processed" / ("%s.json" % msg_id)
    src.rename(dst)
    e2 = inbox_gateway.submit("feedback", {"f": "x"}, idempotency_key="mv-1")
    assert e2["data"]["replayed"] is True
    assert e2["data"]["state"] == "processed"


def test_submit_rejects_bad_kind_and_payload(store):
    env = inbox_gateway.submit("not_a_kind", {})
    assert env["ok"] is False and env["error"]["code"] == "bad_kind"
    env2 = inbox_gateway.submit("question", ["not", "dict"])
    assert env2["ok"] is False and env2["error"]["code"] == "bad_payload"


def test_receipt_not_found(store):
    env = inbox_gateway.receipt("lg-nonexistent")
    validate_envelope(env)
    assert env["ok"] is True
    assert env["data"]["state"] == "not_found"


# ---------------------------------------------------------------- HTTP 端点

@pytest.fixture()
def server(store, monkeypatch):
    class StubLLM:
        def chat(self, system, user, model=None):
            return "pong", "normal"
    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: StubLLM())
    srv = build_server(port=0)  # 随机空闲端口
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(base, path, obj):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_http_health(server):
    out = _get(server, "/health")
    assert out["ok"] is True and out["service"] == "legion-gateway"


def test_http_llm_chat(server):
    env = _post(server, "/llm/chat", {"system": "s", "user": "ping"})
    validate_envelope(env)
    assert env["ok"] is True and env["data"]["text"] == "pong"


def test_http_inbox_flow(server):
    env = _post(server, "/inbox/submit",
                {"kind": "question", "payload": {"q": 1}, "source": "feishu"})
    validate_envelope(env)
    msg_id = env["data"]["id"]
    rec = _get(server, "/inbox/receipt/%s" % msg_id)
    validate_envelope(rec)
    assert rec["data"]["state"] == "inbox"


def test_http_404_and_400(server):
    try:
        urllib.request.urlopen(server + "/nope", timeout=10)
        raised = False
    except urllib.error.HTTPError as e:
        raised = (e.code == 404)
    assert raised
    try:
        urllib.request.urlopen(
            server + "/llm/chat",
            data=b"\xff\xfe not json", timeout=10)
        raised = False
    except urllib.error.HTTPError as e:
        raised = (e.code == 400)
    assert raised
