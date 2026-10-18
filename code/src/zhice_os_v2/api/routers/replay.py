"""复盘 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/replay", tags=["replay"], dependencies=[Depends(get_current_user)])


def _replay_detail(db: PersistencePort, replay_id: str) -> dict:
    for r in db.list_replays():
        if r.replay_id == replay_id:
            return {
                "replay_id": r.replay_id,
                "strategy_version_id": r.strategy_version_id,
                "market": r.market,
                "ts_code": r.ts_code,
                "pnl": str(r.pnl),
                "pnl_pct": str(r.pnl_pct),
                "exit_reason": r.exit_reason,
                "status": r.status,
            }
    raise HTTPException(status_code=404, detail="replay not found")


def _attribution_rows(replay_id: str) -> list[dict]:
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        rows = session.query(
            orm.AttributionRecordModel.dimension,
            orm.AttributionRecordModel.weight,
            orm.AttributionRecordModel.pnl_amount,
            orm.AttributionRecordModel.note,
        ).filter(orm.AttributionRecordModel.replay_id == replay_id).all()
        return [
            {
                "dimension": r[0],
                "weight": float(r[1]) if r[1] is not None else 0,
                "pnl_amount": float(r[2]) if r[2] is not None else 0,
                "note": r[3] or "",
            }
            for r in rows
        ]
    finally:
        session.close()


@router.get("")
def list_replays(db: PersistencePort = Depends(get_db)):
    return [
        {
            "replay_id": r.replay_id,
            "strategy_version_id": r.strategy_version_id,
            "market": r.market,
            "ts_code": r.ts_code,
            "pnl": str(r.pnl),
            "pnl_pct": str(r.pnl_pct),
            "exit_reason": r.exit_reason,
            "status": r.status,
        }
        for r in db.list_replays()
    ]


# ── query 形态（主）：replay_id 生成时把含 `/` 的 ts_code（CRYPTO 如 UNI/USDT）
# 拼进 id，path 模板匹配不了斜杠 → /replay/{id} 系统性 404，须走 ?replay_id=。
# 字面量路由须注册在 /{replay_id} 之前，否则被通配单段吃掉。原 path 形态保留兼容。 ──


@router.get("/detail")
def get_replay_by_query(replay_id: str = Query(...), db: PersistencePort = Depends(get_db)):
    return _replay_detail(db, replay_id)


@router.get("/attribution")
def get_attribution_by_query(replay_id: str = Query(...)):
    return _attribution_rows(replay_id)


@router.get("/{replay_id}")
def get_replay(replay_id: str, db: PersistencePort = Depends(get_db)):
    return _replay_detail(db, replay_id)


@router.get("/{replay_id}/attribution")
def get_attribution(replay_id: str):
    return _attribution_rows(replay_id)
