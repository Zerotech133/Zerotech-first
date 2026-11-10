# -*- coding: utf-8 -*-
"""A4 回归（2026-11-10）：buy_filter 严禁静默回退。

背景：run_job 入口已有守卫（2026-11-09 判例——未知 buy_filter 把
「只做1买」悄悄变「只做3买」，数字看着正常、结论完全错），但
calc_chanlun_signals / calc_chanlun_signals_market 内部两处
``.get(buy_filter, 兜底)`` 仍会静默回落。2026-11-10 收口为
``_resolve_buy_filter``（fail-fast 抛 ValueError），本测试钉死三件事：
  ① _resolve_buy_filter 对非法值抛 ValueError、合法值返回正确集合；
  ② 两个 calc 函数对非法值在碰缓存/重算之前就抛；
  ③ 源码层 tripwire：backtest_engine_v2.py 不得再出现 buy_filter
     的 .get 静默兜底写法。
"""

import inspect
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest_engine_v2 as bev  # noqa: E402

BAD_VALUES = ["4", "", "2买", "23 ", "1,3", None, "3买"]
LEGAL = {"all": {"1买", "2买", "3买"}, "23": {"2买", "3买"},
         "1": {"1买"}, "2": {"2买"}, "3": {"3买"}}


def test_resolve_rejects_illegal():
    for bad in BAD_VALUES:
        with pytest.raises(ValueError):
            bev._resolve_buy_filter(bad)


def test_resolve_accepts_legal():
    for k, want in LEGAL.items():
        assert bev._resolve_buy_filter(k) == want


def test_calc_chanlun_signals_raises_before_any_work():
    with pytest.raises(ValueError):
        bev.calc_chanlun_signals("20251013", "20251020", buy_filter="4")


def test_calc_market_raises_before_cache_check():
    with pytest.raises(ValueError):
        bev.calc_chanlun_signals_market(
            "20251013", "20251020", buy_filter="4"
        )


def test_source_has_no_silent_fallback():
    """tripwire：任何人再写回 ``BUY_FILTERS.get(buy_filter, 兜底)`` 即红。

    模式特意用全限定 ``BUY_FILTERS.get(buy_filter``——避免误伤
    _resolve_buy_filter docstring 里的教学字面量。"""
    src = inspect.getsource(bev)
    assert "BUY_FILTERS.get(buy_filter" not in src, (
        "backtest_engine_v2 出现 buy_filter 静默兜底（A4 回归）"
    )
