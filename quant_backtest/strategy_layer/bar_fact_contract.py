# -*- coding: utf-8 -*-
r"""行情事实契约 bar_fact_contract/v1（纯函数，只读；WO-BAR-FACT-CONTRACT-20261101）。

把「MA / RSI / 量比 / 日收益 / 事实字段校验」收敛为一套**可复现、口径显式**的
纯函数契约，供候选生成 guard / ACAD1 / Strategy Health 等只读消费，消除同名指标
口径漂移（张松仓实测暴露的三类问题）：

1) **RSI14 实为 SMA14 而非 Wilder**：本契约把两种口径显式参数化——
   ``compute_rsi(closes, 14, method="wilder")``（经典 Wilder 平滑）
   与 ``method="sma14"``（近 14 段差分简单平均，即历史实现口径）；
2) **MA 未复权**：事实字段必须声明 ``price_adjust``（``none`` / ``qfq`` / ``hqf``），
   ``none`` 会带 warning（校验仍 pass，但消费方可知未复权）；
3) **CRYPTO 半根日线导致量比 0.0009**：``compute_volume_ratio`` 检测「末根量 <
   近 20 日均量 1%」→ 判未完成（partial），返回 ``None`` 不输出假量比；
   ``volume_ratio_fact`` 给出 partial/unknown 结构化原因。

设计红线
--------
- **纯函数、只读**：无网络 / 无 DB / 不写盘 / 不依赖 pandera 等第三方；
- **不造数**：数据不足 / 窗口含缺失 → 返回 ``None`` 或 ``status="unknown"``，
  绝不填充猜测值；
- **口径显式**：``schema = bar_fact_contract/v1``；RSI 方法、复权口径、bar 完整性
  都是显式输入/字段，不用隐式默认掩盖差异；
- **closed-on-failure**：枚举值/参数非法（如未知 rsi_method、period<=0）抛
  ``ValueError``，不静默降级。

对外 API
--------
``SCHEMA`` / ``MA_DEFAULT_PERIOD`` / ``RSI_DEFAULT_PERIOD`` / ``RSI_METHODS`` /
``PRICE_ADJUSTS`` / ``REQUIRED_FACT_FIELDS`` / ``PARTIAL_VOLUME_RATIO_THRESHOLD``

- ``compute_ma(closes, period=20) -> float | None``
    简单均线（最近 ``period`` 根收盘均值）；窗口内缺失/非数值 → ``None``。
- ``compute_rsi(closes, period=14, method="wilder") -> float | None``
    ``method="wilder"``：经典 Wilder 平滑（初始 period 段简单均值，之后
    ``avg=(prev*(period-1)+cur)/period``）；``method="sma14"``：近 period 段
    差分简单平均（历史实现口径）。全涨 → 100，全跌 → 0，完全走平 → 50；
    数据不足/含缺失 → ``None``；未知 method → ``ValueError``。
- ``compute_volume_ratio(volumes, complete_bar=True, period=20) -> float | None``
    量比 = 末根量 / 前 ``period`` 根均量；``complete_bar=False`` 或检测到
    末根量 < 均量 1%（半根/停牌量）→ ``None``；数据不足 → ``None``。
- ``volume_ratio_fact(volumes, complete_bar=True, period=20) -> dict``
    量比的结构化事实：``{schema, status: ok|partial|unknown, volume_ratio,
    complete_bar, complete_bar_effective, partial_reason, baseline_period,
    baseline_volume, latest_volume}``（partial/unknown 时 volume_ratio=None）。
- ``compute_return(close, pre_close=None, fallback_prev_close=None) -> float | None``
    日收益（**百分数**，+1.23 表示 +1.23%）：优先 ``pre_close``，缺失/非法时用
    ``fallback_prev_close``（公司行动/停牌后昨收缺失场景）；两者都不可用 → ``None``。
- ``validate_fact(fact) -> dict``
    校验事实字段 ``asof / source / price_adjust / rsi_method / complete_bar``：
    返回 ``{schema, status: pass|unknown|fail, issues, missing, invalid, warnings}``；
    缺字段/空值 → **unknown（不猜 pass）**；``complete_bar=false`` → unknown
    （半根 bar，不判 pass）；枚举/格式非法 → fail；``price_adjust=none`` 带 warning。

用法::

    from strategy_layer.bar_fact_contract import (
        compute_ma, compute_rsi, compute_volume_ratio, compute_return, validate_fact,
    )

    compute_ma(closes)                                      # 20 日均线
    compute_rsi(closes, 14, method="wilder")                # 经典 RSI14
    compute_rsi(closes, 14, method="sma14")                 # 历史 SMA14 口径
    compute_volume_ratio(volumes, complete_bar=True)        # 半根日线 → None
    compute_return(close=10.5, pre_close=None,
                   fallback_prev_close=10.0)               # +5.0
    validate_fact({"asof": "20261101", "source": "tushare",
                   "price_adjust": "qfq", "rsi_method": "wilder",
                   "complete_bar": True})                   # status=pass
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Optional, Sequence

SCHEMA = "bar_fact_contract/v1"

MA_DEFAULT_PERIOD = 20
RSI_DEFAULT_PERIOD = 14
VOLUME_BASELINE_PERIOD = 20
#: 末根量 / 前 N 根均量 严格小于该阈值 → 视为未完成 bar（半根/停牌量）
PARTIAL_VOLUME_RATIO_THRESHOLD = 0.01

RSI_METHODS = ("wilder", "sma14")
RSI_METHOD_DEFAULT = "wilder"
PRICE_ADJUSTS = ("none", "qfq", "hqf")

STATUS_PASS = "pass"
STATUS_UNKNOWN = "unknown"
STATUS_FAIL = "fail"
VALIDATE_STATUSES = (STATUS_PASS, STATUS_UNKNOWN, STATUS_FAIL)

VOLUME_STATUS_OK = "ok"
VOLUME_STATUS_PARTIAL = "partial"
VOLUME_STATUS_UNKNOWN = "unknown"

#: validate_fact 必填字段（缺一 → unknown，不猜 pass）
REQUIRED_FACT_FIELDS = ("asof", "source", "price_adjust", "rsi_method", "complete_bar")
#: 复权口径提示：none=未复权（合法但需消费方知情）
_PRICE_ADJUST_WARNINGS = {
    "none": "price_adjust=none（未复权：MA/RSI 可能含除权缺口）",
}

_ASOF_RE = re.compile(r"^(?:\d{8}|\d{4}-\d{2}-\d{2})$")
_EPSILON = 1e-12


# ── 基础工具（纯函数，无副作用） ──────────────────────────────────────
def _as_series(values: Any, name: str) -> list:
    """把输入转为 list；非序列（str/None/dict/生成器以外）抛 ValueError。"""
    if isinstance(values, (list, tuple, range)):
        return list(values)
    raise ValueError("%s 必须为 list/tuple 序列，收到 %s" % (name, type(values).__name__))


def _to_number(value: Any) -> Optional[float]:
    """数值归一：int/float 且有限 → float；bool/None/NaN/Inf/非法 → None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _clean_series(values: Any, name: str) -> list:
    """逐元素归一到 float/None（None 保留为缺失位，由调用方决定是否容忍）。"""
    series = _as_series(values, name)
    return [_to_number(item) for item in series]


