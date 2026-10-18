"""市场环境服务（V1 market_env_service 语义移植 + V2 原有实时查询兼容）。

V1 算法：基准近 6 根日线 →
  returns_5d = close[-1]/close[0] - 1；volatility_5d = 极差/最低（非标准差）
  _classify: ret>2% BULL/2 ← ret<-2% BEAR/4 ← 极差>5% VOLATILE/4 ← SIDEWAYS/3
  T213 子指标（note JSON）：env_score/risk_appetite/sector_activity/trading_window
缺基准或 bars<2 → SIDEWAYS/3 降级不崩。

策略路由（V1 strategy_router._REGIME_CONFIG）：按 regime 决定策略启停/敞口帽/
优先级 boost。V2 语义：ACTIVE=PROD、STAGING=STAGING、其余=DRAFT。
"""
from __future__ import annotations

import logging
from datetime import date

from zhice_os_v2.infrastructure.market_data.kline import fetch_db_bars

logger = logging.getLogger(__name__)

# V1 基准：CN=沪深300、US=标普、HK=恒指、CRYPTO=BTC
REGIME_BENCHMARK = {"CN": "000300.SH", "US": "^GSPC", "HK": "^HSI", "CRYPTO": "BTC/USDT"}
# 实时查询兜底（V2 旧基准：CN 上证代理可查、US/HK 指数代理无日K 时退化用原代理）
_FALLBACK_BENCH = {"CN": "000001.SH", "US": "AAPL", "HK": "00700", "CRYPTO": "BTC/USDT"}

# V1 strategy_router 规则表
_REGIME_CONFIG = {
    "BULL":     {"prod_enabled": True,  "staging_enabled": True,  "max_exposure_pct": 0.80, "boost": 1, "cooling": False},
    "SIDEWAYS": {"prod_enabled": True,  "staging_enabled": True,  "max_exposure_pct": 0.60, "boost": 0, "cooling": False},
    "BEAR":     {"prod_enabled": True,  "staging_enabled": True,  "max_exposure_pct": 0.30, "boost": -1, "cooling": False},
    "VOLATILE": {"prod_enabled": True,  "staging_enabled": False, "max_exposure_pct": 0.20, "boost": -2, "cooling": True},
}


def _classify(ret5: float, vol5: float) -> tuple[str, int]:
    if ret5 > 0.02:
        return "BULL", 2
    if ret5 < -0.02:
        return "BEAR", 4
    if vol5 > 0.05:
        return "VOLATILE", 4
    return "SIDEWAYS", 3


def compute_regime(market: str, benchmark: str | None = None) -> dict:
    """V1 算法实时计算（不落表）。"""
    bench = benchmark or REGIME_BENCHMARK.get(market)
    intended_bench = bench  # 记录意图基准，用于降级标注
    bars = fetch_db_bars(bench, "day", limit=6) if bench else None
    degraded = False
    if not bars or len(bars) < 2:
        # 退化：V2 旧基准代理（保证 /market/regime 不空）
        bench2 = _FALLBACK_BENCH.get(market)
        bars = fetch_db_bars(bench2, "day", limit=6) if bench2 else None
        if not bars or len(bars) < 2:
            return {"market": market, "benchmark": bench,
                    "benchmark_intended": intended_bench, "degraded": True,
                    "regime": "SIDEWAYS",
                    "risk_level": 3, "returns_5d": 0.0, "volatility_5d": 0.0,
                    "note": {"degraded": "no_benchmark"}, "ret5": 0.0, "ret20": 0.0}
        degraded = True
        logger.warning(
            "Market %s regime using fallback benchmark %s (intended: %s)"
            " — no index data available",
            market, bench2, intended_bench,
        )
        bench = bench2
    closes = [float(b["close"]) for b in bars if b.get("close")]
    if len(closes) < 2:
        return {"market": market, "benchmark": bench,
                "benchmark_intended": intended_bench, "degraded": degraded,
                "regime": "SIDEWAYS",
                "risk_level": 3, "returns_5d": 0.0, "volatility_5d": 0.0,
                "note": {"degraded": "insufficient_data"}, "ret5": 0.0, "ret20": 0.0}
    ret5 = closes[-1] / closes[0] - 1
    vol5 = (max(closes) - min(closes)) / min(closes) if min(closes) > 0 else 0.0
    regime, risk = _classify(ret5, vol5)
    # T213 子指标（V1 公式）
    env_score = max(0.0, min(100.0, 50 + ret5 * 1000 - vol5 * 200))
    risk_appetite = "RISK_ON" if risk <= 2 else ("RISK_OFF" if risk >= 4 else "NEUTRAL")
    sector_activity = "ACTIVE" if vol5 > 0.04 else ("QUIET" if vol5 < 0.01 else "MODERATE")
    trading_window = ("FAVORABLE" if regime == "BULL" and vol5 <= 0.04
                      else ("UNFAVORABLE" if regime in ("BEAR", "VOLATILE") else "NEUTRAL"))
    return {
        "market": market, "benchmark": bench,
        "benchmark_intended": intended_bench, "degraded": degraded,
        "regime": regime, "risk_level": risk,
        "returns_5d": round(ret5, 6), "volatility_5d": round(vol5, 6),
        "ret5": round(ret5, 4), "ret20": round(ret5, 4),  # 兼容旧字段
        "note": {"env_score": round(env_score, 1), "risk_appetite": risk_appetite,
                 "sector_activity": sector_activity, "trading_window": trading_window},
    }


