# -*- coding: utf-8 -*-
"""change_gate 单测：快照/回归/回滚。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import change_gate as cg  # noqa: E402


def test_snapshot_check_and_rollback(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("版本一", encoding="utf-8")
    store = tmp_path / "store"
    cg.snapshot("ai1", prompt, 0.9, "score", "m1", "base", store)
    assert cg.check("ai1", 0.9, prompt, 0.0, store)["ok"] is True
    assert cg.check("ai1", 0.8, prompt, 0.0, store)["ok"] is False
    assert cg.check("ai1", 0.85, prompt, 0.1, store)["ok"] is True
    prompt.write_text("版本二", encoding="utf-8")
    out = cg.rollback("ai1", prompt, store)
    assert out["ok"] is True
    assert prompt.read_text(encoding="utf-8") == "版本一"
    assert out["backup"]


def test_check_missing_baseline(tmp_path):
    out = cg.check("nope", 1.0, tmp_path / "p.md", 0.0, tmp_path / "s")
    assert out["ok"] is False and out["error"] == "baseline_not_found"
