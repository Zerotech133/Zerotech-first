"""telegram binding & notification outbox (Phase C Task 2)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-07

Telegram 定向通知 Phase C Task 2：新建 challenge / preference / event / delivery
四张表 + v2_user.telegram_chat_id 唯一索引。纯 additive，不改任何现有表结构与行为
（安全铁律：broadcast 广播现状不变，本 schema 只为后续 directed 通知铺路）。

表：
- v2_telegram_binding_challenge：一次性 deep-link 绑定验证。只存 token 的 SHA-256
  哈希，绝不存明文 token。
- v2_user_notification_preference：用户级通知偏好，unique(user_id, channel)。
- v2_notification_event：outbox 上游事件。payload 为通用 JSON，约定**绝不**写入
  chat_id / token 等敏感数据（schema 层不设列，约束写死在此注释与 ORM docstring）。
- v2_notification_delivery：outbox 下游投递。unique(event_id, user_id, channel) 为
  幂等锚点；复合索引 (status, next_attempt_at) 供 worker 认领扫描；CHECK
  ck_v2_notification_delivery_status 限定 PENDING/SENDING/RETRY/SENT/FAILED；
  SENDING 滞留超 5 分钟（claimed_at 超时）由 worker 回收为 RETRY（服务层职责）。

chat_id 唯一索引 uq_v2_user_telegram_chat_id：一个 Telegram chat 只能绑一个用户。
**建索引前先查重复**——若存在重复 telegram_chat_id 则中止迁移并抛出清晰错误（列出
重复值个数），绝不静默选择归属；解决数据后重跑。未绑定（NULL）不受唯一索引限制。

upgrade/downgrade 对称：downgrade 先删子表（delivery→event）再删独立表
（preference/challenge），最后删 v2_user 上的唯一索引。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _abort_on_duplicate_telegram_chat_id() -> None:
    """建唯一索引前检查重复 chat_id：有重复即中止，绝不静默选一个归属。"""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT telegram_chat_id, COUNT(*) AS n FROM v2_user "
            "WHERE telegram_chat_id IS NOT NULL "
            "GROUP BY telegram_chat_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if rows:
        dup_list = ", ".join(f"{row.telegram_chat_id}x{row.n}users" for row in rows)
        raise RuntimeError(
            f"aborting revision 0023: v2_user.telegram_chat_id has "
            f"{len(rows)} duplicated value(s) [{dup_list}]; "
            "resolve chat ownership manually before upgrading "
            "(migration will never silently pick one owner)"
        )


def upgrade() -> None:
    _abort_on_duplicate_telegram_chat_id()

    # ── 1. v2_telegram_binding_challenge ─────────────────────────────────────
    op.create_table(
        "v2_telegram_binding_challenge",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("challenge_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_tg_challenge_user_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_v2_telegram_binding_challenge_challenge_id",
        "v2_telegram_binding_challenge",
        ["challenge_id"],
    )
    op.create_index(
        "ix_v2_telegram_binding_challenge_user_id",
        "v2_telegram_binding_challenge",
        ["user_id"],
    )
    op.create_index(
        "ix_v2_telegram_binding_challenge_token_hash",
        "v2_telegram_binding_challenge",
        ["token_hash"],
    )

    # ── 2. v2_user_notification_preference ───────────────────────────────────
    op.create_table(
        "v2_user_notification_preference",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("preference_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_user_notif_pref_user_id"),
            nullable=False,
        ),
        sa.Column(
            "channel", sa.String(32), nullable=False, server_default="telegram"
        ),
        sa.Column(
            "market_reports_enabled",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "system_alerts_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("quiet_hours", sa.JSON(), nullable=True),
        sa.Column(
            "timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"
        ),
        sa.Column("locale", sa.String(8), nullable=False, server_default="zh"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "channel",
            name="uq_v2_user_notification_preference_user_channel",
        ),
    )
    op.create_index(
        "ix_v2_user_notification_preference_preference_id",
        "v2_user_notification_preference",
        ["preference_id"],
    )
    op.create_index(
        "ix_v2_user_notification_preference_user_id",
        "v2_user_notification_preference",
        ["user_id"],
    )

    # ── 3. v2_notification_event ─────────────────────────────────────────────
    op.create_table(
        "v2_notification_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_strategy_id",
            sa.String(64),
            sa.ForeignKey(
                "v2_user_strategy_instance.user_strategy_id",
                name="fk_v2_notification_event_user_strategy_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey(
                "v2_user_account.account_id",
                name="fk_v2_notification_event_account_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_notification_event_user_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_v2_notification_event_event_id", "v2_notification_event", ["event_id"]
    )
    op.create_index(
        "ix_v2_notification_event_user_strategy_id",
        "v2_notification_event",
        ["user_strategy_id"],
    )
    op.create_index(
        "ix_v2_notification_event_account_id", "v2_notification_event", ["account_id"]
    )
    op.create_index(
        "ix_v2_notification_event_user_id", "v2_notification_event", ["user_id"]
    )
    op.create_index(
        "ix_v2_notification_event_event_type", "v2_notification_event", ["event_type"]
    )

    # ── 4. v2_notification_delivery ──────────────────────────────────────────
    op.create_table(
        "v2_notification_delivery",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("delivery_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "event_id",
            sa.String(64),
            sa.ForeignKey(
                "v2_notification_event.event_id",
                name="fk_v2_notification_delivery_event_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id", name="fk_v2_notification_delivery_user_id"),
            nullable=False,
        ),
        sa.Column(
            "channel", sa.String(32), nullable=False, server_default="telegram"
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','SENDING','RETRY','SENT','FAILED')",
            name="ck_v2_notification_delivery_status",
        ),
        sa.UniqueConstraint(
            "event_id", "user_id", "channel",
            name="uq_v2_notification_delivery_event_user_channel",
        ),
    )
    op.create_index(
        "ix_v2_notification_delivery_delivery_id",
        "v2_notification_delivery",
        ["delivery_id"],
    )
    op.create_index(
        "ix_v2_notification_delivery_event_id",
        "v2_notification_delivery",
        ["event_id"],
    )
    op.create_index(
        "ix_v2_notification_delivery_user_id",
        "v2_notification_delivery",
        ["user_id"],
    )
    op.create_index(
        "ix_v2_notification_delivery_status", "v2_notification_delivery", ["status"]
    )
    # worker claim 扫描专用复合索引（status, next_attempt_at）。
    op.create_index(
        "ix_v2_notification_delivery_status_next_attempt",
        "v2_notification_delivery",
        ["status", "next_attempt_at"],
    )

    # ── 5. v2_user.telegram_chat_id 唯一索引 ─────────────────────────────────
    op.create_index(
        "uq_v2_user_telegram_chat_id", "v2_user", ["telegram_chat_id"], unique=True
    )


def downgrade() -> None:
    # 与 upgrade 严格对称、逆序：先子表（delivery→event），再独立表，最后删用户索引。
    op.drop_index("uq_v2_user_telegram_chat_id", table_name="v2_user")
    op.drop_index(
        "ix_v2_notification_delivery_status_next_attempt",
        table_name="v2_notification_delivery",
    )
    op.drop_index(
        "ix_v2_notification_delivery_status", table_name="v2_notification_delivery"
    )
    op.drop_index(
        "ix_v2_notification_delivery_user_id", table_name="v2_notification_delivery"
    )
    op.drop_index(
        "ix_v2_notification_delivery_event_id", table_name="v2_notification_delivery"
    )
    op.drop_index(
        "ix_v2_notification_delivery_delivery_id", table_name="v2_notification_delivery"
    )
    op.drop_table("v2_notification_delivery")
    op.drop_index(
        "ix_v2_notification_event_event_type", table_name="v2_notification_event"
    )
    op.drop_index(
        "ix_v2_notification_event_user_id", table_name="v2_notification_event"
    )
    op.drop_index(
        "ix_v2_notification_event_account_id", table_name="v2_notification_event"
    )
    op.drop_index(
        "ix_v2_notification_event_user_strategy_id", table_name="v2_notification_event"
    )
    op.drop_index(
        "ix_v2_notification_event_event_id", table_name="v2_notification_event"
    )
    op.drop_table("v2_notification_event")
    op.drop_index(
        "ix_v2_user_notification_preference_user_id",
        table_name="v2_user_notification_preference",
    )
    op.drop_index(
        "ix_v2_user_notification_preference_preference_id",
        table_name="v2_user_notification_preference",
    )
    op.drop_table("v2_user_notification_preference")
    op.drop_index(
        "ix_v2_telegram_binding_challenge_token_hash",
        table_name="v2_telegram_binding_challenge",
    )
    op.drop_index(
        "ix_v2_telegram_binding_challenge_user_id",
        table_name="v2_telegram_binding_challenge",
    )
    op.drop_index(
        "ix_v2_telegram_binding_challenge_challenge_id",
        table_name="v2_telegram_binding_challenge",
    )
    op.drop_table("v2_telegram_binding_challenge")
