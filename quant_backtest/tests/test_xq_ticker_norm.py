# -*- coding: utf-8 -*-
"""WO-QT-20261020-079：xq_ticker_norm 双轨格式适配层测试。

先红后绿：本文件先于模块落地，首跑必红（ModuleNotFoundError）。
覆盖：前缀↔后缀双向往返、BJ 分支、畸形值容错、批量混合形态、港美股不后缀化。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.dirname(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from xq_ticker_norm import (
    norm_from_suffix,
    norm_from_suffix_many,
    norm_to_suffix,
    norm_to_suffix_many,
)


# ---------------------------------------------------------------------------
# 标量：前缀式 → 后缀式
# ---------------------------------------------------------------------------

class TestToSuffix:
    @pytest.mark.parametrize("raw,want", [
        ("SZ300750", "300750.SZ"),
        ("SH600519", "600519.SH"),
        ("BJ830799", "830799.BJ"),
        ("SZ163807", "163807.SZ"),  # 场内基金同格式
        ("sz300750", "300750.SZ"),  # 大小写不敏感
        ("  SH600519  ", "600519.SH"),  # 首尾空白
    ])
    def test_prefix_to_suffix(self, raw, want):
        assert norm_to_suffix(raw) == want

    @pytest.mark.parametrize("raw", [
        "300750.SZ", "600519.SH", "830799.BJ",  # 已是后缀式 → 原样（幂等）
    ])
    def test_already_suffix_passthrough(self, raw):
        assert norm_to_suffix(raw) == raw

    # 港股裸 5 位码 / 美股字母形态：不支持后缀化 → None（不造数）
    @pytest.mark.parametrize("raw", [
        "00700", "01810", "20000", "AAPL", "TSLA",
    ])
    def test_hk_us_not_supported(self, raw):
        assert norm_to_suffix(raw) is None

    # 畸形值容错：不抛异常，返回 None
    @pytest.mark.parametrize("raw", [
        None, "", "   ", 123, "XX123456", "SZ30A750", "S300750",
        "SZ3007500", "30075", "SH-600519", object(),
    ])
    def test_malformed_returns_none(self, raw):
        assert norm_to_suffix(raw) is None


# ---------------------------------------------------------------------------
# 标量：后缀式 → 前缀式
# ---------------------------------------------------------------------------

class TestFromSuffix:
    @pytest.mark.parametrize("raw,want", [
        ("300750.SZ", "SZ300750"),
        ("600519.SH", "SH600519"),
        ("830799.BJ", "BJ830799"),
        ("163807.sz", "SZ163807"),
    ])
    def test_suffix_to_prefix(self, raw, want):
        assert norm_from_suffix(raw) == want

    @pytest.mark.parametrize("raw", [
        "SZ300750", "SH600519", "BJ830799",  # 已是前缀式 → 原样（幂等）
    ])
    def test_already_prefix_passthrough(self, raw):
        assert norm_from_suffix(raw) == raw

    # 港股/美股/指数专属后缀（.HK/.CSI 等）：不支持 → None
    @pytest.mark.parametrize("raw", [
        "00700", "00700.HK", "AAPL", "000300.CSI", "HS300",
    ])
    def test_hk_index_exotic_not_supported(self, raw):
        assert norm_from_suffix(raw) is None

    @pytest.mark.parametrize("raw", [
        None, "", "  ", 123, "300750.XX", "30075.SZ", "3007500.SZ",
        "SZ30A750", object(),
    ])
    def test_malformed_returns_none(self, raw):
        assert norm_from_suffix(raw) is None


# ---------------------------------------------------------------------------
# 双向往返
# ---------------------------------------------------------------------------

class TestRoundTrip:
    @pytest.mark.parametrize("canonical", [
        "SZ300750", "SH600519", "BJ830799", "SZ002938",
    ])
    def test_round_trip_prefix(self, canonical):
        assert norm_from_suffix(norm_to_suffix(canonical)) == canonical

    @pytest.mark.parametrize("symbol", [
        "300750.SZ", "600519.SH", "830799.BJ",
    ])
    def test_round_trip_suffix(self, symbol):
        assert norm_to_suffix(norm_from_suffix(symbol)) == symbol

    def test_idempotent_both_ways(self):
        # 两次转换结果稳定
        once = norm_to_suffix("SH600519")
        assert norm_to_suffix(once) == once
        once_b = norm_from_suffix("600519.SH")
        assert norm_from_suffix(once_b) == once_b


# ---------------------------------------------------------------------------
# 批量 helper
# ---------------------------------------------------------------------------

class TestBatch:
    def test_to_suffix_mixed(self):
        vals = ["SZ300750", "SH600519", "00700", None, "SZ30A750", "BJ830799"]
        out, invalid = norm_to_suffix_many(vals)
        assert out == ["300750.SZ", "600519.SH", None, None, None, "830799.BJ"]
        assert invalid == 3

    def test_from_suffix_mixed(self):
        vals = ["300750.SZ", "AAPL", "600519.SH", "", "830799.BJ"]
        out, invalid = norm_from_suffix_many(vals)
        assert out == ["SZ300750", None, "SH600519", None, "BJ830799"]
        assert invalid == 2

    def test_empty_input(self):
        assert norm_to_suffix_many([]) == ([], 0)
        assert norm_from_suffix_many([]) == ([], 0)

    def test_all_invalid(self):
        out, invalid = norm_to_suffix_many(["00700", "AAPL", ""])
        assert out == [None, None, None]
        assert invalid == 3

    def test_order_preserved(self):
        vals = ["BJ830799", "SH600519", "SZ300750"]
        out, invalid = norm_to_suffix_many(vals)
        assert out == ["830799.BJ", "600519.SH", "300750.SZ"]
        assert invalid == 0

    def test_batch_accepts_tuple(self):
        out, invalid = norm_to_suffix_many(("SZ300750",))
        assert out == ["300750.SZ"]
        assert invalid == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
