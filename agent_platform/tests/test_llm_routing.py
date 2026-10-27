# -*- coding: utf-8 -*-
"""MODEL_PROFILES 自动路由验收（10-18 llm.py 改造；10-27 B05 单轨化）

三层验证：
1. 离线：resolve_profile 精确/模糊/默认三档 + make_llm 前缀路由（零网络）
2. 离线：chat() 构造的 body 内 options 随 profile 变化（monkeypatch op.open）
   + 2026-10-27 B05：openai/ 委托 OpenClaw 载体（monkeypatch engine.openclaw_cli.run）、
     openai-direct/ 保留旧直连（env key 缺失 → LLMError）双前缀语义
3. live（--live 才跑）：9b 快判读 + 35b 长输出 + NovaMax 1238 三路真实请求

跑法（在 agent_platform 目录）：
    python -m pytest tests/test_llm_routing.py -v          # 离线层
    python tests/test_llm_routing.py --live                # live 层
"""

import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import llm as llm_mod  # noqa: E402
from engine import openclaw_cli as oc_mod  # noqa: E402  B05：OpenClaw 载体桥（monkeypatch run）


# ---------------------------------------------------------------- 离线层


def test_profile_exact():
    p = llm_mod.resolve_profile("qwen3.6:35b-a3b")
    # 守下限而不是锁死具体值：35b 实测生成 559s + 冷加载 38.5s，600s 只剩 41s 余量，
    # 因此 timeout 不得低于 600（当前实现为 900，判例见 engine/llm.py 注释）。
    # 不写死 900：超时是会随实测演进的运行参数，写死会让本测试与未提交的实现改动互相锁死。
    assert p["num_predict"] == 4000
    assert p["timeout"] >= 600, "35b 超时不得低于实测下限 600s（生成559s+冷加载38.5s）"


def test_profile_fuzzy_tag_variant():
    p = llm_mod.resolve_profile("qwen3.6:35b-a3b:latest")
    assert p["num_predict"] == 4000


def test_profile_default_for_unknown():
    p = llm_mod.resolve_profile("some-future-model:7b")
    assert p == llm_mod.DEFAULT_PROFILE


def test_profile_default_for_none():
    assert llm_mod.resolve_profile(None) == llm_mod.DEFAULT_PROFILE


def test_make_llm_routes_nova():
    l = llm_mod.make_llm("nova/1234/unsloth_Qwen3.5-35B-A3B-GGUF")
    assert isinstance(l, llm_mod.NovaMaxLLM) and l.port == 1234
    l2 = llm_mod.make_llm("nova/1238")
    assert isinstance(l2, llm_mod.NovaMaxLLM) and l2.port == 1238


def test_make_llm_routes_ollama():
    assert isinstance(llm_mod.make_llm("ollama/qwen3.5:9b"), llm_mod.OllamaLLM)
    # glm/ 路由见 test_make_llm_routes_glm_cloud；此处验证文档承诺的兜底行为：
    # 真名带斜杠的本地模型（如 huihui_ai/…）与裸模型名 → OllamaLLM 直连本地
    assert isinstance(
        llm_mod.make_llm("huihui_ai/Qwen3.8-abliterated:latest"), llm_mod.OllamaLLM
    )
    assert isinstance(llm_mod.make_llm("qwen3.5:9b"), llm_mod.OllamaLLM)


def test_make_llm_routes_glm_cloud():
    """10-18 分工定案：参谋长层 glm/ 前缀 → 云端 GLMCloudLLM"""
    l = llm_mod.make_llm("glm/glm-5.3")
    assert isinstance(l, llm_mod.GLMCloudLLM)
    assert l.model == "glm-5.3"
    assert l.ENDPOINT.startswith("https://open.bigmodel.cn")


def test_make_llm_routes_openai_via_openclaw():
    """2026-10-27 B05：openai/ → OpenClawLLM（OpenClaw agent-config 载体，不再直连）"""
    l = llm_mod.make_llm("openai/gpt-6.0")
    assert isinstance(l, llm_mod.OpenClawLLM)
    assert l.agent == "zhuge"
    # binding 里的模型名只作请求口径记录；实际模型由 agent-config 决定
    assert l.model == "gpt-6.0"
    assert not isinstance(l, llm_mod.OpenAILLM)