def _require_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("%s 必须为正整数，收到 %r" % (name, value))
    return value


def _require_complete_bar_flag(complete_bar: Any) -> bool:
    if not isinstance(complete_bar, bool):
        raise ValueError("complete_bar 必须为 bool，收到 %r" % (complete_bar,))
    return complete_bar


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    """RSI 公式（avg_loss≈0：有涨→100、全平→50）。"""
    if avg_loss <= _EPSILON:
        return 100.0 if avg_gain > _EPSILON else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# ── MA ───────────────────────────────────────────────────────────────
def compute_ma(closes: Sequence[float], period: int = MA_DEFAULT_PERIOD) -> Optional[float]:
    """简单均线：最近 ``period`` 根收盘的算术平均（不补数、不外推）。

    - 最近 ``period`` 根窗口内出现 None/非数值 → ``None``（unknown，不猜）；
    - 序列长度 < period → ``None``；
    - ``period`` 非法（非正整数）→ ``ValueError``（closed-on-failure）。
    """
    period = _require_positive_int(period, "period")
    series = _as_series(closes, "closes")
    if len(series) < period:
        return None
    window = series[-period:]
    numbers = [_to_number(item) for item in window]
    if any(number is None for number in numbers):
        return None
    return sum(numbers) / period


# ── RSI（Wilder / SMA14 双口径） ─────────────────────────────────────
def compute_rsi(
    closes: Sequence[float],
    period: int = RSI_DEFAULT_PERIOD,
    method: str = RSI_METHOD_DEFAULT,
) -> Optional[float]:
    """RSI（默认经典 Wilder 口径；``method="sma14"`` 为历史简单平均口径）。

    Parameters
    ----------
    closes : sequence[float]
        收盘序列（至少 ``period + 1`` 根；含 None/非数值 → ``None``，不插值）。
    period : int
        差分段数（默认 14）。
    method : {"wilder", "sma14"}
        - ``wilder``：初始 avg = 前 ``period`` 段差分简单均值，之后
          ``avg = (prev * (period - 1) + cur) / period``；
        - ``sma14``：仅取最后 ``period`` 段差分的简单平均（历史实现口径，
          与 RSI14 名义不符——故本契约显式区分）。
    全涨→100、全跌→0、完全走平→50；数据不足/含缺失 → ``None``；
    未知 method → ``ValueError``。
    """
    period = _require_positive_int(period, "period")
    if method not in RSI_METHODS:
        raise ValueError(
            "未知 RSI method: %r（允许: %s）" % (method, ", ".join(RSI_METHODS))
        )
    series = _clean_series(closes, "closes")
    if len(series) < period + 1:
        return None
    if any(number is None for number in series):
        return None
    diffs = [
        series[index] - series[index - 1] for index in range(1, len(series))
    ]
    gains = [max(diff, 0.0) for diff in diffs]
    losses = [max(-diff, 0.0) for diff in diffs]
    if method == "sma14":
        window_gains = gains[-period:]
        window_losses = losses[-period:]
        avg_gain = sum(window_gains) / period
        avg_loss = sum(window_losses) / period
    else:  # wilder：初始 period 段简单均值 + 后续 Wilder 平滑
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for index in range(period, len(diffs)):
            avg_gain = (avg_gain * (period - 1) + gains[index]) / period
            avg_loss = (avg_loss * (period - 1) + losses[index]) / period
    return _rsi_from_avgs(avg_gain, avg_loss)


