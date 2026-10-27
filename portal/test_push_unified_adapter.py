# -*- coding: utf-8 -*-
"""P2 适配器单测：disabled 原样 / shadow 只记录 / enabled 换新卡 / 异常回落。"""
import json
import os
import tempfile
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import feishu_push as fp  # noqa: E402
import push_unified_adapter as A  # noqa: E402


@pytest.fixture()
def cfg(monkeypatch):
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "push_unified.json")
    monkeypatch.setattr(fp, "UNIFIED_CFG", path)
    yield path


def _write(path, **kw):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kw, f, ensure_ascii=False)


LEGACY = {"header": {"title": {"tag": "plain_text", "content": "旧卡"}}}
SECTIONS = ["**结论** 今日候选 3 只", "**要点** T1 共振 1 只"]


def test_disabled_returns_legacy(cfg):
    _write(cfg, enabled=False)
    assert A.adapt("picks", LEGACY, "picks", "选股", SECTIONS) == LEGACY


def test_producer_override_disabled(cfg):
    _write(cfg, enabled=True, producers={"picks": {"enabled": False}})
    assert A.adapt("picks", LEGACY, "picks", "选股", SECTIONS) == LEGACY


def test_enabled_returns_unified(cfg):
    _write(cfg, enabled=True)
    card = A.adapt("picks", LEGACY, "picks", "选股", SECTIONS,
                   ts="2026-10-25", source="选股引擎")
    assert "🎯" in card["header"]["title"]["content"]
    assert card["schema"] == "2.0"


def test_shadow_returns_legacy_and_logs(cfg, tmp_path, monkeypatch):
    _write(cfg, enabled=True, producers={"picks": {"shadow": True}})
    monkeypatch.setattr(fp, "HERE", str(tmp_path))
    card = A.adapt("picks", LEGACY, "picks", "选股", SECTIONS)
    assert card == LEGACY
    log = os.path.join(str(tmp_path), "data", "push_unified_shadow.jsonl")
    assert os.path.exists(log) and "picks" in open(log, encoding="utf-8").read()


def test_exception_falls_back(monkeypatch):
    monkeypatch.setattr(A, "fp", None)
    assert A.adapt("picks", LEGACY, "picks", "选股", SECTIONS) == LEGACY
