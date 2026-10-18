"""v2_app_state 表读写辅助（设置/熔断/配额持久化共用）。

DB 不可用时回退内存默认值，保证 API 不因持久化故障而崩溃。
"""
from __future__ import annotations

import copy
import logging
from datetime import UTC, datetime

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

# 内存回退层：DB 读写失败时兜底（保底不崩）
_memory_state: dict[str, dict] = {}


def _load_state(key: str, default: dict) -> dict:
    """读 v2_app_state[key]；无记录或 DB 失败时返回 default 深拷贝。"""
    try:
        session = get_session()
        try:
            row = session.get(orm.AppStateModel, key)
            if row is not None and isinstance(row.value, dict):
                _memory_state[key] = copy.deepcopy(row.value)
                return copy.deepcopy(row.value)
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断 API
        logger.warning("app_state load failed key=%s: %s", key, exc)
    if key in _memory_state:
        return copy.deepcopy(_memory_state[key])
    return copy.deepcopy(default)


def _save_state(key: str, value: dict) -> None:
    """写 v2_app_state[key]（upsert）；DB 失败时只写内存兜底。"""
    _memory_state[key] = copy.deepcopy(value)
    try:
        session = get_session()
        try:
            row = session.get(orm.AppStateModel, key)
            now = datetime.now(UTC)
            if row is None:
                session.add(orm.AppStateModel(key=key, value=value, updated_at=now))
            else:
                row.value = value
                row.updated_at = now
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断 API
        logger.warning("app_state save failed key=%s: %s", key, exc)


def _load_all(prefix: str) -> dict[str, dict]:
    """读所有 key 以 prefix 开头的状态（如 quota:）；DB 失败回退内存层。"""
    out: dict[str, dict] = {}
    db_ok = False
    try:
        session = get_session()
        try:
            rows = (
                session.query(orm.AppStateModel)
                .filter(orm.AppStateModel.key.like(f"{prefix}%"))
                .all()
            )
            for row in rows:
                if isinstance(row.value, dict):
                    out[row.key] = copy.deepcopy(row.value)
            db_ok = True
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("app_state load_all failed prefix=%s: %s", prefix, exc)
    if not db_ok:
        out = {k: copy.deepcopy(v) for k, v in _memory_state.items() if k.startswith(prefix)}
    else:
        for k, v in out.items():
            _memory_state[k] = copy.deepcopy(v)
    return out
