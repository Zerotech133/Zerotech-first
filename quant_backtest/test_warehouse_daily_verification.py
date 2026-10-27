"""只读目标核验：真实临时仓、逐键证据和注入查询结果。"""

import hashlib
import json

import pandas as pd
import pytest

import data_integrity


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(codes=("A", "B"), day="20261008"):
    return [
        dict(
            ts_code=code,
            trade_date=day,
            open=1.0,
            high=2.0,
            low=1.0,
            close=2.0,
            vol=1.0,
            amount=2.0,
        )
        for code in codes
    ]


def put(root, table, records):
    folder = root / "tdx" / table
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "part_0000.parquet"
    pd.DataFrame(records).to_parquet(path, index=False)
    return path


def fixture(root, table="ts_daily", keys=("ts_code", "trade_date"), records=None):
    records = records if records is not None else rows()
    put(root, table, records)
    evidence = root / "integrity"
    evidence.mkdir(exist_ok=True)
    source = evidence / "source.body.json"
    source.write_text(
        json.dumps({"target": "2026-10-08", "keys": ["A", "B"]}), encoding="utf-8"
    )
    payload = dict(
        schema_version=1,
        table=table,
        primary_key=list(keys),
        qualification_status="qualified",
        sources={
            "snapshot": dict(
                path="integrity/source.body.json",
                sha256=sha(source),
                valid_dates=["2026-10-08"],
            )
        },
        dates={
            "2026-10-08": dict(
                complete=True,
                calendar=dict(is_open=True, source_ids=["snapshot"]),
                keys=[
                    dict(key={k: r[k] for k in keys}, source_ids=["snapshot"])
                    for r in records
                ],
                exclusions=[],
            )
        },
    )
    path = evidence / (table + ".json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    required = {
        "ts_daily": ["open", "high", "low", "close", "vol", "amount"],
        "ts_daily_basic": ["turnover_rate", "total_mv", "circ_mv"],
        "ts_margin_detail": ["rzye", "rzrqye"],
        "tdx_sector_daily": ["open", "high", "low", "close", "vol", "amount"],
        "tdx_sector_min": ["open", "high", "low", "close", "vol", "amount"],
    }[table]
    target = dict(
        table=table,
        date="2026-10-09",
        target="2026-10-08",
        status="due",
        verification_rule=dict(
            version=1,
            rule_id="daily-key-v1",
            source="tdx",
            primary_key=list(keys),
            required_columns=required,
            eligibility=dict(path="integrity/" + path.name, sha256=sha(path)),
            query=dict(table=table, codes=[r[keys[0]] for r in records]),
        ),
    )
    return target, payload, path


class Reader:
    """模拟已绑定仓根的查询接口；返回真实目标查询物化结果。"""

    def __init__(self, root):
        self.root = root
        self.calls = []

    def read(self, table, *, codes, start, end):
        self.calls.append((table, codes, start, end))
        frame = pd.concat(
            [
                pd.read_parquet(p)
                for p in (self.root / "tdx" / table).glob("part_*.parquet")
            ],
            ignore_index=True,
        )
        dates = frame.trade_date.astype(str).str.replace("-", "", regex=False)
        code = "ts_code" if "ts_code" in frame else "code"
        return frame.loc[dates.eq(start) & frame[code].isin(codes)].copy()


def verify(root, target, reader=None):
    return data_integrity.verify_targets(root, [target], reader=reader)[target["table"]]


def update_evidence(target, payload, path):
    path.write_text(json.dumps(payload), encoding="utf-8")
    target["verification_rule"]["eligibility"]["sha256"] = sha(path)


def test_zero_new_rows_with_exact_target_and_query_is_passed(tmp_path):
    target, _, _ = fixture(tmp_path)
    target.update(done=True, exit_code=0, new_rows=0)
    reader = Reader(tmp_path)
    result = verify(tmp_path, target, reader)
    assert result["status"] == "passed"
    assert (
        result["metrics"]["expected_keys"] == 2
        and result["metrics"]["observed_unique_keys"] == 2
    )
    assert result["metrics"]["missing_keys"] == []
    assert result["verified_at"].endswith("+00:00")
    assert len(result["warehouse_digest"]) == len(result["eligibility_digest"]) == 64
    assert reader.calls == [("ts_daily", ["A", "B"], "20261008", "20261008")]


