"""回测 API。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.application.services.backtest_service import BacktestService
from zhice_os_v2.domain.strategy.spec import StrategySpec

router = APIRouter(prefix="/backtest", tags=["backtest"], dependencies=[Depends(get_current_user)])


class BacktestBody(BaseModel):
    spec: dict
    ts_code: str
    start: str
    end: str
    initial_capital: float = 100000.0


@router.post("/run")
def run(body: BacktestBody):
    spec = StrategySpec.model_validate(body.spec)
    return BacktestService().run(
        spec=spec,
        ts_code=body.ts_code,
        start=date.fromisoformat(body.start),
        end=date.fromisoformat(body.end),
        initial_capital=body.initial_capital,
    )


class WalkForwardBody(BaseModel):
    spec: dict
    ts_code: str
    strategy_version_id: str
    start: str
    end: str
    folds: int = 4


@router.post("/walk-forward")
def walk_forward(body: WalkForwardBody):
    """folds 段滚动回测，oos_consistency 落库供晋升 OOS 门消费。"""
    from zhice_os_v2.application.services.walk_forward_service import run_walk_forward

    spec = StrategySpec.model_validate(body.spec)
    return run_walk_forward(spec, body.ts_code, body.strategy_version_id,
                            date.fromisoformat(body.start),
                            date.fromisoformat(body.end), folds=body.folds)


class ExitLabBody(BaseModel):
    spec: dict
    ts_code: str
    variants: list[str] | None = None


@router.post("/exit-lab")
def exit_lab(body: ExitLabBody):
    """出场规则擂台：FIXED/TIGHT/LOOSE 变体对决。"""
    from zhice_os_v2.application.services.walk_forward_service import run_exit_lab

    spec = StrategySpec.model_validate(body.spec)
    return run_exit_lab(spec, body.ts_code, body.variants)
