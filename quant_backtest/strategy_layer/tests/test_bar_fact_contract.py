# -*- coding: utf-8 -*-
"""bar_fact_contract 单测（WO-BAR-FACT-CONTRACT-20261101，行情事实契约）。

覆盖验收口径：
1) MA：默认 20 日窗口、自定义窗口只取最近 N 根、数据不足/窗口缺失 → None、
   非法 period closed-on-failure；
2) RSI：Wilder 经典示例数值、Wilder 与 SMA14 在同序列上的显著差异、
   SMA14 与历史实现口径（近 N 段差分简单平均）逐值一致、手算小样本
   （period=3）、全涨 100/全跌 0/走平 50、数据不足/缺失 → None、非法 method；
3) 量比：正常量比 = 末根/前 20 根均量；半根日线（末根 < 均量 1%，张松仓
   CRYPTO 场景）→ None/partial 且不输出假量比；显式 complete_bar=false →
   partial；恰好 1% 边界不算 partial；数据不足/零均量 → unknown；
4) 日收益（百分数）：正常、pre_close 缺失回退 fallback_prev_close、pre_close
   非法回退、pre_close 优先于 fallback、双缺失/非正价 → None；
5) validate_fact：字段齐全合法 → pass；缺字段/空值 → unknown（不猜 pass）；
   complete_bar=false → unknown；枚举/asof/source/bool 类型非法 → fail；
   price_adjust=none 带未复权 warning；
6) 纯读：仅 stdlib 依赖，无网络/DB/第三方/写盘调用。

数值期望由参考实现离线推导后硬编码（Wilder 未做历史四舍五入）。
"""

from __future__ import annotations

import ast
import os

import pytest

from strategy_layer import bar_fact_contract as bfc

# ── 参考序列 ─────────────────────────────────────────────────────────
# Wilder/StockCharts 经典 RSI14 示例序列（33 根）
WILDER_EXAMPLE = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]
# period=14、closes 长度 17 的构造序列：Wilder 平滑与 SMA14 简单平均明显不同
CRAFTED_CLOSES = [
    100.0, 102.0, 104.0, 103.0, 102.0, 105.0, 104.5, 104.0,
    105.5, 103.5, 104.5, 103.5, 105.5, 105.0, 105.5, 104.5, 103.5,
]
# period=3 手算小样本：diffs = +1 +1 -1 +2 0 -1
P3_CLOSES = [10.0, 11.0, 12.0, 11.0, 13.0, 13.0, 12.0]


def _legacy_sma14_rsi(closes, n=14):
    """历史实现口径参考（agent_platform .../strategy_20261018_sma20_rsi14.py::_rsi）。"""
    gains, losses = [], []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 1e-12 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


# ── 1. schema / 常量 ─────────────────────────────────────────────────
class TestSchema:
    def test_schema_and_constants(self):
        assert bfc.SCHEMA == "bar_fact_contract/v1"
        assert bfc.MA_DEFAULT_PERIOD == 20
        assert bfc.RSI_DEFAULT_PERIOD == 14
        assert bfc.VOLUME_BASELINE_PERIOD == 20
        assert bfc.PARTIAL_VOLUME_RATIO_THRESHOLD == 0.01
        assert bfc.RSI_METHODS == ("wilder", "sma14")
        assert bfc.PRICE_ADJUSTS == ("none", "qfq", "hqf")  # 契约口径：hqf=后复权
        assert bfc.REQUIRED_FACT_FIELDS == (
            "asof", "source", "price_adjust", "rsi_method", "complete_bar",
        )
        assert bfc.VALIDATE_STATUSES == ("pass", "unknown", "fail")

    def test_public_callables_declared(self):
        for name in (
            "compute_ma",
            "compute_rsi",
            "compute_volume_ratio",
            "volume_ratio_fact",
            "compute_return",
            "validate_fact",
        ):
            assert callable(getattr(bfc, name))
            assert name in bfc.__all__


