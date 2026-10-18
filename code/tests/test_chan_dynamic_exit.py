import math

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan.dynamic_exit import (
    evaluate_position_pivot_exit,
    price_breaks_pivot_low,
)
from zhice_os_v2.domain.strategy.strategies.chan.engine import (
    build_zs_list,
    extract_bi_list,
)


def _make_sine_df(periods: int = 120) -> pd.DataFrame:
    """构造正弦波日线 df（周期 20 根，振幅 ±30），用于稳定产出 czsc 笔和中枢。

    120 根 / 周期 20 = 6 个完整振荡，实测产出 11 笔、1 个有效中枢 zd=68 zg=132。
    """
    dates = pd.date_range("2023-01-03", periods=periods, freq="B")
    closes = [100 + 30 * math.sin(2 * math.pi * i / 20) for i in range(periods)]
    return pd.DataFrame({
        "dt": dates,
        "open": [c - 0.5 for c in closes],
        "high": [c + 2.0 for c in closes],
        "low": [c - 2.0 for c in closes],
        "close": closes,
        "volume": [1000.0] * periods,
        "amount": [c * 1000.0 for c in closes],
    })


def test_break_below_pivot_low_triggers():
    zs_list = [{"zd": 10.0, "zg": 11.0}, {"zd": 12.0, "zg": 13.0}]  # 取最后一个
    assert price_breaks_pivot_low(zs_list, current_price=11.9) is True
    assert price_breaks_pivot_low(zs_list, current_price=12.0) is False
    assert price_breaks_pivot_low(zs_list, current_price=12.5) is False


def test_no_pivot_returns_false():
    assert price_breaks_pivot_low([], current_price=5.0) is False


def test_evaluate_no_crash_on_short_series():
    df = pd.DataFrame({
        "dt": pd.to_datetime(["2024-01-05", "2024-01-06", "2024-01-07"]),
        "open": [10.0, 9.0, 8.0], "high": [10.0, 9.0, 8.0],
        "low": [9.0, 8.0, 7.0], "close": [9.0, 8.0, 7.0],
        "volume": [100.0, 90.0, 80.0],
    })
    assert evaluate_position_pivot_exit("TEST", df, current_price=5.0) is False


# ─── 端到端正向路径：合成数据真实产出中枢 ────────────────────────────────────

def test_sine_df_produces_real_hub():
    """合成正弦波 df 确实能让 czsc 产出 ≥1 个有效中枢，否则后续断言无意义。"""
    df = _make_sine_df()
    _, czsc_obj = extract_bi_list("TEST", df)
    zs_list = build_zs_list(czsc_obj.bi_list)
    assert len(zs_list) >= 1, (
        f"czsc 未产出中枢（bi={len(czsc_obj.bi_list)}），"
        "端到端测试无法验证动态出场逻辑"
    )
    # 断言中枢下沿 zd 在合理价格区间内（正弦波 base=100 振幅±30，low-2=68）
    last_zd = zs_list[-1]["zd"]
    assert last_zd < 100, f"中枢下沿 zd={last_zd} 不在预期范围"


def test_evaluate_breaks_hub_bottom_triggers_exit():
    """现价远低于中枢下沿 zd → evaluate_position_pivot_exit 返回 True。"""
    df = _make_sine_df()
    # 合成中枢 zd=68；现价=50 严格低于下沿，应触发离场
    result = evaluate_position_pivot_exit("TEST", df.copy(), current_price=50.0)
    assert result is True, (
        "现价 50 低于中枢下沿 68，应触发出场但返回 False"
    )


def test_evaluate_above_hub_top_no_exit():
    """现价远高于中枢上沿 zg → evaluate_position_pivot_exit 返回 False。"""
    df = _make_sine_df()
    # 合成中枢 zg=132；现价=200 远高于上沿，不应触发离场
    result = evaluate_position_pivot_exit("TEST", df.copy(), current_price=200.0)
    assert result is False, (
        "现价 200 高于中枢上沿 132，不应触发出场但返回 True"
    )
