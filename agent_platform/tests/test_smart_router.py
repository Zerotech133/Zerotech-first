# -*- coding: utf-8 -*-
"""SmartRouter 单测（零网络）：关键词路由/等级兜底/注册绑定优先/云端熔断/自动适配"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

import router as router_mod  # noqa: E402


def test_route_respects_registered_binding():
    r = router_mod.route("随便什么任务", respect_binding="glm/glm-5.3")
    assert r["binding"] == "glm/glm-5.3" and "尊重注册绑定" in r["reason"]


def test_route_auto_keyword_cloud_heavy():
    r = router_mod.route("对研发方案做一次走形检查并复盘", respect_binding="auto")
    assert r["binding"] == "glm/glm-5.3"
    assert "关键词命中" in r["reason"]


def test_route_auto_keyword_dispatch_flash():
    r = router_mod.route("把各部门产出归集汇总做初筛", respect_binding="auto")
    assert r["binding"] == "glm/glm-5.3-flash"


def test_route_auto_keyword_vision_special():
    r = router_mod.route("识别这张截图里的图表走势", respect_binding="auto")
    assert r["binding"] == "nova/1234"


def test_route_auto_keyword_local_data():
    r = router_mod.route("抓取今日行情数据清洗后入库", respect_binding="auto")
    assert r["binding"] == "ollama/qwen3.5:9b"


def test_route_auto_keyword_distill():
    r = router_mod.route("跑一轮 SFT 蒸馏生成训练数据集", respect_binding="auto")
    assert r["binding"] == "ollama/qwen3.6:35b-a3b"


def test_route_auto_keyword_embed():
    r = router_mod.route("把这批文档算嵌入向量", respect_binding="auto", task_type="embed")
    assert r["binding"] == "ollama/bge-m3"


def test_route_grade_fallback():
    r = router_mod.route("处理一下这个事", respect_binding="auto", grade="E3")
    assert r["binding"] == "glm/glm-5.3"
    r2 = router_mod.route("处理一下这个事", respect_binding="auto", grade="E2")
    assert r2["binding"] == "ollama/qwen3.5:9b"


def test_route_cloud_breaker_downgrades(monkeypatch):
    h = router_mod.ChannelHealth(fail_threshold=2, cooldown=60)
    h.record_fail(); h.record_fail()
    monkeypatch.setattr(router_mod, "CLOUD_HEALTH", h)
    r = router_mod.route("对方案做深度判读", respect_binding="auto")
    assert r["binding"] == "ollama/qwen3.5:9b" and "熔断" in r["reason"]


def test_auto_assign_preview_no_write():
    roster = [
        {"agent_id": "A0", "role_profile": "chief_of_staff", "grade": "E3",
         "dept": "chief", "binding": "glm/glm-5.3"},
        {"agent_id": "DEV2", "role_profile": "model_trainer", "grade": "E1",
         "dept": "dev", "binding": "ollama/qwen3.5:9b"},
        {"agent_id": "DATA1", "role_profile": "data_engineer", "grade": "E1",
         "dept": "data", "binding": "ollama/qwen3.5:9b"},
        {"agent_id": "SEC2", "role_profile": "security_lead", "grade": "E3",
         "dept": "quality", "binding": "ollama/qwen3.5:9b"},
    ]
    wrote = []
    out = router_mod.auto_assign(roster, apply=True, write_fn=lambda *a: wrote.append(a))
    m = {o["agent_id"]: o for o in out}
    assert m["A0"]["new"] == "glm/glm-5.3" and not m["A0"]["changed"]
    assert m["DEV2"]["new"] == "ollama/qwen3.6:35b-a3b" and m["DEV2"]["changed"]
    assert m["DATA1"]["new"] == "ollama/qwen3.5:9b" and not m["DATA1"]["changed"]
    assert m["SEC2"]["new"] == "glm/glm-5.3" and m["SEC2"]["changed"]
    assert len(wrote) == 2  # 只写变化的


def test_auto_assign_craft_protection_and_chief_line():
    """工匠保护：E3 工程岗也不上云；E1 参谋线走 flash 档"""
    roster = [
        {"agent_id": "DEV1", "role_profile": "code_engineer", "grade": "E3",
         "dept": "dev", "binding": "ollama/qwen3.5:9b"},
        {"agent_id": "A0A", "role_profile": "chief_assistant", "grade": "E1",
         "dept": "chief", "binding": "glm/glm-5.3-flash"},
        {"agent_id": "A0", "role_profile": "chief_of_staff", "grade": "E3",
         "dept": "chief", "binding": "glm/glm-5.3"},
    ]
    out = router_mod.auto_assign(roster, apply=False)
    m = {o["agent_id"]: o for o in out}
    assert m["DEV1"]["new"] == "ollama/qwen3.5:9b" and not m["DEV1"]["changed"]
    assert m["A0A"]["new"] == "glm/glm-5.3-flash" and not m["A0A"]["changed"]
    assert m["A0"]["new"] == "glm/glm-5.3" and not m["A0"]["changed"]


def test_provider_packing():
    assert router_mod._pack("glm/glm-5.3", ["x"])["provider"] == "cloud-zhipu"
    assert router_mod._pack("nova/1234", ["x"])["provider"] == "special-novamax"
    assert router_mod._pack("ollama/qwen3.5:9b", ["x"])["provider"] == "local-ollama"
    # 2026-10-27 B05：openai/ 已是 OpenClaw agent-config 载体（与 L5/8796 同源），
    # /api/route 预览不得再显示直连 cloud-openai；直连旧通道只在 engine 层保留。
    assert router_mod._pack("openai/gpt-6.0", ["x"])["provider"] == "openclaw"
    assert router_mod._pack("openai-direct/gpt-6.0", ["x"])["provider"] == "cloud-openai-direct"


# ---------------------------------------------------------------- 异族绑定（B 案）

def test_route_exotic_ai1_openai():
    """AI1=诸葛亮：10-21 异族编制裁决优先于关键词/等级/旧 glm 注册绑定"""
    r = router_mod.route("方法论研究与评测", agent_id="AI1", respect_binding="auto")
    assert r["binding"] == "openai/gpt-6.0"
    assert r["provider"] == "openclaw"      # B05：载体口径，非直连 cloud-openai
    assert "异族编制裁决" in r["reason"]
    # 即使显式传了 10-18 的 glm 旧档，也以异族裁决为准
    r2 = router_mod.route("随便什么任务", agent_id="AI1",
                          respect_binding="glm/glm-5.3")
    assert r2["binding"] == "openai/gpt-6.0"
    assert r2["provider"] == "openclaw"


def test_auto_assign_ai1_openai():
    """auto_assign 不得把 AI1 从 openai 改回 glm（10-26 B 案回归）"""
    roster = [
        {"agent_id": "AI1", "role_profile": "ai_methodology_researcher",
         "grade": "E3", "dept": "academy", "binding": "glm/glm-5.3"},
        {"agent_id": "A0", "role_profile": "chief_of_staff", "grade": "E3",
         "dept": "chief", "binding": "glm/glm-5.3"},
    ]
    out = router_mod.auto_assign(roster, apply=False)
    m = {o["agent_id"]: o for o in out}
    assert m["AI1"]["new"] == "openai/gpt-6.0" and m["AI1"]["changed"]
    assert m["A0"]["new"] == "glm/glm-5.3" and not m["A0"]["changed"]
