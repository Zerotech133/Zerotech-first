"""Task A1：0029 会话吊销代数 auth_version TDD（migrations/versions/0029_user_auth_version.py）。

约束：
- v2_user 增列 auth_version INTEGER NOT NULL DEFAULT 1，旧行由常量默认回填 1；
- 前向安全迁移：downgrade 显式拒绝（forward-only），绝不删除已递增的安全代数列；
- 用真实 sqlite + MigrationContext/Operations 行为验证 add_column（SQLite 支持 ADD COLUMN）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0029_user_auth_version.py"
)


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0029_user_auth_version", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load_migration_module()


def test_revision_is_0029_on_top_of_0028():
    assert MIG.revision == "0029"
    assert MIG.down_revision == "0028"


def test_upgrade_adds_non_null_auth_version_and_backfills(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE v2_user (id INTEGER PRIMARY KEY, user_id VARCHAR(64) NOT NULL)"
        )
        conn.exec_driver_sql("INSERT INTO v2_user (id, user_id) VALUES (1, 'legacy-user')")
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(MIG, "op", Operations(context))

        MIG.upgrade()

        cols = {c["name"]: c for c in inspect(conn).get_columns("v2_user")}
        assert "auth_version" in cols
        assert cols["auth_version"]["nullable"] is False
        backfilled = conn.exec_driver_sql(
            "SELECT auth_version FROM v2_user WHERE id = 1"
        ).scalar_one()
        assert backfilled == 1


def test_downgrade_is_explicitly_blocked():
    with pytest.raises(RuntimeError, match="forward-only"):
        MIG.downgrade()
