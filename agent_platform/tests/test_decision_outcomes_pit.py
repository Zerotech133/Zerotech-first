# -*- coding: utf-8 -*-
"""decision_outcomes PIT/raw refs 增量测试（K-ORG-20261101-D1RAWPIT）。

覆盖：
- 新 outcome 行补 pit_as_of（数据时点）/ pit_source / raw_refs_status /
  raw_refs_in_bundle，schema 向后兼容（旧读取方忽略未知键）；
- 旧 outcome 行（缺新字段）可读/可统计/可幂等，不崩；
- 历史回放只补「账本行确有证据接线字段」的 PIT/raw，不凭空补；已有值不动；
- 只读/--no-write 文件字节不变；不伪造 hit/model。
全部 tmp_path 隔离，不触碰生产 sidecar。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

WT = pathlib.Path(__file__).resolve().parents[2]
PLATFORM = WT / "agent_platform"
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

_spec = importlib.util.spec_from_file_location(
    "decision_outcomes_pit", PLATFORM / "decision_outcomes.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

BARS = [
    ["2026-10-27", "1", "100", "1", "1", "1"],
    ["2026-10-28", "1", "102", "1", "1", "1"],
]


def _ledger_row(**kw):
    row = {"status": "done", "date": "2026-10-27", "symbol": "600519",
           "run_id": "r1", "price_at_decision": "100",
           "recommendation": "BUY", "confidence": 0.8, "latency_s": 10.0,
           "parse_ok": True, "model": "glm/glm-5.3", "fallback": False}
    row.update(kw)
    return row


def _raw_ref(pit="2026-10-26"):
    return {"source": "market_technical", "uri": "raw://kline_parquet/x",
            "as_of": "2026-10-27T18:00:00+0800", "hash": "0" * 16,
            "schema": "evidence_ref/v2", "kind": "kline_parquet",
            "raw_uri": "quant_backtest/data/ashare/600519_SH.parquet",
            "raw_sha256": "a" * 64, "raw_row_range": [10, 20],
            "raw_verified": True, "pit_as_of": pit}


def _write_jsonl(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 新行字段
def test_compute_outcome_carries_pit_and_raw_fields():
    ledger = _ledger_row(
        evidence_refs=[
            {"source": "fundamental", "uri": "make_context://600519/f",
             "as_of": "2026-10-27T18:00:00+0800", "hash": "f" * 16},
            _raw_ref("2026-10-26"),
        ],
        evidence_raw_refs_summary={"status": "partial",
                                   "raw_refs_in_bundle": 1,
                                   "extra_raw_refs": 0},
    )
    out = m.compute_outcome(ledger, lambda s, n: BARS)
    assert out["pit_as_of"] == "2026-10-26"
    assert out["pit_source"] == "raw_ref:kline_parquet"
    assert out["raw_refs_status"] == "partial"
    assert out["raw_refs_in_bundle"] == 1
    # 旧字段语义不变
    assert out["decision_id"] == m.decision_id("600519", "2026-10-27", "r1")
    assert out["hit"] == 1 and out["model"] == "glm/glm-5.3"
    json.dumps(out, ensure_ascii=False)


def test_compute_outcome_pit_falls_back_to_decision_date():
    out = m.compute_outcome(_ledger_row(), lambda s, n: BARS)
    assert out["pit_as_of"] == "2026-10-27"
    assert out["pit_source"] == "decision_date"   # 诚实标注：非 raw
    assert out["raw_refs_status"] is None         # 无 raw 证据 → 不编造
    assert out["raw_refs_in_bundle"] is None


def test_invalid_pit_not_fabricated():
    ledger = _ledger_row(evidence_refs=[_raw_ref("not-a-date")],
                         evidence_raw_refs_summary={"status": "partial"})
    out = m.compute_outcome(ledger, lambda s, n: BARS)
    # 非法 pit 被丢弃 → 回退决策日期（不落非法串，不推断原始值）
    assert out["pit_as_of"] == "2026-10-27"
    assert out["pit_source"] == "decision_date"
    assert out["raw_refs_status"] == "partial"


# ---------------------------------------------------------------- 旧行回放
def test_repair_fills_pit_when_ledger_has_evidence(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row(
        run_id="r-ev", evidence_refs=[_raw_ref("2026-10-26")],
        evidence_raw_refs_summary={"status": "partial",
                                   "raw_refs_in_bundle": 1})])
    out = _write_jsonl(tmp_path / "out.jsonl", [{
        "run_id": "r-ev", "date": "2026-10-27", "symbol": "600519",
        "decision_id": m.decision_id("600519", "2026-10-27", "r-ev"),
        "model": "glm/glm-5.3", "hit": 1}])
    rep = m.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert rep["repaired"] == 1 and rep["repaired_pit"] == 1
    assert rep["repaired_fields"]["pit_as_of"] == 1
    assert rep["repaired_fields"]["raw_refs_status"] == 1
    row = m._load_jsonl(str(out))[0]
    assert row["pit_as_of"] == "2026-10-26"
    assert row["pit_source"] == "raw_ref:kline_parquet"
    assert row["raw_refs_status"] == "partial"
    # 补完再跑：幂等、零写
    before = out.read_bytes()
    again = m.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert again["repaired"] == 0 and again["repaired_pit"] == 0
    assert again["written"] is False
    assert out.read_bytes() == before


def test_repair_skips_legacy_ledger_without_evidence(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl",
                          [_ledger_row(run_id="r-old")])  # 无证据接线字段
    out = _write_jsonl(tmp_path / "out.jsonl", [{
        "run_id": "r-old", "date": "2026-10-27", "symbol": "600519",
        "decision_id": "keep-me", "model": "keep-model", "hit": None}])
    before = out.read_bytes()
    rep = m.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert rep["repaired"] == 0 and rep["repaired_pit"] == 0
    assert rep["written"] is False
    assert out.read_bytes() == before  # 不为无证据旧行凭空补时点
    row = m._load_jsonl(str(out))[0]
    assert "pit_as_of" not in row


def test_existing_pit_values_not_overwritten(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row(
        run_id="r-keep", evidence_refs=[_raw_ref("2026-10-26")],
        evidence_raw_refs_summary={"status": "partial",
                                   "raw_refs_in_bundle": 1})])
    out = _write_jsonl(tmp_path / "out.jsonl", [{
        "run_id": "r-keep", "date": "2026-10-27", "symbol": "600519",
        "decision_id": "keep-id", "model": "keep-model",
        "pit_as_of": "keep-pit", "pit_source": "keep-source",
        "raw_refs_status": "keep-status", "raw_refs_in_bundle": 1}])
    rep = m.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert rep["repaired"] == 0
    row = m._load_jsonl(str(out))[0]
    assert row["pit_as_of"] == "keep-pit"
    assert row["pit_source"] == "keep-source"
    assert row["raw_refs_status"] == "keep-status"


def test_legacy_outcome_without_new_fields_compatible(tmp_path):
    old = {"ts": 1.0, "run_id": "r0", "date": "2026-10-20",
           "symbol": "600519", "recommendation": "WATCH", "hit": None,
           "model": "glm/glm-5.3"}
    path = _write_jsonl(tmp_path / "out.jsonl", [old])
    rows = m._load_jsonl(str(path))
    summary = m.summarize(rows, [], week="2026-W43")
    assert summary["outcomes"] == 1
    assert summary["pit"]["outcomes_with_pit_as_of"] == 0
    assert summary["pit"]["raw_refs_status"] == {}
    # --no-write 零改动
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [])
    before = path.read_bytes()
    assert m.main(["--no-write", "--ledger", str(ledger),
                   "--out", str(path)]) == 0
    assert path.read_bytes() == before


def test_summary_counts_pit_and_raw_status():
    rows = [
        {"run_id": "a", "recommendation": "BUY", "hit": 1, "confidence": 0.8,
         "pit_as_of": "2026-10-29", "raw_refs_status": "partial"},
        {"run_id": "b", "recommendation": "WATCH", "hit": None,
         "raw_refs_status": "none"},
        {"run_id": "c"},
    ]
    summary = m.summarize(rows, [], week="2026-W44")
    assert summary["pit"]["outcomes_with_pit_as_of"] == 1
    assert summary["pit"]["outcomes_with_raw_status"] == 2
    assert summary["pit"]["raw_refs_status"] == {"partial": 1, "none": 1}
    assert summary["outcomes"] == 3
