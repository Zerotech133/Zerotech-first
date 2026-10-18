"""治理 API：策略晋升/降级。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.governance_service import GovernanceService
from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/governance", tags=["governance"], dependencies=[Depends(get_current_user)])


class DemoteBody(BaseModel):
    reason: str = "manual demotion"


@router.post("/strategies/{strategy_version_id}/promote")
def promote(strategy_version_id: str, db: PersistencePort = Depends(get_db)):
    try:
        version = GovernanceService(db).promote(strategy_version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"strategy_version_id": version.strategy_version_id, "status": version.status}


@router.post("/strategies/{strategy_version_id}/demote")
def demote(strategy_version_id: str, body: DemoteBody, db: PersistencePort = Depends(get_db)):
    try:
        version = GovernanceService(db).demote(strategy_version_id, body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"strategy_version_id": version.strategy_version_id, "status": version.status}


@router.get("/auto-review")
def auto_review(market: str | None = None, db: PersistencePort = Depends(get_db)):
    """自动审查（衰减监控建议）。

    C6：market 给定则只审该市场（非法值 422 market.invalid，对齐 B4 口径，
    不再静默忽略回退全市场）；缺省保持全市场（存量行为）。
    """
    if market is not None and market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    return GovernanceService(db).auto_review(market=market)


# ---------- 晋升决策（半自动：评估留痕 + 人工确认执行） ----------

@router.get("/promotion-decisions")
def list_promotion_decisions(confirm_status: str | None = None, limit: int = 50):
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        q = session.query(orm.PromotionDecisionModel).order_by(
            orm.PromotionDecisionModel.created_at.desc())
        if confirm_status:
            q = q.filter(orm.PromotionDecisionModel.confirm_status == confirm_status)
        rows = q.limit(limit).all()
        return [{
            "decision_id": r.decision_id, "strategy_version_id": r.strategy_version_id,
            "decision": r.decision, "from_status": r.from_status, "to_status": r.to_status,
            "reason": r.reason, "samples": r.samples,
            "win_rate": float(r.win_rate) if r.win_rate is not None else None,
            "pnl_ratio": float(r.pnl_ratio) if r.pnl_ratio is not None else None,
            "confirm_status": r.confirm_status, "created_at": r.created_at.isoformat(),
        } for r in rows]
    finally:
        session.close()


@router.post("/promotion-decisions/{decision_id}/confirm")
def confirm_promotion_decision(decision_id: str):
    from zhice_os_v2.application.services.promotion_service import confirm_decision

    r = confirm_decision(decision_id)
    if r.get("error"):
        raise HTTPException(status_code=409, detail=r["error"])
    return r


@router.get("/promotion-eval/{strategy_version_id}")
def promotion_eval(strategy_version_id: str, manual: bool = False):
    from zhice_os_v2.application.services.promotion_service import evaluate_promotion

    result = evaluate_promotion(strategy_version_id, manual=manual)
    # C5：未知版本此前 200 {"error": "version_not_found"}（前端会当成功形态渲染），
    # 改 404 {code}（复用 strategy.not_found，errors.json 已有键）。
    if result.get("error") == "version_not_found":
        raise HTTPException(status_code=404, detail={"code": "strategy.not_found"})
    return result


# ---------- 灰度发布 ----------

class GrayBody(BaseModel):
    scope: str = "10%仓位上限"
    observation_days: int = 14


@router.post("/gray/start/{strategy_version_id}")
def gray_start(strategy_version_id: str, body: GrayBody):
    from zhice_os_v2.application.services.promotion_service import start_gray_release

    return start_gray_release(strategy_version_id, body.scope, body.observation_days)


class GrayConcludeBody(BaseModel):
    conclusion: str = ""
    request_promotion: bool = False


@router.post("/gray/{gray_release_id}/conclude")
def gray_conclude(gray_release_id: str, body: GrayConcludeBody):
    from zhice_os_v2.application.services.promotion_service import conclude_gray_release

    return conclude_gray_release(gray_release_id, body.conclusion, body.request_promotion)