# ── 2. MA ────────────────────────────────────────────────────────────
class TestComputeMa:
    def test_ma_default_period_20(self):
        closes = [float(value) for value in range(1, 26)]  # 1..25
        expected = sum(closes[-20:]) / 20.0
        assert bfc.compute_ma(closes) == pytest.approx(expected)
        assert bfc.compute_ma(closes) == pytest.approx(15.5)  # 6..25 均值

    def test_ma_custom_period_uses_recent_window_only(self):
        assert bfc.compute_ma([1, 2, 3, 4, 5, 6, 7], 5) == pytest.approx(5.0)
        assert bfc.compute_ma((10.0, 20.0, 30.0), 2) == pytest.approx(25.0)

    def test_ma_insufficient_returns_none(self):
        assert bfc.compute_ma(list(range(19)), 20) is None
        assert bfc.compute_ma([], 20) is None

    def test_ma_missing_value_in_window_returns_none(self):
        closes = [1.0] * 19 + [None]
        assert bfc.compute_ma(closes, 20) is None
        assert bfc.compute_ma([1.0] * 19 + [float("nan")], 20) is None
        # 缺失在窗口之外不影响（只取最近 20 根）
        assert bfc.compute_ma([None] + [1.0] * 20, 20) == pytest.approx(1.0)

    def test_ma_invalid_period_raises(self):
        for bad in (0, -1, True, "20", 2.0):
            with pytest.raises(ValueError):
                bfc.compute_ma([1.0, 2.0, 3.0], bad)  # type: ignore[arg-type]

    def test_ma_rejects_non_sequence(self):
        with pytest.raises(ValueError):
            bfc.compute_ma(None)  # type: ignore[arg-type]


# ── 3. RSI（Wilder vs SMA14） ───────────────────────────────────────
class TestComputeRsi:
    def test_wilder_known_example(self):
        # Wilder 定义逐根平滑（未做历史四舍五入）→ 37.7888…
        assert bfc.compute_rsi(WILDER_EXAMPLE, 14, "wilder") == pytest.approx(
            37.788771982057824, abs=0.01
        )

    def test_wilder_and_sma14_differ_on_same_series(self):
        wilder = bfc.compute_rsi(WILDER_EXAMPLE, 14, "wilder")
        sma14 = bfc.compute_rsi(WILDER_EXAMPLE, 14, "sma14")
        assert wilder == pytest.approx(37.788771982057824, abs=0.01)
        assert sma14 == pytest.approx(30.217669654289423, abs=0.01)
        assert abs(wilder - sma14) > 5.0, "历史 RSI14 实为 SMA14，口径差异必须可测"

    def test_sma14_matches_legacy_implementation(self):
        for closes in (
            WILDER_EXAMPLE,
            CRAFTED_CLOSES,
            [10.0 + value for value in range(20)],
            [100.0 - value * 0.5 for value in range(20)],
        ):
            assert bfc.compute_rsi(closes, 14, "sma14") == pytest.approx(
                _legacy_sma14_rsi(closes, 14), abs=1e-9
            )

    def test_crafted_series_wilder_vs_sma14(self):
        assert bfc.compute_rsi(CRAFTED_CLOSES, 14, "wilder") == pytest.approx(
            57.86845484377229, abs=1e-6
        )
        assert bfc.compute_rsi(CRAFTED_CLOSES, 14, "sma14") == pytest.approx(
            48.484848484848484, abs=1e-6
        )

    def test_period_three_hand_calculation(self):
        # diffs=+1,+1,-1,+2,0,-1；Wilder: 初始(-1)均值 g=1,l=0，平滑 3 步后
        # g=(1*2+0)/3,(1*2+2)/3=2/3? 见期望值：Wilder=53.3333，SMA14=66.6667
        assert bfc.compute_rsi(P3_CLOSES, 3, "wilder") == pytest.approx(
            53.33333333333333, abs=1e-9
        )
        assert bfc.compute_rsi(P3_CLOSES, 3, "sma14") == pytest.approx(
            66.66666666666666, abs=1e-9
        )

    def test_all_gains_is_100(self):
        closes = [10.0 + value for value in range(20)]
        assert bfc.compute_rsi(closes, 14, "wilder") == pytest.approx(100.0)
        assert bfc.compute_rsi(closes, 14, "sma14") == pytest.approx(100.0)

    def test_all_losses_is_zero(self):
        closes = [100.0 - value for value in range(20)]
        assert bfc.compute_rsi(closes, 14, "wilder") == pytest.approx(0.0)
        assert bfc.compute_rsi(closes, 14, "sma14") == pytest.approx(0.0)

    def test_flat_series_is_50(self):
        assert bfc.compute_rsi([50.0] * 20, 14, "wilder") == pytest.approx(50.0)
        assert bfc.compute_rsi([50.0] * 20, 14, "sma14") == pytest.approx(50.0)

    def test_insufficient_data_returns_none(self):
        assert bfc.compute_rsi([10.0] * 14, 14, "wilder") is None  # 需 period+1 根
        assert bfc.compute_rsi([], 14, "sma14") is None
        assert bfc.compute_rsi([10.0] * 15, 14, "wilder") is not None

    def test_missing_value_returns_none(self):
        closes = [10.0 + value for value in range(19)] + [None]
        assert bfc.compute_rsi(closes, 14, "wilder") is None
        assert bfc.compute_rsi(closes, 14, "sma14") is None

    def test_default_method_is_wilder(self):
        assert bfc.compute_rsi(WILDER_EXAMPLE) == bfc.compute_rsi(
            WILDER_EXAMPLE, 14, "wilder"
        )

    def test_invalid_method_raises(self):
        for bad in ("ema", "SMA14", "", None):
            with pytest.raises(ValueError):
                bfc.compute_rsi(WILDER_EXAMPLE, 14, bad)  # type: ignore[arg-type]

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            bfc.compute_rsi(WILDER_EXAMPLE, 0, "wilder")


