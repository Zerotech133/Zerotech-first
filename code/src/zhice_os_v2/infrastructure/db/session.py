"""数据库会话工厂。"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from zhice_os_v2.config.settings import get_settings


@lru_cache(maxsize=1)
def _engine():
    settings = get_settings()
    return create_engine(settings.db_main_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def _session_factory():
    return sessionmaker(bind=_engine(), expire_on_commit=False)


def get_session() -> Session:
    return _session_factory()()