def test_current_date_and_duplicate_count_cannot_hide_missing_code(tmp_path):
    target, _, _ = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows(("A", "A")))
    target.update(done=True, exit_code=0, reference_max_count=2)
    result = verify(tmp_path, target, Reader(tmp_path))
    assert result["status"] == "partial"
    assert (
        result["metrics"]["duplicate_rows"] == 1
        and result["metrics"]["observed_unique_keys"] == 1
    )
    assert result["metrics"]["missing_keys"] == [
        {"ts_code": "B", "trade_date": "20261008"}
    ]


def test_required_null_and_optional_valuation_are_distinct(tmp_path):
    records = [
        dict(
            ts_code=c,
            trade_date="20261008",
            turnover_rate=1.0,
            total_mv=2.0,
            circ_mv=1.0,
            pe=None,
        )
        for c in ("A", "B")
    ]
    target, _, _ = fixture(tmp_path, "ts_daily_basic", records=records)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "passed"
    records[-1]["turnover_rate"] = None
    put(tmp_path, "ts_daily_basic", records)
    result = verify(tmp_path, target, Reader(tmp_path))
    assert (
        result["status"] == "partial"
        and result["metrics"]["required_nulls"]["turnover_rate"] == 1
    )


@pytest.mark.parametrize(
    "fault",
    [
        "no_rule",
        "max_count_only",
        "calendar_gap",
        "incomplete_keys",
        "no_key_source",
        "source_sha",
        "ledger_sha",
    ],
)
def test_insufficient_qualification_stays_unknown(tmp_path, fault):
    target, payload, path = fixture(tmp_path)
    if fault == "no_rule":
        target.pop("verification_rule")
    elif fault == "max_count_only":
        payload["dates"]["2026-10-08"].pop("keys")
        payload["dates"]["2026-10-08"]["reference_max_count"] = 2
    elif fault == "calendar_gap":
        payload["dates"]["2026-10-08"].pop("calendar")
    elif fault == "incomplete_keys":
        payload["dates"]["2026-10-08"]["complete"] = False
    elif fault == "no_key_source":
        payload["dates"]["2026-10-08"]["keys"][0]["source_ids"] = []
    elif fault == "source_sha":
        payload["sources"]["snapshot"]["sha256"] = "0" * 64
    elif fault == "ledger_sha":
        target["verification_rule"]["eligibility"]["sha256"] = "0" * 64
    if fault not in ("no_rule", "ledger_sha"):
        update_evidence(target, payload, path)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_no_query_reader_never_claims_up_to_date(tmp_path):
    target, _, _ = fixture(tmp_path)
    result = verify(tmp_path, target)
    assert (
        result["status"] == "unknown" and result["reason"] == "query_reader_unavailable"
    )


@pytest.mark.parametrize("query_fault", ["empty", "wrong_value", "exception"])
def test_query_result_is_checked_and_errors_are_not_collection_errors(
    tmp_path, query_fault
):
    target, _, _ = fixture(tmp_path)

    class BadReader(Reader):
        def read(self, *args, **kwargs):
            if query_fault == "exception":
                raise ValueError("真实查询失败")
            frame = super().read(*args, **kwargs)
            if query_fault == "empty":
                return frame.iloc[:0]
            frame.loc[0, "close"] = 99
            return frame

    result = verify(tmp_path, target, BadReader(tmp_path))
    assert result["status"] == "partial"
    assert result["metrics"]["query"]["status"] == "failed"
    assert result["reason"].startswith("query_")
    assert "collection_error" not in result


def test_target_outside_historical_gap_not_repaired_or_counted(tmp_path):
    target, _, _ = fixture(tmp_path)
    target.update(history_missing_keys=["OLD"], new_rows=0)
    put(tmp_path, "ts_daily", rows() + rows(("A",), "20261007"))
    result = verify(tmp_path, target, Reader(tmp_path))
    assert result["status"] == "passed"
    assert result["metrics"]["scope"] == {"start": "2026-10-08", "end": "2026-10-08"}
    assert result["metrics"]["observed_rows"] == 2
    assert result["metrics"]["history_verified"] is False


def test_changed_eligibility_invalidates_old_digest(tmp_path):
    target, payload, path = fixture(tmp_path)
    first = verify(tmp_path, target, Reader(tmp_path))
    payload["dates"]["2026-10-08"]["keys"].append(
        dict(key={"ts_code": "NEW", "trade_date": "20261008"}, source_ids=["snapshot"])
    )
    update_evidence(target, payload, path)
    target["verification_rule"]["query"]["codes"].append("NEW")
    result = verify(tmp_path, target, Reader(tmp_path))
    assert result["status"] == "partial"
    assert result["eligibility_digest"] != first["eligibility_digest"]
    assert result["warehouse_digest"] == first["warehouse_digest"]


