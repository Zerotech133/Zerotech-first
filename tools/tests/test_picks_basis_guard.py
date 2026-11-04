# -*- coding: utf-8 -*-
"""TodaysPicks 基准新鲜度闸门测试（2026-11-04 立）。

背景：TodaysPicks 15:40 早于 TdxSync_A_高频 17:35，basis 恒为 D-1；
2026-11-04 实测 17:35 同步后才写入当日 ts_daily。本闸门 fail-closed：
ashare 收盘库已有当日 > 行情湖基准日 → rc=4 拒绝写盘。
"""
import importlib.util
import os

import pytest

BUILDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "portal",
    "build_todays_picks.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("build_todays_picks_bg", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


def test_same_day_pass(mod):
    assert mod._basis_guard("20261104", "20261104") is None


def test_weekend_same_last_trading_day_pass(mod):
    # 周六日两库同为周五收盘，基准相等，不误报
    assert mod._basis_guard("20261031", "20261031") is None


def test_lake_lagging_ashare_fails_closed(mod):
    err = mod._basis_guard("20261103", "20261104")
    assert err
    assert "20261103" in err and "20261104" in err


def test_ashare_probe_missing_fails_closed(mod, monkeypatch):
    # 2026-11-05 审计修复：探测失败=无法证明新鲜，必须 rc=4，不能静默写 D-1。
    monkeypatch.setattr(mod, "_ashare_latest_day", lambda *a, **k: None)
    err = mod._basis_guard("20261103", None)
    assert err and "探测失败" in err
    # 显式 None 同样回退到真实探测（此处已被 monkeypatch）
    assert mod._basis_guard("20261103", None)


def test_ashare_probe_missing_env_escape(mod, monkeypatch):
    monkeypatch.setattr(mod, "_ashare_latest_day", lambda *a, **k: None)
    monkeypatch.setenv("PICKS_BASIS_ALLOW_PROBE_FAIL", "1")
    assert mod._basis_guard("20261103", None) is None


def test_basis_missing_degrades(mod):
    assert mod._basis_guard(None, "20261104") is None


def test_guard_can_be_disabled(mod, monkeypatch):
    monkeypatch.setenv("PICKS_BASIS_GUARD", "0")
    assert mod._basis_guard("20261103", "20261104") is None


def test_guard_default_on(mod, monkeypatch):
    monkeypatch.delenv("PICKS_BASIS_GUARD", raising=False)
    assert mod._basis_guard("20261103", "20261104")
