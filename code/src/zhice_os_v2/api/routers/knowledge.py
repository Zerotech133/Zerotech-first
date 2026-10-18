"""知识/复盘 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.knowledge_service import KnowledgeService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(get_current_user)])


@router.get("/replay/search")
def search_replays(
    q: str = Query(""),
    market: str | None = None,
    limit: int = Query(20, le=100),
    db: PersistencePort = Depends(get_db),
):
    return KnowledgeService(db).search_replays(q=q, market=market, limit=limit)


def _explain(db: PersistencePort, replay_id: str) -> dict:
    try:
        return KnowledgeService(db).explain_replay(replay_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="replay not found") from exc


# query 形态（主）：含 `/` 的 replay_id（CRYPTO ts_code 拼入 id）走 path 必 404，
# 须走 ?replay_id=；原 /replay/explain/{replay_id} 保留兼容无斜杠调用。
@router.get("/replay/explain")
def explain_replay_by_query(replay_id: str = Query(...), db: PersistencePort = Depends(get_db)):
    return _explain(db, replay_id)


@router.get("/replay/explain/{replay_id}")
def explain_replay(replay_id: str, db: PersistencePort = Depends(get_db)):
    return _explain(db, replay_id)
