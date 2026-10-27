# -*- coding: utf-8 -*-
"""A4 市场覆盖快照探针（quant_backtest/a4_market_coverage_probe.py）单测。

覆盖：池文件扫描状态、dated 快照结构与落盘、运行留痕 run_log.jsonl
（ok/error 两类，error 仍向上抛异常保持 fail-closed）。
"""

import json

import pytest

import a4_market_coverage_probe as probe


def _write_pool(path, rows, raw_lines=()):
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    lines.extend(raw_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ok_row(code, day):
    return {"code": code, "type": "1买", "detect_date": day, "dt": day}


def _patch_paths(monkeypatch, tmp_path, pools):
    report_dir = tmp_path / "reports"
    run_log = report_dir / "a4_market_coverage_snap" / "run_log.jsonl"
    monkeypatch.setattr(probe, "POOLS", pools)
    monkeypatch.setattr(probe, "REPORT_DIR", report_dir)
    monkeypatch.setattr(probe, "RUN_LOG", run_log)
    return report_dir, run_log


def test_scan_pool_ok_counts(tmp_path):
    pool = _write_pool(
        tmp_path / "cn.jsonl",
        [
            _ok_row("000001.SZ", "2026-10-16"),
            _ok_row("000002.SZ", "2026-10-16"),
            _ok_row("000001.SZ", "2026-10-15"),
        ],
    )
    result = probe.scan_pool(pool)
    assert result["status"] == "OK"
    assert result["rows"] == 3
    assert result["unique_codes"] == 2
    assert result["bad_json"] == 0
    assert result["per_date_counts"] == {"2026-10-15": 1, "2026-10-16": 2}
    assert result["stats"]["distinct_detect_dates"] == 2
    assert result["stats"]["date_range"] == ["2026-10-15", "2026-10-16"]


def test_scan_pool_missing_and_bad_json(tmp_path):
    missing = probe.scan_pool(tmp_path / "nope.jsonl")
    assert missing["status"] == "FILE_MISSING"
    assert missing["path"].endswith("nope.jsonl")

    pool = tmp_path / "bad.jsonl"
    pool.write_text('{"code": "000001.SZ"}\nnot-json\n[1, 2]\n', encoding="utf-8")
    result = probe.scan_pool(pool)
    assert result["rows"] == 1
    assert result["bad_json"] == 2  # not-json 与非对象行均计入


def test_main_writes_snapshot_and_ok_runlog(monkeypatch, tmp_path):
    cn = _write_pool(
        tmp_path / "cn.jsonl",
        [
            _ok_row("000001.SZ", "2026-10-16"),
            _ok_row("000002.SZ", "2026-10-15"),
        ],
    )
    us = _write_pool(
        tmp_path / "us.jsonl",
        [
            _ok_row("AAPL.US", "2026-10-16"),
        ],
    )
    hk = _write_pool(
        tmp_path / "hk.jsonl",
        [
            _ok_row("00700.HK", "2026-10-16"),
        ],
    )
    report_dir, run_log = _patch_paths(
        monkeypatch, tmp_path, {"CN": cn, "US": us, "HK": hk}
    )

    report = probe.main()
    assert report["verdict"] == "NO_MARKET_SCOPE_DECISION"

    snaps = sorted((report_dir / "a4_market_coverage_snap").glob("2*.json"))
    assert len(snaps) == 1
    snap = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert snap["scope"] == "LOCAL_POOL_FILE_OBSERVATION_NOT_SUPPLY_CERTIFICATION"
    assert snap["snapshot_at"] == report["snapshot_at"]
    for code in ("CN", "US", "HK"):
        assert snap["markets"][code]["status"] == "OK"
        assert snap["markets"][code]["rows"] >= 1
        assert "latest_dates" in snap["markets"][code]

    log_lines = run_log.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["event"] == "ok"
    assert entry["markets"] == {"CN": "OK", "US": "OK", "HK": "OK"}


def test_main_fail_open_but_records_missing_hk(monkeypatch, tmp_path):
    cn = _write_pool(tmp_path / "cn.jsonl", [_ok_row("000001.SZ", "2026-10-16")])
    us = _write_pool(tmp_path / "us.jsonl", [_ok_row("AAPL.US", "2026-10-16")])
    report_dir, run_log = _patch_paths(
        monkeypatch, tmp_path, {"CN": cn, "US": us, "HK": tmp_path / "no_hk.jsonl"}
    )

    report = probe.main()  # HK 缺失不阻断：观测连续性优先，状态显式记录

    snaps = list((report_dir / "a4_market_coverage_snap").glob("2*.json"))
    assert len(snaps) == 1
    snap = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert snap["markets"]["HK"] == {"status": "FILE_MISSING"}
    assert report["markets"]["HK"]["status"] == "FILE_MISSING"
    entry = json.loads(run_log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["event"] == "ok"
    assert entry["markets"]["HK"] == "FILE_MISSING"


def test_main_error_logged_then_raised(monkeypatch, tmp_path):
    cn = _write_pool(tmp_path / "cn.jsonl", [_ok_row("000001.SZ", "2026-10-16")])
    blocker = tmp_path / "reports_blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    run_log = tmp_path / "elsewhere" / "run_log.jsonl"
    monkeypatch.setattr(probe, "POOLS", {"CN": cn, "US": cn, "HK": cn})
    monkeypatch.setattr(probe, "REPORT_DIR", blocker)
    monkeypatch.setattr(probe, "RUN_LOG", run_log)

    with pytest.raises(FileExistsError):
        probe.main()

    entry = json.loads(run_log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["event"] == "error"
    assert entry["error_type"] == "FileExistsError"
