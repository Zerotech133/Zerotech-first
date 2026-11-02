# -*- coding: utf-8 -*-
"""审批抽样审计：样本固定、全 approved 风险可见、JSON 纯。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import approval_audit  # noqa: E402


def test_audit_sample():
    a = approval_audit.build_audit(10)
    assert a["read_only"] is True
    assert a["total"] >= 40
    assert a["sample_n"] == 10
    assert len(a["sample"]) == 10
    assert any("100% approved" in x for x in a["findings"])


def test_audit_json(capsys):
    rc = approval_audit.main(["--n", "5", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["sample_n"] == 5
