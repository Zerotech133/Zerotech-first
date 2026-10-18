"""通知渠道运维健康告警（多渠道改造 Task 12）：白名单 kind + 白名单字段正文。

企业微信群机器人 webhook（``ALERT_WEBHOOK_URL``）自本任务起只承载**运维**告警：
普通用户业务正文（止损/风控拒绝/标的/成交明细）一律不再进 webhook（改落
v2_alert 或用户 outbox），防业务信息泄漏进运维群。本模块是运维告警的唯一
结构化入口：

- :func:`send_notification_ops_alert`：kind 必须 ∈ ``_ALLOWED_OPS_KINDS``（未知
  kind 记 warning 日志并拒绝，不发 webhook）；正文只渲染 kind/channel/counts/
  duration_seconds/worker_job_id 白名单字段——counts 键值双重白名单（键 ∈
  ``_ALLOWED_COUNT_KEYS`` 且值为非负 int），任何字符串值/越界键（symbol/uid/
  chat_id/body 等）在渲染前即被丢弃，泄漏红线在数据入口收敛而非依赖调用方
  自律。频控复用 alert_service 的 300s (level,title) 频控：title 由 kind+channel
  派生，即同 kind+channel 5 分钟去重、不同渠道互不抑制。

- :func:`collect_notification_health` / :class:`NotificationHealthSnapshot`：按
  渠道统计 outbox delivery 健康度（只统计**到期** PENDING/RETRY——worker 应处理
  而未处理的工作；未到期 RETRY 是退避的正常语义，不计）。``failed_last_hour``
  以 ``created_at`` 近一小时为口径（表无失败时刻列，created_at 是最近的稳定
  代理）。

- :func:`check_delivery_backlog`：worker 每轮后由 scheduler 调用，对超阈值渠道
  发 ``delivery_backlog`` 运维告警。**停用渠道（如 wxpusher_delivery_enabled=
  False）的 PENDING 滞留是"停投不丢"的预期语义**，整体从快照排除——既不计数
  也不告警，杜绝误报。缺省会话仅在 sql 后端生效（memory/测试静默跳过），DB
  异常 best-effort 返回 []，绝不拖垮 worker job。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from zhice_os_v2.domain.notification.preference import SUPPORTED_NOTIFICATION_CHANNELS
from zhice_os_v2.infrastructure.db import models as orm

logger = logging.getLogger(__name__)

#: 运维告警 kind 白名单（契约固定四类；业务语义绝不入此集）。
_ALLOWED_OPS_KINDS = frozenset(
    {
        "channel_auth_failed",
        "delivery_backlog",
        "worker_stalled",
        "terminal_failure_rate",
    }
)

#: counts 计数键白名单：只有这些键、且值为非负 int 才渲染进正文。
_ALLOWED_COUNT_KEYS = frozenset(
    {
        "pending",
        "retry",
        "failed",
        "failed_last_hour",
        "sent",
        "recovered",
        "claimed",
        "oldest_due_seconds",
    }
)

#: backlog 判定阈值：到期工作滞留超此时长（秒）即告警。
BACKLOG_MIN_OLDEST_DUE_SECONDS = 600
#: backlog 判定阈值：到期量（pending+retry）达到此条数即告警。
BACKLOG_MIN_DUE_COUNT = 100

#: 运维告警恒 WARN：必须吃 300s 频控（send_alert 对 CRITICAL 豁免频控）。
_OPS_ALERT_LEVEL = "WARN"

#: worker_job_id 白名单（S-3）：生产 job id 均为该字符集的短标识符。
_WORKER_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: 健康检查异常日志的 warning 抑制窗口（S-1）：首次 warning 运维可见，窗口内降 debug。
_HEALTH_WARN_INTERVAL_SEC = 300.0
_last_health_warn_monotonic = 0.0


@dataclass(frozen=True)
class NotificationHealthSnapshot:
    """单渠道投递健康快照（只统计启用渠道的**到期**工作）。

    - ``pending``/``retry``：状态为 PENDING/RETRY 且已到期（next_attempt_at 为
      NULL 或 ≤ now）的条数——worker 应处理而未处理的量；
    - ``failed_last_hour``：近一小时创建且已终态 FAILED 的条数（created_at 代理
      口径，表无失败时刻列）；
    - ``oldest_due_seconds``：最老一条到期工作已等待的秒数（0=无到期工作）。
    """

    channel: str
    pending: int
    retry: int
    failed_last_hour: int
    oldest_due_seconds: int


# ── 内部：白名单清洗与正文构造 ────────────────────────────────────────────────


def _sanitize_counts(counts: Mapping[str, int] | None) -> dict[str, int]:
    """counts 双重白名单过滤：键 ∈ _ALLOWED_COUNT_KEYS 且值为非负 int 才保留。

    字符串值（可夹带任意业务文本/收件人）与越界键（symbol/uid/chat_id/body…）
    在渲染前即被丢弃；丢弃只记键名（绝不回显值，防日志二次泄漏）。
    """
    if not counts:
        return {}
    safe: dict[str, int] = {}
    dropped: list[str] = []
    for key, value in counts.items():
        if key not in _ALLOWED_COUNT_KEYS:
            dropped.append(key)
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            dropped.append(key)
            continue
        safe[key] = value
    if dropped:
        logger.debug("notification_ops_alert counts_dropped keys=%s", sorted(set(dropped)))
    return safe


def _sanitize_int(value: int | None) -> int | None:
    """标量整数字段白名单校验（非负 int 才渲染；bool 是 int 子类，显式排除）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _sanitize_channel(channel: str | None) -> str | None:
    """channel 白名单校验（S-3）：必须 ∈ SUPPORTED_NOTIFICATION_CHANNELS。

    越界值（脏库渠道/注入串）丢弃字段（告警仍发出、标题回退 '-'，可用性优先——
    脏值不能静默掉整条运维告警），只记字段名不回显值。
    """
    if channel is None:
        return None
    if channel not in SUPPORTED_NOTIFICATION_CHANNELS:
        logger.debug("notification_ops_alert field_dropped field=channel")
        return None
    return channel


