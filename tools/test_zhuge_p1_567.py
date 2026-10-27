# -*- coding: utf-8 -*-
"""P1-6/P1-7 离线单测：规划分解 + 自我反思（不联网/不调模型）。"""
import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plan = _load("zhuge_planner")
refl = _load("zhuge_reflector")


def test_plan_classifies_and_has_steps():
    p = plan.build_plan("数据库连接失败怎么排查")
    assert p["ok"] is True and p["type"] == "diagnose"
    assert 2 <= len(p["steps"]) <= 6
    for s in p["steps"]:
        assert s["title"] and s["action"] and s["verify"]
    assert p["executable"] is False and p["read_only"] is True


def test_plan_other_types():
    assert plan.build_plan("帮我做一个评测方案")["type"] == "evaluate"
    assert plan.build_plan("选型：向量库怎么选")["type"] == "decide"
    assert plan.build_plan("今天天气")["type"] == "plan"


def test_plan_empty_fail_visible():
    p = plan.build_plan("   ")
    assert p["ok"] is False and p["error"] == "empty_question"


def test_reflect_empty_fails():
    r = refl.reflect("")
    assert r["verdict"] == "fail" and r["consistent"] is False
    assert any(i["code"] == "empty" for i in r["issues"])


def test_reflect_citation_pass():
    r = refl.reflect("目标对象未被修改 [S1]，建议先取证。",
                     sources=[{"id": "S1", "path": "x"}])
    assert r["checks"]["citation_count"] == 1
    assert not any(i["code"] == "ungrounded_factual" for i in r["issues"])


def test_reflect_ungrounded_flagged():
    r = refl.reflect("这是一段没有任何引用的结论，长度也够二十个字了。",
                     sources=[{"id": "S1", "path": "x"}])
    assert any(i["code"] == "ungrounded_factual" for i in r["issues"])


def test_reflect_uncertainty_flagged():
    r = refl.reflect("这个方案可能是对的，也许可以再等等看效果。")
    assert any(i["code"] == "uncertainty_without_evidence" for i in r["issues"])


def test_reflect_contradiction_with_memory():
    r = refl.reflect("这个问题没有异常。",
                     memories=[{"text": "这个问题存在异常，需要排查。"}])
    assert r["checks"]["memory_checked"] == 1


def test_reflect_note_and_cli_roundtrip():
    rep = refl.reflect("")
    assert "[自检]" in refl.to_note(rep)
