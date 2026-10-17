# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar, BarQuery


def make_bar(dt, closed=True):
    return Bar("000001", "SSE", "1m", dt, date(2026, 10, 16), 1, 2, 0.5, 1.5, 0, 0, dt, dt, closed, "test", "v1", "none", "ok")


def test_bar_normalizes_utc_and_preserves_zero_volume():
    local = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
    bar = make_bar(local)
    assert bar.dt.tzinfo == timezone.utc
    assert bar.v == 0
    assert bar.source_trace == ()


def test_query_sorts_duplicates_and_as_of():
    first = make_bar(datetime(2026, 10, 16, 1, 2, tzinfo=timezone.utc), False)
    second = make_bar(datetime(2026, 10, 16, 1, 1, tzinfo=timezone.utc), True)
    query = BarQuery(as_of=datetime(2026, 10, 16, 1, 2, tzinfo=timezone.utc), closed_only=True)
    assert query.apply([first, second]) == (second,)


def test_as_of_hides_future_availability_and_close():
    as_of = datetime(2026, 10, 16, 1, 5, tzinfo=timezone.utc)
    available_later = make_bar(as_of, True)
    object.__setattr__(available_later, "available_at", datetime(2026, 10, 16, 1, 6, tzinfo=timezone.utc))
    close_later = make_bar(as_of, True)
    object.__setattr__(close_later, "close_at", datetime(2026, 10, 16, 1, 6, tzinfo=timezone.utc))
    visible = make_bar(as_of, True)
    query = BarQuery(as_of=as_of, closed_only=True)
    assert query.apply([available_later, close_later, visible]) == (visible,)


def test_future_as_of_and_naive_time_rejected():
    with pytest.raises(ValueError):
        make_bar(datetime(2026, 10, 16, 1, 1))
    with pytest.raises(ValueError):
        BarQuery(as_of=datetime.now(timezone.utc).replace(year=2099))
