# -*- coding: utf-8 -*-
"""L4 provider 契约测试（含 2026-10-31 WO-L4-M4-HARDEN 的 artifact_ref 门）。

2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B1）：补充
``reason = no_research_artifact`` 机读标记与真实 l4_status.json 数据契约护栏。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


# ── 2026-10-31 WO-D1-B123-20261031（B1）：no_research_artifact 机读标记 ──
def test_established_without_artifact_ref_marks_no_research_artifact(tmp_path):
    """B1 核心判据：established 无 artifact_ref → unresolved + 机读标记。"""
    p, _ = _l4(tmp_path, {"status": "established", "source": "text_guess",
                          "as_of": "2026-10-30", "rationale": "L4=定性成立"})
    r = L.load_l4("600519", path=p)
    assert r["status"] == "unresolved"
    assert r["reason"] == "no_research_artifact"
    assert r["artifact_invalid"] == "missing"


def test_established_empty_artifact_ref_marks_no_research_artifact(tmp_path):
    p, _ = _l4(tmp_path, {"status": "established", "artifact_ref": "   "})
    r = L.load_l4("600519", path=p)
    assert r["status"] == "unresolved"
    assert r["reason"] == "no_research_artifact"


def test_established_invalid_artifact_marks_no_research_artifact(tmp_path):
    """artifact 存在但不可核验（schema 不符）同属「无研究级产物」。"""
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "RA-schema.json").write_text(json.dumps({"schema": "other/v1"}),
                                         encoding="utf-8")
    p, _ = _l4(tmp_path, {"status": "established",
                          "artifact_ref": "RA-schema.json"})
    r = L.load_l4("600519", path=p, artifact_root=root)
    assert r["status"] == "unresolved"
    assert r["reason"] == "no_research_artifact"
    assert r["artifact_invalid"] == "schema_mismatch"


def test_established_with_valid_artifact_has_no_reason_marker(tmp_path):
    root = _artifact(tmp_path)
    p, _ = _l4(tmp_path, {"status": "established",
                          "artifact_ref": "RA-test.json"})
    r = L.load_l4("600519", path=p, artifact_root=root)
    assert r["status"] == "established"
    assert "reason" not in r          # 合法 established 不得被误标
    assert "artifact_invalid" not in r


def test_unresolved_rows_do_not_get_no_research_artifact_marker(tmp_path):
    p, _ = _l4(tmp_path, {"status": "unresolved", "source": "xsec"})
    assert "reason" not in L.load_l4("600519", path=p)


def test_real_l4_status_established_rows_have_artifact_ref():
    """数据契约护栏：仓内 l4_status.json 的任何 established 行必须带非空 artifact_ref。

    data/ 不入 git，worktree 检出可能没有该文件 → skip；有则逐行校验（不写死
    票号/状态，数据随生产更新）。
    """
    if not L.L4_PATH.is_file():
        pytest.skip("本检出无 agent_platform/data/xsec/l4_status.json（data 不入 git）")
    doc = json.loads(L.L4_PATH.read_text(encoding="utf-8"))
    for symbol, rec in (doc.get("symbols") or {}).items():
        if str((rec or {}).get("status") or "").strip().lower() == "established":
            assert str(rec.get("artifact_ref") or "").strip(), (
                "L4 symbol %s 声明 established 但缺 artifact_ref（B1 契约）"
                % symbol)
