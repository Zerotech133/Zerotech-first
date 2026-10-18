"""walk-forward 回测引擎（V1 backtest_runner.run_walk_forward 移植，简化版）。

folds=4 滚动窗：把 [start,end] 等分 4 段，每段独立跑 BacktestService.run，
oos_consistency = 正收益子窗数 / folds。结果落 v2_backtest_run
（params.walk_forward=True + oos_consistency）供晋升 OOS 门
（promotion_service._latest_oos）消费。

出场擂台（run_exit_lab，V1 语义）：FIXED/TIGHT/LOOSE 变体对决，产出切换建议。
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from zhice_os_v2.domain.strategy.spec import RiskSpec, StrategySpec

logger = logging.getLogger(__name__)


def run_walk_forward(spec: StrategySpec, ts_code: str, sv: str,
                     start: date, end: date, folds: int = 4,
                     persist: bool = True) -> dict:
    """folds 段滚动回测 → 落库。返回 {oos_consistency, folds_detail, run_id}。"""
    from zhice_os_v2.application.services.backtest_service import BacktestService

    total_days = (end - start).days
    if total_days < folds * 30:
        return {"error": f"range_too_short: {total_days}d < {folds}*30d"}
    seg = timedelta(days=total_days // folds)

    fold_results = []
    for i in range(folds):
        s = start + seg * i
        e = (start + seg * (i + 1)) if i < folds - 1 else end
        r = BacktestService().run(spec, ts_code, s, e)
        if "error" in r:
            fold_results.append({"fold": i + 1, "start": str(s), "end": str(e),
                                 "error": r["error"], "return": None})
        else:
            fold_results.append({"fold": i + 1, "start": str(s), "end": str(e),
                                 "return": r["total_return"],
                                 "trade_count": r["trade_count"],
                                 "max_drawdown": r["max_drawdown"]})
    valid = [f for f in fold_results if f.get("return") is not None]
    oos = round(sum(1 for f in valid if f["return"] > 0) / folds, 2) if valid else 0.0
    samples = sum(f.get("trade_count", 0) for f in valid)
    avg_ret = (sum(f["return"] for f in valid) / len(valid)) if valid else 0.0

    result = {"oos_consistency": oos, "folds_detail": fold_results,
              "avg_fold_return": round(avg_ret, 4), "total_trades": samples}
    if persist:
        run_id = f"wf-{sv}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        try:
            from zhice_os_v2.domain.ontology.objects import utc_now
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            session = get_session()
            try:
                session.add(orm.BacktestRunModel(
                    run_id=run_id, strategy_version_id=sv, market=spec.market,
                    status="DONE",
                    params={"walk_forward": True, "folds": folds,
                            "oos_consistency": oos, "ts_code": ts_code,
                            "start": str(start), "end": str(end)},
                    result=result, sample_count=samples, created_at=utc_now()))
                session.commit()
                result["run_id"] = run_id
            finally:
                session.close()
        except Exception:
            logger.exception("walk_forward persist failed")
    logger.info("walk_forward sv=%s oos=%s folds=%s", sv, oos, len(fold_results))
    return result


def run_exit_lab(spec: StrategySpec, ts_code: str,
                 variants: list[str] | None = None) -> dict:
    """出场规则擂台（V1 run_exit_lab 语义）：对比不同出场参数组合。"""
    from zhice_os_v2.application.services.backtest_service import BacktestService

    variants = variants or ["FIXED", "TIGHT", "LOOSE"]
    end = date.today()
    start = end - timedelta(days=365)
    lab = {}
    for v in variants:
        s = spec.model_copy(deep=True)
        if v == "FIXED":
            s.risk = RiskSpec(stop_loss=0.06, take_profit=0.20)
        elif v == "TIGHT":
            s.risk = RiskSpec(stop_loss=0.04, take_profit=0.08)
        elif v == "LOOSE":
            s.risk = RiskSpec(stop_loss=0.10, take_profit=0.30)
        r = BacktestService().run(s, ts_code, start, end)
        lab[v] = ({k: r.get(k) for k in ("total_return", "max_drawdown",
                                         "sharpe", "trade_count")}
                  if "error" not in r else {"error": r["error"]})
    best = max((v for v in lab if "error" not in lab[v]),
               key=lambda v: lab[v].get("sharpe") or 0, default=None)
    return {"ts_code": ts_code, "variants": lab,
            "suggestion": f"建议切换到 {best}（Sharpe 最高）" if best else "无可用变体"}
