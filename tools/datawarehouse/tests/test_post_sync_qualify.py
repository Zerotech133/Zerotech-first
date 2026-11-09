# -*- coding: utf-8 -*-
"""post_sync_qualify 测试（2026-11-02 task-73）：mock registry/qualify 调用，全部 tmp。

覆盖：混合 verdict 汇总归因、全 ok 写回/仅 ok 写入、partial/bad 不写、写回失败 rc≠0、
复判失败 rc≠0、无 unknown、分批上限（≤20/批）、JSON/--out/日志、人类摘要。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

import post_sync_qualify as pq  # noqa: E402


def _registry(path: Path, unknown, others=("evidence_ok",)):
    rows = [{"asset_id": t, "qualification_status": "unknown"} for t in unknown]
    for i, s in enumerate(others):
        rows.append({"asset_id": "other_%d" % i, "qualification_status": s})
    doc = {"physical_count": len(rows), "display_count": 0,
           "catalog_sha256": "sha-fixture", "physical_tables": rows}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _after_registry(before: dict, upgraded: set) -> dict:
    doc = json.loads(json.dumps(before))
    for e in doc["physical_tables"]:
        if e["asset_id"] in upgraded:
            e["qualification_status"] = "evidence_ok"
    return doc


def _make_probe(verdicts: dict, apply_ok=True, apply_validation=True):
    """返回 (probe, calls)；verdicts: {table: verdict}；apply 时附 registry_write。"""
    calls = []

    def probe(tables, apply, timeout):
        calls.append({"tables": list(tables), "apply": apply})
        cards = []
        bad = 0
        for t in tables:
            v = verdicts.get(t, "evidence_partial")
            if v == "evidence_ok":
                reasons = []
            elif v == "evidence_bad":
                reasons = ["no_parquet"]
                bad += 1
            else:
                reasons = ["stale_gt_7d"]
            cards.append({"table": t, "verdict": v, "reasons": reasons})
        payload = {"tool": "qualify_probe", "code": 0 if bad == 0 else 1,
                   "summary": {"tables": len(cards)},
                   "tables": cards}
        if apply:
            payload["registry_write"] = {
                "applied": apply_ok, "backup": "warehouse_registry.json.bak-fixture",
                "bytes": 123, "validation": {"ok": apply_validation},
            }
        return {"rc": 0 if (bad == 0 or not apply) else 1, "payload": payload,
                "error": None, "stdout_tail": ""}

    return probe, calls


def _run(tmp_path, verdicts, unknown, *, apply=False, batch_size=20, probe=None,
         apply_ok=True, apply_validation=True):
    reg_path = _registry(tmp_path / "registry.json", unknown)
    before = json.loads(reg_path.read_text(encoding="utf-8"))
    if probe is None:
        probe, calls = _make_probe(verdicts, apply_ok=apply_ok,
                                   apply_validation=apply_validation)
    else:
        calls = []
    upgraded = {t for t, v in verdicts.items() if v == "evidence_ok"}
    seen = {"n": 0}

    def read_reg(path):
        seen["n"] += 1
        if seen["n"] == 1:
            return before
        return _after_registry(before, upgraded) if apply else before

    summary = pq.run(reg_path, apply=apply, batch_size=batch_size, probe=probe,
                     read_registry_fn=read_reg,
                     log_path=tmp_path / "post_sync_qualify.log")
    return summary, calls, before


# ---------- 1. dry-run 混合 verdict ----------
def test_dry_run_mixed_verdicts(tmp_path):
    unknown = ["a", "b", "c", "d"]
    verdicts = {"a": "evidence_ok", "b": "evidence_partial", "c": "evidence_bad",
                "d": "evidence_ok"}
    summary, calls, _ = _run(tmp_path, verdicts, unknown)
    assert summary["exit_code"] == 0 and summary["mode"] == "dry_run"
    v = summary["dry_run_summary"]["verdicts"]
    assert (v["ok"], v["partial"], v["bad"], v["unknown"]) == (2, 1, 1, 4)
    assert summary["dry_run_summary"]["ok"] == ["a", "d"]
    assert summary["dry_run_summary"]["by_reason"]["stale_gt_7d"] == 1
    assert summary["dry_run_summary"]["by_reason"]["no_parquet"] == 1
    assert summary["apply"]["requested"] is False
    assert len(calls) == 1 and calls[0]["apply"] is False
    log = (tmp_path / "post_sync_qualify.log").read_text(encoding="utf-8")
    assert "start mode=dry_run" in log and "done mode=dry_run" in log


# ---------- 2. 仅 ok 写回 + after/upgraded ----------
def test_apply_writes_only_ok_tables(tmp_path):
    unknown = ["a", "b", "c", "d"]
    verdicts = {"a": "evidence_ok", "b": "evidence_partial", "c": "evidence_bad",
                "d": "evidence_ok"}
    summary, calls, _ = _run(tmp_path, verdicts, unknown, apply=True)
    assert summary["exit_code"] == 0
    dry = [c for c in calls if not c["apply"]]
    app = [c for c in calls if c["apply"]]
    assert len(dry) == 1 and dry[0]["tables"] == ["a", "b", "c", "d"]
    assert len(app) == 1 and set(app[0]["tables"]) == {"a", "d"}  # 仅 ok 表
    assert summary["apply"]["applied_tables"] == ["a", "d"]
    assert summary["apply"]["skipped_tables"] == []
    assert summary["apply"]["batches"][0]["validation_ok"] is True
    assert summary["apply"]["batches"][0]["backup"].endswith("bak-fixture")
    assert summary["after"]["unknown_count"] == 2
    assert summary["after"]["upgraded_count"] == 2
    assert set(summary["after"]["upgraded_tables"]) == {"a", "d"}


# ---------- 3. partial/bad 不写 ----------
def test_partial_bad_not_written(tmp_path):
    unknown = ["a", "b"]
    verdicts = {"a": "evidence_partial", "b": "evidence_bad"}
    summary, calls, _ = _run(tmp_path, verdicts, unknown, apply=True)
    assert summary["exit_code"] == 0
    assert [c for c in calls if c["apply"]] == []  # 未调用 --apply
    assert summary["apply"]["applied_tables"] == []
    assert "无 verdict=ok" in (summary["apply"]["error"] or "")
    assert summary["after"]["unknown_count"] == 2  # 未写回


# ---------- 4. 写回失败 → rc≠0 ----------
def test_apply_validation_failure_rc_nonzero(tmp_path):
    unknown = ["a"]
    verdicts = {"a": "evidence_ok"}
    summary, calls, _ = _run(tmp_path, verdicts, unknown, apply=True,
                             apply_validation=False)
    assert summary["exit_code"] == 2
    assert summary["apply"]["failed"] is True
    assert summary["apply"]["applied_tables"] == []
    assert summary["apply"]["batches"][0]["validation_ok"] is False
    log = (tmp_path / "post_sync_qualify.log").read_text(encoding="utf-8")
    assert "apply FAILED" in log


# ---------- 5. 复判失败 → rc≠0 ----------
def test_probe_error_rc_nonzero(tmp_path):
    def probe(tables, apply, timeout):
        return {"rc": 2, "payload": {"code": 2,
                                     "error": "registry.catalog_sha256 与 catalog 不一致"},
                "error": None, "stdout_tail": ""}

    reg_path = _registry(tmp_path / "registry.json", ["a", "b"])
    summary = pq.run(reg_path, apply=False, probe=probe,
                     read_registry_fn=lambda p: json.loads(
                         reg_path.read_text(encoding="utf-8")),
                     log_path=tmp_path / "log.jsonl")
    assert summary["exit_code"] == 2
    assert summary["batches"][0]["error"]
    assert summary.get("error") is None  # registry 本身可读，失败来自 probe


# ---------- 6. 无 unknown → rc=0 ----------
def test_no_unknown_rc_zero(tmp_path):
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps({
        "physical_count": 2, "display_count": 0, "catalog_sha256": "sha",
        "physical_tables": [{"asset_id": "o1", "qualification_status": "evidence_ok"},
                            {"asset_id": "o2", "qualification_status": "deprecated"}],
    }), encoding="utf-8")

    def probe(tables, apply, timeout):  # 不应被调用
        raise AssertionError("no unknown → probe 不应调用")

    summary = pq.run(reg_path, apply=True, probe=probe,
                     read_registry_fn=lambda p: json.loads(reg_path.read_text(encoding="utf-8")),
                     log_path=tmp_path / "log.jsonl")
    assert summary["exit_code"] == 0 and summary["batches"] == []
    assert summary["dry_run_summary"]["verdicts"]["unknown"] == 0
    assert summary["apply"]["applied_tables"] == []


# ---------- 7. 分批上限（dry-run/apply 均 ≤batch-size） ----------
def test_batch_limits(tmp_path):
    unknown = ["t%02d" % i for i in range(45)]
    verdicts = {t: "evidence_ok" for t in unknown}
    summary, calls, _ = _run(tmp_path, verdicts, unknown, apply=True, batch_size=20)
    assert summary["exit_code"] == 0
    dry_sizes = [len(c["tables"]) for c in calls if not c["apply"]]
    app_sizes = [len(c["tables"]) for c in calls if c["apply"]]
    assert dry_sizes == [20, 20, 5]
    assert app_sizes == [20, 20, 5]
    assert all(s <= 20 for s in dry_sizes + app_sizes)
    assert len(summary["apply"]["applied_tables"]) == 45


# ---------- 8. CLI --json/--out + batch-size 校验 + 人类摘要 ----------
def test_cli_json_out_and_size_guard(tmp_path, monkeypatch):
    unknown = ["a", "b"]
    reg_path = _registry(tmp_path / "registry.json", unknown)
    verdicts = {"a": "evidence_ok", "b": "evidence_partial"}
    probe, _calls = _make_probe(verdicts)
    monkeypatch.setattr(pq, "_run_qualify", probe)
    out = tmp_path / "summary.json"
    rc = pq.main(["--dry-run", "--json", "--registry", str(reg_path),
                  "--out", str(out), "--log", str(tmp_path / "log.jsonl"),
                  "--batch-size", "20"])
    assert rc == 0 and out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    for key in ("tool", "mode", "registry", "batches", "dry_run_summary",
                "apply", "after", "exit_code", "elapsed_s"):
        assert key in doc
    assert doc["dry_run_summary"]["verdicts"]["ok"] == 1
    # 人类摘要
    text = pq.render_human(doc)
    assert "post_sync_qualify" in text and "verdicts" in text and "exit_code=0" in text
    # 超上限（>20）→ rc=2
    rc2 = pq.main(["--dry-run", "--registry", str(reg_path),
                   "--batch-size", "21", "--log", str(tmp_path / "log.jsonl")])
    assert rc2 == 2
