#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feeder 测试：全部用临时目录合成 parquet/JSON 小样本，不读大文件。

qpzm_parquet：计入门槛（≥21 根含锚日，对齐 qpzm_backtest.MIN_BARS_ENV）、breadth/med20 口径、
锚日指定/缺失/最新锚；lake：T-1 锚位语义、null 跳过、med20 缺维。
"""

import json

import pandas as pd

from gatekeeper.feeders.lake import LakeFeeder
from gatekeeper.feeders.qpzm_parquet import QpzmParquetFeeder, MIN_BARS_ENV


def _dates(n, start=1):
    """合成连续交易日键 YYYYMMDD（当月递增，仅作排序键用）。"""
    return ["202610%02d" % (start + i) for i in range(n)]


def _make_parquet(data_dir, code, closes, dates):
    # pre_close 为真实湖列集（feeder 复权链需要；缺列整票跳过与 qpzm 同语义）
    pre = [closes[0]] + list(closes[:-1])
    df = pd.DataFrame({
        "ts_code": code, "trade_date": dates, "close": closes,
        "pre_close": pre,
    })
    df.to_parquet(str(data_dir / ("%s.parquet" % code)), index=False)


def test_min_bars_and_small_sample(tmp_path):
    """小样本行为：<21 根不计入分母；≥21 根计入（对齐 qpzm 门槛）。"""
    d = _dates(25)
    up = [1.0 * (1.01 ** i) for i in range(25)]        # A：上行，锚日 close>MA20
    dn = [1.0 * (0.99 ** i) for i in range(25)]        # B：下行，close<MA20
    _make_parquet(tmp_path, "000001_SZ", up, d)
    _make_parquet(tmp_path, "000002_SZ", dn, d)
    _make_parquet(tmp_path, "000004_SZ", [2.0] * 10, _dates(10))  # C：10 根淘汰
    _make_parquet(tmp_path, "000005_SZ", [2.0] * 20, _dates(20))  # D：恰 20 根仍不足门槛
    f = QpzmParquetFeeder(str(tmp_path))
    anchor, feats = f.get_features("cn")
    assert anchor == d[-1]
    assert feats["n_stocks"] == 2                      # C/D 不足 21 根不计入
    assert abs(feats["breadth"] - 0.5) < 1e-9          # 1/2
    exp = ((up[-1] / up[-21] - 1) + (dn[-1] / dn[-21] - 1)) / 2
    assert abs(feats["med20"] - exp) < 1e-9            # 20 日收益中位（两股均值）
    assert feats["breadth"] is not None and feats["med20"] is not None


def test_exact_threshold_bars_counted(tmp_path):
    """恰 MIN_BARS_ENV=21 根：计入分母；MA20=rolling(20,min_periods=1)
    全窗均值（对齐 qpzm load_wide :466），20 根门槛外不计入。"""
    d = _dates(MIN_BARS_ENV)
    closes = [1.0] * (MIN_BARS_ENV - 1) + [2.0]        # 末根跳升 → close>MA20
    _make_parquet(tmp_path, "600000_SH", closes, d)
    f = QpzmParquetFeeder(str(tmp_path))
    anchor, feats = f.get_features("cn")
    assert anchor == d[-1]
    assert feats["n_stocks"] == 1                      # 恰 21 根：计入
    ma = (sum(closes[:-1]) + 2.0) / 20.0               # rolling(20) 全窗均值
    assert feats["breadth"] == 1.0 and 2.0 > ma
    assert feats["med20"] is not None                  # ret20 恰 21 根可算
    # 边界：恰 20 根（门槛外）→ n_stocks=0 缺维
    import pandas as _pd
    d2 = tmp_path / "edge"
    d2.mkdir()
    _make_parquet(d2, "600001_SH", closes[:-1], d[:-1])
    f2 = QpzmParquetFeeder(str(d2))
    a2, ft2 = f2.get_features("cn")
    assert a2 == d[-2] and ft2["n_stocks"] == 0 and ft2["breadth"] is None


def test_anchor_date_given_and_missing(tmp_path):
    d = _dates(25)
    up = [1.0 * (1.01 ** i) for i in range(25)]   # 上行 → 锚日 close>MA20
    _make_parquet(tmp_path, "000001_SZ", up, d)
    f = QpzmParquetFeeder(str(tmp_path))
    anchor, feats = f.get_features("cn", "2026-10-21")  # 带横杠亦可；第21根恰过门槛
    assert anchor == "20261021" and feats["breadth"] == 1.0
    anchor, feats = f.get_features("cn", "20261020")    # 第 20 根：门槛外缺维
    assert anchor == "20261020" and feats["n_stocks"] == 0 \
        and feats["breadth"] is None
    anchor, feats = f.get_features("cn", "20250101")           # 湖外日期
    assert anchor == "20250101"
    assert feats["breadth"] is None and feats["med20"] is None
    assert feats["anchor_missing"] is True


def test_empty_data_dir(tmp_path):
    f = QpzmParquetFeeder(str(tmp_path))
    anchor, feats = f.get_features("cn")
    assert anchor is None and feats["breadth"] is None \
        and feats["med20"] is None


def test_lake_t1_anchor_semantics(tmp_path):
    """显式锚日 → 严格取前一根有效值（T-1）；None → 最后有效值即 T-1 锚。"""
    fp = str(tmp_path / "series.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"dates": ["20261013", "20261014", "20261015"],
                   "breadth": [0.50, 0.42, None]}, f)      # 末位 null 跳过
    f = LakeFeeder(fp)
    anchor, feats = f.get_features("cn", "20261015")
    assert anchor == "20261014" and feats["breadth"] == 0.42
    assert feats["anchor_semantics"] == "T-1"
    anchor, feats = f.get_features("cn")                   # None → 最后有效值
    assert anchor == "20261014" and feats["breadth"] == 0.42
    assert feats["med20"] is None                          # 湖无 med20 → 缺维


def test_lake_missing_and_empty(tmp_path):
    fp = str(tmp_path / "series.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"dates": ["20261013"], "breadth": [0.5]}, f)
    f = LakeFeeder(fp)
    anchor, feats = f.get_features("cn", "20261013")       # 锚日早于首个值
    assert anchor == "20261013" and feats.get("anchor_missing") is True
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({}, f)
    anchor, feats = LakeFeeder(fp).get_features("cn")
    assert anchor is None and feats["breadth"] is None
