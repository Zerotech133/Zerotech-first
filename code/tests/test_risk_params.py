"""风控参数读取模块单元测试（risk_params.py）。

覆盖：数值校验边界 / 全量解析 / 非法回退默认 / 止损百分数→小数换算 /
读取异常回退策略级硬编码（A1 接线核心语义）。不连任何数据库——
_load_raw_risk_params 全部 monkeypatch。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import zhice_os_v2.application.services.risk_params as rp
from zhice_os_v2.application.services.risk_params import (
    DEFAULT_RISK_PARAMS,
    effective_stop_loss_pct,
    load_risk_params,
    settings_stop_loss_override,
)


def _raw(monkeypatch, value):
    monkeypatch.setattr(rp, "_load_raw_risk_params", lambda: value)


# ── _coerce 数值边界 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0, -5, 101, None, "abc", "", float("nan"), float("inf")])
def test_coerce_invalid(bad):
    assert rp._coerce(bad) is None


@pytest.mark.parametrize("good,expect", [(20.0, 20.0), (100, 100.0), ("8.0", 8.0), (0.5, 0.5)])
def test_coerce_valid(good, expect):
    assert rp._coerce(good) == expect


# ── load_risk_params：全量解析 / 缺失回退 ────────────────────────────────────

def test_load_full_params(monkeypatch):
    _raw(monkeypatch, {"max_single_position_pct": 25.0, "max_total_position_pct": 75.0,
                       "max_daily_loss_pct": 3.0, "stop_loss_pct": 8.0})
    p = load_risk_params()
    assert (p.max_single_position_pct, p.max_total_position_pct,
            p.max_daily_loss_pct, p.stop_loss_pct) == (25.0, 75.0, 3.0, 8.0)


def test_load_missing_one_falls_back_to_default(monkeypatch):
    """任一参数缺失/非法 → 整体回退代码默认（规则照跑，默认值守门）。"""
    _raw(monkeypatch, {"max_single_position_pct": 25.0, "max_total_position_pct": 75.0,
                       "max_daily_loss_pct": 3.0})  # 缺 stop_loss_pct
    assert load_risk_params() == DEFAULT_RISK_PARAMS


def test_load_illegal_value_falls_back(monkeypatch):
    """参数越界（0=关规则 / 负 / >100）→ 回退默认。"""
    _raw(monkeypatch, {"max_single_position_pct": 25.0, "max_total_position_pct": 0,
                       "max_daily_loss_pct": 3.0, "stop_loss_pct": 8.0})
    assert load_risk_params() == DEFAULT_RISK_PARAMS


def test_load_read_failure_falls_back(monkeypatch):
    """读取异常（DB 挂/无记录）→ 回退默认。"""
    _raw(monkeypatch, None)
    assert load_risk_params() == DEFAULT_RISK_PARAMS


def test_default_matches_settings_router_default():
    """代码默认必须与 api/routers/settings.py _DEFAULT_STATE 同步（A4 一致性守护）。"""
    from zhice_os_v2.api.routers.settings import _DEFAULT_STATE
    d = _DEFAULT_STATE["risk_params"]
    assert DEFAULT_RISK_PARAMS.max_single_position_pct == d["max_single_position_pct"]
    assert DEFAULT_RISK_PARAMS.max_total_position_pct == d["max_total_position_pct"]
    assert DEFAULT_RISK_PARAMS.max_daily_loss_pct == d["max_daily_loss_pct"]
    assert DEFAULT_RISK_PARAMS.stop_loss_pct == d["stop_loss_pct"]


# ── 止损换算：百分数(8.0) → 小数(0.08) ──────────────────────────────────────

def test_stop_loss_override_converts_percent_to_decimal(monkeypatch):
    _raw(monkeypatch, {"stop_loss_pct": 8.0})
    assert settings_stop_loss_override() == Decimal("0.08")


def test_stop_loss_override_unavailable(monkeypatch):
    """读取失败/未配置 → None（调用方回退策略级硬编码）。"""
    _raw(monkeypatch, None)
    assert settings_stop_loss_override() is None


def test_stop_loss_override_illegal(monkeypatch):
    _raw(monkeypatch, {"stop_loss_pct": "not-a-number"})
    assert settings_stop_loss_override() is None


def test_effective_stop_loss_uses_setting(monkeypatch):
    _raw(monkeypatch, {"stop_loss_pct": 8.0})
    assert effective_stop_loss_pct(Decimal("0.05")) == Decimal("0.08")


def test_effective_stop_loss_falls_back_to_hardcoded(monkeypatch):
    """读取异常回退现行硬编码（0.04/0.05/0.06 策略级值原样透传）。"""
    _raw(monkeypatch, None)
    for fb in ("0.04", "0.05", "0.06", "0.07"):
        assert effective_stop_loss_pct(Decimal(fb)) == Decimal(fb)