# ── 4. 量比（半根/未完成 bar 检测） ─────────────────────────────────
class TestVolumeRatio:
    @staticmethod
    def _volumes(latest, baseline=1000.0):
        return [baseline] * 20 + [latest]

    def test_normal_volume_ratio(self):
        volumes = self._volumes(1500.0)
        assert bfc.compute_volume_ratio(volumes) == pytest.approx(1.5)
        fact = bfc.volume_ratio_fact(volumes)
        assert fact["schema"] == bfc.SCHEMA
        assert fact["status"] == "ok"
        assert fact["volume_ratio"] == pytest.approx(1.5)
        assert fact["baseline_volume"] == pytest.approx(1000.0)
        assert fact["latest_volume"] == pytest.approx(1500.0)
        assert fact["baseline_period"] == 20
        assert fact["complete_bar_effective"] is True
        assert fact["partial_reason"] is None

    def test_half_bar_detected_below_1pct(self):
        """张松仓 CRYPTO 场景：末根量 < 近 20 日均量 1% → partial，不输出假量比。"""
        volumes = self._volumes(9.0)  # 0.9%
        assert bfc.compute_volume_ratio(volumes) is None
        fact = bfc.volume_ratio_fact(volumes)
        assert fact["status"] == "partial"
        assert fact["volume_ratio"] is None
        assert fact["complete_bar_effective"] is False
        assert fact["partial_reason"] == "volume_below_1pct_baseline"

    def test_declared_incomplete_bar(self):
        volumes = self._volumes(1500.0)
        assert bfc.compute_volume_ratio(volumes, complete_bar=False) is None
        fact = bfc.volume_ratio_fact(volumes, complete_bar=False)
        assert fact["status"] == "partial"
        assert fact["volume_ratio"] is None
        assert fact["partial_reason"] == "declared_incomplete"
        assert fact["complete_bar"] is False

    def test_exact_1pct_boundary_not_partial(self):
        """阈值严格小于 1%：恰好 1%（10/1000）不算 partial。"""
        volumes = self._volumes(10.0)
        fact = bfc.volume_ratio_fact(volumes)
        assert fact["status"] == "ok"
        assert fact["volume_ratio"] == pytest.approx(0.01)

    def test_insufficient_bars_unknown(self):
        assert bfc.compute_volume_ratio([1000.0] * 20) is None
        fact = bfc.volume_ratio_fact([1000.0] * 20)
        assert fact["status"] == "unknown"
        assert fact["partial_reason"] == "insufficient_bars"
        assert fact["volume_ratio"] is None

    def test_missing_or_nonpositive_baseline_unknown(self):
        volumes = [None] * 20 + [1500.0]
        fact = bfc.volume_ratio_fact(volumes)
        assert fact["status"] == "unknown"
        assert fact["partial_reason"] == "missing_volume"
        zero_baseline = [0.0] * 20 + [1500.0]
        assert bfc.compute_volume_ratio(zero_baseline) is None
        assert bfc.volume_ratio_fact(zero_baseline)["partial_reason"] == (
            "nonpositive_baseline"
        )

    def test_invalid_arguments_raise(self):
        volumes = self._volumes(1500.0)
        with pytest.raises(ValueError):
            bfc.compute_volume_ratio(volumes, period=0)
        with pytest.raises(ValueError):
            bfc.compute_volume_ratio(volumes, complete_bar="yes")  # type: ignore[arg-type]

    def test_fact_shape(self):
        fact = bfc.volume_ratio_fact(self._volumes(1500.0))
        assert set(fact) == {
            "schema",
            "status",
            "volume_ratio",
            "complete_bar",
            "complete_bar_effective",
            "partial_reason",
            "baseline_period",
            "baseline_volume",
            "latest_volume",
        }