def test_make_llm_routes_openai_direct_to_legacy():
    """旧直连保留为 openai-direct/ 前缀（A2-B05 单轨化后的迁移/对照通道）"""
    l = llm_mod.make_llm("openai-direct/gpt-6.0")
    assert isinstance(l, llm_mod.OpenAILLM)
    assert l.model == "gpt-6.0"
    assert "openai.com" in l.DEFAULT_BASE


def test_openclaw_delegation_success(monkeypatch):
    """openai/ 委托 OpenClaw：run 返回 ("好", None) → chat 返回文本 + normal"""
    calls = {}

    def fake_run(agent, prompt, model=None, timeout=None):
        calls.update(agent=agent, prompt=prompt, timeout=timeout)
        return "好", None

    monkeypatch.setattr(oc_mod, "run", fake_run)
    monkeypatch.setattr(
        oc_mod, "agent_primary_model", lambda agent=None: "deepseek/deepseek-chat"
    )
    llm = llm_mod.make_llm("openai/gpt-6.0")
    text, status = llm.chat("你是军师", "给个结论")
    assert (text, status) == ("好", "normal")
    # 载体纪律：agent 固定 zhuge；prompt = system+user 拼单行
    assert calls["agent"] == "zhuge"
    assert "你是军师" in calls["prompt"] and "给个结论" in calls["prompt"]
    assert "\n" not in calls["prompt"]
    # carrier 元信息（诚实标签：实际模型 + 是否中转；运行时真源=agent-config）
    assert llm.last_meta["mode"] == "openclaw"
    assert llm.last_meta["agent"] == "zhuge"
    assert llm.last_meta["carrier"] == "deepseek/deepseek-chat"
    assert llm.last_meta["relay"] is True


def test_openclaw_delegation_failure_raises(monkeypatch):
    """载体失败/超时 → LLMError，不静默降档本地 9b（异族席纪律）"""

    def fake_run(agent, prompt, model=None, timeout=None):
        return None, "timeout"

    monkeypatch.setattr(oc_mod, "run", fake_run)
    monkeypatch.setattr(oc_mod, "agent_primary_model", lambda agent=None: "")
    llm = llm_mod.make_llm("openai/gpt-6.0")
    with pytest.raises(llm_mod.LLMError) as ei:
        llm.chat("s", "u")
    assert "timeout" in str(ei.value)
    assert llm.last_meta["mode"] == "openclaw" and llm.last_meta["error"] == "timeout"


def test_openclaw_empty_output_raises(monkeypatch):
    """空回（None/""/cli 的 empty_output 形态）一律 LLMError"""
    for ret in ((None, "empty_output"), ("", None), (None, None)):
        monkeypatch.setattr(
            oc_mod, "run", lambda agent, prompt, model=None, timeout=None, _r=ret: _r
        )
        monkeypatch.setattr(oc_mod, "agent_primary_model", lambda agent=None: "")
        with pytest.raises(llm_mod.LLMError):
            llm_mod.make_llm("openai/gpt-6.0").chat("s", "u")


def test_openclaw_prompt_single_line_and_truncated(monkeypatch):
    """prompt = system+user 拼单行，超长截 16000（口径与 8796 /api/chat 一致）"""
    captured = {}

    def fake_run(agent, prompt, model=None, timeout=None):
        captured["prompt"] = prompt
        return "ok", None

    monkeypatch.setattr(oc_mod, "run", fake_run)
    monkeypatch.setattr(oc_mod, "agent_primary_model", lambda agent=None: "")
    llm_mod.OpenClawLLM().chat("多行\n系统", "长" * 20000)
    p = captured["prompt"]
    assert "\n" not in p
    assert p.startswith("多行 系统 长")
    assert len(p) == llm_mod.OpenClawLLM.PROMPT_LIMIT == 16000


def test_ollamallm_delegates_openai_direct_prefix(monkeypatch):
    """OllamaLLM 兼容门面：openai-direct/ → 旧直连 OpenAILLM（迁移/对照通道）"""
    called = {}

    class FakeOpenAI:
        def __init__(self, model=None):
            called["model"] = model

        def chat(self, system, user, model=None):
            return "ok", "normal"

    monkeypatch.setattr(llm_mod, "OpenAILLM", FakeOpenAI)
    out, st = llm_mod.OllamaLLM().chat("s", "u", model="openai-direct/gpt-6.0")
    assert called["model"] == "gpt-6.0" and out == "ok" and st == "normal"


