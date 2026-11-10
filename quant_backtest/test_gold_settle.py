# -*- coding: utf-8 -*-
"""P2-a 回归：gold_signal_shadow 结算定位不再前视。

判据（任一不过即为回归）：
  1. detect_date 晚于 dt 时，入场必须晚于旧口径 —— 这正是被修掉的前视。
  2. detect_date 落在非交易日（周末/停牌）时，入场取其后第一个交易日，不能丢样本。
  3. 旧 journal 行（无 detect_date）退回 dt 并标 legacy_dt，可被审计区分。
  4. 用合成行情重算 d5：旧口径收益 > 新口径收益（旧口径偷看了端点）。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _load():
    spec = importlib.util.spec_from_file_location(
        "gold_signal_shadow", os.path.join(HERE, "gold_signal_shadow.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


DTS = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
       "2026-10-14", "2026-10-15", "2026-10-16"]


def test_detect_same_day_enters_next_bar():
    m = _load()
    j = {"type": "b3", "dt": "2026-10-09", "detect_date": "2026-10-09"}
    i0, basis, lag = m.settle_anchor(DTS, j)
    assert basis == "detect_date"
    assert DTS[i0] == "2026-10-09"
    assert DTS[i0 + 1] == "2026-10-12"       # 入场 bar
    assert lag == 0


def test_detect_lag_shifts_entry_later():
    """核心：dt=10-09 但 10-12 才看见 → 入场必须是 10-13，不是 10-12。"""
    m = _load()
    j = {"type": "b3", "dt": "2026-10-09", "detect_date": "2026-10-12"}
    i0, basis, lag = m.settle_anchor(DTS, j)
    assert basis == "detect_date"
    assert DTS[i0] == "2026-10-12"
    assert DTS[i0 + 1] == "2026-10-13"
    assert lag == 1                          # 滞后 1 根 bar（10-09 → 10-12）


def test_detect_on_non_trading_day_uses_next_session():
    """detect_date=10-10（周六，不在 bar 里）→ 锚为 10-09，入场 10-12。"""
    m = _load()
    j = {"type": "b3", "dt": "2026-10-09", "detect_date": "2026-10-10"}
    i0, basis, lag = m.settle_anchor(DTS, j)
    assert basis == "detect_date"
    assert DTS[i0] == "2026-10-09"
    assert DTS[i0 + 1] == "2026-10-12"


def test_legacy_row_falls_back_and_is_flagged():
    m = _load()
    j = {"type": "b3", "dt": "2026-10-09"}
    i0, basis, lag = m.settle_anchor(DTS, j)
    assert basis == "legacy_dt"
    assert DTS[i0] == "2026-10-09"
    assert lag is None


def test_detect_before_first_bar_returns_no_anchor():
    m = _load()
    j = {"type": "b3", "dt": "2026-10-08", "detect_date": "2026-01-01"}
    i0, basis, _ = m.settle_anchor(DTS, j)
    assert basis == "detect_date"
    assert i0 is None                        # 还没走到，样本挂 pending，不能瞎结算


def test_no_lookahead_in_return_calc():
    """合成行情：dt 当晚出信号，次日（确认日）已跳空高开。
    旧口径在 dt+1 入场拿到跳空前价格，收益被抬高；新口径晚一根 bar，
    买到跳空后的价 —— 差额就是前视虚增。"""
    m = _load()
    closes = [105.0, 140.0, 151.0, 153.0, 155.0, 157.0, 159.0, 161.0, 163.0]
    opens = [100.0, 130.0, 150.0, 152.0, 154.0, 156.0, 158.0, 160.0, 162.0]
    j = {"type": "b3", "dt": "2026-10-08", "detect_date": "2026-10-09"}

    def d5(anchor):
        entry = opens[anchor + 1]
        j2 = anchor + 1 + 5
        return closes[j2] / entry - 1.0 if j2 < len(closes) else None

    old = d5(DTS.index("2026-10-08"))        # 旧口径：按 dt 定位
    new = d5(m.settle_anchor(DTS, j)[0])     # 新口径：按 detect_date
    assert old is not None and new is not None
    assert new < old, "新口径应低于旧口径（旧口径偷看端点），实际 old=%.4f new=%.4f" % (old, new)
