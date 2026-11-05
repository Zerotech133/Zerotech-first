# -*- coding: utf-8 -*-
"""T8 成本口径默认值单测（qpzm_backtest.default_cost_roundtrip）。

口径依据：config.trade.by_market 的 cost_bps / slippage_bps 为**单边**费率，
回测 cost 逐笔往返扣一次（trade_returns: ret = exit/entry - 1 - cost），
故往返 = 2×(cost_bps + slippage_bps)/10000。

跑法（两种都支持）：
  "C:/Program Files/Python311/python.exe" -m pytest tests/test_cost_default.py -q
  "C:/Program Files/Python311/python.exe" tests/test_cost_default.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import backtest  # noqa: E402  （被 qpzm_backtest 复用的 config 读取层）
import qpzm_backtest as qpzm  # noqa: E402


def test_cn_default_is_30bp():
    assert qpzm.default_cost_roundtrip("CN") == 0.0030


def test_other_markets_follow_config():
    assert qpzm.default_cost_roundtrip("US") == 0.0012
    assert qpzm.default_cost_roundtrip("HK") == 0.0012
    assert qpzm.default_cost_roundtrip("CRYPTO") == 0.0020


def test_slippage_change_propagates(monkeypatch):
    """内存里改 slippage_bps，推导值须随之变化（不碰真实 config.json）。"""
    orig = backtest.load_trade_config
    base = orig("CN")

    def fake(market=None, cfg=None):
        d = dict(orig(market, cfg))
        if market == "CN":
            d["cost_bps"] = base["cost_bps"]
            d["slippage_bps"] = base["slippage_bps"] + 5  # 3 -> 8
        return d

    monkeypatch.setattr(backtest, "load_trade_config", fake)
    expect = 2.0 * (base["cost_bps"] + base["slippage_bps"] + 5) / 10000.0
    assert qpzm.default_cost_roundtrip("CN") == expect
    assert expect == 0.0040
    # 与未改前不同，证明不是硬编码常量
    assert qpzm.default_cost_roundtrip("CN") != 0.0030


def test_unknown_market_falls_back(monkeypatch):
    """market 不在 by_market → 0.0030 且不抛异常（并打印告警）。"""
    val = qpzm.default_cost_roundtrip("__NO_SUCH_MARKET__")
    assert val == 0.0030


def test_load_failure_falls_back_without_raise(monkeypatch):
    """config 读取抛异常 → 同样回退 0.0030，不允许把异常抛给调用方。"""

    def boom(market=None, cfg=None):
        raise FileNotFoundError("config.json not found")

    monkeypatch.setattr(backtest, "load_trade_config", boom)
    assert qpzm.default_cost_roundtrip("CN") == 0.0030


if __name__ == "__main__":
    class _MP:
        """最小 monkeypatch（pytest 缺失时直跑用）。"""

        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo = []

    _mp = _MP()
    _cases = [
        ("test_cn_default_is_30bp", False),
        ("test_other_markets_follow_config", False),
        ("test_slippage_change_propagates", True),
        ("test_unknown_market_falls_back", True),
        ("test_load_failure_falls_back_without_raise", True),
    ]
    _fail = 0
    for _name, _need_mp in _cases:
        try:
            globals()[_name](_mp) if _need_mp else globals()[_name]()
            print("PASS %s" % _name)
        except AssertionError as exc:
            _fail += 1
            print("FAIL %s: %s" % (_name, exc))
        finally:
            _mp.undo()
    print("---- %d passed, %d failed ----" % (len(_cases) - _fail, _fail))
    sys.exit(1 if _fail else 0)
