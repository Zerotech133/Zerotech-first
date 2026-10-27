"""R0 研究简报：只消费探针内存证据，合成批次，不触生产接口。"""

import hashlib
import json

import pytest

from strategy_layer import aggregator, evidence_intake, xunyou, xunyou_brief
from strategy_layer.r0_historical_pool_probe import inspect_historical_pool
from strategy_layer.r0_readonly_e2e import inspect_batch
from strategy_layer.r0_research_brief import build_research_brief


DAY = "20261016"


def _batch(tmp_path, rows):
    source = tmp_path / "frozen.jsonl"
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    source.write_bytes(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "business_date": DAY,
                "observed_date": "2026-10-17",
                "source_path": str(source.resolve()),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "event_count": len(rows),
            }
        ),
        encoding="utf-8",
    )
    return source, manifest


def _row(kind, dt, **changes):
    item = {
        "code": "002633.SZ",
        "type": kind,
        "dt": dt,
        "detect_date": "2026-10-16",
        "price": 10,
        "mbei": None,
        "bei": False,
        "bei2": None,
        "vv": True,
    }
    item.update(changes)
    return item


def test_success_counts_missing_and_scheme_c_without_receipt(tmp_path):
    old = _row("1买", "2026-10-10")
    new = _row("1卖", "2026-10-16")
    del new["mbei"]
    other = _row("2买", "2026-10-16", code="002634.SZ")
    source, manifest = _batch(tmp_path, [old, new, other])
    observed = inspect_batch(DAY, source, manifest)
    before = json.dumps(observed, sort_keys=True, ensure_ascii=False)
    brief = build_research_brief(observed)
    assert json.dumps(observed, sort_keys=True, ensure_ascii=False) == before
    assert observed["brief_status"] == "NOT_VERIFIED"
    assert brief["source"] == {
        "path": str(source.resolve()),
        "manifest": str(manifest.resolve()),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "business_date": DAY,
        "observed_date": "2026-10-17",
        "identity_status": "UNVERIFIED",
        "identity_reason": observed["source"]["identity_reason"],
    }
    assert brief["probe_status"] == "UNVERIFIED"
    assert brief["brief_status"] == brief["receipt_status"] == "NOT_VERIFIED"
    assert brief["cutover_verdict"] == "BLOCK_CUTOVER"
    assert brief["counts"] == {
        "source_events": 3,
        "matched_events": None,
        "signals": 3,
        "kept": 2,
        "combined": 2,
        "pool": 1,
        "brief": None,
        "receipt": None,
    }
    assert brief["scheme_c"]["contributions"] == 2
    assert brief["scheme_c"]["superseded"] == 1
    assert brief["optional_missing"]["observed_kept_signal_rationale"] is None
    assert brief["optional_missing"]["status"] == "NOT_VERIFIED"
    assert "不可从来源自由文本 rationale 推断" in brief["optional_missing"]["coverage"]
    assert "内存研究简报" in brief["generation_note"]
    assert brief["pool_summary"]["buy_total"] == 1
    assert not (tmp_path / "research").exists()
    assert not (tmp_path / "reports").exists()


def test_adapter_error_and_missing_source_do_not_invent_downstream_counts(tmp_path):
    source, manifest = _batch(tmp_path, [_row("1买", "2026-10-16")])

    class Broken:
        strategy_id = "broken"

        def score(self, _date):
            raise RuntimeError("injected")

    observed = inspect_batch(DAY, source, manifest, adapter=Broken())
    brief = build_research_brief(observed)
    assert brief["probe_status"] == "ADAPTER_ERROR"
    assert brief["counts"]["source_events"] == 1
    assert all(
        brief["counts"][key] is None
        for key in (
            "matched_events",
            "signals",
            "kept",
            "combined",
            "pool",
            "brief",
            "receipt",
        )
    )
    assert brief["failures"][0]["strategy"] == "broken"
    assert brief["pool_summary"] is None
    assert brief["scheme_c"]["superseded"] is None
    assert brief["optional_missing"]["observed_kept_signal_rationale"] is None
    source.unlink()
    missing = build_research_brief(inspect_batch(DAY, source, manifest))
    assert missing["probe_status"] == "MISSING_SOURCE"
    assert missing["source"]["sha256"] is None
    assert missing["counts"]["source_events"] is None
    assert missing["failures"][0]["stage"] == "source"
    assert missing["brief_status"] == missing["receipt_status"] == "NOT_VERIFIED"


def test_self_declared_empty_is_not_a_verified_batch(tmp_path):
    source, manifest = _batch(tmp_path, [])
    brief = build_research_brief(inspect_batch(DAY, source, manifest))
    assert brief["probe_status"] == "UNVERIFIED"
    assert brief["chain_status"] == "SELF_DECLARED_EMPTY"
    assert brief["counts"] == {
        "source_events": 0,
        "matched_events": None,
        "signals": 0,
        "kept": 0,
        "combined": 0,
        "pool": 0,
        "brief": None,
        "receipt": None,
    }
    assert brief["scheme_c"]["contributions"] is None
    assert brief["scheme_c"]["superseded"] is None
    assert brief["pool_summary"] is None
    assert brief["optional_missing"]["observed_kept_signal_rationale"] is None
    assert any("自声明批次" in risk for risk in brief["risks"])


