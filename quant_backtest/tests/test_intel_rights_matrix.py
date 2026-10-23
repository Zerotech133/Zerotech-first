"""K-MDC-R00 tests: rights matrix honesty — BLOCK propagation, no-grant guard."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_rights_matrix import (  # noqa: E402
    RightsRejected, SourceRight, build_matrix, verify_no_grant,
)

REPO = Path(__file__).resolve().parents[3]


def test_matrix_flags_xueqiu_guba_blocked_and_gate_shuts():
    m = build_matrix(REPO)
    states = {r["source_id"]: r["state"] for r in m["matrix"]}
    assert states["xueqiu"] == "BLOCK" and states["guba"] == "BLOCK"
    assert m["gate"] == "BLOCK"
    assert set(m["blocked_sources"]) == {"xueqiu", "guba"}
    for r in m["matrix"]:
        assert r["evidence"], f"{r['source_id']} 缺证据说明"


def test_tushare_tdx_claimed_with_evidence_not_verified():
    m = build_matrix(REPO)
    states = {r["source_id"]: r["state"] for r in m["matrix"]}
    assert states["tushare"] in ("claimed", "unknown")
    assert states["tdx"] in ("claimed", "unknown")
    assert "verified" not in states.values()  # 审计快照不得自封 verified


def test_audit_output_can_never_grant():
    m = build_matrix(REPO)
    verify_no_grant(m)  # 正常快照通过
    forged = {"matrix": [dict(r) for r in m["matrix"]]}
    forged["matrix"][0]["state"] = "verified"
    with pytest.raises(RightsRejected, match="must not claim verified"):
        verify_no_grant(forged)


def test_source_right_invariants():
    with pytest.raises(RightsRejected, match="verified requires"):
        SourceRight("s", "t", "party", None, None, None, "verified", evidence="")
    with pytest.raises(RightsRejected, match="state"):
        SourceRight("s", "t", None, None, None, None, "approved")


def test_matrix_snapshot_written(tmp_path):
    import json
    m = build_matrix(REPO)
    out = tmp_path / "rights_matrix.json"
    out.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["gate"] == m["gate"] and len(reloaded["matrix"]) == 4
