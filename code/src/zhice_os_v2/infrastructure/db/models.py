"""SQLAlchemy ORM 模型（V2 独立表结构）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from zhice_os_v2.infrastructure.db.base import Base


class StrategyModel(Base):
    __tablename__ = "v2_strategy"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('PUBLIC','PRIVATE')", name="ck_v2_strategy_visibility"
        ),
        CheckConstraint(
            "publication_status IN "
            "('PRIVATE','PENDING_REVIEW','PUBLISHED','REJECTED')",
            name="ck_v2_strategy_publication",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    market: Mapped[str] = mapped_column(String(16), index=True)
    strategy_type: Mapped[str] = mapped_column(String(64), default="V2_EXTERNAL")
    status: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(String(512), default="")
    # 归属/可见性/发布态（多用户策略地基；owner=NULL 即平台公共策略）。
    owner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="PUBLIC")
    publication_status: Mapped[str] = mapped_column(String(24), default="PUBLISHED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StrategyPublicationRequestModel(Base):
    """私有策略发布申请：审核通过在同事务派生平台公共策略/版本并回写 derived_strategy_id。"""

    __tablename__ = "v2_strategy_publication_request"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_v2_publication_status",
        ),
        # 同一 source 只能有一条 PENDING 申请（PG/SQLite 部分唯一索引）。
        Index(
            "uq_v2_publication_pending_source",
            "source_strategy_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_strategy_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_strategy.strategy_id"), index=True
    )
    requester_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), index=True, default="PENDING")
    reviewed_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user.user_id")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    derived_strategy_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_strategy.strategy_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserAccountModel(Base):
    """持久化用户账户：独立资金/风控账本，只属于一个用户，固定一个市场与本位币。"""

    __tablename__ = "v2_user_account"
    __table_args__ = (
        CheckConstraint(
            "account_type IN ('SHARED_PORTFOLIO','STRATEGY_DEDICATED')",
            name="ck_v2_user_account_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','FROZEN','CLOSED')",
            name="ck_v2_user_account_status",
        ),
        CheckConstraint("initial_capital > 0", name="ck_v2_user_account_initial_capital"),
        CheckConstraint("cash_balance >= 0", name="ck_v2_user_account_cash_balance"),
        CheckConstraint("reserved_cash >= 0", name="ck_v2_user_account_reserved_cash"),
        Index("ix_v2_user_account_user_market", "user_id", "market"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    account_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    market: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(16))
    account_type: Mapped[str] = mapped_column(String(24))
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    reserved_cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), server_default="0")
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserStrategyInstanceModel(Base):
    """用户策略运行实例：某用户在某账户上运行某具体策略版本的边界。

    `enabled` 才表示是否实际运行（Strategy/Version.status 只表示发布/治理）。同一账户内
    禁止重复启用同一版本，由部分唯一索引 uq_v2_user_strategy_active_account_version
    （enabled IS TRUE）+ 服务预检双保险。
    """

    __tablename__ = "v2_user_strategy_instance"
    __table_args__ = (
        CheckConstraint(
            "execution_mode IN ('MANUAL','SEMI_AUTO','FULL_AUTO')",
            name="ck_v2_user_strategy_execution_mode",
        ),
        CheckConstraint(
            "max_position_ratio > 0 AND max_position_ratio <= 1",
            name="ck_v2_user_strategy_max_position_ratio",
        ),
        Index("ix_v2_user_strategy_user_enabled", "user_id", "enabled"),
        Index("ix_v2_user_strategy_version", "strategy_version_id"),
        Index("ix_v2_user_strategy_account", "account_id"),
        # 同一账户同一版本最多一个启用实例（PG/SQLite 部分唯一索引）。
        Index(
            "uq_v2_user_strategy_active_account_version",
            "account_id",
            "strategy_version_id",
            unique=True,
            postgresql_where=text("enabled IS TRUE"),
            sqlite_where=text("enabled IS TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_strategy_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    strategy_version_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_strategy_version.strategy_version_id")
    )
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user_account.account_id")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    auto_execute: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    execution_mode: Mapped[str] = mapped_column(String(16), server_default="MANUAL")
    capital_quota: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    max_position_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), server_default="0.1"
    )
    risk_parameters: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    strategy_parameters: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    notification_preferences: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    upgrade_available_version_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_strategy_version.strategy_version_id")
    )
    pending_upgrade_version_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_strategy_version.strategy_version_id")
    )
    upgrade_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountReservationModel(Base):
    """账户资金预留台账（两阶段资金语义）。

    reserve 冻结（reserved_cash↑，RESERVED）；consume 实扣（reserved_cash↓ +
    cash_balance↓，CONSUMED）；release 解冻（reserved_cash↓，RELEASED）。所有写路径在
    v2_user_account 行 FOR UPDATE 锁内串行化，保证并发下可用现金与配额检查原子。
    idempotency_key 唯一防同一业务动作重复预留/扣减。
    """

    __tablename__ = "v2_account_reservation"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_v2_account_reservation_amount"),
        CheckConstraint(
            "status IN ('RESERVED','CONSUMED','RELEASED')",
            name="ck_v2_account_reservation_status",
        ),
        Index("ix_v2_account_reservation_account_id", "account_id"),
        Index("ix_v2_account_reservation_user_strategy_id", "user_strategy_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    reservation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user_account.account_id")
    )
    user_strategy_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user_strategy_instance.user_strategy_id")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(16), server_default="RESERVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccessAuditModel(Base):
    """管理员全局访问审计：记录谁(actor)对哪类资源做了什么全局查看动作。

    只存元数据（actor_user_id/action/resource_type/resource_id/request_id/created_at），
    **绝不**存密码/Token/敏感响应正文。索引 (actor_user_id, created_at) 与
    (resource_type, resource_id) 支撑"按操作者时序"与"按资源"两类审计检索。
    """

    __tablename__ = "v2_access_audit"
    __table_args__ = (
        Index("ix_v2_access_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_v2_access_audit_resource", "resource_type", "resource_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    audit_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    actor_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelegramBindingChallengeModel(Base):
    """一次性 Telegram 绑定 challenge（deep-link 验证）。

    只存 token 的 SHA-256 哈希（token_hash unique），**绝不**存明文 token。同一用户
    发新 challenge 即作废旧未消费行（服务层负责）；consumed_at 非空表示已被 webhook
    消费，不可重放。生命周期实现见 telegram_binding_service（Phase C Task 3）。
    """

    __tablename__ = "v2_telegram_binding_challenge"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    challenge_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserNotificationBindingModel(Base):
    """渠道无关的用户通知绑定；每用户每渠道及每渠道收件人均唯一。"""

    __tablename__ = "v2_user_notification_binding"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_v2_notification_binding_status",
        ),
        UniqueConstraint(
            "user_id", "channel", name="uq_v2_notification_binding_user_channel"
        ),
        UniqueConstraint(
            "channel", "recipient_id", name="uq_v2_notification_binding_recipient"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    binding_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    recipient_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationBindingChallengeModel(Base):
    """渠道无关的一次性绑定 challenge，只持久化 token 哈希。"""

    __tablename__ = "v2_notification_binding_challenge"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "provider_code",
            name="uq_v2_notification_challenge_provider_code",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    challenge_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserNotificationPreferenceModel(Base):
    """用户级通知偏好（每用户每渠道一行，unique(user_id, channel)）。

    market_reports_enabled：按市场开关（如 {"CN": true, "US": false}）；
    system_alerts_enabled：系统级告警总开关；quiet_hours：静默时段（如
    {"start": "23:00", "end": "07:00"}）；timezone/locale 决定渲染时区与语言。
    偏好读写服务见 notification_preference_service（Phase C Task 4）。
    """

    __tablename__ = "v2_user_notification_preference"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "channel", name="uq_v2_user_notification_preference_user_channel"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    preference_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), server_default="telegram")
    market_reports_enabled: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    system_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    signal_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    fill_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    morning_report_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    evening_report_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    quiet_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Shanghai")
    locale: Mapped[str] = mapped_column(String(8), server_default="zh")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationEventModel(Base):
    """通知事件（outbox 上游）：交易事务内落库的业务事件。

    user_strategy_id / account_id / user_id 定位事件归属（nullable 归属列供全局
    事件如系统告警留空 user_strategy_id）。payload 为通用 JSON，**绝不**写入
    chat_id / token 等敏感数据（约定约束，见迁移 0023 注释）。事件入队服务见
    notification_outbox_service（Phase C Task 5）。
    """

    __tablename__ = "v2_notification_event"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_strategy_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user_strategy_instance.user_strategy_id"), index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user_account.account_id"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryModel(Base):
    """通知投递（outbox 下游）：worker claim/send/retry 的执行单元。

    状态机：PENDING→SENDING→SENT；失败→RETRY（指数退避，next_attempt_at）→…
    →FAILED。unique(event_id, user_id, channel) 是 outbox 幂等锚点（同事件同用户
    同渠道最多一条）；复合索引 (status, next_attempt_at) 支撑 worker 认领扫描；
    SENDING 滞留超 5 分钟（claimed_at 超时）由 worker 回收为 RETRY。last_error_code
    只存稳定错误码，**绝不**存含 chat_id/token 的原始响应。worker 见
    notification_worker（Phase C Task 7）。
    """

    __tablename__ = "v2_notification_delivery"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','SENDING','RETRY','SENT','FAILED')",
            name="ck_v2_notification_delivery_status",
        ),
        UniqueConstraint(
            "event_id", "user_id", "channel",
            name="uq_v2_notification_delivery_event_user_channel",
        ),
        Index(
            "ix_v2_notification_delivery_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_notification_event.event_id"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), server_default="telegram")
    status: Mapped[str] = mapped_column(String(16), index=True, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertModel(Base):
    __tablename__ = "v2_alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), index=True, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrategyVersionModel(Base):
    __tablename__ = "v2_strategy_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_strategy.strategy_id"), index=True)
    version_no: Mapped[str] = mapped_column(String(32))
    market: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    spec: Mapped[dict] = mapped_column(JSON)
    research_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRequestModel(Base):
    __tablename__ = "v2_approval_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_intent_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    token: Mapped[str] = mapped_column(String(128))
    auto_follow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # 运行时归属（Phase B 多用户执行隔离，迁移 0021）。nullable，legacy 行为为 NULL。
    user_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class PositionModel(Base):
    __tablename__ = "v2_position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    open_qty: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    open_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    close_qty: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # V2 production data alignment
    trade_plan_id: Mapped[str | None] = mapped_column(String(64), index=True)
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pnl_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    # 成交手续费（买入 lot = 全额；平仓留痕 lot = 买入比例费 + 卖出费）。
    fee: Mapped[Decimal] = mapped_column(Numeric(24, 8), server_default="0")
    # 运行时归属（Phase B 多用户执行隔离，迁移 0021）。nullable，legacy 行为为 NULL。
    user_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class ReplayModel(Base):
    __tablename__ = "v2_replay"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    replay_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32))
    pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    pnl_pct: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    holding_period_seconds: Mapped[int] = mapped_column(BigInteger)
    exit_reason: Mapped[str] = mapped_column(String(32))
    attribution: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # V2 production data alignment
    open_date: Mapped[date | None] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    open_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    review_status: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str | None] = mapped_column(String(32))
    # 运行时归属（Phase B 多用户执行隔离，迁移 0021）。nullable，legacy 行为为 NULL。
    user_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class InstrumentModel(Base):
    __tablename__ = "v2_instrument"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128), default="")
    lot_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BarDailyModel(Base):
    __tablename__ = "v2_bar_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    source: Mapped[str] = mapped_column(String(32), default="v1_import")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketCalendarModel(Base):
    __tablename__ = "v2_market_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BarMinuteModel(Base):
    __tablename__ = "v2_bar_minute"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    source: Mapped[str] = mapped_column(String(32), default="v1_import")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LatestSnapshotModel(Base):
    """盘中实时快照（V1 fact_latest_snapshot 同步而来）：每标的一行 current。"""
    __tablename__ = "v2_latest_snapshot"
    __table_args__ = (UniqueConstraint("market", "symbol", name="uq_v2_snapshot_market_symbol"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    bid1: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    ask1: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="v1_sync")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FundamentalModel(Base):
    __tablename__ = "v2_fundamental"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    ann_date: Mapped[date | None] = mapped_column(Date)
    roe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe_dt: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    debt_to_assets: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    netprofit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    profit_dedt_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConceptModel(Base):
    __tablename__ = "v2_concept"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    concept_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    concept_name: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), default="v1_import")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConceptMembershipModel(Base):
    __tablename__ = "v2_concept_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    concept_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Production data tables (V2 承接 V1 完整生产数据)
# ---------------------------------------------------------------------------

class CandidatePoolModel(Base):
    __tablename__ = "v2_candidate_pool"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    pool_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    pool_date: Mapped[date] = mapped_column(Date, index=True)
    pool_type: Mapped[str] = mapped_column(String(32))
    batch_no: Mapped[int] = mapped_column(Integer, default=1)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateAssetModel(Base):
    __tablename__ = "v2_candidate_asset"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    candidate_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pool_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    candidate_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    candidate_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TradePlanModel(Base):
    __tablename__ = "v2_trade_plan"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    plan_status: Mapped[str] = mapped_column(String(32), index=True)
    ref_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    entry_conditions: Mapped[dict | None] = mapped_column(JSON)
    exit_conditions: Mapped[dict | None] = mapped_column(JSON)
    invalidation_conditions: Mapped[dict | None] = mapped_column(JSON)
    risk_budget: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    max_position_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    confidence_level: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 运行时归属（Phase B 多用户执行隔离，迁移 0021）。nullable，legacy 行为为 NULL。
    user_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class OrderIntentModel(Base):
    __tablename__ = "v2_order_intent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    intent_type: Mapped[str] = mapped_column(String(32))
    intent_side: Mapped[str] = mapped_column(String(16))
    intent_qty: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    intent_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(32), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 运行时归属（Phase B 多用户执行隔离，迁移 0021）。nullable，legacy 行为为 NULL。
    # request/execution/fill 经 intent 解析归属（Task 2），故不在此三表加列。
    user_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)


class OrderRequestModel(Base):
    __tablename__ = "v2_order_request"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    intent_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    broker_channel: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionRecordModel(Base):
    __tablename__ = "v2_execution_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128))
    execution_status: Mapped[str] = mapped_column(String(32), index=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FillRecordModel(Base):
    __tablename__ = "v2_fill_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fill_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    execution_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    broker_trade_id: Mapped[str | None] = mapped_column(String(128))
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    fill_qty: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    fill_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    fill_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    sell_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RiskCheckModel(Base):
    __tablename__ = "v2_risk_check"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    check_stage: Mapped[str] = mapped_column(String(32))
    check_round: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RiskDecisionModel(Base):
    __tablename__ = "v2_risk_decision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    check_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    decision_result: Mapped[str] = mapped_column(String(32), index=True)
    warning_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_results: Mapped[dict | None] = mapped_column(JSON)
    approver_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttributionRecordModel(Base):
    __tablename__ = "v2_attribution_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attribution_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    replay_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    dimension: Mapped[str] = mapped_column(String(64))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    pnl_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningCaseModel(Base):
    __tablename__ = "v2_learning_case"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    learning_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    replay_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    lesson_summary: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="REVIEWED")  # DRAFT/REVIEWED（学习闭环）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionReviewModel(Base):
    __tablename__ = "v2_decision_review"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    review_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    review_date: Mapped[date] = mapped_column(Date, index=True)
    decision_type: Mapped[str] = mapped_column(String(32))
    ts_code: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    metrics: Mapped[dict | None] = mapped_column(JSON)
    verdict: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshotModel(Base):
    __tablename__ = "v2_portfolio_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    available_cash: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    position_market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    position_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyReportModel(Base):
    __tablename__ = "v2_daily_report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    report_type: Mapped[str] = mapped_column(String(32))
    facts: Mapped[dict | None] = mapped_column(JSON)
    narrative: Mapped[str | None] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperimentRunModel(Base):
    __tablename__ = "v2_experiment_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    config: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppStateModel(Base):
    """应用状态 KV 持久化：settings / circuit_breaker / quota:<sv_id> 等。"""

    __tablename__ = "v2_app_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdjFactorModel(Base):
    """CN 复权因子（tushare adj_factor，按日全市场同步）。供持仓成本/盈亏重述。"""

    __tablename__ = "v2_adj_factor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_v2_adj_factor_code_date"),
    )


class EarningsForecastModel(Base):
    """业绩预告（tushare forecast_vip；EARN_SURPRISE 策略 + 护栏暴雷闸）。"""

    __tablename__ = "v2_earnings_forecast"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    ann_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    forecast_type: Mapped[str | None] = mapped_column(String(64))
    p_change_min: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    p_change_max: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    net_profit_min: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    net_profit_max: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("ts_code", "ann_date", "end_date", name="uq_v2_forecast_key"),
    )


class HolderTradeModel(Base):
    """大股东/董监高增减持（tushare stk_holdertrade；HOLDER_BUY 策略 + 护栏减持闸）。"""

    __tablename__ = "v2_holder_trade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    ann_date: Mapped[date | None] = mapped_column(Date, index=True)
    holder_name: Mapped[str | None] = mapped_column(String(128))
    holder_type: Mapped[str | None] = mapped_column(String(8))
    in_de: Mapped[str | None] = mapped_column(String(4), index=True)
    change_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    change_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    after_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("ts_code", "ann_date", "holder_name", "change_vol",
                         name="uq_v2_holder_trade_key"),
    )


class TopListModel(Base):
    """龙虎榜（tushare top_list；TOP_NET_BUY 策略）。"""

    __tablename__ = "v2_top_list"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    l_buy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    l_sell: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    net_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", "reason", name="uq_v2_top_list_key"),
    )


class DividendModel(Base):
    """分红送配（tushare dividend；复权/公司行动参考）。"""

    __tablename__ = "v2_dividend"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    ann_date: Mapped[date | None] = mapped_column(Date)
    div_proc: Mapped[str | None] = mapped_column(String(16))
    cash_div_tax: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    stk_div: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    record_date: Mapped[date | None] = mapped_column(Date)
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("ts_code", "end_date", "ex_date", "div_proc",
                         name="uq_v2_dividend_key"),
    )


class MarketEnvironmentModel(Base):
    """市场环境（V1 market_environment：regime 判定 + T213 子指标存 note JSON）。"""

    __tablename__ = "v2_market_environment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    env_date: Mapped[date] = mapped_column(Date, index=True)
    regime: Mapped[str] = mapped_column(String(16))          # BULL/BEAR/VOLATILE/SIDEWAYS
    risk_level: Mapped[int] = mapped_column(Integer)          # 1-5
    benchmark: Mapped[str | None] = mapped_column(String(32))
    returns_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    volatility_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    note: Mapped[dict | None] = mapped_column(JSON)           # 子指标(env_score/risk_appetite/...)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("market", "env_date", name="uq_v2_market_env_date"),
    )


class PromotionDecisionModel(Base):
    """策略晋升决策留痕（V1 PromotionDecision + V2 半自动 confirm_status）。

    自动评估 job 写 PENDING → 人工确认 CONFIRMED 后才执行状态变更（同事务留痕）。
    """

    __tablename__ = "v2_promotion_decision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(16))         # PROMOTE/REJECT/DEFER/ROLLBACK
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    pnl_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    approver_type: Mapped[str] = mapped_column(String(16), default="SYSTEM")
    experiment_run_id: Mapped[str | None] = mapped_column(String(64))
    confirm_status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/CONFIRMED/EXPIRED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrayReleaseModel(Base):
    """灰度发布（V1 GrayRelease：STAGING→PROD 间受控观测）。"""

    __tablename__ = "v2_gray_release"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gray_release_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(64), default="10%仓位上限")
    observation_days: Mapped[int] = mapped_column(Integer, default=14)
    metrics_threshold: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE/CONCLUDED
    observed_metrics: Mapped[dict | None] = mapped_column(JSON)
    conclusion: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BacktestRunModel(Base):
    """回测运行记录（walk-forward 结果供晋升 OOS 门消费）。"""

    __tablename__ = "v2_backtest_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="DONE")
    params: Mapped[dict | None] = mapped_column(JSON)      # walk_forward/oos_consistency/folds...
    result: Mapped[dict | None] = mapped_column(JSON)      # equity/folds 明细
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserModel(Base):
    __tablename__ = "v2_user"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'USER', 'TRADER', 'RESEARCHER', 'RISK_MANAGER', 'VIEWER')",
            name="ck_v2_user_role",
        ),
        # 一个 Telegram chat 只能绑一个用户（Phase C 安全绑定边界；迁移 0023 建库前
        # 先做重复检查，绝不静默选归属）。普通唯一索引而非约束：未绑定（NULL）不受限。
        Index("uq_v2_user_telegram_chat_id", "telegram_chat_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="VIEWER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    locale: Mapped[str] = mapped_column(String(8), default="zh")
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 3c：最近改密时刻（迁移 0025；NULL=从未改密）。改密后 token 校验用它踢旧会话。
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 单元 A：会话吊销代数（迁移 0029）。签发 token 携带此值；角色/停用/改密递增，
    # 使旧 token 立即失效（见 api/dependencies.resolve_token_identity）。
    auth_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MembershipModel(Base):
    """会员资格（迁移 0026）。每用户一行；tier=free 永久 / member 到期制。

    真实有效性以 valid_until>=now 懒判定（tier=member 且过期即失效）；
    status 仅供展示与到期扫描标记。
    """

    __tablename__ = "v2_membership"
    __table_args__ = (
        CheckConstraint("tier IN ('free','member')", name="ck_v2_membership_tier"),
        CheckConstraint(
            "status IN ('active','expired','revoked')", name="ck_v2_membership_status"
        ),
        # B1：撤销字段成对（同为空或同非空）。
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name="ck_v2_membership_revocation_pair",
        ),
        # B1：status=revoked 当且仅当 revoked_at 非空。
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name="ck_v2_membership_revoked_status",
        ),
        CheckConstraint("version >= 1", name="ck_v2_membership_version"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), unique=True, index=True
    )
    tier: Mapped[str] = mapped_column(String(16), default="free")
    # NULL=永久（free）；member 为到期时刻（永久会员 is_permanent=True 时也为 NULL）。
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    # B1（迁移 0030）：永久/撤销/乐观锁。
    is_permanent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StrategySubscriptionModel(Base):
    """策略订阅（迁移 0026）。用户对某内置策略的按月订阅。

    valid_until>=now 即有效，用于向订阅者扇出该策略入场/出场信号。
    unique(user_id, strategy_id)：同一策略每用户仅一行，续订叠加 valid_until。
    """

    __tablename__ = "v2_strategy_subscription"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "strategy_id", name="uq_v2_strategy_subscription_user_strategy"
        ),
        CheckConstraint(
            "status IN ('active','expired','revoked')",
            name="ck_v2_strategy_subscription_status",
        ),
        # B1：永久时 valid_until 为空、非永久时非空。
        CheckConstraint(
            "(is_permanent AND valid_until IS NULL) OR "
            "(NOT is_permanent AND valid_until IS NOT NULL)",
            name="ck_v2_strategy_subscription_term",
        ),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name="ck_v2_strategy_subscription_revocation_pair",
        ),
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name="ck_v2_strategy_subscription_revoked_status",
        ),
        CheckConstraint("version >= 1", name="ck_v2_strategy_subscription_version"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_strategy.strategy_id"), index=True
    )
    # B1：永久订阅时为空；非永久为到期时刻。
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_permanent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdminOperationModel(Base):
    """管理员写操作请求级幂等（迁移 0030）。

    唯一键 (actor_user_id, idempotency_key)：同键同指纹返回首次 response，
    同键不同指纹 → 409。与逐资源审计 v2_entitlement_audit 分离。
    """

    __tablename__ = "v2_admin_operation"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id", "idempotency_key", name="uq_v2_admin_operation_actor_key"
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48))
    target_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EntitlementAuditModel(Base):
    """逐资源权益审计（迁移 0030）。一次批量操作 → 一条 operation + 每资源一条 audit。

    source_type=ADMIN（actor 非空）/ PAYMENT（actor 可空，用 source_order_id）。
    不含 token/密码/密钥/webhook 原文。
    """

    __tablename__ = "v2_entitlement_audit"
    __table_args__ = (
        Index("ix_v2_entitlement_audit_target", "target_user_id", "created_at"),
        Index("ix_v2_entitlement_audit_actor", "actor_user_id", "created_at"),
        Index(
            "ix_v2_entitlement_audit_resource",
            "resource_type",
            "resource_id",
            "created_at",
        ),
        Index("ix_v2_entitlement_audit_action", "action", "created_at"),
        Index("ix_v2_entitlement_audit_operation", "operation_id"),
        UniqueConstraint(
            "source_order_id", name="uq_v2_entitlement_audit_source_order"
        ),
    )

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_admin_operation.operation_id"), nullable=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), nullable=True
    )
    target_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id")
    )
    source_type: Mapped[str] = mapped_column(String(16))  # ADMIN / PAYMENT
    action: Mapped[str] = mapped_column(String(48))
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    before_state: Mapped[dict] = mapped_column(JSON)
    after_state: Mapped[dict] = mapped_column(JSON)
    source_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentOrderModel(Base):
    """支付订单（迁移 0027）。用户自助购买会员/订阅的商户订单。

    amount_usdt=应付 USDT；actual_amount=回调实收（发放依据须 >= amount_usdt）。
    status pending→paid（或 expired/canceled）。
    """

    __tablename__ = "v2_payment_order"
    __table_args__ = (
        CheckConstraint(
            "product_type IN ('membership','subscription')",
            name="ck_v2_payment_order_product_type",
        ),
        CheckConstraint(
            "status IN ('pending','paid','expired','canceled')",
            name="ck_v2_payment_order_status",
        ),
        # 单元 C：发放状态机（迁移 0031）
        CheckConstraint(
            "grant_status IN "
            "('pending','processing','retrying','granted','failed','manual_review')",
            name="ck_v2_payment_order_grant_status",
        ),
        CheckConstraint(
            "grant_attempts >= 0 AND automatic_grant_attempts >= 0",
            name="ck_v2_payment_order_grant_attempts",
        ),
        CheckConstraint(
            "(product_type='membership' AND product_ref IS NULL) OR "
            "(product_type='subscription' AND product_ref IS NOT NULL)",
            name="ck_v2_payment_order_product_ref_pairing",
        ),
        CheckConstraint(
            "grant_review_decision IS NULL OR "
            "grant_review_decision IN ('RETRY','MARK_GRANTED')",
            name="ck_v2_payment_order_grant_review_decision",
        ),
        CheckConstraint(
            "(grant_reviewed_at IS NULL) = (grant_reviewed_by_user_id IS NULL)",
            name="ck_v2_payment_order_review_pair",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), index=True
    )
    product_type: Mapped[str] = mapped_column(String(16))
    # membership 为 NULL；subscription 为被订阅的内置策略 strategy_id（0031 加 FK，非级联）。
    product_ref: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_strategy.strategy_id"), nullable=True
    )
    months: Mapped[int] = mapped_column(Integer)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    trade_type: Mapped[str] = mapped_column(String(20), default="usdt.trc20")
    bepusdt_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pay_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    notify_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── 单元 C 发放状态机（迁移 0031）──
    grant_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", index=True
    )
    grant_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    automatic_grant_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    next_grant_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_grant_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_grant_error_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    grant_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grant_lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    historical_grant_ambiguous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    grant_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grant_reviewed_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("v2_user.user_id"), nullable=True
    )
    grant_review_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(
        String(128), default="", server_default=""
    )
    market_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentEventModel(Base):
    """支付回调事件（迁移 0027）。unique(trade_id) 保证同一交易回调只处理一次。"""

    __tablename__ = "v2_payment_event"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    trade_id: Mapped[str] = mapped_column(String(128), unique=True)
    block_txid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MirrorSimAccountModel(Base):
    """跟单模拟账户（订阅者自助开，独立于 v2_user_account/enforced 执行链）。"""
    __tablename__ = "v2_mirror_sim_account"
    __table_args__ = (
        CheckConstraint("initial_capital > 0", name="ck_v2_mirror_sim_account_capital"),
        CheckConstraint("cash_balance >= 0", name="ck_v2_mirror_sim_account_cash"),
        CheckConstraint("status IN ('active','frozen')", name="ck_v2_mirror_sim_account_status"),
        UniqueConstraint("user_id", "strategy_id", name="uq_v2_mirror_sim_account_user_strategy"),
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_user.user_id"), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_strategy.strategy_id"), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MirrorSimPositionModel(Base):
    __tablename__ = "v2_mirror_sim_position"
    __table_args__ = (
        UniqueConstraint("mirror_account_id", "symbol", name="uq_v2_mirror_sim_position_acct_symbol"),
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)


class MirrorSimFillModel(Base):
    __tablename__ = "v2_mirror_sim_fill"
    __table_args__ = (
        CheckConstraint("side IN ('BUY','SELL')", name="ck_v2_mirror_sim_fill_side"),
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"),
        nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
