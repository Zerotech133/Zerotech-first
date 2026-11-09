# -*- coding: utf-8 -*-
"""W1 resolver：6 组合/126 引用全解析；未知引用不假绿。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import resolver as R  # noqa: E402


def test_all_compositions_resolve():
    res = R.check_all(strict=True)
    s = res["summary"]
    assert s["manifests"] == 6
    assert s["refs"] >= 100
    assert s["errors"] == 0 and s["warnings"] == 0
    assert s["ok"] is True


def test_unknown_agent_is_error():
    # 直接检查解析核心（不落 manifest 文件）
    regs = __import__("composition").load_registries()
    errs, warns = [], []
    __import__("composition").resolve_ref("agent/NOT_EXIST", regs, errs, warns)
    assert errs and "未登记" in errs[0]


def test_manifest_check_returns_refs():
    p = ROOT / "compositions" / "acad1-legion" / "manifest.yaml"
    res = R.check_manifest(p)
    assert res["ok"] is True and len(res["refs"]) > 10
