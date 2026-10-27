"""A4 CLI 仅消费探针内存结果，错误不输出半截 JSON。"""

import json

import pytest

from strategy_layer import a4_comparison_cli as cli
from strategy_layer.aggregator import SignalAggregator


@pytest.fixture
def inputs(tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text(
        json.dumps(
            {
                "code": "000001.SZ",
                "type": "1买",
                "detect_date": "2026-10-16",
                "dt": "2026-10-15",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    history_dir = tmp_path / "picks_history"
    history_dir.mkdir()
    archive = history_dir / "todays_20261016.json"
    archive.write_text(
        json.dumps(
            {
                "scan_run_date": "2026-10-16",
                "trade_basis": "2026-10-16 收盘",
                "strategies": {"chanlun": {"batch_date": "2026-10-16"}},
                "stocks": [
                    {
                        "code": "000001.SZ",
                        "sources": [{"s": "chanlun", "tag": "1买", "dt": "2026-10-15"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pool, history_dir, archive


def test_same_day_default_archive_only_compares_in_probe(inputs, monkeypatch, capsys):
    pool, history_dir, archive = inputs
    monkeypatch.setattr(cli.probe, "PORTAL_HISTORY", history_dir)

    def forbidden_run(*args, **kwargs):
        pytest.fail("SignalAggregator.run 不得被调用")

    monkeypatch.setattr(SignalAggregator, "run", forbidden_run)
    assert cli.main(["--trade-date", "20261016", "--pool-path", str(pool)]) == 0
    out = capsys.readouterr()
    assert not out.err
    assert out.out.count("\n") == 1
    data = json.loads(out.out)
    assert out.out.strip() == json.dumps(
        data, ensure_ascii=False, separators=(",", ":")
    )
    assert archive.exists()
    assert data["history_status"] == "MATCHED"
    assert data["source_file_statuses"] == [{"path": str(pool), "status": "OK"}]
    assert "probe" not in data  # CLI 仅交付摘要，不输出逐行原始证据。
    assert data["comparison"]["archive_status"] == "MATCHED"
    assert data["comparison"]["observation_comparability"] == "COMPARABLE_WITHIN_PROBE"
    assert (
        data["comparison"]["cutover_verdict"]
        == data["cutover_verdict"]
        == "BLOCK_CUTOVER"
    )


def test_wrong_day_archive_is_not_comparable(inputs, capsys):
    pool, _, archive = inputs
    assert (
        cli.main(
            [
                "--trade-date",
                "20261017",
                "--pool-path",
                str(pool),
                "--history",
                str(archive),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["history_status"] == "DATE_MISMATCH"
    assert data["comparison"]["archive_status"] == "DATE_MISMATCH"
    assert data["comparison"]["observation_comparability"] == "NOT_COMPARABLE"
    assert data["cutover_verdict"] == "BLOCK_CUTOVER"


def test_missing_archive_and_pool_path_are_evidence_not_missing_market(inputs, capsys):
    pool, history_dir, _ = inputs
    absent_pool = history_dir / "missing.jsonl"
    absent_history = history_dir / "absent.json"
    assert (
        cli.main(
            [
                "--trade-date",
                "20261016",
                "--history",
                str(absent_history),
                "--pool-path",
                str(pool),
                "--pool-path",
                str(absent_pool),
            ]
        )
        == 0
    )
    out = capsys.readouterr()
    assert not out.err
    data = json.loads(out.out)
    assert data["history_status"] == "UNREADABLE"
    assert {"path": str(absent_pool), "status": "MISSING"} in data[
        "source_file_statuses"
    ]
    assert data["comparison"]["archive_status"] == "INVALID"
    assert data["comparison"]["missing_source_paths"] == [str(absent_pool)]
    assert (
        data["comparison"]["missing_market_inference"]
        == "NOT_INFERRED_FROM_MISSING_PATHS"
    )
    assert data["comparison"]["observation_comparability"] == "NOT_COMPARABLE"
    assert data["cutover_verdict"] == "BLOCK_CUTOVER"


@pytest.mark.parametrize("date", ["2026-10-16", "20261340"])
def test_invalid_trade_date_fails_with_short_stderr(inputs, date, capsys):
    pool, _, archive = inputs
    assert (
        cli.main(
            ["--trade-date", date, "--history", str(archive), "--pool-path", str(pool)]
        )
        != 0
    )
    out = capsys.readouterr()
    assert not out.out
    assert out.err.startswith("error: ")
    assert len(out.err.splitlines()) == 1


def test_missing_only_pool_fails_closed(inputs, capsys):
    _, _, archive = inputs
    assert (
        cli.main(
            [
                "--trade-date",
                "20261016",
                "--history",
                str(archive),
                "--pool-path",
                str(archive.parent / "missing.jsonl"),
            ]
        )
        != 0
    )
    out = capsys.readouterr()
    assert out.out == ""
    assert "source pool has no valid rows" in out.err