def test_no_target_rows_or_uninitialized_new_code_is_missing(tmp_path):
    target, _, _ = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows(day="20261007"))
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "missing"


def test_cross_table_equal_counts_but_different_keys_fails(tmp_path):
    target, _, _ = fixture(tmp_path)
    put(
        tmp_path,
        "ts_daily_basic",
        [
            dict(
                ts_code=c,
                trade_date="20261008",
                turnover_rate=1.0,
                total_mv=2.0,
                circ_mv=1.0,
            )
            for c in ("A", "C")
        ],
    )
    target["verification_rule"]["reconcile"] = dict(
        table="ts_daily_basic", source="tdx", primary_key=["ts_code", "trade_date"]
    )
    result = verify(tmp_path, target, Reader(tmp_path))
    assert result["status"] == "partial"
    assert result["metrics"]["reconciliation"]["missing_keys"] == [
        {"ts_code": "B", "trade_date": "20261008"}
    ]
    assert result["metrics"]["reconciliation"]["extra_keys"] == [
        {"ts_code": "C", "trade_date": "20261008"}
    ]


def test_verified_exclusion_is_bound_to_exact_target_day(tmp_path):
    target, payload, path = fixture(tmp_path)
    payload["dates"]["2026-10-08"]["keys"] = payload["dates"]["2026-10-08"]["keys"][:1]
    payload["dates"]["2026-10-08"]["exclusions"] = [
        dict(
            key={"ts_code": "B", "trade_date": "20261008"},
            source_ids=["snapshot"],
            reason="全天停牌",
        )
    ]
    update_evidence(target, payload, path)
    put(tmp_path, "ts_daily", rows(("A",)))
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "passed"
    payload["sources"]["snapshot"]["valid_dates"] = ["2026-10-07"]
    update_evidence(target, payload, path)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


@pytest.mark.parametrize("fault", ["table_path", "eligibility_path", "source_path"])
def test_external_paths_and_unknown_tables_are_not_read(tmp_path, fault):
    target, payload, path = fixture(tmp_path)
    if fault == "table_path":
        target["table"] = "../../outside"
    elif fault == "eligibility_path":
        target["verification_rule"]["eligibility"]["path"] = "../../outside.json"
    else:
        payload["sources"]["snapshot"]["path"] = "../../outside.json"
        update_evidence(target, payload, path)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_verification_does_not_write_any_warehouse_file(tmp_path):
    target, _, _ = fixture(tmp_path)
    files = lambda: {
        str(p.relative_to(tmp_path)): (sha(p), p.stat().st_mtime_ns)
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    before = files()
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "passed"
    assert files() == before


def test_digest_changes_when_warehouse_values_change(tmp_path):
    target, _, _ = fixture(tmp_path)
    first = verify(tmp_path, target, Reader(tmp_path))
    modified = rows()
    modified[0]["close"] = 3.0
    put(tmp_path, "ts_daily", modified)
    second = verify(tmp_path, target, Reader(tmp_path))
    assert second["warehouse_digest"] != first["warehouse_digest"]
    assert second["eligibility_digest"] == first["eligibility_digest"]


def test_waiting_and_unknown_target_never_promoted_by_done(tmp_path):
    target, _, _ = fixture(tmp_path)
    target.update(status="unknown", done=True, exit_code=0)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"
    target["status"] = "waiting_release"
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "waiting_release"


def test_board_and_margin_use_their_explicit_keys_and_required_columns(tmp_path):
    board = [
        dict(
            code=str(c),
            trade_date="20261008",
            open=1.0,
            high=2.0,
            low=1.0,
            close=2.0,
            vol=1.0,
            amount=2.0,
        )
        for c in (1, 2)
    ]
    target, _, _ = fixture(
        tmp_path, "tdx_sector_daily", keys=("code", "trade_date"), records=board
    )
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "passed"
    margin = [
        dict(ts_code=c, trade_date="20261008", rzye=1.0, rzrqye=2.0) for c in ("A", "B")
    ]
    target, _, _ = fixture(tmp_path, "ts_margin_detail", records=margin)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "passed"


@pytest.mark.parametrize("fault", ["new_part", "file_value", "eligibility"])
def test_snapshot_change_during_reader_does_not_publish_old_green(tmp_path, fault):
    target, payload, path = fixture(tmp_path)

    class ChangingReader(Reader):
        def read(self, *args, **kwargs):
            result = super().read(*args, **kwargs)
            if fault == "new_part":
                pd.DataFrame(rows(("C",))).to_parquet(
                    tmp_path / "tdx/ts_daily/part_0001.parquet", index=False
                )
            elif fault == "file_value":
                changed = rows()
                changed[0]["close"] = 9.0
                put(tmp_path, "ts_daily", changed)
            else:
                payload["dates"]["2026-10-08"]["complete"] = False
                path.write_text(json.dumps(payload), encoding="utf-8")
            return result

    result = verify(tmp_path, target, ChangingReader(tmp_path))
    assert (
        result["status"] == "unknown"
        and result["reason"] == "verification_snapshot_changed"
    )


@pytest.mark.parametrize("fault", ["array", "bad_sources", "bad_source_ids"])
def test_malformed_qualification_returns_unknown_without_crashing(tmp_path, fault):
    target, payload, path = fixture(tmp_path)
    if fault == "array":
        payload = []
    elif fault == "bad_sources":
        payload["sources"]["snapshot"]["valid_dates"] = None
    else:
        payload["dates"]["2026-10-08"]["keys"][0]["source_ids"] = [{}]
    update_evidence(target, payload, path)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_invalid_target_date_suffix_is_not_counted(tmp_path):
    target, _, _ = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows(day="20261008garbage"))
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "missing"