def _sanitize_worker_job_id(worker_job_id: str | None) -> str | None:
    """worker_job_id 白名单校验（S-3）：^[A-Za-z0-9_.-]{1,64}$，违规整字段丢弃。"""
    if worker_job_id is None:
        return None
    if not _WORKER_JOB_ID_RE.fullmatch(worker_job_id):
        logger.debug("notification_ops_alert field_dropped field=worker_job_id")
        return None
    return worker_job_id


def _log_health_failure(stage: str) -> None:
    """健康检查异常日志（S-1）：首次 warning（运维可见），300s 窗口内后续降 debug。

    负责暴露投递问题的组件自身不能全员 debug 静默；但 DB 长时间不可用时 worker
    每 15s 一轮会刷屏，故窗口内抑制为 debug（简化频控：模块级单调时间戳）。
    """
    global _last_health_warn_monotonic
    now = time.monotonic()
    if now - _last_health_warn_monotonic >= _HEALTH_WARN_INTERVAL_SEC:
        _last_health_warn_monotonic = now
        logger.warning("notification health check failed stage=%s", stage, exc_info=True)
    else:
        logger.debug("notification health check failed stage=%s", stage, exc_info=True)


def _ops_title(kind: str, channel: str | None) -> str:
    """稳定标题前缀 + kind + channel：运维侧可按前缀路由；频控键随之按 kind+channel 去重。"""
    return f"[ops] notification {kind} channel={channel or '-'}"


def _build_ops_body(
    kind: str,
    channel: str | None,
    counts: Mapping[str, int] | None,
    duration_seconds: int | None,
    worker_job_id: str | None,
) -> str:
    """正文 = 白名单字段键值行（kind/channel/白名单计数/duration/job id），无任何自由文本位。"""
    safe_counts = _sanitize_counts(counts)
    lines = [f"kind={kind}"]
    if channel:
        lines.append(f"channel={channel}")
    for key in sorted(safe_counts):
        lines.append(f"{key}={safe_counts[key]}")
    duration = _sanitize_int(duration_seconds)
    if duration is not None:
        lines.append(f"duration_seconds={duration}")
    if worker_job_id:
        lines.append(f"worker_job_id={worker_job_id}")
    return "\n".join(lines)


# ── 运维告警唯一结构化入口 ────────────────────────────────────────────────────


def send_notification_ops_alert(
    *,
    kind: str,
    channel: str | None = None,
    counts: Mapping[str, int] | None = None,
    duration_seconds: int | None = None,
    worker_job_id: str | None = None,
) -> bool:
    """发一条通知运维告警（企业微信 webhook，只承载白名单 kind + 白名单字段）。

    - kind 不在 ``_ALLOWED_OPS_KINDS``：记 warning 日志并拒绝（return False），
      绝不发 webhook——「webhook 不承载业务正文」红线的第一道闸；
    - 正文只含白名单字段（见 :func:`_build_ops_body`），counts 经双重白名单
      清洗，不接受任何任意 message/payload/symbol/uid/chat_id 入参；
    - 频控：复用 alert_service 300s (level,title) 频控，title 由 kind+channel
      派生 → 同 kind+channel 5 分钟去重。

    永不抛异常（底层 send_alert 同契约），返回是否送达 webhook。
    """
    if kind not in _ALLOWED_OPS_KINDS:
        logger.warning("notification_ops_alert rejected kind=%s", kind)
        return False
    # S-3：channel/worker_job_id 与 counts 同受白名单约束，自由字符串绝不直渲染正文
    safe_channel = _sanitize_channel(channel)
    title = _ops_title(kind, safe_channel)
    body = _build_ops_body(
        kind,
        safe_channel,
        counts,
        duration_seconds,
        _sanitize_worker_job_id(worker_job_id),
    )
    from zhice_os_v2.application.services.alert_service import send_alert

    return send_alert(_OPS_ALERT_LEVEL, title, body, kind=kind)


# ── 健康快照与 backlog 判定 ──────────────────────────────────────────────────