# ── 5. 日收益（pre_close fallback） ──────────────────────────────────
class TestComputeReturn:
    def test_normal_return_percent(self):
        assert bfc.compute_return(10.5, 10.0) == pytest.approx(5.0)
        assert bfc.compute_return(9.5, 10.0) == pytest.approx(-5.0)

    def test_pre_close_fallback(self):
        assert bfc.compute_return(10.5, None, 10.0) == pytest.approx(5.0)
        # pre_close 非法（0/负/NaN）时回退 fallback_prev_close
        for bad_pre_close in (0, -1.0, float("nan")):
            assert bfc.compute_return(10.5, bad_pre_close, 10.0) == pytest.approx(5.0)

    def test_pre_close_preferred_over_fallback(self):
        assert bfc.compute_return(10.5, 10.0, 8.0) == pytest.approx(5.0)

    def test_unknown_when_no_valid_basis(self):
        assert bfc.compute_return(10.5, None, None) is None
        assert bfc.compute_return(10.5, 0, 0) is None
        assert bfc.compute_return(None, 10.0) is None
        assert bfc.compute_return(0, 10.0) is None
        assert bfc.compute_return(float("nan"), 10.0) is None


# ── 6. validate_fact ─────────────────────────────────────────────────
def _valid_fact(**overrides):
    fact = {
        "asof": "20261101",
        "source": "tushare:pro",
        "price_adjust": "qfq",
        "rsi_method": "wilder",
        "complete_bar": True,
    }
    fact.update(overrides)
    return fact


