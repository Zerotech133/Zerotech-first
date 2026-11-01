# -*- coding: utf-8 -*-
"""PIT 工具契约：上市天数按信号日计算、次新股排除、BJ 排除。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import pit_utils


def _sb():
    return pd.DataFrame({"ts_code": ["A.SZ", "B.SZ", "920001.BJ"],
                          "list_date": ["20200101", "20220101", "20230101"]}).set_index("ts_code")


def test_listed_days_pit():
    sb = _sb()
    d = pit_utils.listed_days(sb, "20210101")
    assert d.loc["A.SZ"] >= 250
    assert d.loc["B.SZ"] < 250          # 2022 才上市，2021 信号日不应通过
    assert d.loc["B.SZ"] < 0


def test_listed_ok_pit():
    sb = _sb()
    ok = pit_utils.listed_ok(sb, "20210101")
    assert bool(ok.loc["A.SZ"]) is True
    assert bool(ok.loc["B.SZ"]) is False


def test_exclude_bj():
    idx = pd.Index(["A.SZ", "920001.BJ"])
    m = pit_utils.exclude_bj(idx)
    assert list(m) == [True, False]
