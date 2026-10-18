"""aw_* ORM 五表（架构稿 §4.2 / §8）：method / agent / decision / learning_link / graduation。

独立 DeclarativeBase（不挂 zhice Base.metadata）——create_all 只建 aw_* 表，
不触碰 zhice v2_* 迁移体系；时间全部 UTC（timezone=True）。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AwBase(DeclarativeBase):
    """agent_platform 专属 metadata（与 zhice v2_* 物理隔离）。"""

    pass


def _utcnow() -> datetime:
    from zhice_os_v2.agent_platform.config import utc_now

    return utc_now()


class AwMethodModel(AwBase):
    """方法注册表：毕业权重是仲裁 STEP1 的直接输入（闭环合拢点）。

    T07：playbook_id nullable 列 + (method_id, playbook_id) 唯一约束——
    按 (method, playbook) 独立毕业留痕；NULL 行=跨包默认权重（旧语义不变，
    单列主键保留，registry.load_weights 仍按 method_id 盖章共享实例）。
    """

    __tablename__ = "aw_method"
    __table_args__ = (
        UniqueConstraint("method_id", "playbook_id", name="uq_aw_method_playbook"),
    )

    method_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)      # 毕业权重，[0.3, 3.0]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    playbook_id: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                                    index=True)   # T07 包级权重留痕
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)  # 参数快照（回放用）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow,
                                                 onupdate=_utcnow)


class AwAgentModel(AwBase):
    """Agent 分片登记（market 一级 + symbol 二级）。"""

    __tablename__ = "aw_agent"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbols_json: Mapped[list] = mapped_column(JSON, default=list)
    method_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_hz: Mapped[str] = mapped_column(String(8), default="5s")
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING/STOPPED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AwDecisionModel(AwBase):
    """决策留痕表 —— 学习闭环唯一事实源，votes_json/model_verdict 永不删改。"""

    __tablename__ = "aw_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    ts_code: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    bar_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action: Mapped[str] = mapped_column(String(8))                 # SELL / HOLD / WAIT
    score_sell: Mapped[float] = mapped_column(Float, default=0.0)
    score_hold: Mapped[float] = mapped_column(Float, default=0.0)
    votes_json: Mapped[list] = mapped_column(JSON, default=list)   # 全部 MethodVote 原文
    regime_risk_off: Mapped[bool] = mapped_column(Boolean, default=False)
    arbitration_path: Mapped[str] = mapped_column(String(16))      # WEIGHTED/REGIME_VETO/LLM/DROPPED
    model_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING_CONFIRM")
    fwd_ret_5d: Mapped[float | None] = mapped_column(Float, nullable=True)  # T+5 前向收益（回填）
    playbook_id: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                                    index=True)  # T06 打法包归因（NULL=默认包）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AwLearningLinkModel(AwBase):
    """决策 ↔ zhice LearningCase 关联（T04 学习闭环消费，v1 只建表）。"""

    __tablename__ = "aw_learning_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("aw_decision.decision_id"), index=True
    )
    learning_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    link_type: Mapped[str] = mapped_column(String(24), default="MANUAL_CONFIRM")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AwPlaybookBindingModel(AwBase):
    """标的 → 打法包绑定（T06）：一标的一包；解绑=物理删行。

    symbol 为自然主键（缺省包不落行——playbook_for 缺省返回 default）。
    """

    __tablename__ = "aw_playbook_binding"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    playbook_id: Mapped[str] = mapped_column(String(64), index=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AwPlaybookEventModel(AwBase):
    """事件催化登记（T07 event_window 数据源，手工/外部注入）。

    event_type ∈ order_tender（订单/中标）/ price_hike（涨价）/ theme（题材）；
    不接自动新闻源（后续任务）。同标的多事件共存——评估时取最紧急窗口。
    """

    __tablename__ = "aw_playbook_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(32))    # order_tender/price_hike/theme
    note: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(64), default="manual")  # manual/api/...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AwGraduationModel(AwBase):
    """方法毕业记录（判据 v2：n≥30 有效标的/域，CRYPTO 降档 6；±1pp 阈值）。"""

    __tablename__ = "aw_graduation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    eval_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    mean_fwd_ret: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String(16))              # PROMOTE / DEMOTE / HOLD
    old_weight: Mapped[float] = mapped_column(Float, default=1.0)
    new_weight: Mapped[float] = mapped_column(Float, default=1.0)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
