# -*- coding: utf-8 -*-
"""PREFILTERON 回归：JEV 子进程 env 默认 safe、父值可覆盖；wrapper 静态检查。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import night_batch as nb  # noqa: E402


def test_jev_child_env_default_safe(monkeypatch):
    monkeypatch.delenv("JEV_PREFILTER", raising=False)
    assert nb._jev_child_env()["JEV_PREFILTER"] == "safe"


def test_jev_child_env_respects_parent(monkeypatch):
    monkeypatch.setenv("JEV_PREFILTER", "0")
    assert nb._jev_child_env()["JEV_PREFILTER"] == "0"


def test_wrappers_enable_safe_prefilter():
    repo = Path(__file__).resolve().parents[2]
    for rel in ("jev-eval/start_jev_night.ps1", "jev-eval/jev_backfill_task.ps1"):
        text = (repo / rel).read_text(encoding="utf-8")
        assert "JEV_PREFILTER" in text and "safe" in text, rel
