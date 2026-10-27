# -*- coding: utf-8 -*-
"""kg_bclass cyq 拉取口径单测（OPS 2026-10-09：空片永久占位修复）。

只测 _pull_one(keep_empty) / _recent_open_days 纯逻辑，不连 tushare/Neo4j。"""

import os
import sys
import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_bclass


@pytest.fixture
def stub_ts(monkeypatch):
    """替身 tushare_sync：记录调用，可编程返回。"""
    box = types.SimpleNamespace(calls=[], ret=pd.DataFrame({"a": [1]}))

    def fake_pull(api, params, cap):
        box.calls.append((api, dict(params), cap))
        return box.ret

    monkeypatch.setitem(
        sys.modules, "tushare_sync", types.SimpleNamespace(pull_paged=fake_pull)
    )
    return box


def test_pull_one_skips_existing(stub_ts, tmp_path):
    f = tmp_path / "d_20261008.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(str(f))  # 非空片
    kg_bclass._pull_one("cyq_perf", {"trade_date": "20261008"}, str(f))
    assert stub_ts.calls == []  # 文件存在且有数据 → 不调 API


def test_pull_one_empty_not_kept(stub_ts, tmp_path):
    f = tmp_path / "d_20261009.parquet"
    stub_ts.ret = pd.DataFrame()  # 数据未出 → 0 行
    kg_bclass._pull_one(
        "cyq_perf", {"trade_date": "20261009"}, str(f), keep_empty=False
    )
    assert not f.exists()  # 不落片：下次自然重拉
    assert len(stub_ts.calls) == 1


def test_pull_one_overrides_empty_shard(stub_ts, tmp_path):
    """0 行空片 + keep_empty=False：视为缺失重拉覆盖（DeletePending 绕过）。"""
    f = tmp_path / "d_20261008.parquet"
    pd.DataFrame({"a": pd.Series([], dtype="int64")}).to_parquet(str(f))
    kg_bclass._pull_one(
        "cyq_perf", {"trade_date": "20261008"}, str(f), keep_empty=False
    )
    assert len(stub_ts.calls) == 1  # 空片不挡重拉
    assert len(pd.read_parquet(str(f))) == 1  # 覆盖为有数据片


def test_pull_one_rows_written(stub_ts, tmp_path):
    f = tmp_path / "d_20261008.parquet"
    kg_bclass._pull_one(
        "cyq_perf", {"trade_date": "20261008"}, str(f), keep_empty=False
    )
    assert f.exists()
    assert len(pd.read_parquet(f)) == 1


def test_recent_open_days(monkeypatch, tmp_path):
    monkeypatch.setattr(kg_bclass, "TS_DIR", str(tmp_path))
    os.makedirs(os.path.join(str(tmp_path), "trade_cal"))
    now = datetime.now()
    d = lambda k: (now - timedelta(days=k)).strftime("%Y%m%d")  # noqa: E731
    pd.DataFrame(
        {"cal_date": [d(4), d(3), d(2), d(1), d(0)], "is_open": [1, 1, 0, 1, 1]}
    ).to_parquet(os.path.join(str(tmp_path), "trade_cal", "part_0.parquet"))
    assert kg_bclass._recent_open_days(3) == [d(3), d(1), d(0)]  # 剔除休市 d(2)
    assert kg_bclass._recent_open_days(2) == [d(1), d(0)]


def test_recent_open_days_no_cal(monkeypatch, tmp_path):
    monkeypatch.setattr(kg_bclass, "TS_DIR", str(tmp_path))
    assert kg_bclass._recent_open_days() == []
