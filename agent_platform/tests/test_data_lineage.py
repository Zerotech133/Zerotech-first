# -*- coding: utf-8 -*-
"""数据血缘 MVP 测试（WO-DATA-LINEAGE-20261027）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "data_lineage.py"


def _run(root: Path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def _ok(r):
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_emit_and_dataset_lineage(tmp_path):
    out = _ok(_run(tmp_path, "emit", "--job", "ts_daily_sync", "--status", "COMPLETE",
                   "--run-id", "r1",
                   "--inputs", json.dumps([{"namespace": "table", "name": "tdx_raw"}]),
                   "--outputs", json.dumps([{"namespace": "file", "name": "ts_daily",
                                             "version": "20261026"}])))
    assert out["run"]["run_id"] == "r1"
    ds = _ok(_run(tmp_path, "dataset", "--name", "ts_daily"))
    assert ds["runs"] and ds["runs"][0]["job"] == "ts_daily_sync"
    assert ds["runs"][0]["inputs"] == ["tdx_raw"]


def test_manifest_hash_and_check(tmp_path):
    f = tmp_path / "out.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    _ok(_run(tmp_path, "emit", "--job", "freeze", "--status", "COMPLETE", "--run-id", "r-freeze",
             "--inputs", json.dumps([{"name": "ts_daily"}]),
             "--outputs", json.dumps([{"namespace": "file", "name": "lcs1_panel",
                                       "uri": "out.csv", "version": "v1"}])))
    man = _ok(_run(tmp_path, "manifest", "--datasets", json.dumps(["lcs1_panel"])))
    item = man["datasets"]["lcs1_panel"]
    assert item["sha256"] and item["produced_by"]["run_id"] == "r-freeze"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "data_version": "x", "sample_in": "x", "sample_out": "x", "cost_model": "x",
        "lineage": {"datasets": [{"name": "lcs1_panel", "version": "v1",
                                  "sha256": item["sha256"], "produced_by": "r-freeze",
                                  "uri": "out.csv"}]},
    }, ensure_ascii=False), encoding="utf-8")
    chk = _ok(_run(tmp_path, "check", "--receipt", str(receipt)))
    assert chk["ok"] is True


def test_check_missing_lineage_and_bad_hash(tmp_path):
    r = tmp_path / "bad.json"
    r.write_text(json.dumps({"data_version": "x"}, ensure_ascii=False), encoding="utf-8")
    proc = _run(tmp_path, "check", "--receipt", str(r))
    assert proc.returncode == 2
    f = tmp_path / "x.csv"
    f.write_text("1", encoding="utf-8")
    r.write_text(json.dumps({"lineage": {"datasets": [
        {"name": "d", "version": "v", "sha256": "deadbeef", "produced_by": "r", "uri": "x.csv"}
    ]}}, ensure_ascii=False), encoding="utf-8")
    proc = _run(tmp_path, "check", "--receipt", str(r))
    assert proc.returncode == 2
    assert "sha256_mismatch" in proc.stdout


def test_dry_run_zero_write(tmp_path):
    out = _ok(_run(tmp_path, "--dry-run", "emit", "--job", "j", "--status", "START"))
    assert out["dry_run"] is True
    assert not (tmp_path / "agent_platform" / "data" / "lineage").exists()
