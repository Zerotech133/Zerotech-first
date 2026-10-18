"""基本面护栏（V1 纯逻辑移植版）。

只包含与数据源无关的判定逻辑；数据加载由基础设施层实现。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FundamentalGateConfig:
    enabled: bool = True
    require_profit: bool = True
    max_debt_ratio: float | None = None
    min_netprofit_yoy: float | None = None
    block_on_missing: bool = False
    block_earnings_warning: bool = True
    earnings_warning_types: tuple[str, ...] = ("首亏", "续亏", "预减")
    earnings_warning_max_drop: float | None = -50.0
    earnings_warning_window_days: int = 180
    block_big_reduction: bool = True
    reduction_window_days: int = 90
    reduction_ratio_threshold: float = 2.0


def evaluate(fund: dict | None, cfg: FundamentalGateConfig) -> tuple[bool, str]:
    if fund is None:
        if cfg.block_on_missing:
            return False, "无财务数据(拦截)"
        return True, "无财务数据(放行)"

    def _fnum(v):
        return None if v is None else float(v)

    if cfg.require_profit:
        pd = _fnum(fund.get("profit_dedt_cny"))
        roe_dt = _fnum(fund.get("roe_dt"))
        roe = _fnum(fund.get("roe"))
        if pd is not None:
            if pd <= 0:
                return False, f"亏损:扣非净利={pd:.0f}≤0"
        elif roe_dt is not None:
            if roe_dt <= 0:
                return False, f"亏损:扣非ROE={roe_dt:.2f}%≤0"
        elif roe is not None:
            if roe <= 0:
                return False, f"亏损:ROE={roe:.2f}%≤0"

    if cfg.max_debt_ratio is not None:
        dr = _fnum(fund.get("debt_to_assets"))
        if dr is not None and dr > cfg.max_debt_ratio:
            return False, f"高负债:资产负债率={dr:.1f}%>{cfg.max_debt_ratio:.0f}%"

    if cfg.min_netprofit_yoy is not None:
        yoy = _fnum(fund.get("netprofit_yoy"))
        if yoy is not None and yoy < cfg.min_netprofit_yoy:
            return False, f"净利下滑:同比={yoy:.1f}%<{cfg.min_netprofit_yoy:.0f}%"

    return True, "通过"


def evaluate_forecast(fc: dict | None, cfg: FundamentalGateConfig) -> tuple[bool, str]:
    if fc is None:
        return True, "无近端预告"
    ftype = fc.get("forecast_type")
    if ftype and ftype in cfg.earnings_warning_types:
        return False, f"业绩预告暴雷:{ftype}"
    if cfg.earnings_warning_max_drop is not None:
        pmax = fc.get("p_change_max")
        if pmax is not None and float(pmax) < cfg.earnings_warning_max_drop:
            return False, f"业绩预告大幅下滑:净利变动上限{float(pmax):.0f}%<{cfg.earnings_warning_max_drop:.0f}%"
    return True, "预告通过"


def evaluate_reduction(total_reduce: float | None, cfg: FundamentalGateConfig) -> tuple[bool, str]:
    if total_reduce is None:
        return True, "无减持记录"
    if total_reduce > cfg.reduction_ratio_threshold:
        return False, f"股东大额减持:近{cfg.reduction_window_days}天累计减持{total_reduce:.2f}%>{cfg.reduction_ratio_threshold:.1f}%"
    return True, "减持在阈值内"


def filter_picks_pure(
    picks: list[dict],
    funds: dict[str, dict],
    forecasts: dict[str, dict],
    reductions: dict[str, float],
    cfg: FundamentalGateConfig,
) -> tuple[list[dict], list[dict]]:
    kept, rejected = [], []
    for p in picks:
        ts_code = p.get("ts_code")
        if not ts_code:
            kept.append(p)
            continue
        passed, reason = evaluate(funds.get(ts_code), cfg)
        if passed and cfg.block_earnings_warning:
            passed, reason = evaluate_forecast(forecasts.get(ts_code), cfg)
        if passed and cfg.block_big_reduction:
            passed, reason = evaluate_reduction(reductions.get(ts_code), cfg)
        if passed:
            kept.append(p)
        else:
            rejected.append({"ts_code": ts_code, "reason": reason})
    return kept, rejected
