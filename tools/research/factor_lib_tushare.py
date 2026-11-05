"""Tushare factor_list（272 因子）→ factor_lib 的映射与 B1 行情因子实现。

背景
----
`tushare.factor_list` 返回 272 个因子（9 类）的静态清单，只有名称/类型/描述，
没有取值。本模块把清单映射为 factor_lib 可注册的因子：

- ``TUSHARE_FACTOR_MAP``：272 条映射（factor_type / batch / status / requires / note）。
- **B1 行情量价批次**：本模块实现 86 个非 Alpha101 因子（Momentum 20 / Liquidity 35 /
  Risk 25 / Reversal 3 / Size 3），依赖日行情 + adj_factor + daily_basic + index_daily。
- **B2 Alpha101 批次**：101 条已实现（alpha101_engine.py 表达式引擎）。
- **B3 财务 PIT 批次**：85 条 Growth/Quality/Value 已注册（84 条实现；dividend_yield_3y_avg
  因 dividend 表缺失标记 blocked_missing_data）。

约定
----
- 收益一律用后复权价 ``adj_close``；指数收益来自 index_daily（000300.SH / 000001.SH）。
- 换手率优先用 daily_basic.turnover_rate（百分数 → 小数）；缺失时用 vol/float_share 兜底。
- 所有 rolling 操作只使用过去窗口（``shift(+n)`` / ``rolling``），不产生前视。
- 因子函数签名 ``fn(panel) -> DataFrame``；缺字段时返回全 NaN 面板，由 factor_lib 的
  valid 掩码剔除，不会让整批评测失败。
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd

from alpha101_engine import evaluate_formula, load_formulas
from factor_lib_financial import B3_FACTORS, register_financial

SOURCE = "tushare.factor_list"

BATCH_B1 = "B1_price_volume"
BATCH_B2 = "B2_alpha101_expression"
BATCH_B3 = "B3_financial_pit"

TYPE_NAMES: dict[str, tuple[str, ...]] = {
    "Alpha101": tuple(f"alpha101_{i}" for i in range(1, 102)),
    "Growth": (
        "asset_growth_qoq", "eaa", "eap", "gross_margin_qoq", "np_ttm_qoq", "pa",
        "peg_252d", "sa", "yoy_net_asset", "yoy_net_profit", "yoy_ocf", "yoy_revenue",
        "yoy_roa", "yoy_roe", "yoy_total_asset",
    ),
    "Liquidity": (
        "amount_ma_20d", "avg_turnover_10d", "avg_turnover_126d", "avg_turnover_20d",
        "avg_turnover_21d", "avg_turnover_252d", "avg_turnover_42d", "avg_turnover_5d",
        "avg_turnover_63d", "bias_std_turn_126d_252d", "bias_std_turn_126d_504d",
        "bias_std_turn_21d_252d", "bias_std_turn_21d_504d", "bias_std_turn_42d_252d",
        "bias_std_turn_42d_504d", "bias_std_turn_63d_252d", "bias_std_turn_63d_504d",
        "bias_turn_126d_252d", "bias_turn_126d_504d", "bias_turn_21d_252d",
        "bias_turn_21d_504d", "bias_turn_42d_252d", "bias_turn_42d_504d",
        "bias_turn_63d_252d", "bias_turn_63d_504d", "std_turnover_126d",
        "std_turnover_21d", "std_turnover_252d", "std_turnover_42d", "std_turnover_63d",
        "sum_abs_rtn_amount_20d", "turnover_ma_20d", "turnover_ma_20d_120d",
        "volume_alpha_300d_000001", "volume_alpha_300d_000300",
    ),
    "Momentum": (
        "MACD", "alpha_1000d_000300", "alpha_125d_000300", "alpha_1320d_000001",
        "alpha_250d_000300", "alpha_500d_000300", "alpha_528d_000001",
        "alpha_792d_000001", "days_down_up", "dea", "dif", "ma_20d",
        "price_position_ir_60d", "return_126d", "return_21d", "return_252d",
        "return_42d", "return_5d", "return_63d", "rsrs",
    ),
    "Quality": (
        "ar_ap_to_revenue", "asset_turnover", "cash_profit_ratio", "cash_ratio", "cfcr",
        "current_ratio", "de", "debt_asset_ratio", "delta_asset_turnover",
        "delta_cash_ratio", "delta_current_ratio", "delta_de", "delta_gpm",
        "delta_inventory_turnover", "delta_npm", "delta_opm", "delta_quick_ratio",
        "delta_roa", "delta_roe", "eps_q", "eps_ttm", "eps_y", "equity_turnover",
        "expenses_to_equity_yoy", "financial_leverage", "fixed_asset_turnover", "gpm_q",
        "gpm_qoq", "gpm_ttm", "gpm_y", "icr", "income_tax_yoy", "inventory_turnover",
        "lra_yoy", "market_value_leverage", "np_to_deferred_tax_yoy",
        "np_to_fixed_assets_yoy", "np_to_inventory_yoy", "np_to_salary_yoy",
        "np_to_total_expenses_yoy", "npm_q", "npm_q_qoq", "npm_tsh", "npm_ttm",
        "npm_ttm_qoq", "npm_y", "opm_ttm", "opm_y", "opt_tpro", "quality_composite",
        "quick_ratio", "receivable_turnover", "roa_q", "roa_ttm", "roa_y", "roe_ttm",
        "roe_ttm_lag63d", "roe_y", "tax_surcharge_yoy",
    ),
    "Reversal": ("price_dist", "rsi", "small_cap_reversal_21d"),
    "Risk": (
        "adjusted_sharpe_750d", "beta_1000d_000300", "beta_125d_000300",
        "beta_1320d_000001", "beta_250d_000300", "beta_500d_000300", "beta_60d_000300",
        "beta_consistency_1320d_000300", "days_beyond_upper_lower_21d", "high_low_126d",
        "high_low_21d", "high_low_252d", "high_low_42d", "high_low_63d", "log_price",
        "return_std_126d", "return_std_21d", "return_std_252d", "return_std_42d",
        "return_std_63d", "sharpe_60d", "sharpe_750d", "sigma_1320d_000001",
        "sigma_1320d_000300", "volume_beta_120d_000300",
    ),
    "Size": ("float_size", "nl_size", "size"),
    "Value": (
        "book_to_market", "dividend_yield_3y_avg", "earnings_cut_to_market",
        "earnings_to_price", "ebitda_to_market", "etp5", "fcf_to_market",
        "ncf_to_market", "ocf_to_market", "pegh5", "sales_to_market",
    ),
}

TUSHARE_FACTOR_MAP: dict[str, dict[str, Any]] = {}
for _type, _names in TYPE_NAMES.items():
    for _name in _names:
        if _type == "Alpha101":
            _batch = BATCH_B2
        elif _type in ("Momentum", "Liquidity", "Risk", "Reversal", "Size"):
            _batch = BATCH_B1
        else:
            _batch = BATCH_B3
        TUSHARE_FACTOR_MAP[_name] = {
            "name": _name,
            "factor_type": _type,
            "batch": _batch,
            "status": "mapped",
            "requires": (),
            "source": SOURCE,
            "note": "",
        }

try:  # pragma: no cover - 公式文件缺失时退化为仅 B1
    _ALPHA101_FORMULAS: dict[str, str] = load_formulas()
except Exception:  # pragma: no cover
    _ALPHA101_FORMULAS = {}

# ---------------------------------------------------------------- 工具函数
def _nan(panel) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)


def _adj(panel) -> pd.DataFrame:
    return panel.fields.get("adj_close", panel.close)


def _ret(panel) -> pd.DataFrame:
    close = _adj(panel)
    return close / close.shift(1) - 1.0


def _ma(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.rolling(int(n), min_periods=max(2, int(n) // 2)).mean()


def _std(df: pd.DataFrame, n: int) -> pd.DataFrame:
    # 截面因子研究通常用总体标准差；与 ddof=1 的差异在 n 较大时可忽略。
    return df.rolling(int(n), min_periods=max(2, int(n) // 2)).std(ddof=0)


def _ema(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.ewm(span=int(n), adjust=False, min_periods=max(1, int(n) // 4)).mean()


def _cum_ret(panel, n: int) -> pd.DataFrame:
    close = _adj(panel)
    return close / close.shift(int(n)) - 1.0


def _xrank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True)


def _safe_div(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a / b.replace(0.0, np.nan)


def _turnover(panel) -> pd.DataFrame:
    """日换手率（小数）。优先 daily_basic.turnover_rate，缺失时用 vol/float_share 兜底。"""
    tr = panel.fields.get("turnover_rate")
    if tr is not None and tr.notna().any().any():
        return tr / 100.0
    vol = panel.optional("vol")
    fs = panel.optional("float_share")
    return _safe_div(vol * 100.0, fs * 1e4)


def _index_ret(panel, code: str) -> pd.Series | None:
    idx = panel.load_meta.get("index_returns")
    if not isinstance(idx, dict):
        return None
    return idx.get(code)


def _index_vol(panel, code: str) -> pd.Series | None:
    idx = panel.load_meta.get("index_volume")
    if not isinstance(idx, dict):
        return None
    return idx.get(code)


def _roll_alpha_beta(x: pd.DataFrame, y, window: int):
    """滚动 OLS：x_t = alpha + beta * y_t + e；返回 (alpha, beta, var_y, mean_y, mean_x)。

    y 可以是 Series（指数收益，按行广播到所有股票）或与 x 同形状的 DataFrame
    （如 RSRS 用 high~low 回归）。
    """
    w = int(window)
    mp = max(3, w // 2)
    if y is None:
        nan = _nan_like(x)
        return nan, nan, None, None, None
    mx = x.rolling(w, min_periods=mp).mean()
    if isinstance(y, pd.Series):
        yv = y.reindex(x.index).astype(float)
        my = yv.rolling(w, min_periods=mp).mean()
        cov = x.mul(yv, axis=0).rolling(w, min_periods=mp).mean() - mx.mul(my, axis=0)
        var_y = (yv * yv).rolling(w, min_periods=mp).mean() - my * my
        beta = cov.div(var_y.replace(0.0, np.nan), axis=0)
        alpha = mx.sub(beta.mul(my, axis=0))
    else:
        yv = y.reindex(index=x.index, columns=x.columns).astype(float)
        my = yv.rolling(w, min_periods=mp).mean()
        cov = (x * yv).rolling(w, min_periods=mp).mean() - mx * my
        var_y = (yv * yv).rolling(w, min_periods=mp).mean() - my * my
        beta = cov.div(var_y.replace(0.0, np.nan))
        alpha = mx - beta * my
    return alpha, beta, var_y, my, mx


def _nan_like(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=df.index, columns=df.columns)


def _beta(panel, code: str, window: int) -> pd.DataFrame:
    _, beta, _, _, _ = _roll_alpha_beta(_ret(panel), _index_ret(panel, code), window)
    return beta


def _resid_std(panel, code: str, window: int) -> pd.DataFrame:
    w = int(window)
    mp = max(3, w // 2)
    x = _ret(panel)
    y = _index_ret(panel, code)
    alpha, beta, var_y, _, mx = _roll_alpha_beta(x, y, w)
    if var_y is None:
        return _nan_like(x)
    var_x = (x * x).rolling(w, min_periods=mp).mean() - mx * mx
    resid_var = (var_x - beta.pow(2).mul(var_y, axis=0)).clip(lower=0.0)
    return np.sqrt(resid_var)


def _sharpe(panel, window: int) -> pd.DataFrame:
    r = _ret(panel)
    return _safe_div(_ma(r, window), _std(r, window))


def _high_low(panel, window: int) -> pd.DataFrame:
    nav = (1.0 + _ret(panel)).cumprod()
    w = int(window)
    mp = max(2, w // 2)
    return _safe_div(nav.rolling(w, min_periods=mp).max(), nav.rolling(w, min_periods=mp).min())


def _macd(panel):
    dif = _ema(_adj(panel), 12) - _ema(_adj(panel), 26)
    dea = _ema(dif, 9)
    return 2.0 * (dif - dea)


def _dif(panel):
    return _ema(_adj(panel), 12) - _ema(_adj(panel), 26)


def _dea(panel):
    return _ema(_dif(panel), 9)


def _days_down_up(panel):
    up = (panel.pct_chg.to_numpy(dtype=float) > 0).astype(np.int64)
    down = (panel.pct_chg.to_numpy(dtype=float) < 0).astype(np.int64)
    rows, cols = up.shape
    cu = np.zeros((rows, cols), dtype=np.int64)
    cd = np.zeros((rows, cols), dtype=np.int64)
    u = np.zeros(cols, dtype=np.int64)
    d = np.zeros(cols, dtype=np.int64)
    for i in range(rows):
        u = np.where(up[i] == 1, u + 1, 0)
        d = np.where(down[i] == 1, d + 1, 0)
        cu[i] = u
        cd[i] = d
    return pd.DataFrame(np.abs(cu - cd - 1), index=panel.dates, columns=panel.codes, dtype=float)


def _price_position_ir(panel, window=60):
    ratio = _safe_div(panel.close - panel.open, panel.high - panel.low)
    return _safe_div(_ma(ratio, window), _std(ratio, window))


def _rsrs(panel, regress=18, zwindow=200):
    # RSRS：low 对 high 的滚动回归斜率，再做时序 zscore（研报口径）
    _, slope, _, _, _ = _roll_alpha_beta(panel.high, panel.low, regress)
    return _safe_div(slope - _ma(slope, zwindow), _std(slope, zwindow))


def _amount_ma(panel, window=20):
    return _ma(panel.optional("amount"), window)


def _avg_turnover(panel, window):
    return _ma(_turnover(panel), window)


def _std_turnover(panel, window):
    return _std(_turnover(panel), window)


def _bias_turnover(panel, short, long):
    t = _turnover(panel)
    return _safe_div(_ma(t, short), _ma(t, long)) - 1.0


def _bias_std_turnover(panel, short, long):
    t = _turnover(panel)
    return _safe_div(_std(t, short), _std(t, long)) - 1.0


def _turnover_ma(panel, short, long=0):
    t = _turnover(panel)
    if long:
        return -_safe_div(_ma(t, short), _ma(t, long))
    return -_ma(t, short)


def _sum_abs_rtn_amount(panel, window=20):
    amount = panel.optional("amount") * 1000.0
    num = _ret(panel).abs().rolling(window, min_periods=max(2, window // 2)).sum()
    den = amount.rolling(window, min_periods=max(2, window // 2)).sum()
    return _safe_div(num, den) * 1e8


def _vol_momentum(vol: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    total = vol.rolling(window, min_periods=max(2, window // 2)).sum()
    return _safe_div(total, total.shift(1)) - 1.0


def _volume_alpha(panel, code, window=300, sum_window=5):
    stock = _vol_momentum(panel.optional("vol"), sum_window)
    idx_vol = _index_vol(panel, code)
    if idx_vol is None:
        return _nan_like(stock)
    idx = idx_vol.reindex(panel.dates).astype(float)
    idx_total = idx.rolling(sum_window, min_periods=max(2, sum_window // 2)).sum()
    idx_mom = idx_total / idx_total.shift(1) - 1.0
    alpha, _, _, _, _ = _roll_alpha_beta(stock, idx_mom, window)
    return alpha


def _volume_beta(panel, code, window=120, sum_window=5):
    stock = _vol_momentum(panel.optional("vol"), sum_window)
    idx_vol = _index_vol(panel, code)
    if idx_vol is None:
        return _nan_like(stock)
    idx = idx_vol.reindex(panel.dates).astype(float)
    idx_total = idx.rolling(sum_window, min_periods=max(2, sum_window // 2)).sum()
    idx_mom = idx_total / idx_total.shift(1) - 1.0
    _, beta, _, _, _ = _roll_alpha_beta(stock, idx_mom, window)
    return beta


def _adjusted_sharpe(panel, window=750):
    r = _ret(panel)
    return _safe_div(_ma(r, window), _std(r, window).pow(4))


def _days_beyond(panel, window=21):
    close = _adj(panel)
    ma = _ma(close, window)
    sd = _std(close, window)
    z = _safe_div(close - ma, sd)
    up = (z > 1).astype(float).rolling(window, min_periods=max(2, window // 2)).sum()
    low = (z < -1).astype(float).rolling(window, min_periods=max(2, window // 2)).sum()
    return up - low


def _log_price(panel):
    return np.log(_adj(panel).where(_adj(panel) > 0))


def _rsi(panel, period=14):
    diff = _adj(panel).diff()
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = _safe_div(avg_gain, avg_loss)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(~((avg_loss <= 0) & (avg_gain > 0)), 100.0)
    return rsi


def _price_dist(panel, window=0):
    close = panel.close
    arr = close.to_numpy(dtype=float)
    scale = np.where(arr < 10.0, 1.0, np.where(arr < 100.0, 10.0, 100.0))
    frac = arr / scale
    dist = (np.ceil(frac) - frac) * scale
    out = pd.DataFrame(dist, index=panel.dates, columns=panel.codes)
    if window:
        out = _ma(out, window)
    return out


def _size(panel):
    ts = panel.optional("total_share")
    return -np.log((ts * panel.close / 100.0).where(lambda x: x > 0))


def _float_size(panel):
    fs = panel.optional("float_share")
    return -np.log((fs * panel.close / 100.0).where(lambda x: x > 0))


def _nl_size(panel):
    size = _size(panel)
    arr = size.to_numpy(dtype=float)
    out = np.full_like(arr, np.nan)
    for i in range(arr.shape[0]):
        y = arr[i]
        mask = np.isfinite(y)
        if mask.sum() < 3:
            continue
        x = y[mask]
        y3 = x ** 3
        mx = float(np.mean(x))
        my = float(np.mean(y3))
        var = float(np.mean((x - mx) ** 2))
        if not np.isfinite(var) or var <= 0:
            continue
        beta = float(np.mean((x - mx) * (y3 - my))) / var
        alpha = my - beta * mx
        out[i, mask] = y3 - alpha - beta * x
    return pd.DataFrame(out, index=panel.dates, columns=panel.codes)


def _small_cap_reversal(panel, window=21):
    cum = _cum_ret(panel, window)
    smallcap = _xrank(-panel.optional("total_mv"))
    return _xrank(-cum) * smallcap


# ---------------------------------------------------------------- 因子注册表
_SPECS: list[dict[str, Any]] = []


def _add(name: str, family: str, direction: int, fn: Callable, requires: tuple[str, ...] = (),
         kind: str = "continuous") -> None:
    if name not in TUSHARE_FACTOR_MAP:
        raise KeyError(f"factor_list 无此因子: {name}")
    _SPECS.append({
        "name": name, "family": family, "kind": kind, "direction": int(direction),
        "description": f"{family}（Tushare factor_list）", "fn": fn,
        "requires": tuple(requires),
    })
    TUSHARE_FACTOR_MAP[name].update({"status": "implemented", "requires": tuple(requires),
                                     "batch": BATCH_B1, "note": ""})


# Momentum（20）
_add("MACD", "Tushare-动量", 1, _macd, ("adj_close",))
_add("dif", "Tushare-动量", 1, _dif, ("adj_close",))
_add("dea", "Tushare-动量", 1, _dea, ("adj_close",))
_add("ma_20d", "Tushare-动量", 1, lambda p: _ma(_adj(p), 20), ("adj_close",))
for _n in (5, 21, 42, 63, 126, 252):
    _add(f"return_{_n}d", "Tushare-动量", 1, lambda p, n=_n: _cum_ret(p, n), ("adj_close",))
_add("days_down_up", "Tushare-动量", 1, _days_down_up, ("pct_chg",))
_add("price_position_ir_60d", "Tushare-动量", 1, _price_position_ir, ("close", "open", "high", "low"))
_add("rsrs", "Tushare-动量", 1, _rsrs, ("adj_close",))
for _n, _code in ((125, "000300.SH"), (250, "000300.SH"), (500, "000300.SH"),
                  (1000, "000300.SH"), (528, "000001.SH"), (792, "000001.SH"),
                  (1320, "000001.SH")):
    _add(f"alpha_{_n}d_{_code.split('.')[0]}", "Tushare-动量", 1,
         lambda p, n=_n, c=_code: _roll_alpha_beta(_ret(p), _index_ret(p, c), n)[0],
         ("adj_close",))

# Liquidity（35）
_add("amount_ma_20d", "Tushare-流动性", 1, _amount_ma, ("amount",))
for _n in (5, 10, 20, 21, 42, 63, 126, 252):
    _add(f"avg_turnover_{_n}d", "Tushare-流动性", -1,
         lambda p, n=_n: _avg_turnover(p, n), ("turnover_rate",))
for _n in (21, 42, 63, 126, 252):
    _add(f"std_turnover_{_n}d", "Tushare-流动性", -1,
         lambda p, n=_n: _std_turnover(p, n), ("turnover_rate",))
for _short in (21, 42, 63, 126):
    for _long in (252, 504):
        _add(f"bias_turn_{_short}d_{_long}d", "Tushare-流动性", -1,
             lambda p, s=_short, l=_long: _bias_turnover(p, s, l), ("turnover_rate",))
        _add(f"bias_std_turn_{_short}d_{_long}d", "Tushare-流动性", -1,
             lambda p, s=_short, l=_long: _bias_std_turnover(p, s, l), ("turnover_rate",))
_add("turnover_ma_20d", "Tushare-流动性", 1, lambda p: _turnover_ma(p, 20), ("turnover_rate",))
_add("turnover_ma_20d_120d", "Tushare-流动性", 1,
     lambda p: _turnover_ma(p, 20, 120), ("turnover_rate",))
_add("sum_abs_rtn_amount_20d", "Tushare-流动性", -1,
     _sum_abs_rtn_amount, ("adj_close", "amount"))
_add("volume_alpha_300d_000001", "Tushare-流动性", 1,
     lambda p: _volume_alpha(p, "000001.SH"), ("vol",))
_add("volume_alpha_300d_000300", "Tushare-流动性", 1,
     lambda p: _volume_alpha(p, "000300.SH"), ("vol",))

# Risk（25）
_add("adjusted_sharpe_750d", "Tushare-风险", 1, _adjusted_sharpe, ("adj_close",))
for _n, _code in ((60, "000300.SH"), (125, "000300.SH"), (250, "000300.SH"),
                  (500, "000300.SH"), (1000, "000300.SH"), (1320, "000001.SH")):
    _add(f"beta_{_n}d_{_code.split('.')[0]}", "Tushare-风险", -1,
         lambda p, n=_n, c=_code: _beta(p, c, n), ("adj_close",))
_add("beta_consistency_1320d_000300", "Tushare-风险", -1,
     lambda p: _beta_consistency(p, "000300.SH", 1320), ("adj_close",))
_add("days_beyond_upper_lower_21d", "Tushare-风险", 1, _days_beyond, ("adj_close",))
for _n in (21, 42, 63, 126, 252):
    _add(f"high_low_{_n}d", "Tushare-风险", -1,
         lambda p, n=_n: _high_low(p, n), ("adj_close",))
_add("log_price", "Tushare-风险", 1, _log_price, ("adj_close",))
for _n in (21, 42, 63, 126, 252):
    _add(f"return_std_{_n}d", "Tushare-风险", -1,
         lambda p, n=_n: _std(_ret(p), n), ("adj_close",))
for _n in (60, 750):
    _add(f"sharpe_{_n}d", "Tushare-风险", 1,
         lambda p, n=_n: _sharpe(p, n), ("adj_close",))
_add("sigma_1320d_000001", "Tushare-风险", -1,
     lambda p: _resid_std(p, "000001.SH", 1320), ("adj_close",))
_add("sigma_1320d_000300", "Tushare-风险", -1,
     lambda p: _resid_std(p, "000300.SH", 1320), ("adj_close",))
_add("volume_beta_120d_000300", "Tushare-风险", -1,
     lambda p: _volume_beta(p, "000300.SH", 120), ("vol",))

# Reversal（3）
_add("price_dist", "Tushare-反转", 1, _price_dist, ("close",))
_add("rsi", "Tushare-反转", -1, _rsi, ("adj_close",))
_add("small_cap_reversal_21d", "Tushare-反转", 1, _small_cap_reversal,
     ("adj_close", "total_mv"))

# Size（3）
_add("float_size", "Tushare-规模", 1, _float_size, ("float_share", "close"))
_add("size", "Tushare-规模", 1, _size, ("total_share", "close"))
_add("nl_size", "Tushare-规模", 1, _nl_size, ("total_share", "close"))


def _beta_consistency(panel, code, window):
    x = _ret(panel)
    y = _index_ret(panel, code)
    if y is None:
        return _nan_like(x)
    alpha, beta, _, _, _ = _roll_alpha_beta(x, y, window)
    resid = x - alpha - beta.mul(y.reindex(x.index), axis=0)
    return _std(beta * resid, window)


# 把 B2/B3 未实现批次的依赖描述补全（便于后续实现时直接查）
for _name, _entry in TUSHARE_FACTOR_MAP.items():
    if _entry["status"] != "mapped":
        continue
    if _entry["factor_type"] == "Alpha101":
        _entry["requires"] = ("adj_close", "vol", "amount", "total_mv", "industry")
        _entry["note"] = "待 B2 表达式引擎（同一批行情数据即可计算）"
    else:
        _entry["requires"] = ("income", "balancesheet", "cashflow", "fina_indicator",
                              "ann_date", "total_mv")
        _entry["note"] = "待 B3 PIT 财报加载（ann_date ≤ T 对齐）"


# ---------------------------------------------------------------- Alpha101 注册
def register_alpha101(register: Callable, existing: dict[str, Any] | None = None) -> int:
    """把 B2 Alpha101 表达式因子注册进 factor_lib；返回新增数量。"""
    existing = existing or {}
    count = 0
    for name, formula in _ALPHA101_FORMULAS.items():
        if name in existing or name not in TUSHARE_FACTOR_MAP:
            continue
        register(name, "Tushare-Alpha101", "continuous", 1,
                 f"Alpha101（Tushare factor_list 表达式）: {formula[:160]}",
                 source=SOURCE,
                 requires=("adj_close", "vol", "amount", "total_mv", "industry"))(
            lambda p, f=formula: evaluate_formula(f, p))
        TUSHARE_FACTOR_MAP[name].update({
            "status": "implemented",
            "requires": ("adj_close", "vol", "amount", "total_mv", "industry"),
            "batch": BATCH_B2,
            "note": "Alpha101 表达式引擎（alpha101_engine.py）",
        })
        count += 1
    return count


# ---------------------------------------------------------------- B3 注册
def register_b3(register: Callable, existing: dict[str, Any] | None = None) -> int:
    """把 B3 PIT 财报因子注册进 factor_lib；返回新增数量（不含 blocked）。"""
    existing = existing or {}
    count = register_financial(register, existing)
    for name, spec in B3_FACTORS.items():
        entry = TUSHARE_FACTOR_MAP.get(name)
        if entry is None:
            continue
        blocked = "blocked" in (spec.get("note") or "")
        entry.update({
            "status": "blocked_missing_data" if blocked else "implemented",
            "requires": tuple(spec.get("requires") or (
                "income", "balancesheet", "cashflow", "fina_indicator", "ann_date", "total_mv")),
            "batch": BATCH_B3,
            "note": spec.get("note") or "PIT 财报因子（factor_lib_financial.py）",
        })
    return count


def register_batch(register: Callable, existing: dict[str, Any] | None = None) -> int:
    """把 B1/B2/B3 已实现因子注册进 factor_lib；返回注册数量。"""
    existing = existing or {}
    count = 0
    for spec in _SPECS:
        if spec["name"] in existing:
            continue
        register(spec["name"], spec["family"], spec["kind"], spec["direction"],
                 spec["description"], source=SOURCE, requires=spec["requires"])(spec["fn"])
        count += 1
    count += register_alpha101(register, existing)
    count += register_b3(register, existing)
    return count


def factor_map_summary() -> dict[str, Any]:
    by_batch: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for entry in TUSHARE_FACTOR_MAP.values():
        by_batch[entry["batch"]] = by_batch.get(entry["batch"], 0) + 1
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        by_type[entry["factor_type"]] = by_type.get(entry["factor_type"], 0) + 1
    return {
        "total": len(TUSHARE_FACTOR_MAP),
        "by_type": dict(sorted(by_type.items())),
        "by_batch": dict(sorted(by_batch.items())),
        "by_status": dict(sorted(by_status.items())),
        "implemented": sum(1 for e in TUSHARE_FACTOR_MAP.values() if e["status"] == "implemented"),
    }


__all__ = ["TUSHARE_FACTOR_MAP", "TYPE_NAMES", "SOURCE", "BATCH_B1", "BATCH_B2",
           "BATCH_B3", "register_batch", "register_alpha101", "register_b3",
           "factor_map_summary"]
