"""迁移 0033：通用通知绑定/挑战、偏好开关和 Telegram 数据回填。"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0033_domestic_notification_channels.py"
)
_NOW = datetime(2026, 9, 17, 8, 30, tzinfo=UTC)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0033", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load()


def _setup_prereqs(conn) -> None:
    """Create the minimal 0032-era schema used by the SQLite migration harness."""
    conn.exec_driver_sql(
        "CREATE TABLE v2_user ("
        "id INTEGER PRIMARY KEY, user_id VARCHAR(64) NOT NULL UNIQUE, "
        "telegram_chat_id VARCHAR(32))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_user_notification_preference ("
        "id INTEGER PRIMARY KEY, preference_id VARCHAR(64) NOT NULL UNIQUE, "
        "user_id VARCHAR(64) NOT NULL, channel VARCHAR(32) NOT NULL DEFAULT 'telegram', "
        "market_reports_enabled JSON NOT NULL DEFAULT '{}', "
        "system_alerts_enabled BOOLEAN NOT NULL DEFAULT 1, quiet_hours JSON, "
        "timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai', "
        "locale VARCHAR(8) NOT NULL DEFAULT 'zh', created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "CONSTRAINT uq_v2_user_notification_preference_user_channel "
        "UNIQUE (user_id, channel), FOREIGN KEY(user_id) REFERENCES v2_user(user_id))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_telegram_binding_challenge ("
        "id INTEGER PRIMARY KEY, challenge_id VARCHAR(64) NOT NULL UNIQUE, "
        "user_id VARCHAR(64) NOT NULL, token_hash VARCHAR(128) NOT NULL UNIQUE, "
        "expires_at DATETIME NOT NULL, consumed_at DATETIME, created_at DATETIME NOT NULL, "
        "FOREIGN KEY(user_id) REFERENCES v2_user(user_id))"
    )
    # Sentinel 0023 table: downgrade must not touch pre-0033 objects.
    conn.exec_driver_sql(
        "CREATE TABLE v2_notification_delivery (id INTEGER PRIMARY KEY, delivery_id VARCHAR(64))"
    )


def _seed_legacy_data(conn) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO v2_user (id, user_id, telegram_chat_id) VALUES "
            "(1, 'usr-alice', ' 12345678 '), "
            "(2, 'usr-blank', '   '), (3, 'usr-none', NULL)"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO v2_user_notification_preference "
            "(id, preference_id, user_id, channel, created_at, updated_at) "
            "VALUES (1, 'pref-alice', 'usr-alice', 'telegram', :now, :now)"
        ),
        {"now": _NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO v2_telegram_binding_challenge "
            "(id, challenge_id, user_id, token_hash, expires_at, consumed_at, created_at) "
            "VALUES (1, 'legacy-challenge-1', 'usr-alice', 'legacy-token-hash', "
            ":expires_at, :consumed_at, :created_at)"
        ),
        {
            "expires_at": datetime(2026, 9, 17, 9, 0, tzinfo=UTC),
            "consumed_at": datetime(2026, 9, 17, 8, 45, tzinfo=UTC),
            "created_at": datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
        },
    )


def _operations(conn) -> Operations:
    context = MigrationContext.configure(conn, opts={"render_as_batch": True})
    return Operations(context)


def _upgrade(conn, monkeypatch) -> None:
    monkeypatch.setattr(MIG, "op", _operations(conn))
    MIG.upgrade()


def test_0033_revision_chain() -> None:
    assert MIG.revision == "0033"
    assert MIG.down_revision == "0032"


def test_0033_upgrade_creates_tables_columns_and_constraints(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)

        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        assert {
            "v2_user_notification_binding",
            "v2_notification_binding_challenge",
        } <= tables

        binding_columns = {column["name"]: column for column in inspector.get_columns(
            "v2_user_notification_binding"
        )}
        assert set(binding_columns) == {
            "id", "binding_id", "user_id", "channel", "recipient_id", "status",
            "bound_at", "revoked_at", "created_at", "updated_at",
        }
        assert binding_columns["status"]["nullable"] is False
        binding_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("v2_user_notification_binding")
        }
        assert ("user_id", "channel") in binding_unique_constraints
        assert ("channel", "recipient_id") in binding_unique_constraints
        binding_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("v2_user_notification_binding")
        }
        binding_id_index = binding_indexes[
            "ix_v2_user_notification_binding_binding_id"
        ]
        assert bool(binding_id_index["unique"]) is True
        assert tuple(binding_id_index["column_names"]) == ("binding_id",)
        assert ("binding_id",) not in binding_unique_constraints
        assert sum(
            tuple(index["column_names"]) == ("binding_id",)
            for index in binding_indexes.values()
        ) == 1
        assert any(
            constraint["name"] == "ck_v2_notification_binding_status"
            for constraint in inspector.get_check_constraints("v2_user_notification_binding")
        )

        challenge_columns = {column["name"]: column for column in inspector.get_columns(
            "v2_notification_binding_challenge"
        )}
        assert set(challenge_columns) == {
            "id", "challenge_id", "user_id", "channel", "token_hash", "provider_code",
            "expires_at", "consumed_at", "created_at",
        }
        assert challenge_columns["provider_code"]["nullable"] is True
        challenge_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "v2_notification_binding_challenge"
            )
        }
        assert ("channel", "provider_code") in challenge_unique_constraints
        challenge_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("v2_notification_binding_challenge")
        }
        for index_name, column_name in (
            ("ix_v2_notification_binding_challenge_challenge_id", "challenge_id"),
            ("ix_v2_notification_binding_challenge_token_hash", "token_hash"),
        ):
            index = challenge_indexes[index_name]
            assert bool(index["unique"]) is True
            assert tuple(index["column_names"]) == (column_name,)
            assert (column_name,) not in challenge_unique_constraints
            assert sum(
                tuple(candidate["column_names"]) == (column_name,)
                for candidate in challenge_indexes.values()
            ) == 1

        preference_columns = {column["name"]: column for column in inspector.get_columns(
            "v2_user_notification_preference"
        )}
        for name in (
            "signal_enabled",
            "fill_enabled",
            "morning_report_enabled",
            "evening_report_enabled",
        ):
            assert preference_columns[name]["nullable"] is False
            assert preference_columns[name]["default"] is not None

        preference = conn.execute(
            sa.text(
                "SELECT signal_enabled, fill_enabled, morning_report_enabled, "
                "evening_report_enabled FROM v2_user_notification_preference "
                "WHERE preference_id = 'pref-alice'"
            )
        ).mappings().one()
        assert all(bool(value) for value in preference.values())

        # A normal UNIQUE(channel, provider_code) permits multiple NULL provider codes
        # on both PostgreSQL and SQLite; no partial-index semantics are introduced.
        conn.execute(
            sa.text(
                "INSERT INTO v2_notification_binding_challenge "
                "(challenge_id, user_id, channel, token_hash, provider_code, expires_at, "
                "consumed_at, created_at) VALUES ('nchallenge-second-null', 'usr-alice', "
                "'wxpusher', 'second-null-hash', NULL, :now, NULL, :now)"
            ),
            {"now": _NOW},
        )


def test_0033_upgrade_backfills_trimmed_telegram_binding(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)

        rows = conn.execute(
            sa.text(
                "SELECT binding_id, user_id, channel, recipient_id, status, bound_at "
                "FROM v2_user_notification_binding"
            )
        ).mappings().all()
        assert len(rows) == 1
        row = rows[0]
        digest = sha256(b"usr-alice:telegram").hexdigest()[:24]
        assert row["binding_id"] == f"nbind-{digest}"
        assert row["user_id"] == "usr-alice"
        assert row["channel"] == "telegram"
        assert row["recipient_id"] == "12345678"
        assert row["status"] == "active"
        assert row["bound_at"] is not None


def test_0033_upgrade_copies_legacy_challenge_with_stable_distinct_id(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)

        row = conn.execute(
            sa.text(
                "SELECT challenge_id, user_id, channel, token_hash, provider_code, "
                "expires_at, consumed_at, created_at "
                "FROM v2_notification_binding_challenge"
            )
        ).mappings().one()
        digest = sha256(b"telegram:legacy-challenge-1").hexdigest()[:24]
        assert row["challenge_id"] == f"nchallenge-{digest}"
        assert not row["challenge_id"].startswith("nbind-")
        assert row["user_id"] == "usr-alice"
        assert row["channel"] == "telegram"
        assert row["token_hash"] == "legacy-token-hash"
        assert row["provider_code"] is None
        assert str(row["expires_at"]).startswith("2026-09-17 09:00:00")
        assert str(row["consumed_at"]).startswith("2026-09-17 08:45:00")
        assert str(row["created_at"]).startswith("2026-09-17 08:00:00")


def test_0033_duplicate_failure_leaves_no_ddl_and_is_retryable(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    secret_recipient = "sensitive-recipient-987654"
    # Prerequisites and duplicate rows are committed before the failing migration call.
    with engine.begin() as conn:
        _setup_prereqs(conn)
        conn.execute(
            sa.text(
                "INSERT INTO v2_user (id, user_id, telegram_chat_id) VALUES "
                "(1, 'usr-one', :first), (2, 'usr-two', :second)"
            ),
            {"first": secret_recipient, "second": f"  {secret_recipient}  "},
        )

    with engine.connect() as conn:
        monkeypatch.setattr(MIG, "op", _operations(conn))
        with pytest.raises(RuntimeError) as exc_info:
            MIG.upgrade()

    message = str(exc_info.value)
    assert "1 duplicate" in message
    assert secret_recipient not in message
    assert "usr-one" not in message
    assert "usr-two" not in message

    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        assert "v2_user_notification_binding" not in tables
        assert "v2_notification_binding_challenge" not in tables
        assert not {name for name in tables if name.startswith("_alembic_tmp_")}
        preference_columns = {
            column["name"]
            for column in inspector.get_columns("v2_user_notification_preference")
        }
        assert not {
            "signal_enabled",
            "fill_enabled",
            "morning_report_enabled",
            "evening_report_enabled",
        } & preference_columns

    # Once ownership is corrected, the same database can be upgraded successfully.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE v2_user SET telegram_chat_id = 'different-recipient' "
                "WHERE user_id = 'usr-two'"
            )
        )
        _upgrade(conn, monkeypatch)
        rows = conn.execute(
            sa.text(
                "SELECT user_id, recipient_id FROM v2_user_notification_binding "
                "ORDER BY user_id"
            )
        ).all()
        assert rows == [
            ("usr-one", secret_recipient),
            ("usr-two", "different-recipient"),
        ]


def test_0033_constraints_reject_duplicate_binding_ownership(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)
        conn.execute(
            sa.text(
                "INSERT INTO v2_user (id, user_id, telegram_chat_id) "
                "VALUES (4, 'usr-bob', NULL)"
            )
        )
        params = {
            "binding_id": "nbind-conflict",
            "user_id": "usr-bob",
            "channel": "telegram",
            "recipient_id": "12345678",
            "status": "active",
            "now": _NOW,
        }
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO v2_user_notification_binding "
                    "(binding_id, user_id, channel, recipient_id, status, bound_at, "
                    "created_at, updated_at) VALUES (:binding_id, :user_id, :channel, "
                    ":recipient_id, :status, :now, :now, :now)"
                ),
                params,
            )


def test_0033_downgrade_removes_only_0033_objects(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)
        MIG.downgrade()

        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        assert "v2_user_notification_binding" not in tables
        assert "v2_notification_binding_challenge" not in tables
        assert {
            "v2_user",
            "v2_user_notification_preference",
            "v2_telegram_binding_challenge",
            "v2_notification_delivery",
        } <= tables
        preference_columns = {
            column["name"]
            for column in inspector.get_columns("v2_user_notification_preference")
        }
        assert not {
            "signal_enabled",
            "fill_enabled",
            "morning_report_enabled",
            "evening_report_enabled",
        } & preference_columns
        assert conn.execute(
            sa.text("SELECT COUNT(*) FROM v2_telegram_binding_challenge")
        ).scalar_one() == 1
        assert conn.execute(sa.text("SELECT COUNT(*) FROM v2_user")).scalar_one() == 3
