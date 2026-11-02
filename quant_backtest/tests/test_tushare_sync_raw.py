# -*- coding: utf-8 -*-
"""tushare raw 日频补拉测试（去重/已存在跳过/当日未发布 skip）。"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "quant_backtest"))

import tushare_sync as T  # noqa: E402


class _EmptyDF:
    empty = True


class _DF:
    empty = False

    def to_parquet(self, path, index=False):
        Path(path).write_text("x", encoding="utf-8")


def test_incr_raw_by_date_dedup_and_skip(tmp_path, monkeypatch):
    calls = []

    def fake_tdir(api):
        d = tmp_path / api
        d.mkdir(exist_ok=True)
        return str(d)

    def fake_pull(api, params, cap, max_off=None):
        calls.append(params["trade_date"])
        if params["trade_date"] == "20261103":
            return _EmptyDF()  # 当日未发布 → skip
        return _DF()

    monkeypatch.setattr(T, "tdir", fake_tdir)
    monkeypatch.setattr(T, "pull_paged", fake_pull)
    T.incr_raw_by_date([("daily_basic", 6000)],
                       ["20261102", "20261102", "20261103"], dryrun=False)
    files = sorted(p.name for p in (tmp_path / "daily_basic").glob("d_*.parquet"))
    assert files == ["d_20261102.parquet"]
    assert calls == ["20261102", "20261103"]  # 去重后仅 2 次请求


def test_incr_raw_by_date_dryrun_no_network(tmp_path, monkeypatch):
    def fake_tdir(api):
        d = tmp_path / api
        d.mkdir(exist_ok=True)
        return str(d)

    def boom(*a, **k):
        raise AssertionError("dryrun 不应请求网络")

    monkeypatch.setattr(T, "tdir", fake_tdir)
    monkeypatch.setattr(T, "pull_paged", boom)
    T.incr_raw_by_date([("daily_basic", 6000)], ["20261102"], dryrun=True)
    assert list((tmp_path / "daily_basic").glob("*")) == []
