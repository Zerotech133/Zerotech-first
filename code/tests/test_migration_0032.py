"""迁移 0032：跟单模拟盘三表 upgrade/downgrade + 約束（sqlite 结构级）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0032_mirror_sim.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0032", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load()


def _setup_prereqs(conn) -> None:
    """Create minimal FK-target tables so SQLite accepts the FK declarations."""
    conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS v2_user "
        "(id INTEGER PRIMARY KEY, user_id VARCHAR(64) NOT NULL UNIQUE)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS v2_strategy "
        "(id INTEGER PRIMARY KEY, strategy_id VARCHAR(64) NOT NULL UNIQUE)"
    )


def test_0032_revision_chain():
    assert MIG.revision == "0032"
    assert MIG.down_revision == "0031"


def test_0032_upgrade_creates_tables(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(MIG, "op", Operations(context))

        MIG.upgrade()

        tables = set(inspect(conn).get_table_names())
        assert {"v2_mirror_sim_account", "v2_mirror_sim_position", "v2_mirror_sim_fill"} <= tables
        cols = {c["name"] for c in inspect(conn).get_columns("v2_mirror_sim_account")}
        assert {"mirror_account_id", "user_id", "strategy_id", "market", "initial_capital",
                "cash_balance", "status", "created_at", "updated_at"} <= cols


def test_0032_downgrade_drops_tables(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(MIG, "op", Operations(context))

        MIG.upgrade()
        MIG.downgrade()

        tables = set(inspect(conn).get_table_names())
        assert "v2_mirror_sim_account" not in tables