# ── 量比（含半根/未完成 bar 检测） ───────────────────────────────────
def volume_ratio_fact(
    volumes: Sequence[float],
    complete_bar: bool = True,
    period: int = VOLUME_BASELINE_PERIOD,
) -> dict:
    """量比结构化事实：量比 = 末根量 / 前 ``period`` 根均量。

    Returns
    -------
    dict
        ``{schema, status: ok|partial|unknown, volume_ratio,
        complete_bar, complete_bar_effective, partial_reason,
        baseline_period, baseline_volume, latest_volume}``

    partial/unknown 判定（不输出假量比，volume_ratio 恒 None）：
    - ``complete_bar=False``（调用方显式声明半根/未完成）→ partial
      ``partial_reason="declared_incomplete"``；
    - 末根量 / 前 period 均量 < ``PARTIAL_VOLUME_RATIO_THRESHOLD``（1%，张松仓
      CRYPTO 半根日线量比 0.0009 场景）→ partial
      ``partial_reason="volume_below_1pct_baseline"``；
    - 数据不足 / 窗口含缺失 / 非正数均量 → unknown（不猜）。
    """
    period = _require_positive_int(period, "period")
    complete_bar = _require_complete_bar_flag(complete_bar)
    series = _clean_series(volumes, "volumes")
    fact = {
        "schema": SCHEMA,
        "status": VOLUME_STATUS_UNKNOWN,
        "volume_ratio": None,
        "complete_bar": complete_bar,
        "complete_bar_effective": complete_bar,
        "partial_reason": None,
        "baseline_period": period,
        "baseline_volume": None,
        "latest_volume": None,
    }
    if len(series) < period + 1:
        fact["partial_reason"] = "insufficient_bars"
        return fact
    baseline_window = series[-(period + 1) : -1]
    latest = series[-1]
    if latest is None or any(item is None for item in baseline_window):
        fact["partial_reason"] = "missing_volume"
        return fact
    baseline = sum(baseline_window) / period
    fact["baseline_volume"] = baseline
    fact["latest_volume"] = latest
    if baseline <= 0:
        fact["partial_reason"] = "nonpositive_baseline"
        return fact
    if not complete_bar:
        fact["status"] = VOLUME_STATUS_PARTIAL
        fact["complete_bar_effective"] = False
        fact["partial_reason"] = "declared_incomplete"
        return fact
    ratio = latest / baseline
    if ratio < PARTIAL_VOLUME_RATIO_THRESHOLD:
        # 末根量远低于近 N 日均量：半根日线/停牌量，判未完成，返回 None
        fact["status"] = VOLUME_STATUS_PARTIAL
        fact["complete_bar_effective"] = False
        fact["partial_reason"] = "volume_below_1pct_baseline"
        return fact
    fact["status"] = VOLUME_STATUS_OK
    fact["complete_bar_effective"] = True
    fact["volume_ratio"] = ratio
    return fact


