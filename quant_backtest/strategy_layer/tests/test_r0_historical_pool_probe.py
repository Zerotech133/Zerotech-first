"""历史多日 CN 池：仅内存事后截取；真实池只核查 20261019。"""
import hashlib
import json
from pathlib import Path

import pytest

from strategy_layer import aggregator, evidence_intake, xunyou, xunyou_brief
from strategy_layer import r0_historical_pool_probe as probe


DAY = "20261019"
REAL_POOL = Path(__file__).resolve().parents[2] / "data" / "chanlun_pool.jsonl"


def row(kind="1买", dt="2026-10-19", **changes):
    item = {"market": "CN", "code": "002633.SZ", "type": kind,
            "dt": dt, "detect_date": "2026-10-19", "price": 10.0}
    item.update(changes)
    return item


def source(tmp_path, rows):
    path = tmp_path / "pool.jsonl"
    path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n"
                              for r in rows))
    return path


def forbidden(*_args, **_kwargs):
    pytest.fail("禁止进入生产、PG、落盘或影子作业路径")


@pytest.fixture(autouse=True)
def block_writers(monkeypatch):
    monkeypatch.setattr(aggregator.SignalAggregator, "run", forbidden)
    monkeypatch.setattr(aggregator, "persist_combined", forbidden)
    monkeypatch.setattr(aggregator, "_pg_connect", forbidden)
    monkeypatch.setattr(aggregator.subprocess, "run", forbidden)
    monkeypatch.setattr(evidence_intake, "record_pool_intake", forbidden)
    monkeypatch.setattr(xunyou, "write_upstream_report", forbidden)
    monkeypatch.setattr(xunyou, "_atomic_write", forbidden)
    monkeypatch.setattr(xunyou_brief, "build_brief", forbidden)
    monkeypatch.setattr(xunyou_brief, "_atomic_write", forbidden)


def test_multiday_snapshot_scheme_c_brief_and_one_read(tmp_path, monkeypatch):
    path = source(tmp_path, [row("1买", "2026-10-18", detect_date="2026-10-18"),
                             row("1买", "2026-10-10"), row("1卖", "2026-10-19"),
                             row("2买", "2026-10-19", code="002634.SZ")])
    original = Path.read_bytes
    calls = []

    def read_once(self):
        calls.append(self)
        data = original(self)
        if self == path:
            path.write_bytes(b"changed after read\n")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_once)
    result = probe.inspect_historical_pool(DAY, path)
    assert calls == [path.resolve()]
    assert result["source"]["sha256"] == hashlib.sha256(
        b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n"
                 for r in [row("1买", "2026-10-18", detect_date="2026-10-18"),
                           row("1买", "2026-10-10"), row("1卖", "2026-10-19"),
                           row("2买", "2026-10-19", code="002634.SZ")])).hexdigest()
    assert result["counts"] == {"source_events": 4, "matched_events": 3,
                                "signals": 3, "kept": 2, "combined": 2, "pool": 1}
    assert result["combined"][0]["action"] == "sell"
    assert result["combined"][0]["contributions"]["chan_monitor"]["superseded"][0]["action"] == "buy"
    assert result["pool"]["pool"][0]["symbol"] == "002634.SZ"
    assert result["brief"]["scheme_c"]["superseded"] == 1
    assert result["brief"]["pool_summary"]["buy_total"] == 1
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["brief"]["receipt_status"] == "NOT_VERIFIED"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
    assert not (tmp_path / "research").exists()


@pytest.mark.parametrize("bad", [
    {"market": "HK", "code": "002633.SZ"},
    {"code": "00700.HK"}, {"code": "AAPL.US"},
    {"detect_date": "2026-02-30"}, {"dt": "2026-10-20"},
    {"price": 0}, {"price": True}, {"type": "other"},
    {"mbei": "true"},
])
def test_invalid_row_any_day_fails_closed(tmp_path, bad):
    path = source(tmp_path, [row(), row(**{"detect_date": "2026-10-18", "dt": "2026-10-18", **bad})])
    result = probe.inspect_historical_pool(DAY, path)
    assert result["status"] == "INVALID_SOURCE"
    assert result["counts"]["matched_events"] is None
    assert result["counts"]["signals"] is None
    assert result["brief"]["pool_summary"] is None
    assert "第 2 行" in result["failures"][0]["reason"]


@pytest.mark.parametrize("raw", [b'{"market":"CN","market":"CN"}\n',
                                   b'{"market":"CN",}\n', b'\n',
                                   b'{"price":NaN}\n', b'\xff\n'])
def test_malformed_jsonl_fails_closed(tmp_path, raw):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(raw)
    assert probe.inspect_historical_pool(DAY, path)["status"] == "INVALID_SOURCE"


@pytest.mark.parametrize("day", ["20260230", "2026-10-19", "２０２６１０１９", "", None])
def test_business_day_must_be_explicit_and_valid(tmp_path, day):
    with pytest.raises(ValueError):
        probe.inspect_historical_pool(day, tmp_path / "missing.jsonl")


def test_no_match_is_not_a_trusted_zero_and_missing_is_not_zero(tmp_path):
    path = source(tmp_path, [row(detect_date="2026-10-18", dt="2026-10-18")])
    result = probe.inspect_historical_pool(DAY, path)
    assert result["status"] == "NO_MATCH_UNVERIFIED"
    assert result["counts"]["source_events"] == 1
    assert result["counts"]["signals"] is None
    assert result["counts"]["pool"] is None
    assert result["brief"]["pool_summary"] is None
    assert any("未匹配" in risk for risk in result["brief"]["risks"])
    missing = probe.inspect_historical_pool(DAY, tmp_path / "absent.jsonl")
    assert missing["status"] == "INVALID_SOURCE"
    assert missing["source"]["sha256"] is None
    assert missing["counts"]["source_events"] is None


def test_partial_conversion_fails_closed(tmp_path, monkeypatch):
    path = source(tmp_path, [row()])
    monkeypatch.setattr(probe.ChanMonitorAdapter, "_convert", lambda *_: None)
    result = probe.inspect_historical_pool(DAY, path)
    assert result["status"] == "CONVERSION_ERROR"
    assert result["counts"]["signals"] is None
    assert result["brief"]["receipt_status"] == "NOT_VERIFIED"


def test_real_multiday_pool_historical_read_only():
    assert REAL_POOL.is_file(), "真实历史池缺失，不能将缺源视为零事件"
    before = REAL_POOL.stat()
    result = probe.inspect_historical_pool(DAY, REAL_POOL)
    after = REAL_POOL.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert result["source"]["sha256"] and len(result["source"]["sha256"]) == 64
    assert result["counts"]["source_events"] is not None
    assert result["status"] in ("UNVERIFIED", "NO_MATCH_UNVERIFIED")
    if result["status"] == "UNVERIFIED":
        assert result["counts"]["matched_events"] > 0
        assert result["counts"]["signals"] == result["counts"]["matched_events"]
    else:
        assert result["counts"]["signals"] is None
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["brief"]["receipt_status"] == "NOT_VERIFIED"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