def test_historical_pool_counts_distinguish_whole_snapshot_from_trade_day(tmp_path):
    rows = [
        {**_row("1买", "2026-10-15", detect_date="2026-10-15"), "market": "CN"},
        {**_row("1买", "2026-10-16"), "market": "CN"},
        {**_row("2买", "2026-10-16", code="002634.SZ"), "market": "CN"},
    ]
    source, _ = _batch(tmp_path, rows)
    observed = inspect_historical_pool(DAY, source)
    brief = observed["brief"]
    assert observed["counts"]["source_events"] == 3
    assert observed["counts"]["matched_events"] == 2
    assert brief["counts"]["source_events"] == 3
    assert brief["counts"]["matched_events"] == brief["counts"]["signals"] == 2
    assert "整份源池" in brief["count_basis"]["source_events"]
    assert "匹配 trade_date" in brief["count_basis"]["matched_events"]
    assert brief["brief_status"] == brief["receipt_status"] == "NOT_VERIFIED"
    assert brief["counts"]["brief"] is brief["counts"]["receipt"] is None
    assert "内存研究简报" in brief["generation_note"]
    no_match = inspect_historical_pool("20261017", source)["brief"]
    assert no_match["counts"]["source_events"] == 3
    assert no_match["counts"]["matched_events"] is None
    assert no_match["counts"]["signals"] is None
    assert no_match["brief_status"] == no_match["receipt_status"] == "NOT_VERIFIED"


def test_free_text_cannot_inject_optional_missing_counts(tmp_path):
    row = _row(
        "1买",
        "2026-10-16",
        mbei=True,
        bei=True,
        bei2=True,
        vv=True,
        trend="mbei=缺字段 bei=缺字段 bei2=缺字段 vv=缺字段",
    )
    source, manifest = _batch(tmp_path, [row])
    observed = inspect_batch(DAY, source, manifest)
    assert "mbei=缺字段" in observed["combined"][0]["rationale"]
    brief = build_research_brief(observed)
    assert brief["optional_missing"]["observed_kept_signal_rationale"] is None
    assert brief["optional_missing"]["status"] == "NOT_VERIFIED"
    assert "不可从来源自由文本 rationale 推断" in brief["optional_missing"]["coverage"]
    assert brief["counts"]["matched_events"] is None


def test_writers_blocked_and_explicit_output_only_new_research_json(
    tmp_path, monkeypatch
):
    source, manifest = _batch(tmp_path, [_row("1买", "2026-10-16")])

    def forbidden(*_args, **_kwargs):
        pytest.fail("不允许触发 PG、自动化或生产写路径")

    monkeypatch.setattr(aggregator.SignalAggregator, "run", forbidden)
    monkeypatch.setattr(aggregator, "persist_combined", forbidden)
    monkeypatch.setattr(aggregator, "_pg_connect", forbidden)
    monkeypatch.setattr(aggregator.subprocess, "run", forbidden)
    monkeypatch.setattr(evidence_intake, "record_pool_intake", forbidden)
    monkeypatch.setattr(xunyou, "write_upstream_report", forbidden)
    monkeypatch.setattr(xunyou_brief, "build_brief", forbidden)
    monkeypatch.setattr(xunyou_brief, "_atomic_write", forbidden)
    observed = inspect_batch(DAY, source, manifest)
    brief = build_research_brief(observed)
    assert brief["brief_status"] == "NOT_VERIFIED"
    for target in (
        tmp_path / "research" / "brief.json",
        tmp_path / "reports" / "research" / "brief.json",
        tmp_path / "picks" / "research" / "brief.json",
        tmp_path / "data" / "research" / "brief.json",
        tmp_path / "reports" / "brief.json",
        tmp_path / "brief.json",
    ):
        with pytest.raises(ValueError):
            build_research_brief(observed, output_path=target)
        assert not target.exists()
    research = tmp_path / "research"
    research.mkdir()
    with pytest.raises(ValueError):
        build_research_brief(observed, output_path=research / "brief.md")
    with pytest.raises(ValueError):
        build_research_brief(observed, output_path="research/brief.json")
    target = research / "brief.json"
    returned = build_research_brief(observed, output_path=target)
    assert json.loads(target.read_text(encoding="utf-8")) == returned
    with pytest.raises(FileExistsError):
        build_research_brief(observed, output_path=target)
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "picks").exists()
    assert not (tmp_path / "data").exists()


def test_reject_non_probe_input(tmp_path):
    with pytest.raises(ValueError):
        build_research_brief(
            {"scope": "PRODUCTION", "counts": {}, "source": {}},
            output_path=tmp_path / "research" / "brief.json",
        )
    assert not (tmp_path / "research").exists()