class TestValidateFact:
    def test_pass_requires_all_fields(self):
        result = bfc.validate_fact(_valid_fact())
        assert result["schema"] == bfc.SCHEMA
        assert result["status"] == "pass"
        assert result["issues"] == []
        assert result["missing"] == []
        assert result["invalid"] == []
        assert result["warnings"] == []

    def test_asof_accepts_both_formats(self):
        for asof in ("20261101", "2026-11-01"):
            assert bfc.validate_fact(_valid_fact(asof=asof))["status"] == "pass"

    def test_qfq_and_hqf_pass(self):
        for adjust in ("qfq", "hqf"):
            assert bfc.validate_fact(_valid_fact(price_adjust=adjust))["status"] == "pass"

    def test_price_adjust_none_passes_with_warning(self):
        result = bfc.validate_fact(_valid_fact(price_adjust="none"))
        assert result["status"] == "pass"
        assert result["warnings"]
        assert "未复权" in result["warnings"][0]

    def test_missing_fields_unknown_not_pass(self):
        fact = _valid_fact()
        fact.pop("rsi_method")
        fact.pop("complete_bar")
        result = bfc.validate_fact(fact)
        assert result["status"] == "unknown"  # 不猜 pass
        assert set(result["missing"]) == {"rsi_method", "complete_bar"}
        assert any("missing:rsi_method" in issue for issue in result["issues"])

    def test_empty_dict_unknown(self):
        result = bfc.validate_fact({})
        assert result["status"] == "unknown"
        assert set(result["missing"]) == set(bfc.REQUIRED_FACT_FIELDS)

    def test_empty_string_counts_as_missing(self):
        result = bfc.validate_fact(_valid_fact(asof="   "))
        assert result["status"] == "unknown"
        assert result["missing"] == ["asof"]

    def test_complete_bar_false_is_unknown(self):
        result = bfc.validate_fact(_valid_fact(complete_bar=False))
        assert result["status"] == "unknown"
        assert any("complete_bar=false" in issue for issue in result["issues"])

    def test_invalid_price_adjust_fail(self):
        for bad in ("hfq", "adj", "QFQ", 1):
            result = bfc.validate_fact(_valid_fact(price_adjust=bad))
            assert result["status"] == "fail"
            assert "price_adjust" in result["invalid"]

    def test_invalid_rsi_method_fail(self):
        result = bfc.validate_fact(_valid_fact(rsi_method="ema"))
        assert result["status"] == "fail"
        assert "rsi_method" in result["invalid"]

    def test_invalid_asof_format_fail(self):
        for bad in ("2026/11/01", "2026111", "2026-1-1", "x"):
            result = bfc.validate_fact(_valid_fact(asof=bad))
            assert result["status"] == "fail"
            assert "asof" in result["invalid"]

    def test_invalid_source_fail(self):
        for bad in (123, b"tushare", ""):
            result = bfc.validate_fact(_valid_fact(source=bad))
            # 空串按缺字段 unknown；非字符串按非法 fail
            assert result["status"] in ("unknown", "fail")
        assert bfc.validate_fact(_valid_fact(source=123))["status"] == "fail"

    def test_invalid_complete_bar_type_fail(self):
        for bad in ("yes", 1, 0, None):
            result = bfc.validate_fact(_valid_fact(complete_bar=bad))
            if bad is None:
                assert result["status"] == "unknown"  # None 视为缺字段
            else:
                assert result["status"] == "fail"
                assert "complete_bar" in result["invalid"]

    def test_non_mapping_fail(self):
        for bad in (None, [], "fact", 42):
            result = bfc.validate_fact(bad)
            assert result["status"] == "fail"
            assert set(result["missing"]) == set(bfc.REQUIRED_FACT_FIELDS)


# ── 7. 纯读红线（stdlib only / 无网络 DB 写盘） ─────────────────────
_STDLIB_ALLOWED = {"__future__", "math", "re", "typing"}
_FORBIDDEN_ROOTS = {
    "numpy",
    "pandas",
    "pandera",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "socket",
    "psycopg2",
    "sqlite3",
    "subprocess",
    "sqlalchemy",
}
_WRITE_CALLS = {"write_text", "write_bytes", "dump", "dump_all", "save", "remove"}


class TestPurity:
    def test_imports_are_stdlib_only(self):
        source = open(bfc.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(source)
        import_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_roots.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                import_roots.add(node.module.split(".")[0])
        assert import_roots <= _STDLIB_ALLOWED, "只允许 stdlib 依赖: %s" % import_roots
        assert not (import_roots & _FORBIDDEN_ROOTS), "禁止网络/DB/第三方依赖"

    def test_no_write_or_network_calls(self):
        source = open(bfc.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(source)
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else "")
            )
            if name in _WRITE_CALLS:
                found.add(name)
            if name == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for keyword in node.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode = keyword.value.value
                if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
                    found.add("open(%r)" % mode)
        assert found == set(), "契约模块不得写盘: %s" % found

    def test_no_module_level_side_effect_files(self):
        """导入模块后不新建文件（在空目录下 import 无残留）。"""
        module_path = os.path.abspath(bfc.__file__)
        assert os.path.isfile(module_path)
        assert not os.path.exists(module_path + ".tmp")
