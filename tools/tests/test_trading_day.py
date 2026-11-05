# -*- coding: utf-8 -*-
"""tests for trading_day."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from tools.research import trading_day as td


def _write_cal(tmp_path: Path) -> Path:
    data = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE", "SSE", "SZSE"],
            "cal_date": ["20261105", "20261107", "20261109", "20261105"],
            "is_open": [1, 0, 1, 1],
            "pretrade_date": ["20261104", "20261106", "20261106", "20261104"],
        }
    )
    data.to_parquet(tmp_path / "part_0001.parquet", index=False)
    return tmp_path


def test_normalize_date_variants():
    assert td._normalize_date("20261105") == "20261105"
    assert td._normalize_date("2026-11-05") == "20261105"
    assert td._normalize_date(date(2026, 11, 5)) == "20261105"
    assert td._normalize_date(datetime(2026, 11, 5, 18, 0)) == "20261105"


def test_normalize_date_rejects_bad():
    with pytest.raises(ValueError):
        td._normalize_date("2026/11")
    with pytest.raises(ValueError):
        td._normalize_date("abcd")


def test_load_filters_exchange_and_dedupes(tmp_path: Path):
    df = td.load_trade_cal(_write_cal(tmp_path))
    assert list(df.columns[:3]) == ["exchange", "cal_date", "is_open"]
    assert len(df) == 3
    assert set(df["exchange"]) == {"SSE"}


def test_status_open_and_closed(tmp_path: Path):
    root = _write_cal(tmp_path)
    open_day = td.status_for("2026-11-05", data_dir=root)
    closed_day = td.status_for("2026-11-07", data_dir=root)
    assert open_day["is_trading_day"] is True
    assert closed_day["is_trading_day"] is False
    assert open_day["cal_date"] == "20261105"


def test_status_missing_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        td.status_for("2026-11-06", data_dir=_write_cal(tmp_path))


def test_cli_exit_codes(tmp_path: Path, capsys):
    root = _write_cal(tmp_path)
    assert td.main(["check", "--date", "2026-11-05", "--data-dir", str(root)]) == 0
    assert "OPEN" in capsys.readouterr().out
    assert td.main(["check", "--date", "2026-11-07", "--data-dir", str(root)]) == 1
    assert "CLOSED" in capsys.readouterr().out
    assert td.main(["check", "--date", "2026-11-06", "--data-dir", str(root)]) == 2


def test_cli_json(tmp_path: Path, capsys):
    root = _write_cal(tmp_path)
    rc = td.main(["check", "--date", "2026-11-05", "--data-dir", str(root), "--json"])
    assert rc == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["is_trading_day"] is True
