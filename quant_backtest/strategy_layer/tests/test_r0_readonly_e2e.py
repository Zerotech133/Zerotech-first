"""R0-E 冻结 CN 批次的只读、纯内存验收。"""
import hashlib
import json

import pytest

from strategy_layer import Signal
from strategy_layer import aggregator
from strategy_layer.r0_readonly_e2e import inspect_batch, main, write_research_evidence


DAY = "20261016"


def batch(tmp_path, rows, *, day=DAY, declared=None):
    source = tmp_path / "frozen.jsonl"
    raw = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    source.write_bytes(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "business_date": day, "observed_date": "2026-10-17",
        "source_path": str(source.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": len(rows) if declared is None else declared,
    }), encoding="utf-8")
    return source, manifest


def row(kind, dt, symbol="002633.SZ"):
    return {"code": symbol, "type": kind, "dt": dt,
            "detect_date": "2026-10-16", "price": 10}


def test_success_scheme_c_and_pool_without_writers(tmp_path, monkeypatch):
    source, manifest = batch(tmp_path, [row("1买", "2026-10-10"),
                                        row("1卖", "2026-10-16")])
    def forbidden(*_args, **_kwargs):
        raise AssertionError("生产写接口不得触发")
    monkeypatch.setattr(aggregator.SignalAggregator, "run", forbidden)
    monkeypatch.setattr(aggregator, "persist_combined", forbidden)
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["counts"]["source_events"] == 2
    assert result["counts"]["signals"] == 2
    assert result["counts"]["combined"] == 1
    assert result["combined"][0]["action"] == "sell"
    assert result["combined"][0]["divergence"] is False
    assert result["combined"][0]["contributions"]["chan_monitor"]["superseded"][0]["action"] == "buy"
    assert result["pool"]["stats"]["buy_total"] == 0
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
    assert not (tmp_path / "research").exists()


def test_missing_source_is_not_zero(tmp_path):
    source, manifest = batch(tmp_path, [])
    source.unlink()
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "MISSING_SOURCE"
    assert result["pool_status"] == "NOT_VERIFIED"


def test_wrong_business_day_is_not_zero(tmp_path):
    source, manifest = batch(tmp_path, [], day="20261015")
    assert inspect_batch(DAY, source, manifest)["status"] == "WRONG_DAY"
    source, manifest = batch(tmp_path, [row("1买", "2026-10-10")])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["business_date"] = DAY
    manifest.write_text(json.dumps(data), encoding="utf-8")
    source.write_text(source.read_text(encoding="utf-8").replace("2026-10-16", "2026-10-15"), encoding="utf-8")
    data["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert inspect_batch(DAY, source, manifest)["status"] == "WRONG_DAY"


def test_self_declared_empty_batch_is_not_verified_zero(tmp_path):
    source, manifest = batch(tmp_path, [])
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED"
    assert result["chain_status"] == "SELF_DECLARED_EMPTY"
    assert result["counts"]["combined"] == 0
    assert result["source"]["sha256"] == hashlib.sha256(b"").hexdigest()


def test_adapter_exception_and_partial_failure_not_zero(tmp_path):
    source, manifest = batch(tmp_path, [row("1买", "2026-10-16")])
    class Broken:
        strategy_id = "broken"
        def score(self, _date):
            raise RuntimeError("injected")
    result = inspect_batch(DAY, source, manifest, adapter=Broken())
    assert result["status"] == "ADAPTER_ERROR"
    assert result["pool_status"] == "NOT_VERIFIED"
    assert result["failures"][0]["strategy"] == "broken"
    class Partial:
        strategy_id = "partial"
        def score(self, date):
            return [Signal("002633.SZ", "buy", 80, .8, "synthetic", "partial", date)]
    source, manifest = batch(tmp_path, [row("1买", "2026-10-16"), row("2买", "2026-10-16")])
    assert inspect_batch(DAY, source, manifest, adapter=Partial())["status"] == "ADAPTER_ERROR"


def test_hash_count_and_adapter_silent_empty_are_not_zero(tmp_path):
    source, manifest = batch(tmp_path, [row("1买", "2026-10-16")], declared=2)
    assert inspect_batch(DAY, source, manifest)["status"] == "INVALID_SOURCE"
    source, manifest = batch(tmp_path, [row("unknown", "2026-10-16")])
    assert inspect_batch(DAY, source, manifest)["status"] == "INVALID_SOURCE"
    class Silent:
        strategy_id = "silent"
        def score(self, _date):
            return []
    source, manifest = batch(tmp_path, [row("1买", "2026-10-16")])
    assert inspect_batch(DAY, source, manifest, adapter=Silent())["status"] == "ADAPTER_ERROR"
    source.write_bytes(source.read_bytes() + b"\n")
    assert inspect_batch(DAY, source, manifest)["status"] == "INVALID_SOURCE"


def test_self_declared_replacement_and_swap_use_verified_snapshot(tmp_path):
    source, manifest = batch(tmp_path, [row("1买", "2026-10-16")])
    source, manifest = batch(tmp_path, [row("1卖", "2026-10-16")])
    replaced = inspect_batch(DAY, source, manifest)
    assert replaced["status"] == "UNVERIFIED"
    assert replaced["chain_status"] == "VERIFIED_IN_MEMORY"
    assert replaced["combined"][0]["score"] == 5.0

    source, manifest = batch(tmp_path, [row("1买", "2026-10-16")])
    original = source.read_bytes()
    substituted = (json.dumps(row("3卖", "2026-10-16"), ensure_ascii=False) + "\n").encode("utf-8")

    class Swap:
        strategy_id = "chan_monitor"

        def score(self, day):
            from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter
            source.write_bytes(substituted)
            try:
                return ChanMonitorAdapter(pool_globs=[str(source)]).score(day)
            finally:
                source.write_bytes(original)

    result = inspect_batch(DAY, source, manifest, adapter=Swap())
    assert result["status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["combined"][0]["score"] == 95.0


def test_cli_self_declared_batch_is_not_success(tmp_path, capsys):
    source, manifest = batch(tmp_path, [])
    assert main(["--trade-date", DAY, "--source", str(source),
                 "--manifest", str(manifest)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "UNVERIFIED"


def test_output_only_explicit_research_target_and_never_overwrite(tmp_path):
    source, manifest = batch(tmp_path, [])
    result = inspect_batch(DAY, source, manifest)
    with pytest.raises(ValueError):
        write_research_evidence(result, tmp_path / "production" / "out.json")
    research = tmp_path / "research"
    research.mkdir()
    target = research / "evidence.json"
    write_research_evidence(result, target)
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "UNVERIFIED"
    with pytest.raises(FileExistsError):
        write_research_evidence(result, target)
    assert not (tmp_path / "production").exists()
