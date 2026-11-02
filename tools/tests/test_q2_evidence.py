# -*- coding: utf-8 -*-
"""Q2 证据包与 data-warehouse 登录/SLO 探针。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import dw_q2_probe  # noqa: E402
import q2_evidence  # noqa: E402


def test_q2_evidence_all_legions():
    d = q2_evidence.build()
    assert d["read_only"] is True
    assert d["summary"]["total"] == 6
    ids = {x["id"] for x in d["legions"]}
    for cid in ("huangzhong-acceptance", "lusu-d1-flow", "quant-domain", "quant-strategy-line"):
        assert cid in ids
    for x in d["legions"]:
        assert "待 owner" in x["q2_status"]
        assert x["manual_gate"]


def test_q2_evidence_pack_files_exist():
    d = json.loads((ROOT / "docs" / "q2" / "q2_evidence_matrix_v1.json").read_text(encoding="utf-8"))
    assert d["summary"]["total"] == 6
    for x in d["legions"]:
        assert (ROOT / "docs" / "q2" / f"{x['id']}_Q2_evidence_v1.md").is_file()


def test_dw_probe_pending_without_cookie():
    d = dw_q2_probe.build()
    assert d["auth_state"] == "PENDING-LOGIN"
    assert d["slo"]["slo_ok"] is True
    assert all(c["ok"] for c in d["checks"])
    # 不伪造登录通过
    assert d["ok"] is False