def _aware(dt: datetime | None) -> datetime | None:
    """sqlite 读回 naive 时间统一补 UTC（PG 返回 aware 原样；None 透传）。"""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def collect_notification_health(
    session: Session,
    *,
    now: datetime | None = None,
    disabled_channels: Collection[str] = (),
) -> list[NotificationHealthSnapshot]:
    """按渠道统计 delivery 健康快照（只读；停用渠道整体排除）。

    只统计**到期** PENDING/RETRY（next_attempt_at 为 NULL 或 ≤ now）——未到期
    RETRY 是退避的正常语义，不计入 pending/retry 也不参与 oldest_due。快照按
    channel 名升序（确定性）。只出现近一小时 FAILED、无到期工作的渠道也入
    快照（pending/retry 为 0，terminal_failure_rate 观测用）。
    """
    moment = now or datetime.now(UTC)
    disabled = frozenset(disabled_channels)
    delivery = orm.NotificationDeliveryModel
    due_reference = func.coalesce(delivery.next_attempt_at, delivery.created_at)

    due_rows = session.execute(
        select(
            delivery.channel,
            func.sum(case((delivery.status == "PENDING", 1), else_=0)).label("pending"),
            func.sum(case((delivery.status == "RETRY", 1), else_=0)).label("retry"),
            func.min(due_reference).label("oldest_ref"),
        )
        .where(
            delivery.status.in_(("PENDING", "RETRY")),
            or_(
                delivery.next_attempt_at.is_(None),
                delivery.next_attempt_at <= moment,
            ),
        )
        .group_by(delivery.channel)
    ).all()

    failed_rows = session.execute(
        select(delivery.channel, func.count())
        .where(
            delivery.status == "FAILED",
            delivery.created_at >= moment - timedelta(hours=1),
        )
        .group_by(delivery.channel)
    ).all()
    failed_by_channel = {str(channel): int(count or 0) for channel, count in failed_rows}

    snapshots: list[NotificationHealthSnapshot] = []
    for channel, pending, retry, oldest_ref in due_rows:
        channel_name = str(channel)
        if channel_name in disabled:
            continue
        reference = _aware(oldest_ref)
        oldest_due_seconds = (
            max(0, int((moment - reference).total_seconds())) if reference is not None else 0
        )
        snapshots.append(
            NotificationHealthSnapshot(
                channel=channel_name,
                pending=int(pending or 0),
                retry=int(retry or 0),
                failed_last_hour=failed_by_channel.pop(channel_name, 0),
                oldest_due_seconds=oldest_due_seconds,
            )
        )
    for channel in sorted(failed_by_channel):
        if channel in disabled:
            continue
        snapshots.append(
            NotificationHealthSnapshot(
                channel=channel,
                pending=0,
                retry=0,
                failed_last_hour=failed_by_channel[channel],
                oldest_due_seconds=0,
            )
        )
    return sorted(snapshots, key=lambda snap: snap.channel)


def _is_backlog(snapshot: NotificationHealthSnapshot) -> bool:
    """backlog 判定：到期工作滞留超时长阈值，或到期量达条数阈值（任一命中）。"""
    return (
        snapshot.oldest_due_seconds >= BACKLOG_MIN_OLDEST_DUE_SECONDS
        or (snapshot.pending + snapshot.retry) >= BACKLOG_MIN_DUE_COUNT
    )


def check_delivery_backlog(
    *,
    session_factory: Callable[[], Session] | None = None,
    now: datetime | None = None,
    disabled_channels: Collection[str] = (),
) -> list[NotificationHealthSnapshot]:
    """收集各启用渠道快照，对超阈值者发 delivery_backlog 运维告警（300s 频控）。

    - ``disabled_channels``：运维暂停的渠道（如 wxpusher_delivery_enabled=False）
      ——其 PENDING 滞留是"停投不丢"的预期语义，**从快照整体排除**（连计数都不
      出现），绝不触发 backlog 误报；
    - 缺省会话走 ``get_session`` 且仅 sql 后端生效（memory/单测静默跳过）；
    - DB 异常 best-effort 返回 []（绝不拖垮 worker job）：首次 warning、300s 窗口
      内后续 debug（S-1，见 :func:`_log_health_failure`）；
    - 返回本轮统计的快照（含未超阈值渠道，观测/测试用）。
    """
    factory = session_factory
    if factory is None:
        from zhice_os_v2.config.settings import get_settings

        if str(get_settings().persistence_backend) != "sql":
            return []
        from zhice_os_v2.infrastructure.db.session import get_session

        factory = get_session
    try:
        session = factory()
    except Exception:  # noqa: BLE001
        _log_health_failure("session_factory")
        return []
    try:
        snapshots = collect_notification_health(
            session, now=now, disabled_channels=disabled_channels
        )
    except Exception:  # noqa: BLE001
        _log_health_failure("snapshot")
        return []
    finally:
        session.close()

    for snapshot in snapshots:
        if not _is_backlog(snapshot):
            continue
        send_notification_ops_alert(
            kind="delivery_backlog",
            channel=snapshot.channel,
            counts={
                "pending": snapshot.pending,
                "retry": snapshot.retry,
                "failed_last_hour": snapshot.failed_last_hour,
                "oldest_due_seconds": snapshot.oldest_due_seconds,
            },
        )
    return snapshots