def compute_volume_ratio(
    volumes: Sequence[float],
    complete_bar: bool = True,
    period: int = VOLUME_BASELINE_PERIOD,
) -> Optional[float]:
    """量比 = 末根量 / 前 ``period`` 根均量；半根/未完成/数据不足 → ``None``。

    ``complete_bar=False`` 或被 ``volume_ratio_fact`` 判 partial（末根量低于
    近 N 日均量 1%，典型为 CRYPTO 半根日线）→ ``None``，不输出假量比。
    """
    fact = volume_ratio_fact(volumes, complete_bar=complete_bar, period=period)
    return fact["volume_ratio"] if fact["status"] == VOLUME_STATUS_OK else None


# ── 日收益（pre_close fallback） ─────────────────────────────────────
def compute_return(
    close: Any,
    pre_close: Any = None,
    fallback_prev_close: Any = None,
) -> Optional[float]:
    """日收益（**百分数**：+1.23 表示 +1.23%）。

    基准价优先 ``pre_close``；缺失/非法（None、<=0、非数值）时回退
    ``fallback_prev_close``（公司行动/停牌导致昨收缺失场景）；两者都不可用
    或 close 非法 → ``None``（unknown，不猜）。
    """
    price = _to_number(close)
    base = _to_number(pre_close)
    if base is None or base <= 0:
        base = _to_number(fallback_prev_close)
    if price is None or price <= 0 or base is None or base <= 0:
        return None
    return (price / base - 1.0) * 100.0


