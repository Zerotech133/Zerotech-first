# -*- coding: utf-8 -*-
"""T5 第1步：carrier-managed（openai/）席 E3 fallback 豁免验收。

被测：agent_platform/tools/model_binding_check.py 的 E3 fallback 规则。
隔离：tmp 内存夹具（不触碰真仓 registry）。
覆盖：
  ①AI1 openai/ 载体席缺 org fallback → **不判违规**，给 carrier_managed warn；
  ②glm/ 等普通 E3 缺 fallback → 仍判 violation（防豁免过宽）；
  ③openai/ 载体席已有 fallback → 不多发 carrier warn（兼容既有 warnings 计数）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "model_binding_check.py"
_spec = importlib.util.spec_from_file_location(
    "model_binding_check_carrier", str(_TOOL)
)
MBC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MBC)

TIERS = {
    "registry": "test_model_tiers",
    "version": "v1",
    "models": {
        "glm/glm-5.3": {"tier": "strong", "enabled": True},
        "glm/glm-5.3-flash": {"tier": "fast", "enabled": True},
        "deepseek/deepseek-flash": {"tier": "fast", "enabled": True},
        "openai/gpt-6.0": {"tier": "strong", "enabled": False},
    },
    "grade_policy": {
        "E3": {"label": "30年档 · 裁定", "allow": ["strong"], "default": "glm/glm-5.3"},
        "E2": {
            "label": "20年档 · 独立表态",
            "allow": ["strong", "fast"],
            "default": "glm/glm-5.3",
        },
        "E1": {
            "label": "10年档 · 执行",
            "allow": ["fast", "local"],
            "default": "glm/glm-5.3-flash",
        },
    },
    "degraded_fallback": "qwen3.5:9b",
}


def _org(profiles):
    return {
        "departments": [
            {
                "dept": "academy",
                "lead": "AI1",
                "agents": [p["agent_id"] for p in profiles],
                "agent_profiles": profiles,
            }
        ]
    }


def test_openai_carrier_exempt_from_fallback():
    prof = {"agent_id": "AI1", "grade": "E3", "llm_binding": "openai/gpt-6.0"}
    rep = MBC.check(_org([prof]), TIERS)
    assert rep["summary"]["violations"] == 0, rep["violations"]
    assert any(w.get("kind") == "carrier_managed" for w in rep["warnings"]), rep[
        "warnings"
    ]


def test_non_carrier_e3_without_fallback_still_violation():
    prof = {"agent_id": "LEG1", "grade": "E3", "llm_binding": "glm/glm-5.3"}
    rep = MBC.check(_org([prof]), TIERS)
    assert rep["summary"]["violations"] == 1
    assert rep["violations"][0]["kind"] == "fallback_missing"


def test_openai_carrier_with_fallback_no_extra_carrier_warn():
    prof = {
        "agent_id": "AI1",
        "grade": "E3",
        "llm_binding": "openai/gpt-6.0",
        "llm_fallback": ["glm/glm-5.3"],
    }
    rep = MBC.check(_org([prof]), TIERS)
    assert rep["summary"]["violations"] == 0
    assert not any(w.get("kind") == "carrier_managed" for w in rep["warnings"]), rep[
        "warnings"
    ]
