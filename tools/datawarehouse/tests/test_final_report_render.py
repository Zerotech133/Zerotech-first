# -*- coding: utf-8 -*-
"""final_report_render 单元测试：纯函数渲染，不依赖真实数据。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/datawarehouse

from final_report_render import render  # noqa: E402


def _accept(verdict="PASS"):
    return {
        "verdict": verdict,
        "counts": {"pass": 9, "fail": 0, "expected-risk": 1, "unknown": 0, "total": 10},
        "gates": [
            {"gate": "G1", "status": "pass", "reason": "四批 rc=0"},
            {"gate": "G8", "status": "expected-risk", "reason": "A3 rc=1"},
        ],
    }


def test_render_contains_verdict_and_gates():
    md = render(_accept("PASS"), {"K8": {"name": "资质 unknown", "value": 14, "status": "ok"}}, "20261103")
    assert "**PASS**" in md
    assert "| G1 |" in md and "| G8 |" in md and "expected-risk" in md
    assert "K8" in md and "资质 unknown" in md


def test_render_fail_verdict():
    md = render(_accept("FAIL"), {}, "20261103")
    assert "**FAIL**" in md