def compute_and_save(market: str, env_date: date | None = None) -> dict:
    """V1 Step9b：计算并落 v2_market_environment（存在则 UPDATE）。"""
    result = compute_regime(market)
    if result.get("regime") == "SIDEWAYS" and result.get("note", {}).get("degraded"):
        return result  # 退化结果不落表
    d = env_date or date.today()
    from decimal import Decimal

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        row = (
            session.query(orm.MarketEnvironmentModel)
            .filter(orm.MarketEnvironmentModel.market == market,
                    orm.MarketEnvironmentModel.env_date == d)
            .one_or_none()
        )
        if row is None:
            row = orm.MarketEnvironmentModel(market=market, env_date=d)
            session.add(row)
        row.regime = result["regime"]
        row.risk_level = result["risk_level"]
        row.benchmark = result.get("benchmark")
        row.returns_5d = Decimal(str(result.get("returns_5d", 0)))
        row.volatility_5d = Decimal(str(result.get("volatility_5d", 0)))
        row.note = result.get("note")
        session.commit()
        return result
    finally:
        session.close()


def get_current_regime(market: str) -> dict:
    """查最近一条落表记录；无记录实时算（V1 语义：首日 SIDEWAYS）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        row = (
            session.query(orm.MarketEnvironmentModel)
            .filter(orm.MarketEnvironmentModel.market == market)
            .order_by(orm.MarketEnvironmentModel.env_date.desc())
            .first()
        )
        if row is None:
            return compute_regime(market)
        return {"market": market, "benchmark": row.benchmark, "regime": row.regime,
                "risk_level": row.risk_level,
                "returns_5d": float(row.returns_5d or 0), "volatility_5d": float(row.volatility_5d or 0),
                "note": row.note or {}, "env_date": str(row.env_date)}
    finally:
        session.close()


def route_strategies(market: str, strategy_types: list[str], version_status: dict[str, str]) -> dict:
    """V1 Step3：按 regime 过滤/排序策略类型。

    version_status: strategy_type → 状态(ACTIVE/STAGING/DRAFT)。返回
    {enabled: [...按优先级排序...], max_exposure_pct, regime_info}。
    """
    info = get_current_regime(market)
    cfg = _REGIME_CONFIG.get(info.get("regime", "SIDEWAYS"), _REGIME_CONFIG["SIDEWAYS"])
    enabled = []
    for st in strategy_types:
        status = version_status.get(st, "ACTIVE")
        if status == "ACTIVE" and not cfg["prod_enabled"]:
            continue
        if status == "STAGING" and not cfg["staging_enabled"]:
            continue
        if status == "DRAFT":
            continue
        base = 2 if status == "ACTIVE" else 4
        enabled.append((max(0, base + cfg["boost"]), st))
    enabled.sort()
    return {"enabled": [st for _p, st in enabled], "max_exposure_pct": cfg["max_exposure_pct"],
            "cooling": cfg["cooling"], "regime_info": info}


class RegimeService:
    """兼容旧调用面（market.py/reports.py/research.py 用 get_regime）。"""

    def get_regime(self, market: str, benchmark: str | None = None) -> dict:
        return compute_regime(market, benchmark)
