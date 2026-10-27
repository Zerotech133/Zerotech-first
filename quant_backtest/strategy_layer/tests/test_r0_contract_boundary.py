"""R0-Q 独立契约反例：仅合成临时批次与本地源池只读观察。"""

import hashlib
import json
from pathlib import Path

import pytest

from strategy_layer import aggregator
from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter
from strategy_layer.r0_readonly_e2e import inspect_batch


DAY = "20261019"
REAL_POOL = Path(__file__).resolve().parents[2] / "data" / "chanlun_pool.jsonl"


def _row(kind="2买", **changes):
    row = {
        "market": "CN",
        "code": "002633.SZ",
        "type": kind,
        "dt": "2026-10-18",
        "detect_date": "2026-10-19",
        "price": 10.0,
        "mbei": None,
        "bei": None,
        "bei2": None,
        "vv": True,
    }
    row.update(changes)
    return row


def _batch(tmp_path, rows):
    source = tmp_path / "source.jsonl"
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode(
        "utf-8"
    )
    source.write_bytes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "business_date": DAY,
                "observed_date": "2026-10-20",
                "source_path": str(source.resolve()),
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "event_count": len(rows),
            }
        ),
        encoding="utf-8",
    )
    return source, manifest


def test_real_cn_pool_schema_is_readable_but_not_a_single_frozen_day(tmp_path):
    """活池文件只做契约观察，不钉死字节态（WO-QT-20261021-058 判例：
    例行刷新合法变更 sha/行数/最新日，活文件断言必须动态化）。"""
    raw = REAL_POOL.read_bytes()
    rows = [
        json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()
    ]
    assert rows, "活池文件不得为空"
    # 多日池本质：≥2 个 detect_date；"多日池不可伪装为单日冻结文件"契约保留
    days = {row["detect_date"] for row in rows}
    assert len(days) >= 2
    assert all(
        {"code", "type", "dt", "detect_date", "price", "mbei", "bei", "bei2", "vv"}
        <= row.keys()
        for row in rows
    )
    # 源行的可选字段为真实 null/False（全文件维度，不依赖最新日分布）
    assert any(row["mbei"] is None for row in rows)
    assert any(row["vv"] is False for row in rows)
    latest_day = max(days)
    latest = [row for row in rows if row["detect_date"] == latest_day]
    assert latest
    converted = ChanMonitorAdapter(pool_globs=[str(REAL_POOL)]).score(
        latest_day.replace("-", "")
    )
    assert len(converted) == len(latest)
    assert (
        hashlib.sha256(REAL_POOL.read_bytes()).hexdigest()
        == hashlib.sha256(raw).hexdigest()
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "business_date": DAY,
                "observed_date": "2026-10-20",
                "source_path": str(REAL_POOL.resolve()),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "event_count": len(rows),
            }
        ),
        encoding="utf-8",
    )
    result = inspect_batch(DAY, REAL_POOL, manifest)
    assert result["status"] == "WRONG_DAY"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_partial_optional_absence_preserves_existing_score_and_chain_is_only_in_memory(
    tmp_path,
):
    full = _row(bei=None, mbei=None, bei2=None)
    partial = dict(full, code="002634.SZ")
    del partial["mbei"]
    del partial["bei2"]
    source, manifest = _batch(tmp_path, [full, partial])
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    assert result["counts"] == {
        "source_events": 2,
        "signals": 2,
        "kept": 2,
        "combined": 2,
        "pool": 2,
    }
    assert {
        row["symbol"]: (row["score"], row["confidence"]) for row in result["combined"]
    } == {"002633.SZ": (87.0, 0.7), "002634.SZ": (87.0, 0.7)}
    assert "mbei=缺字段" in result["combined"][1]["rationale"]
    assert result["pool_status"] == "IN_MEMORY_ONLY"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_explicit_false_toxic_is_not_missing_optional(tmp_path):
    missing = _row(kind="3买")
    del missing["bei"]
    present_false = dict(missing, code="002634.SZ", bei=False)
    source, manifest = _batch(tmp_path, [missing, present_false])
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    by_symbol = {row["symbol"]: row for row in result["combined"]}
    assert by_symbol["002633.SZ"]["score"] == 77.0
    assert by_symbol["002634.SZ"]["score"] == 60.0
    assert "bei=缺字段" in by_symbol["002633.SZ"]["rationale"]
    assert "bei=False" in by_symbol["002634.SZ"]["rationale"]


def test_duplicate_superseded_and_adapter_failure_do_not_call_writers(
    tmp_path, monkeypatch
):
    source, manifest = _batch(
        tmp_path, [_row("1买", dt="2026-10-16"), _row("1卖", dt="2026-10-18")]
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("只读探针调用了 PG、自动化或证据链写接口")

    monkeypatch.setattr(aggregator.SignalAggregator, "run", forbidden)
    monkeypatch.setattr(aggregator, "persist_combined", forbidden)
    monkeypatch.setattr(aggregator, "_pg_connect", forbidden)
    monkeypatch.setattr(aggregator.subprocess, "run", forbidden)
    from strategy_layer import evidence_intake

    monkeypatch.setattr(evidence_intake, "record_pool_intake", forbidden)
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    assert result["counts"] == {
        "source_events": 2,
        "signals": 2,
        "kept": 1,
        "combined": 1,
        "pool": 0,
    }
    assert result["combined"][0]["action"] == "sell"
    assert (
        result["combined"][0]["contributions"]["chan_monitor"]["superseded"][0][
            "action"
        ]
        == "buy"
    )

    class Broken:
        strategy_id = "broken"

        def score(self, _day):
            raise RuntimeError("injected")

    failure = inspect_batch(DAY, source, manifest, adapter=Broken())
    assert failure["status"] == "ADAPTER_ERROR"
    assert failure["pool_status"] == "NOT_VERIFIED"
    assert failure["counts"]["pool"] is None


def test_manifest_from_same_untrusted_source_is_not_independent_freeze_evidence(
    tmp_path,
):
    # 反例：源被换成另一个同日事件，操作者同步重写 hash/count/日期；无外部回执。
    source, manifest = _batch(tmp_path, [_row("1买")])
    source, manifest = _batch(tmp_path, [_row("1卖")])
    result = inspect_batch(DAY, source, manifest)
    assert result["status"] == "UNVERIFIED", "仅自声明的 manifest 不应充当独立冻结证据"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["combined"][0]["score"] == 7.0
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_adapter_must_convert_exactly_the_hashed_snapshot(tmp_path):
    source, manifest = _batch(tmp_path, [_row("1买", vv=False)])
    original = source.read_bytes()
    substituted = (json.dumps(_row("3卖", vv=False), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )

    class SwapDuringScore:
        strategy_id = "chan_monitor"

        def score(self, day):
            source.write_bytes(substituted)
            try:
                return ChanMonitorAdapter(pool_globs=[str(source)]).score(day)
            finally:
                source.write_bytes(original)

    result = inspect_batch(DAY, source, manifest, adapter=SwapDuringScore())
    assert result["status"] == "UNVERIFIED"
    assert result["source"]["identity_status"] == "UNVERIFIED"
    assert result["chain_status"] == "VERIFIED_IN_MEMORY"
    assert result["brief_status"] == result["receipt_status"] == "NOT_VERIFIED"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
    assert result["combined"][0]["score"] == 95.0, (
        "转换必须绑定初次计算 SHA256 的源字节"
    )
