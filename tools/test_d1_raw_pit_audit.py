# -*- coding: utf-8 -*-
"""D1 raw/decision_id/PIT 只读审计 CLI 测试（K-ORG-20261101-D1RAWPIT）。

隔离纪律：全部 tmp_path 合成账本；不读/写生产 jsonl；审计前后逐字节断言一致。
覆盖：空/缺文件、旧行兼容、decision_id 复算与关联、raw ref 可核验/缺文件拒绝、
PIT 采集时刻 vs 数据时点分账、只读零写。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT / "agent_platform"))
sys.path.insert(0, str(WT / "agent_platform" / "engine"))

spec = importlib.util.spec_from_file_location(
    "d1_raw_pit_audit", WT / "tools" / "d1_raw_pit_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _shadow_dir(root: Path) -> Path:
    folder = root / "agent_platform" / "data" / "shadow"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_jsonl(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def _decision_id(symbol, date, run_id):
    raw = "%s|%s|%s" % (symbol, date, run_id)
    return "D1-%s-%s" % (str(date).replace("-", ""),
                         hashlib.sha256(raw.encode()).hexdigest()[:10])


def _raw_ref(root: Path, rel: str, *, pit_as_of="2026-10-29",
             create=True) -> dict:
    path = root / rel
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"kline-bytes")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        size = len(path.read_bytes())
    else:  # 只造 ref 不造文件（缺文件拒绝用例）
        sha = hashlib.sha256(rel.encode()).hexdigest()
        size = 0
    return {
        "source": "market_technical",
        "uri": "raw://kline_parquet/%s" % rel,
        "as_of": "2026-10-31T18:00:00+0800",
        "hash": sha[:16],
        "schema": "evidence_ref/v2",
        "kind": "kline_parquet",
        "raw_uri": rel,
        "raw_sha256": sha,
        "raw_row_range": [],
        "raw_selector": "",
        "raw_bytes": size,
        "raw_verified": True,
        "raw_verify_error": "",
        "pit_as_of": pit_as_of,
    }


def _shadow_row(run_id="r1", date="2026-10-30", **kw) -> dict:
    row = {"date": date, "symbol": "600519", "run_id": run_id,
           "status": "done", "ts": 1793355131.9,
           "decision_id": _decision_id("600519", date, run_id),
           "model": "glm/glm-5.3", "price_at_decision": 1354.0,
           "recommendation": "WATCH"}
    row.update(kw)
    return row


def _outcome_row(run_id="r1", date="2026-10-30", **kw) -> dict:
    row = {"date": date, "symbol": "600519", "run_id": run_id,
           "decision_id": _decision_id("600519", date, run_id),
           "model": "glm/glm-5.3", "hit": None,
           "next_trade_date": "2026-11-02", "next_close": 1360.0,
           "next_day_return": 0.0044, "confidence": 0.5,
           "recommendation": "WATCH"}
    row.update(kw)
    return row


# ---------------------------------------------------------------- 空/缺文件
def test_missing_files_degrade_not_crash(tmp_path):
    report = audit.run_audit(tmp_path)
    assert report["ok"] is True and report["read_only"] is True
    assert report["shadow"]["file"]["exists"] is False
    assert report["outcomes"]["file"]["exists"] is False
    assert report["shadow"]["rows"] == 0
    ids = {f["id"] for f in report["findings"]}
    assert "shadow_missing" in ids and "outcomes_missing" in ids
    assert audit.main(["--root", str(tmp_path), "--json"]) == 0


def test_empty_files_zero_stats(tmp_path):
    folder = _shadow_dir(tmp_path)
    _write_jsonl(folder / "shadow_ledger.jsonl", [])
    _write_jsonl(folder / "decision_outcomes.jsonl", [])
    report = audit.run_audit(tmp_path)
    assert report["shadow"]["rows"] == 0
    assert report["outcomes"]["rows"] == 0
    assert report["shadow"]["decision_id"]["missing"] == 0


# ---------------------------------------------------------------- 旧行兼容
def test_legacy_rows_are_counted_not_crashed(tmp_path):
    folder = _shadow_dir(tmp_path)
    legacy = {"date": "2026-10-08", "symbol": "600519", "run_id": "old-1",
              "status": "done", "ts": 1.0, "price_at_decision": 100.0}
    _write_jsonl(folder / "shadow_ledger.jsonl", [legacy])
    _write_jsonl(folder / "decision_outcomes.jsonl",
                 [{"run_id": "old-1", "date": "2026-10-08",
                   "symbol": "600519", "hit": None}])
    report = audit.run_audit(tmp_path)
    assert report["shadow"]["decision_id"]["missing"] == 1
    assert report["shadow"]["refs"]["total"] == 0
    assert report["shadow"]["raw_summary"]["rows_with_summary"] == 0
    assert report["outcomes"]["decision_id"]["missing"] == 1
    assert report["outcomes"]["pit_as_of_nonempty"] == 0
    assert report["cross"]["recomputable_decision_id"] == 1
    ids = {f["id"] for f in report["findings"]}
    assert {"shadow_decision_id_missing", "shadow_refs_empty",
            "outcomes_pit_missing"} <= ids


# ---------------------------------------------------------------- 新行统计
def test_new_rows_raw_and_pit_counted(tmp_path):
    folder = _shadow_dir(tmp_path)
    raw = _raw_ref(tmp_path, "quant_backtest/data/ashare/600519_SH.parquet")
    summary_ref = {"source": "fundamental",
                   "uri": "make_context://600519/fundamental",
                   "as_of": "2026-10-31T18:00:00+0800",
                   "hash": "0" * 16}
    row = _shadow_row(
        evidence_refs=[raw, summary_ref],
        evidence_raw_refs=[],
        evidence_raw_refs_summary={"status": "partial",
                                   "raw_refs_in_bundle": 1,
                                   "extra_raw_refs": 1},
    )
    _write_jsonl(folder / "shadow_ledger.jsonl", [row])
    _write_jsonl(folder / "decision_outcomes.jsonl", [
        _outcome_row(decision_id=row["decision_id"],
                     pit_as_of="2026-10-29", pit_source="raw_ref:kline_parquet",
                     raw_refs_status="partial", raw_refs_in_bundle=1)])
    report = audit.run_audit(tmp_path)
    shadow = report["shadow"]
    assert shadow["decision_id"] == {
        "nonempty": 1, "missing": 0, "recomputed": 1, "mismatch": 0,
        "samples": []}
    assert shadow["refs"]["total"] == 2
    assert shadow["refs"]["raw"] == 1 and shadow["refs"]["summary"] == 1
    assert shadow["refs"]["raw_verified"] == 1
    assert shadow["refs"]["raw_unverified"] == 0
    assert shadow["pit"]["refs_data"] == 1
    assert shadow["pit"]["refs_collection_only"] == 1
    assert shadow["raw_summary"]["status"] == {"partial": 1}
    assert shadow["raw_summary"]["extra_raw_refs"] == 1
    outcomes = report["outcomes"]
    assert outcomes["pit_as_of_nonempty"] == 1
    assert outcomes["raw_refs_status"] == {"partial": 1}
    assert outcomes["cross_shadow"] == {"compared": 1, "agree": 1,
                                        "mismatch": 0, "mismatch_samples": []}
    assert report["cross"]["shadow_done_without_outcome"] == 0


def test_decision_id_mismatch_and_missing_raw_file(tmp_path):
    folder = _shadow_dir(tmp_path)
    bad_raw = _raw_ref(tmp_path, "missing/nope.parquet", create=False)
    row = _shadow_row(decision_id="D1-20261030-deadbeef99",
                      evidence_refs=[bad_raw])
    _write_jsonl(folder / "shadow_ledger.jsonl", [row])
    _write_jsonl(folder / "decision_outcomes.jsonl", [
        _outcome_row(decision_id="D1-20261030-deadbeef99")])
    report = audit.run_audit(tmp_path)
    assert report["shadow"]["decision_id"]["mismatch"] == 1
    assert report["shadow"]["decision_id"]["samples"][0]["expected"] == \
        _decision_id("600519", "2026-10-30", "r1")
    refs = report["shadow"]["refs"]
    assert refs["raw"] == 1 and refs["raw_unverified"] == 1
    assert "not_found" in refs["verify_failures"]
    assert report["outcomes"]["decision_id"]["mismatch"] == 1
    assert report["outcomes"]["cross_shadow"]["mismatch"] == 1
    levels = {f["id"]: f["level"] for f in report["findings"]}
    assert levels.get("shadow_decision_id_mismatch") == "high"
    assert levels.get("shadow_raw_unverified") == "high"


# ---------------------------------------------------------------- 只读零写
def test_audit_writes_nothing(tmp_path):
    folder = _shadow_dir(tmp_path)
    raw = _raw_ref(tmp_path, "quant_backtest/data/ashare/600519_SH.parquet")
    _write_jsonl(folder / "shadow_ledger.jsonl",
                 [_shadow_row(evidence_refs=[raw])])
    _write_jsonl(folder / "decision_outcomes.jsonl", [_outcome_row()])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    listing_before = sorted(str(p) for p in tmp_path.rglob("*"))
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert audit.main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(buf.getvalue())
    assert payload["read_only"] is True
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before  # 文件逐字节一致（含 raw 文件）
    assert sorted(str(p) for p in tmp_path.rglob("*")) == listing_before  # 零新增


def test_json_schema_and_human_report(tmp_path):
    folder = _shadow_dir(tmp_path)
    _write_jsonl(folder / "shadow_ledger.jsonl", [_shadow_row()])
    _write_jsonl(folder / "decision_outcomes.jsonl", [_outcome_row()])
    report = audit.run_audit(tmp_path)
    for key in ("schema", "generated_at", "root", "read_only", "shadow",
                "outcomes", "cross", "findings", "ok"):
        assert key in report
    assert report["schema"] == "d1_raw_pit_audit/v1"
    text = audit.render_report(report)
    assert "D1 raw/decision_id/PIT 只读审计" in text
    assert "## shadow_ledger" in text and "## decision_outcomes" in text
    json.dumps(report, ensure_ascii=False)  # 可序列化


def test_decision_id_helper_matches_writer_formula():
    fn = audit._load_decision_id_fn()
    assert fn("600519", "2026-10-30", "df51a1059157") == "D1-20261030-6d46c0beb1"
    for symbol, date, run_id in (("000858", "2026-10-27", "5d66cc48a2f2"),
                                 ("300324", "2026-10-30", "run-x-001")):
        assert fn(symbol, date, run_id) == _decision_id(symbol, date, run_id)
