# -*- coding: utf-8 -*-
"""W1 planned refs 登记：validate 视为可解析，但不掩盖未知引用。"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import composition as c  # noqa: E402


def test_planned_refs_loaded():
    regs = c.load_registries()
    assert "dataset/file/acceptance_corpus" in regs["planned"]
    assert "job/acceptance-1104-runner" in regs["planned"]


def test_planned_ref_resolves_without_warning():
    regs = c.load_registries()
    errors, warnings = [], []
    c.resolve_ref("dataset/file/temp_contract", regs, errors, warnings)
    assert errors == [] and warnings == []


def test_unknown_ref_still_warns():
    regs = c.load_registries()
    errors, warnings = [], []
    c.resolve_ref("dataset/file/__definitely_missing__", regs, errors, warnings)
    assert warnings


def test_planned_registry_has_owner_due_acceptance():
    import yaml
    p = ROOT / "compositions" / "_schema" / "planned_refs.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    refs = data.get("refs") or []
    assert len(refs) >= 8
    for item in refs:
        assert item.get("ref") and item.get("owner") and item.get("due") and item.get("acceptance")

