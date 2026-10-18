"""技术指标纯函数。"""
from __future__ import annotations

import math


def momentum(closes: list[float], window: int = 20) -> float | None:
    if len(closes) < window:
        return None
    return closes[-1] / closes[-window] - 1.0


def ma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def ma_cross(closes: list[float], fast: int = 5, slow: int = 20) -> float | None:
    f = ma(closes, fast)
    s = ma(closes, slow)
    if f is None or s is None:
        return None
    return f - s


def rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in diffs[-window:]]
    losses = [max(-d, 0) for d in diffs[-window:]]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def ema(closes: list[float], window: int) -> list[float]:
    """指数移动平均，返回与输入等长的序列（前 window-1 个为 NaN）。"""
    if len(closes) < window:
        return [float("nan")] * len(closes)
    result: list[float] = []
    k = 2.0 / (window + 1)
    sma = sum(closes[:window]) / window
    result.append(sma)
    for price in closes[window:]:
        result.append(price * k + result[-1] * (1 - k))
    return [float("nan")] * (window - 1) + result


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    return_series: bool = False,
):
    """MACD 指标。return_series=False 返回最新点 dict，True 返回全序列。"""
    if len(closes) < slow + signal:
        return None

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    offset = slow - 1
    macd_line: list[float] = []
    for i in range(offset, len(closes)):
        ef, es = ema_fast[i], ema_slow[i]
        macd_line.append(float("nan") if (ef != ef or es != es) else ef - es)

    valid_macd = [v for v in macd_line if v == v]
    if len(valid_macd) < signal:
        return None
    signal_ema = ema(valid_macd, signal)
    nan_prefix = len(macd_line) - len(valid_macd)
    sig_padded = [float("nan")] * nan_prefix + signal_ema

    histogram_line = [
        float("nan") if (m != m or s != s) else m - s
        for m, s in zip(macd_line, sig_padded, strict=False)
    ]

    if return_series:
        full_nan = [float("nan")] * offset
        return {
            "macd_series": full_nan + macd_line,
            "signal_series": full_nan + sig_padded,
            "histogram_series": full_nan + histogram_line,
        }

    def _last_valid(seq: list[float]) -> float | None:
        for v in reversed(seq):
            if v == v:
                return v
        return None

    mv, sv, hv = _last_valid(macd_line), _last_valid(sig_padded), _last_valid(histogram_line)
    if mv is None or sv is None or hv is None:
        return None
    return {"macd": mv, "signal": sv, "histogram": hv}


def bollinger_bands(
    closes: list[float],
    window: int = 20,
    k: float = 2.0,
    return_series: bool = False,
):
    """布林带。middle = MA(window)，upper/lower = middle ± k * std(window)。"""
    if len(closes) < window:
        return None

    if return_series:
        upper_s, middle_s, lower_s = [], [], []
        for i in range(len(closes)):
            if i < window - 1:
                upper_s.append(float("nan"))
                middle_s.append(float("nan"))
                lower_s.append(float("nan"))
            else:
                w = closes[i - window + 1 : i + 1]
                mid = sum(w) / window
                std = math.sqrt(sum((v - mid) ** 2 for v in w) / window)
                upper_s.append(mid + k * std)
                middle_s.append(mid)
                lower_s.append(mid - k * std)
        return {"upper_series": upper_s, "middle_series": middle_s, "lower_series": lower_s}

    w = closes[-window:]
    mid = sum(w) / window
    std = math.sqrt(sum((v - mid) ** 2 for v in w) / window)
    return {"upper": mid + k * std, "middle": mid, "lower": mid - k * std}
