# -*- coding: utf-8 -*-
"""tests for tools/t1t2_capture.py（T1/T2 A/B trace 受控采集，不造数）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load("t1t2_capture")
met = _load("t1t2_metrics")

_NODE = {"run_id": "b1", "node_key": "t1_market", "agent_id": "T1",
         "execution_mode": "normal", "duration_ms": 1000.0, "output": "x" * 20,
         "structured": {"overallTrend": "up", "selectedIndicators": [],
                        "keySignals": [], "supportLevel": 1.0,
                        "resistanceLevel": 2.0, "confidence": 0.5,
                        "summaryTable": []}}


def _write_full(data_dir, run_id, nodes):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ("%s.jsonl" % run_id)).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8")
    (data_dir / ("%s_summary.json" % run_id)).write_text(
        json.dumps({"duration_ms_total": sum(n["duration_ms"] for n in nodes)}),
        encoding="utf-8")


def _write_utf16_log(path, run_id="abc123def456", stock="002712"):
    text = ("[data] ok\n"
            "====================================\n"
            "投研部运行简报  run_id=%s  stock=%s\n"
            "====================================\n"
            "[D1] degraded\n   {\"recommendation\":\"WATCH\"}\n"
            "[T1] normal\n   多空辩论结论：```json {\"overallTrend\":\"bullish\"}\n"
            "[T2] normal\n   多空辩论结论：```json {\"overallTrend\":\"bearish\"}\n"
            "checkpoint 节点数: 5 / 5\n" % (run_id, stock))
    Path(path).write_bytes(text.encode("utf-16-le"))  # 模拟 PowerShell 重定向
    return path


def test_missing_trace_and_log_is_missing_without_writes(tmp_path):
    archive = tmp_path / "archive"
    rep, _ = cap.capture("nope00000000", "baseline", data_dir=tmp_path / "no-data",
                         archive_dir=archive)
    assert rep["status"] == "missing"
    assert "trace_missing" in rep["reason"]
    assert rep["files"] == []
    assert not archive.exists()  # 零写入


def test_full_trace_capture_manifest_sha_and_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    archive = tmp_path / "archive"
    _write_full(data_dir, "b1", [_NODE])
    rep, manifest = cap.capture("b1", "baseline", data_dir=data_dir,
                                archive_dir=archive)
    assert rep["status"] == "captured" and rep["mode"] == "full_trace"
    target = archive / "baseline" / "b1.jsonl"
    assert target.is_file()
    assert (archive / "baseline" / "b1_summary.json").is_file()
    entry = manifest["arms"]["baseline"]["b1"]
    assert entry["mode"] == "full_trace"
    assert entry["files"][0]["sha256"] == cap._sha256(target)
    # 幂等重采：sha 不变、manifest 不重复
    rep2, manifest2 = cap.capture("b1", "baseline", data_dir=data_dir,
                                  archive_dir=archive,
                                  manifest=cap._load_manifest(archive))
    assert rep2["files"][0]["sha256"] == rep["files"][0]["sha256"]
    assert list(manifest2["arms"]["baseline"]) == ["b1"]


def test_log_salvage_utf16_parsed_without_fabricated_numeric_fields(tmp_path):
    log = _write_utf16_log(tmp_path / "run.log")
    rep, manifest = cap.capture("abc123def456", "json_first",
                                data_dir=tmp_path / "no-data",
                                archive_dir=tmp_path / "archive", from_log=log)
    assert rep["status"] == "captured" and rep["mode"] == "log_salvage"
    salvage = json.loads((tmp_path / "archive" / "json_first" /
                          "abc123def456.salvage.json").read_text(encoding="utf-8"))
    assert salvage["run_id"] == "abc123def456"
    assert salvage["stock"] == "002712"
    assert salvage["fidelity"] == "log_salvage"
    assert {n["agent_id"] for n in salvage["nodes"]} == {"D1", "T1", "T2"}
    assert [n["status"] for n in salvage["nodes"]] == ["degraded", "normal", "normal"]
    assert salvage["degraded_fields"] == ["structured", "duration_ms"]
    # 不伪造：structured/duration 均为 null
    assert all(n["structured"] is None and n["duration_ms"] is None
               for n in salvage["nodes"])
    assert manifest["arms"]["json_first"]["abc123def456"]["mode"] == "log_salvage"


def test_log_run_id_mismatch_is_rejected(tmp_path):
    log = _write_utf16_log(tmp_path / "run.log", run_id="ffffffffffff")
    rep, _ = cap.capture("abc123def456", "x", data_dir=tmp_path / "no-data",
                         archive_dir=tmp_path / "archive", from_log=log)
    assert rep["status"] == "missing"
    assert "run_id_mismatch" in rep["reason"]


def test_dry_run_writes_nothing(tmp_path):
    data_dir = tmp_path / "data"
    archive = tmp_path / "archive"
    _write_full(data_dir, "b1", [_NODE])
    rep, _ = cap.capture("b1", "baseline", data_dir=data_dir,
                         archive_dir=archive, dry_run=True)
    assert rep["status"] == "captured" and rep["dry_run"] is True
    assert rep["files"]  # 计划明确
    assert not archive.exists()


def test_metrics_reads_archived_full_traces(tmp_path):
    """归档 full_trace 可被 metrics 直接复算（worktree 无 team_exec 数据的场景）。"""
    data_dir = tmp_path / "data"
    archive = tmp_path / "archive"
    _write_full(data_dir, "b1", [_NODE, dict(_NODE, agent_id="T2", node_key="t2_market",
                                             duration_ms=3000.0)])
    _write_full(data_dir, "v1", [dict(_NODE, duration_ms=500.0), _NODE])
    runs = {"baseline": {"run_ids": ["b1"]}, "variant": {"run_ids": ["v1"]}}
    for rid, arm in (("b1", "baseline"), ("v1", "variant")):
        cap.capture(rid, arm, data_dir=data_dir, archive_dir=archive)
    rep = met.aggregate(data_dir=tmp_path / "no-data", runs=runs,
                        archive_dir=archive)
    assert rep["available"] is True
    base = rep["arms"]["baseline"]
    assert base["runs"][0]["source"].endswith("b1.jsonl")
    assert base["json_complete_rate"] == 1.0
    assert base["mean_latency_ms"] == 2000.0
    var = rep["arms"]["variant"]
    assert var["mean_latency_ms"] == 750.0
    assert rep["gate"]["passed"] is True


def test_metrics_ignores_salvage_for_numeric_metrics(tmp_path):
    archive = tmp_path / "archive"
    (archive / "variant").mkdir(parents=True)
    (archive / "variant" / "v1.salvage.json").write_text(
        json.dumps({"run_id": "v1", "fidelity": "log_salvage", "nodes": []}),
        encoding="utf-8")
    runs = {"baseline": {"run_ids": ["b1"]}, "variant": {"run_ids": ["v1"]}}
    rep = met.aggregate(data_dir=tmp_path / "no-data", runs=runs, archive_dir=archive)
    assert rep["available"] is False
    assert rep["arms"]["variant"]["runs_found"] == []
    assert rep["arms"]["variant"]["json_complete_rate"] is None