def test_empty_source_body_cannot_qualify_keys(tmp_path):
    target, payload, path = fixture(tmp_path)
    source = tmp_path / "integrity/source.body.json"
    source.write_bytes(b"")
    payload["sources"]["snapshot"]["sha256"] = sha(source)
    update_evidence(target, payload, path)
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_symbolic_link_evidence_is_rejected_even_inside_root(tmp_path):
    target, payload, path = fixture(tmp_path)
    linked = tmp_path / "integrity/linked.json"
    try:
        linked.symlink_to(path)
    except OSError as exc:
        pytest.skip("当前Windows账户没有创建符号链接资格：" + type(exc).__name__)
    target["verification_rule"]["eligibility"]["path"] = "integrity/linked.json"
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_unknown_registered_table_is_visible_without_guessing_event_time(tmp_path):
    target, _, _ = fixture(tmp_path)
    target["table"] = "stock_basic"
    target.update(
        source_time="2026-10-09T12:00:00+08:00", list_date="2026-10-08", done=True
    )
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


def test_missing_declared_required_column_is_partial(tmp_path):
    target, _, _ = fixture(tmp_path)
    records = rows()
    for record in records:
        record.pop("amount")
    put(tmp_path, "ts_daily", records)
    result = verify(tmp_path, target, Reader(tmp_path))
    assert result["status"] == "partial" and result["metrics"]["missing_columns"] == [
        "amount"
    ]


def test_malformed_query_codes_returns_unknown(tmp_path):
    target, _, _ = fixture(tmp_path)
    target["verification_rule"]["query"]["codes"] = [{}]
    assert verify(tmp_path, target, Reader(tmp_path))["status"] == "unknown"


@pytest.mark.parametrize("fault", ["unbound", "wrong_root"])
def test_query_reader_must_explicitly_bind_same_root(tmp_path, fault):
    target, _, _ = fixture(tmp_path)

    class UnboundReader:
        def __init__(self):
            if fault == "wrong_root":
                self.root = tmp_path / "other"

        def read(self, *args, **kwargs):
            return pd.DataFrame(rows())

    result = verify(tmp_path, target, UnboundReader())
    assert result["status"] == "unknown" and result["reason"] == "query_root_unknown"


def test_historical_part_replaced_with_target_rows_invalidates_snapshot(tmp_path):
    target, _, _ = fixture(tmp_path)
    historical = tmp_path / "tdx/ts_daily/part_0001.parquet"
    pd.DataFrame(rows(("C",), "20261007")).to_parquet(historical, index=False)

    class ReplacingReader(Reader):
        def read(self, *args, **kwargs):
            result = super().read(*args, **kwargs)
            pd.DataFrame(rows(("C",))).to_parquet(historical, index=False)
            return result

    result = verify(tmp_path, target, ReplacingReader(tmp_path))
    assert (
        result["status"] == "unknown"
        and result["reason"] == "verification_snapshot_changed"
    )
