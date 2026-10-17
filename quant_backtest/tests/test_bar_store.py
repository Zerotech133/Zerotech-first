# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar, BarQuery
from bar_store import BarStore


def make_bar(dt, available=None, close=None, closed=True):
    return Bar("000001", "SSE", "1m", dt, date(2026, 10, 16), 1, 2, 1, 1.5, None, None, close, available, closed, "fixture", "v1", "none", "ok")


def test_store_query_applies_as_of_and_closed_only():
    as_of = datetime(2026, 10, 16, 1, 10, tzinfo=timezone.utc)
    visible = make_bar(as_of, as_of, as_of)
    unavailable = make_bar(as_of, as_of.replace(minute=11), as_of, True)
    unclosed = make_bar(as_of, as_of, as_of.replace(minute=11), False)
    result = BarStore([unclosed, unavailable, visible]).query(BarQuery(as_of=as_of, closed_only=True))
    assert result == (visible,)


def test_store_is_immutable_and_rejects_wrong_types():
    store = BarStore()
    new_store = store.add(make_bar(datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)))
    assert store.all() == ()
    assert len(new_store.all()) == 1
    with pytest.raises(TypeError):
        BarStore(["not-bar"])
