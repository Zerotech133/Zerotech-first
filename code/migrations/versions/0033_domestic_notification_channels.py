"""domestic notification channels: generic bindings and challenges (0033).

Existing Telegram chat identifiers are copied into the generic binding table. Because
``v2_user.telegram_chat_id`` has no historical binding timestamp, ``bound_at`` records
the UTC migration time rather than claiming to be the original Telegram binding time.
"""
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BINDING_TABLE = "v2_user_notification_binding"
_CHALLENGE_TABLE = "v2_notification_binding_challenge"
_PREFERENCE_TABLE = "v2_user_notification_preference"
_PREFERENCE_COLUMNS = (
    "signal_enabled",
    "fill_enabled",
    "morning_report_enabled",
    "evening_report_enabled",
)


def _binding_bulk_table() -> sa.TableClause:
    return sa.table(
        _BINDING_TABLE,
        sa.column("binding_id", sa.String(64)),
        sa.column("user_id", sa.String(64)),
        sa.column("channel", sa.String(32)),
        sa.column("recipient_id", sa.String(128)),
        sa.column("status", sa.String(16)),
        sa.column("bound_at", sa.DateTime(timezone=True)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _challenge_bulk_table() -> sa.TableClause:
    return sa.table(
        _CHALLENGE_TABLE,
        sa.column("challenge_id", sa.String(64)),
        sa.column("user_id", sa.String(64)),
        sa.column("channel", sa.String(32)),
        sa.column("token_hash", sa.String(128)),
        sa.column("provider_code", sa.String(128)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("consumed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def _legacy_telegram_rows() -> list[dict[str, object]]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT user_id, telegram_chat_id FROM v2_user "
            "WHERE telegram_chat_id IS NOT NULL"
        )
    ).mappings()
    return [dict(row) for row in rows]


def _normalized_telegram_bindings() -> list[tuple[str, str]]:
    bindings: list[tuple[str, str]] = []
    recipient_counts: dict[str, int] = {}
    for row in _legacy_telegram_rows():
        recipient = str(row["telegram_chat_id"] or "").strip()
        if not recipient:
            continue
        bindings.append((str(row["user_id"]), recipient))
        recipient_counts[recipient] = recipient_counts.get(recipient, 0) + 1

    duplicate_count = sum(count > 1 for count in recipient_counts.values())
    if duplicate_count:
        raise RuntimeError(
            "aborting revision 0033: v2_user.telegram_chat_id has "
            f"{duplicate_count} duplicate trimmed recipient value(s); "
            "resolve recipient ownership before upgrading"
        )
    return bindings


def _backfill_telegram_bindings(bindings: list[tuple[str, str]]) -> None:
    if not bindings:
        return

    # The legacy column has no bound_at, so migration time is the first truthful
    # timestamp available for both bound_at and row audit timestamps.
    now = datetime.now(UTC)
    values: list[dict[str, object]] = []
    for user_id, recipient in bindings:
        digest = sha256(f"{user_id}:telegram".encode()).hexdigest()[:24]
        values.append(
            {
                "binding_id": f"nbind-{digest}",
                "user_id": user_id,
                "channel": "telegram",
                "recipient_id": recipient,
                "status": "active",
                "bound_at": now,
                "revoked_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    op.bulk_insert(_binding_bulk_table(), values)


def _copy_legacy_telegram_challenges() -> None:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT challenge_id, user_id, token_hash, expires_at, consumed_at, created_at "
            "FROM v2_telegram_binding_challenge"
        ).columns(
            expires_at=sa.DateTime(timezone=True),
            consumed_at=sa.DateTime(timezone=True),
            created_at=sa.DateTime(timezone=True),
        )
    ).mappings()
    values: list[dict[str, object]] = []
    for row in rows:
        legacy_challenge_id = str(row["challenge_id"])
        digest = sha256(f"telegram:{legacy_challenge_id}".encode()).hexdigest()[:24]
        values.append(
            {
                "challenge_id": f"nchallenge-{digest}",
                "user_id": row["user_id"],
                "channel": "telegram",
                "token_hash": row["token_hash"],
                "provider_code": None,
                "expires_at": row["expires_at"],
                "consumed_at": row["consumed_at"],
                "created_at": row["created_at"],
            }
        )
    if values:
        op.bulk_insert(_challenge_bulk_table(), values)


def upgrade() -> None:
    # SQLite cannot transactionally roll back all DDL/batch-table rebuilds. Validate
    # legacy ownership before the first DDL so a rejected migration is cleanly retryable.
    telegram_bindings = _normalized_telegram_bindings()

    op.create_table(
        _BINDING_TABLE,
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("binding_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_notification_binding_user_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("recipient_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_v2_notification_binding_status",
        ),
        sa.UniqueConstraint(
            "user_id", "channel", name="uq_v2_notification_binding_user_channel"
        ),
        sa.UniqueConstraint(
            "channel", "recipient_id", name="uq_v2_notification_binding_recipient"
        ),
    )
    op.create_index(
        "ix_v2_user_notification_binding_binding_id",
        _BINDING_TABLE,
        ["binding_id"],
        unique=True,
    )
    op.create_index(
        "ix_v2_user_notification_binding_user_id", _BINDING_TABLE, ["user_id"]
    )
    op.create_index(
        "ix_v2_user_notification_binding_channel", _BINDING_TABLE, ["channel"]
    )

    op.create_table(
        _CHALLENGE_TABLE,
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("challenge_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_notification_challenge_user_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("provider_code", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "channel",
            "provider_code",
            name="uq_v2_notification_challenge_provider_code",
        ),
    )
    op.create_index(
        "ix_v2_notification_binding_challenge_challenge_id",
        _CHALLENGE_TABLE,
        ["challenge_id"],
        unique=True,
    )
    op.create_index(
        "ix_v2_notification_binding_challenge_user_id", _CHALLENGE_TABLE, ["user_id"]
    )
    op.create_index(
        "ix_v2_notification_binding_challenge_channel", _CHALLENGE_TABLE, ["channel"]
    )
    op.create_index(
        "ix_v2_notification_binding_challenge_token_hash",
        _CHALLENGE_TABLE,
        ["token_hash"],
        unique=True,
    )

    with op.batch_alter_table(_PREFERENCE_TABLE) as batch_op:
        for column_name in _PREFERENCE_COLUMNS:
            batch_op.add_column(
                sa.Column(
                    column_name,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("true"),
                )
            )

    _backfill_telegram_bindings(telegram_bindings)
    _copy_legacy_telegram_challenges()


def downgrade() -> None:
    with op.batch_alter_table(_PREFERENCE_TABLE) as batch_op:
        for column_name in reversed(_PREFERENCE_COLUMNS):
            batch_op.drop_column(column_name)

    op.drop_index(
        "ix_v2_notification_binding_challenge_token_hash",
        table_name=_CHALLENGE_TABLE,
    )
    op.drop_index(
        "ix_v2_notification_binding_challenge_channel", table_name=_CHALLENGE_TABLE
    )
    op.drop_index(
        "ix_v2_notification_binding_challenge_user_id", table_name=_CHALLENGE_TABLE
    )
    op.drop_index(
        "ix_v2_notification_binding_challenge_challenge_id",
        table_name=_CHALLENGE_TABLE,
    )
    op.drop_table(_CHALLENGE_TABLE)

    op.drop_index(
        "ix_v2_user_notification_binding_channel", table_name=_BINDING_TABLE
    )
    op.drop_index(
        "ix_v2_user_notification_binding_user_id", table_name=_BINDING_TABLE
    )
    op.drop_index(
        "ix_v2_user_notification_binding_binding_id", table_name=_BINDING_TABLE
    )
    op.drop_table(_BINDING_TABLE)
