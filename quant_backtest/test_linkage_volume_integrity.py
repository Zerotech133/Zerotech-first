"""分钟量能未知值的消费回归，仅使用内存样本。"""

import json

import pandas as pd
import pytest

import linkage


def minutes(volumes, times=None):
    times = times or [f"09:{31 + i:02d}" for i in range(len(volumes))]
    return pd.DataFrame(
        [
            {
                "code": "880843",
                "trade_date": "20260930",
                "trade_time": time,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0 + i,
                "vol": volume,
            }
            for i, (time, volume) in enumerate(zip(times, volumes))
        ]
    )


def supply(monkeypatch, monthly, tail=None):
    tail = tail if tail is not None else monthly.iloc[:0]
    monkeypatch.setattr(
        linkage,
        "_read_parquet_code",
        lambda path, code, columns: tail if path.endswith("_tail") else monthly,
    )


@pytest.mark.parametrize("period", ["5m", "30m"])
def test_mixed_bucket_retains_unknown_and_missing_minutes(monkeypatch, period):
    supply(monkeypatch, minutes([2.0, None, 3.0]))
    bars = linkage.block_bars("880843", period)
    assert len(bars) == 1
    assert bars[0]["vol"] is None
    assert bars[0]["vol_complete"] is False
    assert bars[0]["vol_missing_minutes"] == ["2026-09-30 09:32"]
    assert bars[0]["close"] == 12.0
    json.dumps(bars, allow_nan=False)


def test_all_unknown_volume_is_not_zero(monkeypatch):
    supply(monkeypatch, minutes([None, float("nan")]))
    bar = linkage.block_bars("880843", "5m")[0]
    assert bar["vol"] is None
    assert len(bar["vol_missing_minutes"]) == 2


def test_explicit_zero_and_complete_sum_are_valid(monkeypatch):
    supply(monkeypatch, minutes([0.0, 2.0, 3.0]))
    bar = linkage.block_bars("880843", "5m")[0]
    assert bar["vol"] == 5.0
    assert bar["vol_complete"] is True
    supply(monkeypatch, minutes([0.0, 0.0]))
    assert linkage.block_bars("880843", "5m")[0]["vol"] == 0.0


def test_bucket_boundary_and_tail_deduplication(monkeypatch):
    monthly = minutes([2.0, None, 3.0], ["09:34", "09:35", "09:36"])
    supply(monkeypatch, monthly, monthly.iloc[1:].copy())
    bars = linkage.block_bars("880843", "5m")
    assert bars[0]["dt"] == "2026-09-30 09:35"
    assert bars[0]["vol"] is None
    assert bars[0]["vol_missing_minutes"] == ["2026-09-30 09:35"]
    assert bars[1]["dt"] == "2026-09-30 09:40"
    assert bars[1]["vol"] == 3.0
