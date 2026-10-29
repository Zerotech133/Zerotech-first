# -*- coding: utf-8 -*-
"""LCS-1 最小 harness A1/A2/A4 测试（WO-LCS1-HARNESS-MIN-20261029）。"""
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "lcs1_harness.py"
REPO = TOOL.parents[1]


def _run(*args):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def _write_inputs(root: Path):
    (root / "ts_daily").mkdir(parents=True)
    (root / "ts_adj_factor").mkdir()
    (root / "ts_daily_basic").mkdir()
    for name in ("ts_daily", "ts_adj_factor", "ts_daily_basic"):
        (root / name / "p0.parquet").write_bytes(b"fake-" + name.encode())
    (root / "ts_stock_basic.parquet").write_bytes(b"fake-stock-basic")


def _config(tmp: Path) -> Path:
    p = tmp / "config.json"
    p.write_text(json.dumps({
        "topic_id": "AC-TEST",
        "data_version": "fake@1", "sample_in": "2025-01-01~2025-06-30",
        "sample_out": "2025-07-01~2025-12-31", "cost_model": "fee:1bp",
        "strategy_spec": "deliverables/spec.md",
        "tables": ["ts_daily", "ts_adj_factor", "ts_daily_basic", "ts_stock_basic.parquet"],
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_plan_dry_run_no_write(tmp_path):
    root = tmp_path / "data"
    _write_inputs(root)
    out = tmp_path / "out"
    proc = _run("plan", "--config", str(_config(tmp_path)), "--data-root", str(root))
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    assert len(plan["fingerprint"]) == 64
    assert plan["input_files"] == 4
    assert not out.exists()


def test_missing_config_field_rejected(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"topic_id": "x"}), encoding="utf-8")
    proc = _run("plan", "--config", str(cfg))
    assert proc.returncode == 2
    assert "config_missing_fields" in proc.stdout


def test_run_verify_and_reproducible(tmp_path):
    root = tmp_path / "data"
    _write_inputs(root)
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    proc = _run("run", "--config", str(cfg), "--data-root", str(root), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    manifest = out / "manifest.json"
    assert manifest.is_file()
    verify = _run("verify", "--manifest", str(manifest))
    assert verify.returncode == 0, verify.stdout
    assert json.loads(verify.stdout)["ok"] is True
    # A2: 两次 plan 的 fingerprint 一致
    p1 = json.loads(_run("plan", "--config", str(cfg), "--data-root", str(root)).stdout)
    p2 = json.loads(_run("plan", "--config", str(cfg), "--data-root", str(root)).stdout)
    assert p1["fingerprint"] == p2["fingerprint"]


def test_tamper_verify_fails(tmp_path):
    root = tmp_path / "data"
    _write_inputs(root)
    out = tmp_path / "out"
    assert _run("run", "--config", str(_config(tmp_path)), "--data-root", str(root),
                "--out", str(out)).returncode == 0
    (root / "ts_daily" / "p0.parquet").write_bytes(b"tampered")
    verify = _run("verify", "--manifest", str(out / "manifest.json"))
    assert verify.returncode == 1
    assert json.loads(verify.stdout)["ok"] is False


def test_real_default_config_plan():
    receipt = REPO / "agent_platform/data/academy/freeze_receipts/AC-20261017-LCS1.json"
    if not receipt.is_file():
        import pytest
        pytest.skip("worktree 无运行时冻结回执（主仓合并后验证）")
    proc = _run("plan", "--data-root", str(REPO / "quant_backtest" / "data" / "tdx"))
    if proc.returncode != 0:
        # 数据不可用时只要求显式错误，不假绿
        assert proc.returncode == 2
        assert "config" in proc.stdout or "missing" in proc.stdout
        return
    plan = json.loads(proc.stdout)
    assert plan["config"]["data_version"]
    assert plan["fingerprint"]