def test_openai_direct_no_key_raises(monkeypatch):
    """openai-direct/ 旧直连：key 缺失必须抛 LLMError，不静默降档（异族席位纪律）"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_GPT6_KEY", raising=False)
    with pytest.raises(llm_mod.LLMError):
        llm_mod.make_llm("openai-direct/gpt-6.0").chat("s", "u")


def test_openai_direct_chat_parses_response(monkeypatch):
    """离线校验旧直连 OpenAI 兼容请求体/鉴权头/响应解析（零网络）"""
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"total_tokens": 3},
                }
            ).encode("utf-8")

    class FakeOpener:
        def open(self, req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["auth"] = req.headers.get("Authorization")
            captured["timeout"] = timeout
            return FakeResp()

    monkeypatch.setattr(
        llm_mod.urllib.request, "build_opener", lambda handler=None: FakeOpener()
    )
    text, status = llm_mod.OpenAILLM(api_key="test-key").chat("s", "u")
    assert text == "ok" and status == "normal"
    assert captured["body"]["model"] == "gpt-6.0"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["auth"] == "Bearer test-key"


def test_ollamallm_delegates_openai_prefix(monkeypatch):
    """OllamaLLM 兼容门面：openai/ 前缀应委托 OpenClawLLM（不误走直连/本地）"""
    called = {}

    class FakeOpenClaw:
        def __init__(self, model=None):
            called["model"] = model

        def chat(self, system, user, model=None):
            called["chat"] = True
            return "ok", "normal"

    monkeypatch.setattr(llm_mod, "OpenClawLLM", FakeOpenClaw)
    out, st = llm_mod.OllamaLLM().chat("s", "u", model="openai/gpt-6.0")
    assert called["model"] == "gpt-6.0" and out == "ok" and st == "normal"


def test_glm_cloud_no_key_raises(monkeypatch):
    """key 缺失必须抛 LLMError，不静默降档（K-ORG-136 纪律）"""
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod.GLMCloudLLM, "_key", staticmethod(lambda: ""))
    try:
        llm_mod.GLMCloudLLM().chat("s", "u")
        raised = False
    except llm_mod.LLMError:
        raised = True
    assert raised, "key 缺失应抛 LLMError"


def test_route_chat_strips_ollama_prefix(monkeypatch):
    """a0_agent.route_chat 应剥离 ollama/ 前缀后传模型名给 OllamaLLM"""
    import a0_agent as a0_mod

    captured = {}

    class FakeOllama:
        def chat(self, system, user, model=None):
            captured["model"] = model
            return "ok", "normal"

    monkeypatch.setattr(a0_mod, "OllamaLLM", lambda: FakeOllama())
    a0_mod.route_chat("s", "u", model="ollama/qwen3.5:9b")
    assert captured["model"] == "qwen3.5:9b"


def test_ollamallm_delegates_glm_prefix(monkeypatch):
    """OllamaLLM 兼容门面：glm/ 前缀应委托 GLMCloudLLM（interpreter 通用路径云端生效的关键）"""
    called = {}

    class FakeGLM:
        def __init__(self, model=None):
            called["model"] = model

        def chat(self, system, user, model=None):
            called["chat"] = True
            return "ok", "normal"

    monkeypatch.setattr(llm_mod, "GLMCloudLLM", FakeGLM)
    out, st = llm_mod.OllamaLLM().chat("s", "u", model="glm/glm-5.3")
    assert called["model"] == "glm-5.3" and out == "ok" and st == "normal"


def test_ollamallm_delegates_nova_prefix(monkeypatch):
    class FakeNova:
        def __init__(self, port=1238, timeout=600, retries=1):
            called["port"] = port

        def chat(self, system, user, model=None):
            called["chat"] = True
            return "ok", "normal"

    called = {}
    monkeypatch.setattr(llm_mod, "NovaMaxLLM", FakeNova)
    llm_mod.OllamaLLM().chat("s", "u", model="nova/1234/x")
    assert str(called["port"]) == "1234"  # 真类会 int 化,Fake 收原始串


def test_ollamallm_keeps_slashed_local_names(monkeypatch):
    """huihui_ai/… 真名带斜杠的本地模型不得被误分发"""
    llm_mod.OllamaLLM()
    prof = llm_mod.resolve_profile("huihui_ai/Qwen3.8-abliterated:latest")
    assert prof == llm_mod.DEFAULT_PROFILE  # 走默认档直连本地,不进 glm/nova 分支


def test_chat_body_uses_profile(monkeypatch):
    """monkeypatch 掉网络层，断言 body 内 options 随模型档位变化"""
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {
                    "message": {"content": "ok"},
                    "done_reason": "stop",
                    "eval_count": 10,
                }
            ).encode("utf-8")

    def fake_open(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResp()

    import urllib.request

    orig = urllib.request.build_opener

    class FakeOpener:
        def open(self, req, timeout=None):
            return fake_open(req, timeout)

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda handler=None: FakeOpener()
    )

    llm = llm_mod.OllamaLLM()
    llm.chat("s", "u", model="qwen3.6:35b-a3b")
    assert captured["body"]["options"]["num_predict"] == 4000
    assert captured["timeout"] >= 600, "35b 超时不得低于实测下限 600s"

    llm.chat("s", "u", model="qwen3.5:9b")
    assert captured["body"]["options"]["num_predict"] == 400
    assert captured["timeout"] == 300

    monkeypatch.setattr(urllib.request, "build_opener", orig)


def test_chat_respects_caller_timeout_cap(monkeypatch):
    """A2-B01：调用方显式 timeout 是硬上限，不得被 profile(300s) 覆盖。

    L5 本地军师(25s)/意图分类(12s) 曾因此最坏挂 ~601s。
    """
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"message": {"content": "ok"}, "done_reason": "stop"}
            ).encode("utf-8")

    class FakeOpener:
        def open(self, req, timeout=None):
            captured["timeout"] = timeout
            return FakeResp()

    import urllib.request

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda handler=None: FakeOpener()
    )

    llm_mod.OllamaLLM(timeout=12).chat("s", "u", model="qwen3.5:9b")
    assert captured["timeout"] == 12, "12s 预算必须被尊重"
    llm_mod.OllamaLLM(timeout=25).chat("s", "u", model="qwen3.5:9b")
    assert captured["timeout"] == 25
    # 未显式给 timeout 时仍按模型档（9b=300s），保持原行为
    llm_mod.OllamaLLM().chat("s", "u", model="qwen3.5:9b")
    assert captured["timeout"] == 300


# ---------------------------------------------------------------- live 层


def _live():
    print("== live 1/3: ollama qwen3.5:9b 快判读 ==")
    llm = llm_mod.OllamaLLM()
    text, status = llm.chat(
        "你是测试员，只回复一个词：正常", "测试", model="qwen3.5:9b"
    )
    print("   status=%s meta=%s text=%r" % (status, llm.last_meta, text[:60]))
    assert status == "normal" and text.strip()

    print("== live 1.5/3: glm 云端（参谋长席位通道）==")
    try:
        gl = llm_mod.make_llm("glm/glm-5.3")
        textg, statusg = gl.chat("只回复一个词：正常", "测试")
        print("   status=%s meta=%s text=%r" % (statusg, gl.last_meta, textg[:60]))
    except llm_mod.LLMError as e:
        print("   SKIP（key 未配置或通道不可用）: %s" % e)

    print("== live 2/3: ollama qwen3.6:35b-a3b 长输出（蒸馏 teacher 岗）==")
    llm2 = llm_mod.OllamaLLM()
    text2, status2 = llm2.chat(
        "你是 A0 参谋长。用 3 段、每段 2-3 句点评：本地模型自动路由对 AI 组织的意义。",
        "开始点评",
        model="qwen3.6:35b-a3b",
    )
    print("   status=%s meta=%s text=%r" % (status2, llm2.last_meta, text2[:120]))
    assert status2 == "normal" and len(text2) > 100, "35b 应产出长文且未被截断"

    print("== live 3/3: nova 1238 结构化 ==")
    try:
        nv = llm_mod.make_llm("nova/1238")
        text3, status3 = nv.chat('只回复 JSON: {"ok": true}', "测试")
        print("   status=%s meta=%s text=%r" % (status3, nv.last_meta, text3[:60]))
    except llm_mod.LLMError as e:
        print("   SKIP（NovaMax 1238 未运行，watchdog 会按需拉起）: %s" % e)

    print("== 全部通过 ==")


if __name__ == "__main__":
    if "--live" in sys.argv:
        _live()
    else:
        print("离线层请用: python -m pytest tests/test_llm_routing.py -v")
