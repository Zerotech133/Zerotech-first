"""合成 K 线 fixture 工厂（测试与容量基准共用；无 pytest 依赖，可被生产代码 import）。

形态目录（全部确定性或按 seed 可复现）：
- neutral   : 微幅上行锯齿（RSI≈67 / chan HOLD / volume 中性）→ 断言不产 SELL
- chan_div  : 30 根确定性缠论顶背驰尾段（强拉→回调死叉①→弱拉新高→回落死叉②）→ chan SELL 0.7
- vol_div   : 尾段缩量 + 末根放量小幅新高 → volume 量价背离 SELL 0.6
- rsi_dull  : 末 30 根连续急涨 → RSI 钝化 SELL 0.5（同时价格单调新高，volume 多半同步 SELL）
- oversold  : 末 30 根连续急跌 → RSI 超卖 WAIT
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FREQ_15M = pd.Timedelta(minutes=15)


def _frame(dt_index: pd.DatetimeIndex, opens, highs, lows, closes, vols) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dt": dt_index,
            "open": np.asarray(opens, dtype=float),
            "high": np.asarray(highs, dtype=float),
            "low": np.asarray(lows, dtype=float),
            "close": np.asarray(closes, dtype=float),
            "volume": np.asarray(vols, dtype=float),
        }
    )


def _mk_index(n: int, start: str) -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq=FREQ_15M, tz="UTC")


def _tail_from(body_close: float, tail_rets: list[float]) -> np.ndarray:
    """以 body 末收盘为基点，按尾段收益率序列生成尾段收盘价。"""
    return body_close * np.cumprod(1.0 + np.asarray(tail_rets, dtype=float))


def _body_closes(n_body: int, seed: int, drift: float = 0.002) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.003, n_body)
    return 100.0 * np.cumprod(1.0 + rets)


def synth_bars(
    n: int = 200,
    seed: int = 42,
    pattern: str = "neutral",
    start_close: float = 100.0,
    start: str = "2026-09-01T00:00:00Z",
) -> pd.DataFrame:
    """生成升序 OHLCV 合成 K 线（15m 间隔，UTC）。"""
    if pattern == "neutral":
        return _neutral(n, start)
    if pattern == "chan_div":
        return _chan_div(n, seed, start)
    if pattern == "vol_div":
        return _vol_div(n, seed, start)
    if pattern == "rsi_dull":
        return _rsi_tail(n, seed, start, dull_ret=0.012, tail_k=30)
    if pattern == "oversold":
        return _rsi_tail(n, seed, start, dull_ret=-0.012, tail_k=30)
    raise ValueError(f"未知 pattern: {pattern!r}")


# ── neutral：+0.02%/-0.01% 交替微锯齿（净微升） ─────────────────────────────
def _neutral(n: int, start: str) -> pd.DataFrame:
    rets = np.full(n, 0.0002)
    rets[1::2] = -0.0001
    closes = 100.0 * np.cumprod(1.0 + rets)
    opens = np.concatenate([[100.0], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.0002
    lows = np.minimum(opens, closes) * 0.9998
    vols = np.full(n, 100.0)
    return _frame(_mk_index(n, start), opens, highs, lows, closes, vols)


# ── chan_div：末 30 根确定性顶背驰 ─────────────────────────────────────────
_CHAN_TAIL = (
    [0.015] * 9      # 段1 强拉：DIF 峰值 1（价格 ×1.143）
    + [-0.010] * 5   # 段2 回调：死叉①（×0.951 → 1.087）
    + [0.008] * 10   # 段3 弱拉新高：×1.083 → 1.174（价创新高、DIF 峰值更低）
    + [-0.006] * 6   # 段4 回落：死叉②、末根 DIF<DEA（×0.965 → 1.132）
)


def _chan_div(n: int, seed: int, start: str) -> pd.DataFrame:
    n_body = max(40, n - len(_CHAN_TAIL))
    body = _body_closes(n_body, seed, drift=0.002)
    closes = np.concatenate([body, _tail_from(float(body[-1]), _CHAN_TAIL)])
    total = len(closes)
    opens = np.concatenate([[100.0], closes[:-1]])
    spread = np.abs(closes) * 0.0008
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    vols = np.concatenate([
        np.full(n_body, 120.0),
        np.full(9, 180.0), np.full(5, 110.0),   # 段1 放量
        np.full(10, 130.0),                      # 段3 新高但量低于段1
        np.full(6, 80.0),                        # 段4 缩量回落
    ])
    return _frame(_mk_index(total, start), opens, highs, lows, closes, vols[:total])


# ── vol_div：末根价格小幅新高 + 量能显著未新高 ──────────────────────────────
def _vol_div(n: int, seed: int, start: str) -> pd.DataFrame:
    n_body = max(60, n - 1)
    body = _body_closes(n_body, seed, drift=0.003)
    rng = np.random.default_rng(seed + 1)
    vols_body = rng.uniform(120.0, 160.0, n_body)
    # 末根：+0.9% 小幅新高，量能萎缩到 40（远低于 body 任意一根）
    last_close = float(body[-1]) * 1.009
    closes = np.concatenate([body, [last_close]])
    opens = np.concatenate([[100.0], closes[:-1]])
    spread = np.abs(closes) * 0.0006
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    vols = np.concatenate([vols_body, [40.0]])
    return _frame(_mk_index(len(closes), start), opens, highs, lows, closes, vols)


# ── rsi_dull / oversold：末 tail_k 根连续单向 ──────────────────────────────
def _rsi_tail(n: int, seed: int, start: str, dull_ret: float, tail_k: int = 30) -> pd.DataFrame:
    n_body = max(60, n - tail_k)
    body = _body_closes(n_body, seed, drift=0.0)
    tail = _tail_from(float(body[-1]), [dull_ret] * tail_k)
    closes = np.concatenate([body, tail])
    total = len(closes)
    opens = np.concatenate([[100.0], closes[:-1]])
    spread = np.abs(closes) * 0.0005
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    vols = np.full(total, 100.0)
    return _frame(_mk_index(total, start), opens, highs, lows, closes, vols)
