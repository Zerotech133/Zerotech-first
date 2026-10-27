# -*- coding: utf-8 -*-
"""席位级 fallback 链验收（WO-LEGION-DEVEXEC2-20261027）。

承重口径：glm 主绑失败不再硬编码降 `ollama/qwen3.5:9b`，而是按 org.json
`departments[].agent_profiles[].llm_fallback` 的**有序链**降级；无链席位保持旧行为
（只降默认 9b 一次）。全部离线：
  · DeepSeek 通道只做构造期路由 + 桩 client 契约验收，不发任何网络请求、不读真实密钥；
  · `_seat_chat` 用注入的 chat_fn 直接验收四类分支（首/次 fallback、全失败、默认档）；
  · `fallbacks_index` 用 tmp 迷你 org 夹具验收归一规则（过滤/去重/缺省/先到先得）。
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

from engine import org_profiles as P  # noqa: E402
from engine.llm import DeepSeekLLM, LLMError, OllamaLLM, make_llm  # noqa: E402
from gateway import llm_gateway  # noqa: E402
from gateway.envelope import make_envelope  # noqa: E402
from legion_command import serve as srv  # noqa: E402


# ---------------------------------------------------------------- 夹具/工具


def _write_org(root: Path, departments: list) -> Path:
    """写迷你 org.json（只含 departments），返回文件路径。"""
    (root / "registry").mkdir(parents=True, exist_ok=True)
    p = root / "registry" / "org.json"
    p.write_text(
        json.dumps({"departments": departments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def _ok_env(text="答复", status="normal", binding=None):
    """成功信封；binding 显式给才带 binding_resolved（用于验「补齐」分支）。"""
    data = {"text": text, "status": status}
    if binding:
        data["binding_resolved"] = binding
    return make_envelope(
        kind="llm_chat", ok=True, degraded=(status == "degraded"), data=data
    )


def _fail_env(message="HTTP 429 限额"):
    return make_envelope(
        kind="llm_chat", ok=False, error={"code": "llm_error", "message": message}
    )


# ---------------------------------------------------------------- fallbacks_index


def test_fallbacks_index_reads_ordered_chain(tmp_path):
    """正常 list：顺序即优先级，必须原样保序返回。"""
    _write_org(
        tmp_path,
        [
            {
                "dept": "dev",
                "agent_profiles": [
                    {
                        "agent_id": "DEV1",
                        "llm_binding": "glm/glm-5.3",
                        "llm_fallback": [
                            "deepseek/deepseek-flash",
                            "ollama/qwen3.5:9b",
                        ],
                    },
                ],
            }
        ],
    )
    assert P.fallbacks_index(tmp_path)["DEV1"] == [
        "deepseek/deepseek-flash",
        "ollama/qwen3.5:9b",
    ]


def test_fallbacks_index_defaults_to_empty_list(tmp_path):
    """有档案但没配 llm_fallback → 空 list（空链 ≠ 无档案）；缺 org.json → 空索引。"""
    _write_org(
        tmp_path,
        [
            {
                "dept": "ops",
                "agent_profiles": [
                    {"agent_id": "OPS0", "llm_binding": "ollama/qwen3.5:9b"},
                ],
            }
        ],
    )
    assert P.fallbacks_index(tmp_path) == {"OPS0": []}
    assert P.fallbacks_index(tmp_path / "nowhere") == {}


def test_fallbacks_index_filters_and_dedupes(tmp_path):
    """非串/空串一律过滤；去重保序（同一 binding 只留首次出现）。"""
    _write_org(
        tmp_path,
        [
            {
                "dept": "dev",
                "agent_profiles": [
                    {
                        "agent_id": "DEV2",
                        "llm_fallback": [
                            "deepseek/deepseek-flash",
                            "",
                            "   ",
                            None,
                            42,
                            {"model": "ollama/qwen3.5:9b"},
                            ["ollama/qwen3.5:9b"],
                            "  deepseek/deepseek-flash  ",
                            "ollama/qwen3.5:9b",
                        ],
                    },
                ],
            }
        ],
    )
    assert P.fallbacks_index(tmp_path)["DEV2"] == [
        "deepseek/deepseek-flash",
        "ollama/qwen3.5:9b",
    ]


def test_fallbacks_index_first_occurrence_wins(tmp_path):
    """同 key 先到先得（与 profile_index 同策略）：后出现的档案不得改写降级顺序。"""
    _write_org(
        tmp_path,
        [
            {
                "dept": "d1",
                "agent_profiles": [
                    {"agent_id": "X1", "llm_fallback": ["deepseek/deepseek-flash"]}
                ],
            },
            {
                "dept": "d2",
                "agent_profiles": [
                    {"agent_id": "X1", "llm_fallback": ["ollama/qwen3.5:9b"]},
                    {"agent_id": "X2", "llm_fallback": "ollama/qwen3.5:9b"},
                ],
            },
        ],
    )
    idx = P.fallbacks_index(tmp_path)
    assert idx["X1"] == ["deepseek/deepseek-flash"]
    # 单串写法容错成单元素链（真源标准是 list，容忍历史手写）
    assert idx["X2"] == ["ollama/qwen3.5:9b"]


def test_fallbacks_index_readonly(tmp_path):
    """只读：跑一遍不得改动真源字节。"""
    p = _write_org(
        tmp_path,
        [
            {
                "dept": "d",
                "agent_profiles": [
                    {"agent_id": "X1", "llm_fallback": ["deepseek/deepseek-flash"]}
                ],
            }
        ],
    )
    before = p.read_bytes()
    P.fallbacks_index(tmp_path)
    assert p.read_bytes() == before


def test_real_org_e3_chain_available():
    """真实盘面回归锁：glm 主绑席位已配降级链，且链首不是主绑本身（链有效）。"""
    fb = P.fallbacks_index(_PLATFORM)
    prof = P.profile_index(_PLATFORM)
    chained = {a: chain for a, chain in fb.items() if chain}
    assert chained, "真实 org.json 至少要有一个席位配了 llm_fallback"
    for aid, chain in chained.items():
        binding = (prof.get(aid) or {}).get("llm_binding") or ""
        assert chain[0] != binding, "%s 降级链首项与主绑相同（链无效）" % aid
    assert any(c[0].startswith("deepseek/") for c in chained.values()), (
        "研发部 E3 席的降级链首项应为 deepseek/deepseek-flash"
    )


# ---------------------------------------------------------------- DeepSeek 通道


def test_make_llm_routes_deepseek():
    """deepseek/ → DeepSeekLLM（不是 OllamaLLM 兜底）；构造期零网络零 key 读取。"""
    llm = make_llm("deepseek/deepseek-flash")
    assert isinstance(llm, DeepSeekLLM)
    assert not isinstance(llm, OllamaLLM)
    assert llm.model == "deepseek-flash"
    # 缺模型名走缺省档（default 9b 语义不适用于云端通道）
    assert make_llm("deepseek").model == "deepseek-flash"
    # 不误伤本地兜底
    assert isinstance(make_llm("ollama/qwen3.5:9b"), OllamaLLM)


class _StubClient:
    """DeepSeekClient 桩：只验适配器契约（messages/model/预算/状态二值）。"""

    def __init__(self, content="云端降级答复", finish="stop", raises=None):
        self.content = content
        self.finish = finish
        self.raises = raises
        self.calls = []

    def chat_full(
        self,
        messages,
        model=None,
        temperature=0.2,
        max_tokens=800,
        response_format=None,
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
            }
        )
        if self.raises is not None:
            raise self.raises
        return {
            "content": self.content,
            "usage": {"total_tokens": 7},
            "model": model,
            "finish_reason": self.finish,
        }


def test_deepseek_llm_chat_contract():
    client = _StubClient()
    llm = DeepSeekLLM(model="deepseek-flash", client=client)
    text, status = llm.chat("你是田丰", "给个结论")
    assert (text, status) == ("云端降级答复", "normal")
    call = client.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["messages"][0] == {"role": "system", "content": "你是田丰"}
    assert call["messages"][-1] == {"role": "user", "content": "给个结论"}
    # 推理模型 token 预算判例：一句话级 ≥64、结构化 ≥512；默认档不得太小
    assert call["max_tokens"] >= 512
    assert llm.last_meta["finish_reason"] == "stop"


def test_deepseek_llm_status_is_binary():
    """status 二值：finish_reason=="length" 视为截断 → degraded，其余 normal。"""
    _, st = DeepSeekLLM(client=_StubClient(finish="length")).chat("s", "u")
    assert st == "degraded"
    _, st2 = DeepSeekLLM(client=_StubClient(finish="stop")).chat("s", "u")
    assert st2 == "normal"


def test_deepseek_llm_empty_content_raises():
    """假成功判例：HTTP 200 但正文为空 ≠ 可用，必须抛 LLMError（不冒充成功）。"""
    with pytest.raises(LLMError):
        DeepSeekLLM(client=_StubClient(content="   ")).chat("s", "u")


def test_deepseek_llm_client_error_raises_llm_error():
    from engine import deepseek as D

    stub = _StubClient(raises=D.DeepSeekError("HTTP 429 限额"))
    with pytest.raises(LLMError) as ei:
        DeepSeekLLM(client=stub).chat("s", "u")
    assert "429" in str(ei.value)


def test_deepseek_llm_missing_key_raises_llm_error(monkeypatch):
    """缺 key → 构造客户端失败归一为 LLMError；不降档、不触网。"""
    from engine import deepseek as D

    def boom(env=None, explicit_file=None):
        raise D.DeepSeekError("缺少 DeepSeek key（离线验收桩）")

    monkeypatch.setattr(D, "resolve_api_key", boom)
    with pytest.raises(LLMError) as ei:
        DeepSeekLLM().chat("s", "u")
    assert "deepseek" in str(ei.value)


# ---------------------------------------------------------------- 门面前缀口径


def test_prefix_owns_model_includes_deepseek():
    assert "deepseek" in llm_gateway._PREFIX_OWNS_MODEL


def test_gateway_deepseek_does_not_repass_binding(monkeypatch):
    """门面不得把整串 `deepseek/deepseek-flash` 当 model 传（模型已在构造期吃下）。"""
    seen = {}

    class Spy:
        def chat(self, system, user, model=None):
            seen["model"] = model
            return "t", "normal"

    monkeypatch.setattr(llm_gateway, "make_llm", lambda binding: Spy())
    env = llm_gateway.chat(binding="deepseek/deepseek-flash", system="", user="u")
    assert env["ok"] is True
    assert seen["model"] is None


# ---------------------------------------------------------------- _seat_chat 四分支


def test_seat_chat_primary_ok_untouched():
    """主绑成功：不标降级、不多调一次、不碰 data（旧行为不变）。"""
    env, attempts = srv._seat_chat(
        lambda **kw: _ok_env("直答"),
        "glm/glm-5.3",
        ["deepseek/deepseek-flash"],
        "s",
        "u",
    )
    assert attempts == ["glm/glm-5.3"]
    assert env["degraded"] is False and "fallback_from" not in env


def test_seat_chat_first_fallback_wins():
    """场景 1：首个 fallback 成功即止，degraded/from/reason/chain 全部标齐。"""
    chain = ["deepseek/deepseek-flash", "ollama/qwen3.5:9b"]
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        if binding == "glm/glm-5.3":
            return _fail_env("HTTP 429 限额")
        if binding == "deepseek/deepseek-flash":
            return _ok_env("DeepSeek 降级答复")  # 故意不带 binding_resolved
        raise AssertionError("首个 fallback 成功就不该再试 %s" % binding)

    env, attempts = srv._seat_chat(chat_fn, "glm/glm-5.3", chain, "s", "u")
    assert calls == ["glm/glm-5.3", "deepseek/deepseek-flash"]
    assert attempts == calls
    assert env["ok"] is True and env["degraded"] is True
    assert env["fallback_from"] == "glm/glm-5.3"
    assert "429" in env["fallback_reason"]
    assert env["fallback_chain"] == ["deepseek/deepseek-flash"]
    # 补齐：底层没给 binding_resolved 时补成实际生效的 fallback
    assert env["data"]["binding_resolved"] == "deepseek/deepseek-flash"


def test_seat_chat_second_fallback_wins():
    """场景 2：deepseek 也失败 → 落本地 9b；chain 记录两条已尝试档。"""
    chain = ["deepseek/deepseek-flash", "ollama/qwen3.5:9b"]
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        if binding == "glm/glm-5.3":
            return _fail_env("glm 额度耗尽")
        if binding == "deepseek/deepseek-flash":
            return _fail_env("deepseek 缺 key")
        return _ok_env("本地 9b 答复", binding=binding)

    env, attempts = srv._seat_chat(chat_fn, "glm/glm-5.3", chain, "s", "u")
    assert calls == ["glm/glm-5.3", "deepseek/deepseek-flash", "ollama/qwen3.5:9b"]
    assert attempts == calls
    assert env["ok"] is True and env["degraded"] is True
    assert env["fallback_from"] == "glm/glm-5.3"
    assert env["fallback_reason"] == "glm 额度耗尽"
    assert env["fallback_chain"] == chain
    assert env["data"]["binding_resolved"] == "ollama/qwen3.5:9b"


def test_seat_chat_all_fallbacks_fail_returns_primary_error():
    """场景 3：全失败 → 返回**主失败** env（不是末条 fallback 的错）+ 完整 attempts。"""
    chain = ["deepseek/deepseek-flash", "ollama/qwen3.5:9b"]
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        return _fail_env(
            "glm 主绑错误" if binding == "glm/glm-5.3" else "%s 也失败" % binding
        )

    env, attempts = srv._seat_chat(chat_fn, "glm/glm-5.3", chain, "s", "u")
    assert calls == ["glm/glm-5.3", "deepseek/deepseek-flash", "ollama/qwen3.5:9b"]
    assert attempts == calls, "attempts 必须完整（排障要看试过哪些档）"
    assert env["ok"] is False
    assert env["error"]["message"] == "glm 主绑错误"
    assert env["degraded"] is False
    assert "fallback_from" not in env and "fallback_chain" not in env


def test_seat_chat_no_fallback_uses_default_9b():
    """场景 4：无 llm_fallback → 旧行为，只降默认 9b 一次（不静默消失）。"""
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        if binding == "glm/glm-5.3":
            return _fail_env("HTTP 429")
        return _ok_env("本地兜底", binding=binding)

    env, attempts = srv._seat_chat(chat_fn, "glm/glm-5.3", [], "s", "u")
    assert calls == ["glm/glm-5.3", "ollama/qwen3.5:9b"]
    assert attempts == calls
    assert env["degraded"] is True
    assert env["fallback_chain"] == ["ollama/qwen3.5:9b"]
    assert env["data"]["binding_resolved"] == "ollama/qwen3.5:9b"


def test_seat_chat_custom_default_fallback():
    """default_fallback 可注入（会议层之外复用时不锁死 9b）。"""
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        return _fail_env() if binding.startswith("glm/") else _ok_env(binding=binding)

    env, _ = srv._seat_chat(
        chat_fn,
        "glm/glm-5.3",
        None,
        "s",
        "u",
        default_fallback="ollama/qwen3.6:35b-a3b",
    )
    assert calls[-1] == "ollama/qwen3.6:35b-a3b"
    assert env["fallback_chain"] == ["ollama/qwen3.6:35b-a3b"]


def test_seat_chat_non_glm_failure_no_fake_degrade():
    """非 glm 失败原样透传：一次 fallback 都不调（本地失败就是失败，fail-visible）。"""
    calls = []

    def chat_fn(binding=None, system="", user=""):
        calls.append(binding)
        return _fail_env("本地重试耗尽")

    env, attempts = srv._seat_chat(
        chat_fn, "ollama/qwen3.5:9b", ["deepseek/deepseek-flash"], "s", "u"
    )
    assert calls == ["ollama/qwen3.5:9b"]
    assert attempts == ["ollama/qwen3.5:9b"]
    assert env["ok"] is False and env["error"]["message"] == "本地重试耗尽"
    assert "fallback_from" not in env


# ---------------------------------------------------------------- 缓存/接线


def test_resolve_fallbacks_cache_and_invalidation(tmp_path):
    """与 resolve_binding 同一 stamp：返回副本、改档后失效、无档返回 []。"""
    _write_org(
        tmp_path,
        [
            {
                "dept": "dev",
                "agent_profiles": [
                    {
                        "agent_id": "Z9",
                        "llm_binding": "glm/glm-5.3",
                        "llm_fallback": ["deepseek/deepseek-flash"],
                    }
                ],
            }
        ],
    )
    srv._BINDING_CACHE.update(mtime=None, map={})
    assert srv.resolve_fallbacks("Z9", platform_dir=tmp_path) == [
        "deepseek/deepseek-flash"
    ]
    # 副本语义：调用方就地改不污染缓存
    srv.resolve_fallbacks("Z9", platform_dir=tmp_path).append("x")
    assert srv.resolve_fallbacks("Z9", platform_dir=tmp_path) == [
        "deepseek/deepseek-flash"
    ]
    # 改档后指纹失效（内容长度变化确保 size 指纹变化）
    _write_org(
        tmp_path,
        [
            {
                "dept": "dev",
                "agent_profiles": [
                    {
                        "agent_id": "Z9",
                        "llm_binding": "glm/glm-5.3",
                        "llm_fallback": [
                            "ollama/qwen3.5:9b",
                            "deepseek/deepseek-v4-pro",
                        ],
                    }
                ],
            }
        ],
    )
    assert srv.resolve_fallbacks("Z9", platform_dir=tmp_path) == [
        "ollama/qwen3.5:9b",
        "deepseek/deepseek-v4-pro",
    ]
    assert srv.resolve_fallbacks("NOPE", platform_dir=tmp_path) == []


@pytest.fixture()
def server():
    srv_obj = srv.build_server(port=0)
    th = threading.Thread(target=srv_obj.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv_obj.server_address[1]
    srv_obj.shutdown()
    srv_obj.server_close()


def _post(base, path, obj):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def test_http_chat_walks_seat_chain(server, monkeypatch):
    """端到端接线：/api/chat 的 glm 失败块必须走 resolve_fallbacks 给的席位链。"""
    calls = []

    def fake_chat(binding=None, system="", user=""):
        calls.append(binding)
        if binding.startswith("glm/"):
            return _fail_env("HTTP 429 限额")
        if binding.startswith("deepseek/"):
            return _ok_env("DeepSeek 云答复")
        return _ok_env("本地 9b 答复", binding=binding)

    monkeypatch.setattr(llm_gateway, "chat", fake_chat)
    monkeypatch.setattr(
        srv,
        "resolve_fallbacks",
        lambda agent, platform_dir=None: [
            "deepseek/deepseek-flash",
            "ollama/qwen3.5:9b",
        ],
    )
    env = _post(server, "/api/chat", {"agent": "A0", "system": "s", "user": "u"})
    assert calls == ["glm/glm-5.3", "deepseek/deepseek-flash"]
    assert env["ok"] is True and env["degraded"] is True
    assert env["agent"] == "A0"
    assert env["fallback_from"] == "glm/glm-5.3"
    assert env["fallback_chain"] == ["deepseek/deepseek-flash"]
    # 响应 binding 仍取 data.binding_resolved（由 _seat_chat 补齐）
    assert env["binding"] == "deepseek/deepseek-flash"
