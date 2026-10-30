# -*- coding: utf-8 -*-
"""JEV 风险告警桥纯函数单测（JEVALERT）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_risk_alert as ra  # noqa: E402


def _cand(nid="a", conf=0.9, impact="high"):
    return {"news_id": nid, "ts": "2026-10-30 14:00:00", "source": "sina",
            "title": "某公司被立案调查", "bias": "bear", "conf": conf,
            "impact": impact, "severity": "high", "event_type": "risk",
            "horizon": "days", "codes": ["600519"], "reason": "监管处罚"}


def test_select_new_dedup():
    state = {"seen": ["a"]}
    assert ra.select_new([_cand("a"), _cand("b")], state) == [_cand("b")]


def test_flag_text_contains_evidence():
    text = ra.flag_text([_cand("a")])
    assert "JEV 风险告警" in text and "利空·high" in text
    assert "600519" in text and "低信号事件已排除" in text


def test_emit_outbox_appends(tmp_path):
    path = tmp_path / "risk.jsonl"
    n = ra.emit_outbox([_cand("a"), _cand("b")], path)
    assert n == 2 and len(path.read_text(encoding="utf-8").splitlines()) == 2
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["news_id"] == "a" and row["bias"] == "bear"


def test_cli_entry_present():
    assert 'if __name__ == "__main__":' in Path(ra.__file__).read_text(encoding="utf-8")