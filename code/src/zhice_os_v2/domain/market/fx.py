"""跨市场汇率折算（单一真源）。

refresh_fx() 每日调用拉取实时汇率（open.er-api.com，免费无 API Key，CNY 基准）。
失败时静默降级保留现有值，记录 WARNING；fx_stale=True 表示当前值为静态基准或刷新失败，
响应层应据此向前端提示「汇率非实时」。

外部调用：scheduler_jobs.run_refresh_fx_job() 已注册每日 8:00（Asia/Shanghai）自动刷新；
也可手动调用 refresh_fx()。

跨币种金额（美股 USD、港股 HKD、加密 USDT）严禁直接与人民币相加——必须先经本模块
折算到 CNY 再合计。
"""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

# 1 单位该币种 = ? CNY（静态 fallback；refresh_fx() 拉到实时后就地更新）
FX_TO_CNY: dict[str, float] = {"CNY": 1.0, "USD": 7.2, "HKD": 0.92, "USDT": 7.2}
# 数据时点（ISO-8601 日期字符串）；refresh_fx() 成功后更新为刷新当日
FX_ASOF: str = datetime.date.today().isoformat()
# True = 当前值为静态基准或刷新失败，非实时；refresh_fx() 成功后置 False
FX_STALE: bool = True

CURRENCY_BY_MARKET: dict[str, str] = {"CN": "CNY", "US": "USD", "HK": "HKD", "CRYPTO": "USDT"}


def currency_of(market: str) -> str:
    return CURRENCY_BY_MARKET.get(market, "CNY")


def fx_to_cny(market: str) -> float:
    """该市场本位币 → CNY 的汇率。"""
    return FX_TO_CNY.get(currency_of(market), 1.0)


def to_cny(amount: float, market: str) -> float:
    """把某市场本位币金额折算成 CNY。"""
    return amount * fx_to_cny(market)


def refresh_fx() -> None:
    """拉取实时汇率并就地更新 FX_TO_CNY / FX_ASOF / FX_STALE。

    数据源：open.er-api.com（免费，无 API Key，CNY 基准），超时 5s。
    返回格式：rates[XXX] = 1 CNY 能换多少 XXX，故 1 XXX = 1/rates[XXX] CNY。
    失败时静默降级：保留现有值，记录 WARNING，不抛出异常。
    USDT 锚定 USD 汇率（稳定币）。
    """
    global FX_ASOF, FX_STALE
    try:
        import requests

        resp = requests.get(
            "https://open.er-api.com/v6/latest/CNY",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        rates: dict = data.get("rates", {})
        if "USD" in rates and rates["USD"] > 0:
            FX_TO_CNY["USD"] = round(1.0 / rates["USD"], 6)
        if "HKD" in rates and rates["HKD"] > 0:
            FX_TO_CNY["HKD"] = round(1.0 / rates["HKD"], 6)
        FX_TO_CNY["USDT"] = FX_TO_CNY["USD"]
        FX_ASOF = datetime.date.today().isoformat()
        FX_STALE = False
        logger.info(
            "refresh_fx: 汇率已刷新 USD=%.4f HKD=%.4f USDT=%.4f asof=%s",
            FX_TO_CNY["USD"],
            FX_TO_CNY["HKD"],
            FX_TO_CNY["USDT"],
            FX_ASOF,
        )
    except Exception as exc:  # noqa: BLE001 - 必须静默降级，不能向调用方抛异常
        logger.warning("refresh_fx: 拉取失败，保留现有汇率 (%s)", exc)


def get_fx_info() -> dict:
    """返回汇率元信息，供 API 响应透传。

    Returns:
        {"fx_asof": str, "fx_stale": bool}
    """
    return {"fx_asof": FX_ASOF, "fx_stale": FX_STALE}
