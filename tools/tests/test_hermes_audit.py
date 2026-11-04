# -*- coding: utf-8 -*-
"""Hermes 使用审计：真实仓库可盘点、profile 可映射、JSON 纯。"""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import hermes_audit as ha  # noqa: E402


def test_audit_shape():
    a = ha.build_audit()
    assert a["hermes"]["port_8787"] is True
    assert len(a["compositions"]) >= 6
    assert a["refs"]["agent_platform"]["files"] > 50
    assert a["ui"]["nginx_hermes"] is True


def test_profiles_mapped():
    pm = ha._seat_profile_map()
    assert pm["profiles_count"] >= 5
    assert "A0" in pm["matched"]["tianfeng"]
    assert "HUANGZHONG" in pm["matched"]["huangzhong"]
    # zhangsong 已映射到 DATA1；当前唯一未映射席位是 stock-analyst。
    assert "zhangsong" not in pm["unmapped_profiles"]
    assert "DATA1" in pm["matched"]["zhangsong"]
    assert pm["unmapped_profiles"] == ["stock-analyst"]


def test_composition_hermes_declaration_counted():
    a = ha.build_audit()
    assert a["composition_hermes_declared"] <= len(a["compositions"])
    assert "quant-domain" in [c["id"] for c in a["compositions"] if c["declares_hermes"]]


def test_json_cli_pure(capsys):
    rc = ha.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["hermes"]["components"]["orchestrator"] is True

