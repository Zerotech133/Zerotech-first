"""owner-scoped 交易实体的 require_owned 依赖与 loader（Phase B Task 6，纯读）。

设计：
- loader(resource_id, user_id) 返回「**仅当该资源归属该 user**」的资源，否则 None
  （不区分「不存在」与「非 owner」——统一 not-found 等价，不泄露存在性）。
- 归属判定：资源自带 user_strategy_id（Phase B Task 1 归属列）→ 落在
  ``UserStrategyService.list_owned_instance_ids(user_id)`` 集合内才算归属；
  legacy（user_strategy_id 为 NULL）恒不可见。
- require_owned(loader) 把 None → HTTP 404 {"code": "common.not_found"}。

owner 恒取自 JWT（get_current_user），**绝不**接受请求体/查询里的 user_id。
读交易实体走 get_db()（PersistencePort，内存/SQL 后端一致）；实例归属集走
get_user_strategy_service()（生产读真库实例，测试读隔离 sqlite）。二者均为
api.dependencies 的 lru_cache 单例，与 app 请求路径同源。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_db,
    get_user_strategy_service,
)
from zhice_os_v2.domain.security.user import User

_NOT_FOUND = {"code": "common.not_found"}


def _owned_instance_ids(user_id: str) -> set[str]:
    return get_user_strategy_service().list_owned_instance_ids(user_id)


def _is_owned(entity: Any, user_id: str) -> bool:
    """实体归属该 user 当且仅当：实体存在、带 user_strategy_id、且该 id 属该 user。"""
    if entity is None:
        return False
    usid = getattr(entity, "user_strategy_id", None)
    if usid is None:  # legacy（NULL 归属）经 /my 一律不可见
        return False
    return usid in _owned_instance_ids(user_id)


# ---- owner-scoped loader（resource_id, user_id）→ 资源 | None ----
def load_owned_plan(resource_id: str, user_id: str):
    plan = get_db().get_trade_plan(resource_id)
    return plan if _is_owned(plan, user_id) else None


def load_owned_position(resource_id: str, user_id: str):
    pos = get_db().get_position(resource_id)
    return pos if _is_owned(pos, user_id) else None


def load_owned_approval(resource_id: str, user_id: str):
    appr = get_db().get_approval(resource_id)
    return appr if _is_owned(appr, user_id) else None


def load_owned_replay(resource_id: str, user_id: str):
    # PersistencePort 无 get_replay：按 id 从 list_replays 取（只读，规模可控）。
    for r in get_db().list_replays():
        if r.replay_id == resource_id:
            return r if _is_owned(r, user_id) else None
    return None


def require_owned(loader: Callable[[str, str], Any]):
    """把 loader 包成 FastAPI 依赖：非 owner / 不存在 / legacy → 404（not-found 等价）。"""

    def dependency(resource_id: str, current_user: User = Depends(get_current_user)):
        resource = loader(resource_id, current_user.user_id)
        if resource is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        return resource

    return dependency
