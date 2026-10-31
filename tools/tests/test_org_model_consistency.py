# -*- coding: utf-8 -*-
"""org_model_consistency（K-ORG-20261031-ORMMODEL）单测。

全部用 tmp_path 合成最小仓库树，不读/写真实 org/hr/heartbeat 内容；
覆盖：一致、hr 冲突、心跳单值 warn/probe_only info、hr 未落具体 info、
AI1 declared/effective 特例、未知席、bundle 漂移、档位禁用、严格模式、
空数据/缺输入、JSON 与人类摘要输出。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import org_model_consistency as m  # noqa: E402


# ---------------------------------------------------------------- fixtures
def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _write_resume(root, seat, model):
    path = root / "agent_platform" / "hr" / seat / "resume.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "agent_id: %s\nname: %s\nidentity:\n  model: %s\n  born: 2026-10-01\n"
        % (seat, seat, model), encoding="utf-8")


def _default_org():
    return {
        "chief": {"agent_id": "A0", "llm_binding": "glm/glm-5.3"},
        "departments": [{
            "dept": "invest",
            "agents": ["D1", "T1", "AI1", "QS1"],
            "agent_profiles": [
                {"agent_id": "D1", "llm_binding": "glm/glm-5.3", "grade": "E3"},
                {"agent_id": "T1", "llm_binding": "ollama/qwen3.5:9b", "grade": "E2"},
                {"agent_id": "AI1", "llm_binding": "openai/gpt-6.0", "grade": "E3",
                 "binding_note": "2026-10-29 拍板 A：effective=deepseek/deepseek-chat 中转，declared target 保留 openai/gpt-6.0"},
                {"agent_id": "QS1", "llm_binding": "none", "grade": "E1"},
            ],
        }],
    }


def _default_tiers():
    return {
        "version": "v1",
        "models": {
            "glm/glm-5.3": {"tier": "strong", "enabled": True},
            "qwen3.5:9b": {"tier": "local", "enabled": True},
            "deepseek/deepseek-flash": {"tier": "fast", "enabled": True},
            "deepseek/deepseek-chat": {"tier": "fast", "enabled": True},
            "openai/gpt-6.0": {"tier": "strong", "enabled": False},
        },
        "grade_policy": {"E3": {"allow": ["strong"]}},
    }


def _default_heartbeat():
    return {"model": "deepseek-flash", "model_role": "probe_only",
            "seats": ["D1", "T1"]}


def _default_bundle():
    return {"agents": {
        "A0": {"llm": "glm/glm-5.3"},
        "D1": {"llm": "glm/glm-5.3"},
        "T1": {"llm": "ollama/qwen3.5:9b"},
        "AI1": {"llm": "openai/gpt-6.0"},
        "QS1": {"llm": ""},
    }}


def _make_root(tmp_path, *, org=None, tiers=None, heartbeat=None, bundle=None,
               hr_models=None):
    root = tmp_path / "repo"
    _write_json(root / m.ORG_REL, _default_org() if org is None else org)
    _write_json(root / m.TIERS_REL, _default_tiers() if tiers is None else tiers)
    _write_json(root / m.HEARTBEAT_REL,
                _default_heartbeat() if heartbeat is None else heartbeat)
    _write_json(root / m.BUNDLE_REL, _default_bundle() if bundle is None else bundle)
    models = {
        "D1": "glm/glm-5.3",
        "T1": "ollama/qwen3.5:9b",
        "AI1": "openai/gpt-6.0（declared；effective=deepseek/deepseek-chat 中转）",
        "QS1": "none（运行时席，llm=none）",
    }
    if hr_models:
        models.update(hr_models)
    for seat, model in models.items():
        _write_resume(root, seat, model)
    return root


def _rules(result, level=None):
    return [i["rule"] for i in result["issues"]
            if level is None or i["level"] == level]


# ---------------------------------------------------------------- R1 一致
def test_consistent_synthetic(tmp_path):
    root = _make_root(tmp_path)
    result = m.run(root)
    assert result["ok"] is True and result["consistent"] is True
    assert result["summary"]["fail"] == 0
    assert result["summary"]["warn"] == 0
    assert result["exit_code"] == 0
    assert result["summary"]["seats"] == 4
    json.dumps(result, ensure_ascii=False)  # 可序列化
    # 人类摘要 + 严格模式仍 0（无 warn）
    assert m.main(["--root", str(root), "--strict"]) == 0


# ---------------------------------------------------------------- R4 hr 冲突
def test_hr_model_conflict_fails(tmp_path):
    root = _make_root(tmp_path)
    _write_resume(root, "D1", "ollama/qwen3.5:9b")
    result = m.run(root)
    assert result["ok"] is False and result["exit_code"] == 1
    fails = [i for i in result["issues"] if i["level"] == "fail"]
    assert fails and fails[0]["rule"] == "hr_model_conflict"
    assert fails[0]["seat"] == "D1"
    assert fails[0]["org"] == "glm/glm-5.3"
    assert m.main(["--root", str(root)]) == 1


# ---------------------------------------------------------------- R6 心跳
def test_heartbeat_single_model_warn_default_zero_strict_one(tmp_path):
    root = _make_root(tmp_path, heartbeat={
        "model": "deepseek-flash", "seats": ["D1", "T1"]})
    result = m.run(root)
    assert result["ok"] is True  # 无 fail
    assert result["summary"]["warn"] >= 1
    assert "heartbeat_single_model" in _rules(result, "warn")
    assert result["exit_code"] == 0
    assert m.main(["--root", str(root)]) == 0
    assert m.main(["--root", str(root), "--strict"]) == 1


def test_heartbeat_probe_only_is_info(tmp_path):
    root = _make_root(tmp_path)  # 默认已是 probe_only
    result = m.run(root, strict=True)
    assert result["exit_code"] == 0
    assert "heartbeat_single_model" in _rules(result, "info")
    assert result["summary"]["warn"] == 0


# ---------------------------------------------------------------- R5 gap
def test_hr_gap_is_info_and_consistent(tmp_path):
    root = _make_root(tmp_path, hr_models={
        "T1": "按工单通道（chat_openai 等 5 通道经 _persona 统一注入）"})
    result = m.run(root)
    assert result["ok"] is True and result["exit_code"] == 0
    assert "hr_model_gap" in _rules(result, "info")


# ---------------------------------------------------------------- R7 AI1
def test_ai1_declared_effective_allowed(tmp_path):
    root = _make_root(tmp_path)
    result = m.run(root)
    assert result["ok"] is True
    assert "ai1_declared_effective" in _rules(result, "info")
    # 即使 hr 写的是别的具体模型，AI1 declared/effective 特例也不判 fail
    _write_resume(root, "AI1", "ollama/qwen3.5:9b")
    again = m.run(root)
    assert again["ok"] is True
    assert "ai1_declared_effective" in _rules(again, "info")


# ---------------------------------------------------------------- R3 未知席
def test_unknown_seat_fails(tmp_path):
    root = _make_root(tmp_path)
    _write_resume(root, "ZZZ", "glm-5.3")
    result = m.run(root)
    assert result["ok"] is False
    unknown = [i for i in result["issues"]
               if i["rule"] == "unknown_seat" and i["seat"] == "ZZZ"]
    assert unknown and unknown[0]["source"] == "hr"
    # bundle 未知席同样 fail
    root2 = _make_root(tmp_path / "b")
    bundle = _default_bundle()
    bundle["agents"]["YYY"] = {"llm": "glm/glm-5.3"}
    _write_json(root2 / m.BUNDLE_REL, bundle)
    result2 = m.run(root2)
    assert any(i["rule"] == "unknown_seat" and i["seat"] == "YYY"
               for i in result2["issues"] if i["level"] == "fail")


def test_extra_known_seat_is_info_not_fail(tmp_path):
    """ZHAOYUN 等已登记非编制角色出现在 bundle：info，不判未知席 fail。"""
    root = _make_root(tmp_path)
    bundle = _default_bundle()
    bundle["agents"]["ZHAOYUN"] = {"llm": "deepseek/deepseek-v4-pro"}
    _write_json(root / m.BUNDLE_REL, bundle)
    result = m.run(root)
    assert result["ok"] is True and result["exit_code"] == 0
    assert "known_extra_seat" in _rules(result, "info")
    assert not [i for i in result["issues"]
                if i["rule"] == "unknown_seat" and i["seat"] == "ZHAOYUN"]


# ---------------------------------------------------------------- R8/R9
def test_bundle_drift_and_tier_disabled_fail(tmp_path):
    root = _make_root(tmp_path)
    bundle = _default_bundle()
    bundle["agents"]["D1"] = {"llm": "qwen3.5:9b"}
    _write_json(root / m.BUNDLE_REL, bundle)
    result = m.run(root)
    assert any(i["level"] == "fail" and i["rule"] == "bundle_drift"
               and i["seat"] == "D1" for i in result["issues"])

    root2 = _make_root(tmp_path / "t")
    tiers = _default_tiers()
    tiers["models"]["glm/glm-5.3"]["enabled"] = False
    _write_json(root2 / m.TIERS_REL, tiers)
    result2 = m.run(root2)
    assert any(i["level"] == "fail" and i["rule"] == "tier_policy"
               and i["seat"] == "D1" for i in result2["issues"])


def test_e3_policy_warn_and_exception(tmp_path):
    org = _default_org()
    org["departments"][0]["agent_profiles"].append(
        {"agent_id": "SEC2", "llm_binding": "deepseek/deepseek-flash", "grade": "E3"})
    org["departments"][0]["agents"].append("SEC2")
    root = _make_root(tmp_path, org=org, hr_models={"SEC2": "deepseek/deepseek-flash"})
    bundle = _default_bundle()
    bundle["agents"]["SEC2"] = {"llm": "deepseek/deepseek-flash"}
    _write_json(root / m.BUNDLE_REL, bundle)
    result = m.run(root)
    assert result["ok"] is True
    e3_warn = [i for i in result["issues"]
               if i["rule"] == "tier_policy" and i["level"] == "warn"
               and i["seat"] == "SEC2"]
    assert e3_warn
    assert m.main(["--root", str(root), "--strict"]) == 1
    # 显式例外 → info，不再 warn
    excepted = m.run(root, e3_exceptions={"SEC2": "测试登记例外"})
    assert not [i for i in excepted["issues"] if i["rule"] == "tier_policy"
                and i["level"] == "warn" and i["seat"] == "SEC2"]
    assert "e3_policy_exception" in _rules(excepted, "info")


# ---------------------------------------------------------------- 空/缺输入
def test_missing_inputs_and_empty_data(tmp_path):
    # 只有 org.json：tiers/heartbeat/bundle/hr 全缺
    root = tmp_path / "only_org"
    _write_json(root / m.ORG_REL, _default_org())
    result = m.run(root)
    assert result["ok"] is False
    assert any(i["rule"] == "tier_policy" and i["level"] == "fail"
               for i in result["issues"] if i["seat"] in ("", "D1"))
    assert any(i["rule"] in ("heartbeat_missing", "hr_missing")
               and i["level"] == "warn" for i in result["issues"])
    # 空数据不崩
    empty = m.audit({"org": {"profiles": {}, "known": set()}})
    assert empty["ok"] is False
    assert "input_required" in _rules(empty, "fail")
    json.dumps(empty, ensure_ascii=False)
    # root 不存在 → 输入错误退出码 2
    assert m.main(["--root", str(tmp_path / "no_such_repo"), "--json"]) == 2


# ---------------------------------------------------------------- 输出
def test_json_and_human_output(tmp_path, capsys):
    root = _make_root(tmp_path)
    assert m.main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == m.SCHEMA
    assert payload["exit_code"] == 0 and payload["ok"] is True
    assert "summary" in payload and "issues" in payload
    assert m.main(["--root", str(root)]) == 0
    text = capsys.readouterr().out
    assert "org_model_consistency" in text and "结论" in text


def test_normalize_and_concrete_helpers():
    assert m.normalize_model("ollama/qwen3.5:9b") == "qwen3.5:9b"
    assert m.normalize_model("GLM/GLM-5.3") == "glm-5.3"
    # 长 token 优先：flash 不会被拆成 glm-5.3 + glm-5.3-flash
    assert m.concrete_models("glm/glm-5.3-flash") == ["glm-5.3-flash"]
    assert m.concrete_models("openai/gpt-6.0（declared；effective=deepseek/deepseek-chat）") == [
        "deepseek-chat", "gpt-6.0"]
    assert m.is_descriptive("按工单通道") is True
    assert m.is_descriptive("none（运行时席）") is True
    assert m.is_descriptive("glm/glm-5.3") is False
