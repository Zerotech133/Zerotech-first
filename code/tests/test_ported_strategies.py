import pandas as pd

from zhice_os_v2.domain.strategy.strategies.trend_momentum import compute_features, screen


def _df():
    rows = []
    for i in range(30):
        rows.append(
            {
                "security_id": 1,
                "ts_code": "600519.SH",
                "name": "贵州茅台",
                "trade_date": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i),
                "close": 100 + i,
                "turnover_cny": 100_000_000 + i * 1000,
                "pct_change": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_trend_momentum_features_and_screen():
    features = compute_features(_df())
    assert len(features) == 1
    picks = screen(features, max_candidates=1)
    assert len(picks) <= 1
    assert picks[0]["ts_code"] == "600519.SH"


def _high_momentum_df():
    """陡涨样本：多头排列成立、20 日动量 ≈ 80%（超出 CN 默认上限 50%）。"""
    rows = []
    for i in range(30):
        price = 100.0 * (1.03 ** i)
        rows.append(
            {
                "security_id": 2,
                "ts_code": "BTC/USDT",
                "name": "BTC",
                "trade_date": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i),
                "close": price,
                "turnover_cny": 50_000_000,
                "pct_change": 3.0,
            }
        )
    return pd.DataFrame(rows)


def test_screen_momentum_band_is_parametrized():
    """动量带参数化（crypto 化基础）：高动量样本在 CN 默认带 [0.05,0.50] 被挡，
    放宽 momentum_max（crypto profile=3.0）后通过。验证 screen 阈值可注入。
    """
    features = compute_features(_high_momentum_df())
    # CN 默认动量带上限 0.50：ret20≈0.80 超限 → 被挡
    assert screen(features, max_candidates=1) == []
    # 放宽上限至 crypto 口径 3.0 → 通过
    picks = screen(features, max_candidates=1, momentum_max=3.0)
    assert len(picks) == 1
    assert picks[0]["ts_code"] == "BTC/USDT"
