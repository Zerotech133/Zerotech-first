# -*- coding: utf-8 -*-
"""L4 provider 契约测试（含 2026-10-31 WO-L4-M4-HARDEN 的 artifact_ref 门）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))
import l4_provider as L  # noqa: E402

_FREEZE = "tdx-parquet@2026-10-28; files=142; sha256=" + "d" * 64


def _artifact(tmp_path, name="RA-test.json", **overrides):
    """造一个合法 research_artifact/v1 文件，返回 artifact_root。"""
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "research_artifact/v1",
           "artifact_id": "RA-20261031-LCS1-600519-v1",
           "created": "2026-10-31T04:45:00+08:00",
           "data_version": _FREEZE}
    doc.update(overrides)
    (root / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return root


def _l4(tmp_path, rec):
    p = tmp_path / "l4.json"
    p.write_text(json.dumps({"symbols": {"600519": rec}}), encoding="utf-8")
    return p, tmp_path / "artifacts"


def test_missing_file_is_missing(tmp_path):
    r = L.load_l4("600519", path=tmp_path / "nope.json")
    assert r["status"] == "missing"


def test_established_with_valid_artifact_ref(tmp_path):
    root = _artifact(tmp_path)
    p, art_root = _l4(tmp_path, {"status": "established", "source": "xsec",
                                 "as_of": "2026-10-30", "confidence": 0.7,
                                 "artifact_ref": "RA-test.json"})
    r = L.load_l4("600519", path=p, artifact_root=root)
    assert r["status"] == "established", r
    assert r["artifact_ref"] == "RA-test.json"
    assert r["source"] == "xsec"
    assert art_root.exists()


def test_established_without_artifact_ref_is_downgraded(tmp_path):
    p, _ = _l4(tmp_path, {"status": "established", "source": "text_guess",
                          "as_of": "2026-10-30", "rationale": "L4=定性成立"})
    r = L.load_l4("600519", path=p)
    assert r["status"] == "unresolved"
    assert r["artifact_invalid"] == "missing"
    assert "artifact_ref 校验失败(missing)" in r["rationale"]


def test_established_with_empty_artifact_ref_is_downgraded(tmp_path):
    p, _ = _l4(tmp_path, {"status": "established", "artifact_ref": "   "})
    assert L.load_l4("600519", path=p)["status"] == "unresolved"


def test_established_with_unreachable_artifact_ref_is_downgraded(tmp_path):
    p, _ = _l4(tmp_path, {"status": "established",
                          "artifact_ref": "RA-does-not-exist.json"})
    r = L.load_l4("600519", path=p, artifact_root=tmp_path / "artifacts")
    assert r["status"] == "unresolved"
    assert r["artifact_invalid"] == "not_found"


def test_established_with_invalid_artifact_json_is_downgraded(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "RA-broken.json").write_text("{not json", encoding="utf-8")
    p, _ = _l4(tmp_path, {"status": "established",
                          "artifact_ref": "RA-broken.json"})
    r = L.load_l4("600519", path=p, artifact_root=root)
    assert r["status"] == "unresolved"
    assert r["artifact_invalid"] == "invalid_json"


def test_established_rejects_url_and_schema_and_freeze_gaps(tmp_path):
    root = _artifact(tmp_path)
    # 外链
    p, _ = _l4(tmp_path, {"status": "established",
                          "artifact_ref": "https://example.test/RA.json"})
    assert L.load_l4("600519", path=p, artifact_root=root)["artifact_invalid"] == "url_not_local"
    # schema 不符
    bad = _artifact(tmp_path, name="RA-schema.json", schema="other/v1")
    p2, _ = _l4(tmp_path, {"status": "established",
                           "artifact_ref": "RA-schema.json"})
    assert L.load_l4("600519", path=p2, artifact_root=bad)["artifact_invalid"] == "schema_mismatch"
    # 无冻结 sha
    nofreeze = _artifact(tmp_path, name="RA-nofreeze.json", data_version="tdx-parquet@2026-10-28")
    p3, _ = _l4(tmp_path, {"status": "established",
                           "artifact_ref": "RA-nofreeze.json"})
    assert L.load_l4("600519", path=p3, artifact_root=nofreeze)["artifact_invalid"] == "missing_freeze_evidence"


def test_unresolved_does_not_need_artifact(tmp_path):
    p, _ = _l4(tmp_path, {"status": "unresolved", "source": "xsec"})
    r = L.load_l4("600519", path=p)
    assert r["status"] == "unresolved"
    assert "artifact_invalid" not in r


def test_unknown_symbol_and_invalid_status(tmp_path):
    p = tmp_path / "l4.json"
    p.write_text(json.dumps({"symbols": {"600519": {"status": "weird"}}}),
                 encoding="utf-8")
    assert L.load_l4("000858", path=p)["status"] == "missing"
    assert L.load_l4("600519", path=p)["status"] == "unknown"
