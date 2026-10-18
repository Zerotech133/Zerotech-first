"""Transactional outbox：通知事件入队服务（Phase C Task 5；多渠道路由 Task 10）。

设计要点（plan: docs/superpowers/plans/2026-09-06-telegram-directed-notifications.md
Task 5 / docs/superpowers/plans/2026-09-17-domestic-notification-channels.md Task 10）：

- **与业务写同事务**：``enqueue`` 接收调用方 session（与 fill/持仓/报告持久化同一
  事务），本服务**绝不自行 commit/rollback**——outbox 行与业务行同生共死，这是
  transactional outbox 的核心语义（刻意与 Phase B AccountLedgerService 的自管事务
  不同：后者自带 check-and-commit，此处向调用方让渡事务权）。``session=None`` 时
  退化为自开 session 并 commit（便于非事务调用方/运维脚本，行为已在文档注明）。
- **幂等**：event 按 ``event_id`` 唯一、delivery 按 (event_id, user_id, channel)
  唯一；插入走 PG/sqlite 方言的 ``INSERT ... ON CONFLICT DO NOTHING``（其他方言退化
  为查存在即返回）。同 event_id 二次 enqueue 不新建任何行、维持既有 delivery。
- **模式门**（enqueue 时读取 ``telegram_notification_mode``，每次现读不缓存，
  ``mode`` 构造参数可显式覆盖供测试注入）：
  - ``broadcast``（默认）：**no-op**，不写 event/delivery——现状全员广播由调用方
    直接走 ``telegram_notifier.broadcast_telegram``（Task 8 接入），outbox 仅服务
    shadow/directed 两模式；
  - ``shadow``：写 event、**不写 delivery**，并记一条路由判定日志（对照期观测）；
  - ``directed``：写 event + delivery(PENDING)，由 Task 7 worker 认领发送。

- **多渠道路由**（多渠道改造 Task 10；判定在 enqueue 期，结果只影响 delivery 与
  路由日志，event 恒落库——shadow 对照与审计都要完整事件流）。逐渠道
  （telegram / wxpusher，``_ROUTE_CHANNELS``）产出 ``ChannelRoute``：
  - 绑定门（渠道级、最先）：``NotificationBindingRepository.get_active``，读走
    **自开短命只读会话**（C-1 复核修复：路由是快照、worker 发送前仍现读
    binding，无一致性损失；``_routes`` 完全不碰 caller session——语句级错只
    毒化该即弃会话，绝不波及调用方事务）；无 active 绑定 → 不建该渠道
    delivery（reason=no_binding），防 worker 认领后即终态 FAILED 的死行 churn；
    查询异常 fail-closed（reason=binding_query_failed）——只放弃该渠道 delivery、
    不建死行、不上抛、不污染业务事务；
  - 实例门（渠道无关，惰性求值）：实例级事件（非报告族且
    owner.user_strategy_id 非空）过 9 键实例偏好 ``should_notify``——首个通过
    绑定门的渠道才求值（双渠道均无绑定零查询），异常 → 已绑定渠道全部
    preference_query_failed fail-closed；
  - 渠道偏好门（与 Task 5 ``NotificationPreferenceService`` 对齐，偏好按
    (user_id, channel) 逐渠道一行）：
    - signal/fill 族渠道开关交集：fill/stop_loss/take_profit → fill_enabled、
      signal → signal_enabled（``channel_event_enabled`` 同一映射）；无渠道
      开关键的实例事件类型（plan/approval/risk/order/带实例 runtime_error）
      渠道级不适用（空约束恒真），仅实例级门控（计划第 7 条：实例级 9 类
      保持不变）；
    - MARKET_REPORT / USER_REPORT（用户级）：市场 opt-in（``market_reports_enabled``；
      MARKET_REPORT 按 ``payload["market"]``，缺 market 键 fail-closed；
      USER_REPORT 任一市场开启即订阅报告族）∩ 晨/晚开关
      （``payload["report_kind"]`` → morning/evening_report_enabled，Task 11 由
      routing 层写入；缺失时保守要求晨晚双开——缺省偏好全开，与既有行为兼容）；
      报告族事件即便误带实例也按用户级偏好路由（实例偏好无报告键）；
    - RUNTIME_ERROR 无实例（平台级告警）：``system_alerts_enabled``——域对象
      注释明确该标志是 telegram 行上的遗留**全局**开关（非渠道级），各渠道共用
      同一判定（缺省开）；
    - 订阅 SIGNAL（P2，无实例）：订阅本身即用户级 opt-in，再叠该渠道
      signal_enabled（缺省开）；
    - 其余实例级事件无实例（legacy/断链）：fail-closed 不投递（reason=no_instance）。
- **逐渠道 SAVEPOINT**（Task 10）：外层 SAVEPOINT 仍保 event 一次写入（整体失败
  → 全部撤销并上抛）；每个渠道的 delivery INSERT 各包独立 nested SAVEPOINT——
  单渠道语句级失败只回滚该渠道保存点（丢该 delivery），event / 其他渠道
  delivery / 业务行（如 fill）全部保留；失败记
  ``notification_delivery_enqueue_failed``（event_id/channel/error_class 白名单）。

调用方约束（**安全铁律**）：
- ``payload`` 绝不放 chat_id / token / 消息正文等敏感数据——payload 整体落库
  (``v2_notification_event.payload`` JSON) 并供 Task 6 模板渲染，只放业务标识与
  展示字段（fill_id/symbol/price/涨跌幅等）；
- 本服务日志绝不落 chat_id / token / payload 正文——shadow 路由日志只落白名单
  六字段 event_id/user_id/event_type/channel/eligible/reason；delivery 入队失败
  日志只落 event_id/channel/error_class。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from sqlalchemy import Insert, insert, select
from sqlalchemy.orm import Session

from zhice_os_v2.application.services.ownership_resolver import RuntimeOwner
from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
from zhice_os_v2.domain.notification.preference import (
    NOTIFICATION_CHANNEL_TELEGRAM,
    NOTIFICATION_CHANNEL_WXPUSHER,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)
from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)

logger = logging.getLogger(__name__)


class NotificationType(StrEnum):
    """通知事件类型（偏好键 / 事件行 / 模板 / 测试统一用 value）。

    实例级九类（= ``DEFAULT_NOTIFICATION_PREFERENCES`` 键集）+ 报告两类（用户级）。
    """

    SIGNAL = "signal"
    PLAN = "plan"
    APPROVAL = "approval"
    RISK = "risk"
    ORDER = "order"
    FILL = "fill"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    RUNTIME_ERROR = "runtime_error"
    MARKET_REPORT = "market_report"
    USER_REPORT = "user_report"


# 报告族（用户级偏好路由，无视实例偏好）
_REPORT_TYPES = frozenset({NotificationType.MARKET_REPORT, NotificationType.USER_REPORT})

# 多渠道路由目标（Task 10）：SUPPORTED_NOTIFICATION_CHANNELS 的稳定序子集。
_ROUTE_CHANNELS: tuple[str, ...] = (
    NOTIFICATION_CHANNEL_TELEGRAM,
    NOTIFICATION_CHANNEL_WXPUSHER,
)

# 渠道级事件族开关映射（与 Task 5 channel_event_enabled 同源）：fill/stop_loss/
# take_profit → fill_enabled、signal → signal_enabled；未映射的实例事件类型渠道级
# 不适用（空约束恒真），仅实例级 9 键门控（裁决 1：实例级 9 类保持不变）。
_CHANNEL_EVENT_FIELD = {
    NotificationType.SIGNAL.value: "signal_enabled",
    NotificationType.FILL.value: "fill_enabled",
    NotificationType.STOP_LOSS.value: "fill_enabled",
    NotificationType.TAKE_PROFIT.value: "fill_enabled",
}

# 报告族 report_kind → 偏好开关键（payload["report_kind"]，Task 11 由 routing
# 层写入；未知 kind 一律视为关，fail-closed）。
_REPORT_KIND_FIELD = {
    "morning": "morning_report_enabled",
    "evening": "evening_report_enabled",
}

# 路由日志 reason 白名单（短 slug，绝不含敏感数据）
_REASON_ELIGIBLE = "eligible"
_REASON_INSTANCE_OFF = "instance_preference_disabled"
_REASON_REPORT_OFF = "report_preference_disabled"
_REASON_MARKET_MISSING = "market_missing"
_REASON_SYSTEM_ALERTS_OFF = "system_alerts_disabled"
_REASON_NO_INSTANCE = "no_instance"
_REASON_NO_BINDING = "no_binding"
_REASON_BINDING_QUERY_FAILED = "binding_query_failed"
_REASON_PREFERENCE_QUERY_FAILED = "preference_query_failed"
_REASON_CHANNEL_OFF = "channel_preference_disabled"


@dataclass(frozen=True)
class NotificationDelivery:
    """delivery 便捷读视图（worker/测试用；不泄露 ORM 实体）。"""

    delivery_id: str
    event_id: str
    user_id: str
    channel: str
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True)
class ChannelRoute:
    """单渠道 enqueue 期路由判定（worker 发送前仍会现读 binding，此处仅快照）。"""

    channel: str
    eligible: bool
    reason: str


class NotificationOutboxService:
    """通知 outbox 入队服务（幂等 / 模式门 / 多渠道路由；不自管事务）。"""

    def __init__(
        self,
        *,
        mode: Literal["broadcast", "shadow", "directed"] | None = None,
        instance_service: UserStrategyService | None = None,
        preference_repository: NotificationPreferenceRepository | None = None,
        binding_repository: NotificationBindingRepository | None = None,
    ) -> None:
        self._mode_override = mode
        self._instance_service = instance_service
        self._pref_repo = preference_repository
        self._binding_repo = binding_repository

    # ── 入队 ────────────────────────────────────────────────────────────────
    def enqueue(
        self,
        session: Session | None = None,
        *,
        event_id: str,
        owner: RuntimeOwner,
        event_type: NotificationType | str,
        payload: dict,
        occurred_at: datetime,
    ) -> None:
        """把一个业务通知事件写入 outbox（与调用方业务写同事务）。

        - ``session``：调用方事务会话（fill/报告持久化的同一 session）；本方法在其上
          插入行但**不 commit**，持久化时机由调用方事务提交决定。传 None 则自开
          session 并 commit（退化路径，非事务调用方/运维脚本用）。
        - ``event_type``：``NotificationType`` 或其 value 字符串；未知值 ValueError。
        - ``owner``：事件归属（Phase B RuntimeOwner）。报告类无实例事件可传
          ``user_strategy_id=None``（或空串）——按用户级偏好路由。
        - 幂等：同 ``event_id``（及同 (event_id,user_id,channel) delivery）二次调用
          不新建行、维持既有 delivery。
        """
        et = NotificationType(event_type)
        mode = self._mode()
        if mode == "broadcast":
            # 现状广播不变：broadcast 模式下 outbox 为 no-op，由调用方直接
            # broadcast_telegram（Task 8 接入），此处不写任何行。
            logger.debug(
                "notification_outbox broadcast_noop event_id=%s user_id=%s event_type=%s",
                event_id,
                owner.user_id,
                et.value,
            )
            return

        safe_payload = dict(payload or {})
        routes = self._routes(owner, et, safe_payload)

        if session is not None:
            # SAVEPOINT 隔离（终审 Important #1 + Task 10 外层）：调用方事务内的全部
            # enqueue 写包进 ``begin_nested``——event INSERT 整体失败（FK 违反/超长
            # 等 PG 语句级错会把外层事务置 aborted）时回滚到保存点：外层事务恢复可
            # 提交、event 半行撤销、异常照常上抛由调用方吞掉。单渠道 delivery 失败
            # 不走这条路——由 ``_write`` 内逐渠道 SAVEPOINT 吸收（只丢该渠道）。
            # 方言说明：sqlite 的 begin_nested 同为 SAVEPOINT（pysqlite 原生支持），
            # 仅一个边角差异——SAVEPOINT 若是连接首语句（外层尚无任何 DML），
            # RELEASE 会直接提交整个事务；生产调用方（_record_fill）enqueue 前总有
            # 成交写在途，外层 BEGIN 已存在，RELEASE 只释放保存点，与 PG 行为一致。
            nested = session.begin_nested()
            try:
                self._write(
                    session, mode, event_id, owner, et, safe_payload, occurred_at, routes
                )
            except Exception:
                nested.rollback()
                raise
            nested.commit()  # RELEASE SAVEPOINT：写入并入外层事务
        else:
            with self._own_session() as own:
                self._write(
                    own, mode, event_id, owner, et, safe_payload, occurred_at, routes
                )
                own.commit()

        # 安全铁律：只落白名单字段（event_id/user_id/event_type/channel/eligible/
        # reason 或渠道:reason 摘要），绝不落 chat_id / token / payload 正文。
        if mode == "shadow":
            for route in routes:
                logger.info(
                    "notification_shadow event_id=%s user_id=%s event_type=%s "
                    "channel=%s eligible=%s reason=%s",
                    event_id,
                    owner.user_id,
                    et.value,
                    route.channel,
                    route.eligible,
                    route.reason,
                )
        else:  # directed
            logger.debug(
                "notification_enqueued event_id=%s user_id=%s event_type=%s routes=%s",
                event_id,
                owner.user_id,
                et.value,
                ",".join(f"{r.channel}:{r.reason}" for r in routes),
            )

    # ── 便捷读（worker/测试）───────────────────────────────────────────────
    def event_exists(self, event_id: str, *, session: Session | None = None) -> bool:
        """事件是否已入队（幂等/测试观测用）。"""
        with self._reader(session) as s:
            return (
                s.execute(
                    select(orm.NotificationEventModel.id).where(
                        orm.NotificationEventModel.event_id == event_id
                    )
                ).first()
                is not None
            )

    def delivery_count(self, event_id: str, *, session: Session | None = None) -> int:
        """该事件的 delivery 行数（幂等锚点观测）。"""
        with self._reader(session) as s:
            return int(
                s.query(orm.NotificationDeliveryModel)
                .filter(orm.NotificationDeliveryModel.event_id == event_id)
                .count()
            )

    def deliveries_for(
        self, event_id: str, *, session: Session | None = None
    ) -> list[NotificationDelivery]:
        """该事件的 delivery 列表（按插入序；worker/测试用）。"""
        with self._reader(session) as s:
            rows = (
                s.query(orm.NotificationDeliveryModel)
                .filter(orm.NotificationDeliveryModel.event_id == event_id)
                .order_by(orm.NotificationDeliveryModel.id)
                .all()
            )
            return [
                NotificationDelivery(
                    delivery_id=r.delivery_id,
                    event_id=r.event_id,
                    user_id=r.user_id,
                    channel=r.channel,
                    status=r.status,
                    attempt_count=int(r.attempt_count),
                    next_attempt_at=r.next_attempt_at,
                    last_error_code=r.last_error_code,
                )
                for r in rows
            ]

    # ── 内部 ────────────────────────────────────────────────────────────────
    def _mode(self) -> str:
        """当前通知模式：构造参数覆盖优先，否则每次现读 settings（不缓存）。"""
        if self._mode_override is not None:
            return self._mode_override
        from zhice_os_v2.config.settings import get_settings

        return str(get_settings().telegram_notification_mode)

    def _instances(self) -> UserStrategyService:
        if self._instance_service is None:
            # should_notify 只读实例仓储，PersistencePort 不会被触碰；
            # 生产由 Task 8 调用方注入真实实例服务。
            from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

            self._instance_service = UserStrategyService(db=InMemoryDatabase())
        return self._instance_service

    def _prefs(self) -> NotificationPreferenceRepository:
        if self._pref_repo is None:
            self._pref_repo = NotificationPreferenceRepository()
        return self._pref_repo

    def _bindings(self) -> NotificationBindingRepository:
        if self._binding_repo is None:
            self._binding_repo = NotificationBindingRepository()
        return self._binding_repo

    def _routes(
        self,
        owner: RuntimeOwner,
        event_type: NotificationType,
        payload: dict,
    ) -> list[ChannelRoute]:
        """逐渠道路由判定（binding 硬门 → 偏好门）；口径见模块 docstring。

        - binding 读走**自开短命只读会话**（C-1 复核修复，方案 2）：路由本就是
          快照，worker 发送前仍会现读 binding，无一致性损失；``_routes`` 完全
          不碰 caller session——语句级错只毒化该自开会话（用后即弃），绝不波及
          调用方事务，fail-closed 吞异常因此是真正安全的。
        - 实例门渠道无关且惰性求值（S-3）：首个通过绑定门的渠道才查
          ``should_notify``，双渠道均无绑定的用户零额外查询。
        """
        with self._own_session() as own:
            return self._route_channels(own, owner, event_type, payload)

    def _route_channels(
        self,
        reader: Session,
        owner: RuntimeOwner,
        event_type: NotificationType,
        payload: dict,
    ) -> list[ChannelRoute]:
        """在自开短命会话上逐渠道判定（telegram → wxpusher 稳定序）。"""
        user_strategy_id = owner.user_strategy_id or None
        instance_ok: bool | None = None  # 惰性：首个过绑定门的渠道才求值（S-3）
        instance_failed = False
        routes: list[ChannelRoute] = []
        for channel in _ROUTE_CHANNELS:
            # 绑定门（渠道级、最先）：无 active 绑定不建该渠道 delivery（防 worker
            # 认领即终态 FAILED 的死行 churn）；查询异常 fail-closed——只放弃该渠道
            # delivery，不上抛；语句级错落在可丢弃的自开会话上，不污染业务事务。
            try:
                binding = self._bindings().get_active(reader, owner.user_id, channel)
            except Exception:  # noqa: BLE001
                routes.append(ChannelRoute(channel, False, _REASON_BINDING_QUERY_FAILED))
                continue
            if binding is None:
                routes.append(ChannelRoute(channel, False, _REASON_NO_BINDING))
                continue
            if user_strategy_id is not None and event_type not in _REPORT_TYPES:
                if not instance_failed and instance_ok is None:
                    try:
                        instance_ok = bool(
                            self._instances().should_notify(
                                user_strategy_id, event_type.value
                            )
                        )
                    except Exception:  # noqa: BLE001
                        instance_failed = True
                if instance_failed:
                    routes.append(
                        ChannelRoute(channel, False, _REASON_PREFERENCE_QUERY_FAILED)
                    )
                    continue
                if not instance_ok:
                    routes.append(ChannelRoute(channel, False, _REASON_INSTANCE_OFF))
                    continue
            try:
                eligible, reason = self._channel_eligible(
                    owner, channel, event_type, payload
                )
            except Exception:  # noqa: BLE001
                routes.append(
                    ChannelRoute(channel, False, _REASON_PREFERENCE_QUERY_FAILED)
                )
                continue
            routes.append(ChannelRoute(channel, eligible, reason))
        return routes

    def _channel_eligible(
        self,
        owner: RuntimeOwner,
        channel: str,
        event_type: NotificationType,
        payload: dict,
    ) -> tuple[bool, str]:
        """渠道偏好门（binding 已确认 active 后调用；查询异常由调用方 fail-closed）。

        只读（``get`` 不落行）；缺行按领域缺省——渠道开关全开、市场报告全关
        （opt-in）、system_alerts 开。
        """
        pref = self._prefs().get(owner.user_id, channel)
        reports: dict = dict(pref.market_reports_enabled) if pref is not None else {}

        if event_type in _REPORT_TYPES:
            if event_type is NotificationType.MARKET_REPORT:
                market = payload.get("market")
                if not market:
                    return False, _REASON_MARKET_MISSING
                if not reports.get(market):
                    return False, _REASON_REPORT_OFF
            elif not any(bool(v) for v in reports.values()):
                # USER_REPORT（用户维度聚合报告）：任一市场开启即订阅报告族。
                return False, _REASON_REPORT_OFF
            # 晨/晚开关：payload["report_kind"]（Task 11 由 routing 层写入）；
            # 缺失时保守要求晨晚双开（缺省偏好全开，与既有报告行为兼容），
            # 未知 kind 一律视为关（fail-closed）。
            kind = payload.get("report_kind")
            if kind is None:
                kind_ok = pref is None or (
                    bool(pref.morning_report_enabled) and bool(pref.evening_report_enabled)
                )
            else:
                kind_field = _REPORT_KIND_FIELD.get(str(kind))
                kind_ok = kind_field is not None and (
                    pref is None or bool(getattr(pref, kind_field))
                )
            return (True, _REASON_ELIGIBLE) if kind_ok else (False, _REASON_REPORT_OFF)

        if (owner.user_strategy_id or None) is None:
            if event_type is NotificationType.RUNTIME_ERROR:
                # 平台级 runtime_error：system_alerts 是 telegram 行上的遗留全局开关
                # （域对象注释明确非渠道级），各渠道共用同一判定（缺省开）；
                # 渠道即 telegram 时复用已取的 pref（S-1），其余渠道另读 telegram 行。
                alert_pref = (
                    pref
                    if channel == NOTIFICATION_CHANNEL_TELEGRAM
                    else self._prefs().get(owner.user_id, NOTIFICATION_CHANNEL_TELEGRAM)
                )
                enabled = (
                    bool(alert_pref.system_alerts_enabled)
                    if alert_pref is not None
                    else True
                )
                return (
                    (True, _REASON_ELIGIBLE)
                    if enabled
                    else (False, _REASON_SYSTEM_ALERTS_OFF)
                )
            if event_type is NotificationType.SIGNAL:
                # 订阅信号（P2）：订阅行为本身即用户级 opt-in（发件方已核订阅有效性），
                # 此处只叠该渠道 signal_enabled。
                enabled = bool(pref.signal_enabled) if pref is not None else True
                return (
                    (True, _REASON_ELIGIBLE) if enabled else (False, _REASON_CHANNEL_OFF)
                )
            # 实例级事件无实例（legacy/断链）：无偏好权威，fail-closed。
            return False, _REASON_NO_INSTANCE

        # 实例级事件（实例门已在 _routes 通过）：渠道级 signal/fill 族开关交集；
        # 无渠道开关键的事件类型（plan/approval/risk/order/带实例 runtime_error）
        # 渠道级不适用（空约束恒真），仅实例级门控（裁决 1：计划第 7 条
        # 「实例级 9 类保持不变」——不得因缺渠道开关键变死开关）。
        channel_field = _CHANNEL_EVENT_FIELD.get(event_type.value)
        if channel_field is None:
            return True, _REASON_ELIGIBLE
        enabled = bool(getattr(pref, channel_field)) if pref is not None else True
        return (True, _REASON_ELIGIBLE) if enabled else (False, _REASON_CHANNEL_OFF)

    def _write(
        self,
        session: Session,
        mode: str,
        event_id: str,
        owner: RuntimeOwner,
        event_type: NotificationType,
        payload: dict,
        occurred_at: datetime,
        routes: list[ChannelRoute],
    ) -> None:
        """在调用方事务内写入 event（恒）与 delivery（仅 directed 且该渠道 eligible）。

        逐渠道 SAVEPOINT（Task 10）：单渠道 delivery INSERT 语句级失败只回滚该
        渠道保存点——只丢该 delivery，event / 其他渠道 delivery / 调用方业务行
        全部保留；event 写仍由 enqueue 的外层 SAVEPOINT 整体保护。
        """
        now = datetime.now(UTC)
        self._insert_on_conflict_do_nothing(
            session,
            orm.NotificationEventModel,
            {
                "event_id": event_id,
                "user_strategy_id": owner.user_strategy_id or None,
                "account_id": owner.account_id or None,
                "user_id": owner.user_id,
                "event_type": event_type.value,
                "payload": payload,
                "occurred_at": occurred_at,
                "created_at": now,
            },
            index_elements=["event_id"],
        )
        if mode != "directed":
            return
        for route in routes:
            if not route.eligible:
                continue
            channel_nested = session.begin_nested()
            try:
                self._insert_on_conflict_do_nothing(
                    session,
                    orm.NotificationDeliveryModel,
                    {
                        "delivery_id": f"dlv_{uuid.uuid4().hex}",
                        "event_id": event_id,
                        "user_id": owner.user_id,
                        "channel": route.channel,
                        "status": "PENDING",
                        "attempt_count": 0,
                        "created_at": now,
                    },
                    index_elements=["event_id", "user_id", "channel"],
                )
            except Exception as exc:  # noqa: BLE001
                channel_nested.rollback()
                # 安全铁律：只落 event_id/channel/error_class，绝不落 payload/recipient。
                logger.warning(
                    "notification_delivery_enqueue_failed event_id=%s channel=%s "
                    "error_class=%s",
                    event_id,
                    route.channel,
                    type(exc).__name__,
                )
            else:
                channel_nested.commit()

    @staticmethod
    def _insert_on_conflict_do_nothing(
        session: Session,
        model: type[Base],
        values: dict[str, object],
        *,
        index_elements: list[str],
    ) -> None:
        """幂等插入：命中唯一键即跳过（不抛 IntegrityError、不污染外层事务）。

        PG/sqlite 走方言原生 ``INSERT ... ON CONFLICT DO NOTHING``；其他方言退化为
        查存在即返回（本项目生产 PG + 测试 sqlite，退化分支仅保通用语义，竞态由
        唯一约束兜底）。同事务内先前插入的行对后续冲突判定可见（幂等重入）。
        """
        dialect = session.get_bind().dialect.name
        stmt: Insert
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(model).values(**values).on_conflict_do_nothing(
                index_elements=index_elements
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(model).values(**values).on_conflict_do_nothing(
                index_elements=index_elements
            )
        else:  # pragma: no cover - 本项目无第三方言
            table = model.__table__
            conds = [table.c[name] == values[name] for name in index_elements]
            if (
                session.execute(select(table.c[index_elements[0]]).where(*conds)).first()
                is not None
            ):
                return
            stmt = insert(model).values(**values)
        session.execute(stmt)

    @staticmethod
    @contextmanager
    def _own_session() -> Iterator[Session]:
        """自开 session（session=None 退化路径/便捷读缺省）；函数内导入保证可注入。"""
        from zhice_os_v2.infrastructure.db.session import get_session

        s = get_session()
        try:
            yield s
        finally:
            s.close()

    @contextmanager
    def _reader(self, session: Session | None) -> Iterator[Session]:
        """读会话：传入则复用（不关），缺省自开自关。"""
        if session is not None:
            yield session
            return
        with self._own_session() as s:
            yield s
