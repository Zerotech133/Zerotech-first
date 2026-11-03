# -*- coding: utf-8 -*-
"""部门负责人云端模型访问：绑定真源校验。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform"))

from legion_command import serve  # noqa: E402

LEADS = ["D1", "ARCH1", "DATA1", "PM1", "QA1", "DEV3",
         "MKT1", "LEG1", "A0A", "ACAD1", "INTEL0", "LUSU1", "R3"]


def test_all_department_leads_use_cloud_binding():
    for lead in LEADS:
        binding = serve.resolve_binding(lead)
        assert binding.startswith("glm/"), (lead, binding)
        assert not binding.startswith("ollama/"), (lead, binding)


def test_meeting_resolver_keeps_leads_cloud():
    for lead in LEADS:
        assert serve.resolve_meeting_binding(lead).startswith("glm/"), lead


def test_cloud_binding_has_local_fallback():
    from legion_command import hq_meeting  # noqa: F401  确保包可导入
    for lead in LEADS:
        chain = serve.resolve_fallbacks(lead)
        assert "ollama/qwen3.5:9b" in chain, (lead, chain)