# ── 事实字段校验 ─────────────────────────────────────────────────────
def validate_fact(fact: Any) -> dict:
    """校验行情事实字段（``bar_fact_contract/v1``），返回结构化 verdict。

    必填字段：``asof`` / ``source`` / ``price_adjust`` / ``rsi_method`` /
    ``complete_bar``（``REQUIRED_FACT_FIELDS``）。

    状态口径（closed-on-failure，**缺字段不猜 pass**）：
    - ``fail``：字段存在但值非法（price_adjust/rsi_method 不在枚举、asof 格式、
      source 空/非字符串、complete_bar 非 bool）；
    - ``unknown``：必填字段缺失/空值（无法判断，不猜 pass）；或
      ``complete_bar=false``（半根/未完成 bar，数据不可直接当完整事实用）；
    - ``pass``：字段齐全且合法（``complete_bar=true``）；``price_adjust=none``
      时附 ``warnings``（未复权提示，仍 pass）。

    Returns
    -------
    dict
        ``{schema, status, issues, missing, invalid, warnings}``
    """
    result = {
        "schema": SCHEMA,
        "status": STATUS_FAIL,
        "issues": [],
        "missing": [],
        "invalid": [],
        "warnings": [],
    }
    if not isinstance(fact, Mapping):
        result["issues"].append(
            "fact 必须为 dict，收到 %s" % type(fact).__name__
        )
        result["missing"] = list(REQUIRED_FACT_FIELDS)
        return result

    missing = []
    for field in REQUIRED_FACT_FIELDS:
        value = fact.get(field)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip():
            missing.append(field)
    if missing:
        result["missing"] = missing
        result["issues"] = ["missing:%s" % field for field in missing]
        result["status"] = STATUS_UNKNOWN
        return result

    invalid = []
    asof = fact.get("asof")
    if not isinstance(asof, str) or not _ASOF_RE.match(asof.strip()):
        invalid.append("asof")
        result["issues"].append("asof 必须为 YYYYMMDD 或 YYYY-MM-DD: %r" % (asof,))
    source = fact.get("source")
    if not isinstance(source, str) or not source.strip():
        invalid.append("source")
        result["issues"].append("source 必须为非空字符串: %r" % (source,))
    price_adjust = fact.get("price_adjust")
    if price_adjust not in PRICE_ADJUSTS:
        invalid.append("price_adjust")
        result["issues"].append(
            "price_adjust 非法: %r（允许: %s）" % (price_adjust, ", ".join(PRICE_ADJUSTS))
        )
    rsi_method = fact.get("rsi_method")
    if rsi_method not in RSI_METHODS:
        invalid.append("rsi_method")
        result["issues"].append(
            "rsi_method 非法: %r（允许: %s）" % (rsi_method, ", ".join(RSI_METHODS))
        )
    complete_bar = fact.get("complete_bar")
    if not isinstance(complete_bar, bool):
        invalid.append("complete_bar")
        result["issues"].append("complete_bar 必须为 bool: %r" % (complete_bar,))

    if invalid:
        result["invalid"] = invalid
        result["status"] = STATUS_FAIL
        return result

    if price_adjust in _PRICE_ADJUST_WARNINGS:
        result["warnings"].append(_PRICE_ADJUST_WARNINGS[price_adjust])
    if complete_bar is False:
        # 半根/未完成 bar：字段合法但数据不可完整消费 → 显式 unknown，不判 pass
        result["status"] = STATUS_UNKNOWN
        result["issues"].append("complete_bar=false（半根/未完成 bar，不判 pass）")
        return result

    result["status"] = STATUS_PASS
    return result


__all__ = [
    "MA_DEFAULT_PERIOD",
    "PARTIAL_VOLUME_RATIO_THRESHOLD",
    "PRICE_ADJUSTS",
    "REQUIRED_FACT_FIELDS",
    "RSI_DEFAULT_PERIOD",
    "RSI_METHODS",
    "RSI_METHOD_DEFAULT",
    "SCHEMA",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_UNKNOWN",
    "VALIDATE_STATUSES",
    "VOLUME_BASELINE_PERIOD",
    "VOLUME_STATUS_OK",
    "VOLUME_STATUS_PARTIAL",
    "VOLUME_STATUS_UNKNOWN",
    "compute_ma",
    "compute_return",
    "compute_rsi",
    "compute_volume_ratio",
    "validate_fact",
    "volume_ratio_fact",
]
