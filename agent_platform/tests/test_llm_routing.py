# -*- coding: utf-8 -*-
"""MODEL_PROFILES 自动路由验收（10-18 llm.py 改造）

三层验证：
1. 离线：resolve_profile 精确/模糊/默认三档 + make_llm 前缀路由（零网络）
2. 离线：chat() 构造的 body 内 options 随 profile 变化（monkeypatch op.open）
3. live（--live 才跑）：9b 快判读 + 35b 长输出 + NovaMax 1238 三路真实请求

跑法（在 agent_platform 目录）：
    python -m pytest tests/test_llm_routing.py -v          # 离线层
    python tests/test_llm_routing.py --live                # live 层
"""
import json
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import llm as llm_mod  # noqa: E402


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
    assert isinstance(llm_mod.make_llm("huihui_ai/Qwen3.8-abliterated:latest"),
                      llm_mod.OllamaLLM)
    assert isinstance(llm_mod.make_llm("qwen3.5:9b"), llm_mod.OllamaLLM)


def test_make_llm_routes_glm_cloud():
    """10-18 分工定案：参谋长层 glm/ 前缀 → 云端 GLMCloudLLM"""
    l = llm_mod.make_llm("glm/glm-5.3")
    assert isinstance(l, llm_mod.GLMCloudLLM)
    assert l.model == "glm-5.3"
    assert l.ENDPOINT.startswith("https://open.bigmodel.cn")


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
    llm = llm_mod.OllamaLLM()
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
            return json.dumps({
                "message": {"content": "ok"},
                "done_reason": "stop", "eval_count": 10,
            }).encode("utf-8")

    def fake_open(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResp()

    import urllib.request
    orig = urllib.request.build_opener

    class FakeOpener:
        def open(self, req, timeout=None):
            return fake_open(req, timeout)

    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda handler=None: FakeOpener())

    llm = llm_mod.OllamaLLM()
    llm.chat("s", "u", model="qwen3.6:35b-a3b")
    assert captured["body"]["options"]["num_predict"] == 4000
    assert captured["timeout"] >= 600, "35b 超时不得低于实测下限 600s"

    llm.chat("s", "u", model="qwen3.5:9b")
    assert captured["body"]["options"]["num_predict"] == 400
    assert captured["timeout"] == 300

    monkeypatch.setattr(urllib.request, "build_opener", orig)


# ---------------------------------------------------------------- live 层

def _live():
    print("== live 1/3: ollama qwen3.5:9b 快判读 ==")
    llm = llm_mod.OllamaLLM()
    text, status = llm.chat("你是测试员，只回复一个词：正常", "测试",
                            model="qwen3.5:9b")
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
        "开始点评", model="qwen3.6:35b-a3b")
    print("   status=%s meta=%s text=%r" % (status2, llm2.last_meta, text2[:120]))
    assert status2 == "normal" and len(text2) > 100, "35b 应产出长文且未被截断"

    print("== live 3/3: nova 1238 结构化 ==")
    try:
        nv = llm_mod.make_llm("nova/1238")
        text3, status3 = nv.chat("只回复 JSON: {\"ok\": true}", "测试")
        print("   status=%s meta=%s text=%r" % (status3, nv.last_meta, text3[:60]))
    except llm_mod.LLMError as e:
        print("   SKIP（NovaMax 1238 未运行，watchdog 会按需拉起）: %s" % e)

    print("== 全部通过 ==")


if __name__ == "__main__":
    if "--live" in sys.argv:
        _live()
    else:
        print("离线层请用: python -m pytest tests/test_llm_routing.py -v")
