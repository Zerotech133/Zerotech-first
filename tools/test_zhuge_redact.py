# -*- coding: utf-8 -*-
"""P1-8 修复单测：L5 输出内部路径脱敏。"""
import importlib.util
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "portal" / "comm_zhuge.py"


def _load():
    spec = importlib.util.spec_from_file_location("comm_zhuge_redact_test", _P)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_redact_internal_abs_paths():
    c = _load()
    out = c._redact("无法打印 C:\\项目\\_vault 下文件；C:\\aios\\bin 也不可读。")
    assert "C:\\项目" not in out and "C:\\aios" not in out and "_vault" not in out
    assert "[内部路径]" in out


def test_redact_keeps_relative_docs():
    c = _load()
    text = "依据 docs/audit/x.md 与 [S1]，结论。[S1]"
    assert c._redact(text) == text


def test_redact_empty_safe():
    c = _load()
    assert c._redact("") == ""
