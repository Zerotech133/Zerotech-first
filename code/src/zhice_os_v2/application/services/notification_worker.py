"""通知 outbox worker（Phase C Task 7 + 多渠道 Task 9）：claim → send → CAS 回写 → 重试/回收。

消费 :class:`NotificationOutboxService`（Task 5）落库的 delivery 行，按状态机推进：
``PENDING/RETRY →(claim)→ SENDING →(send)→ SENT | RETRY | FAILED``。由 scheduler 每
15 秒驱动一轮（``run_notification_worker_job``，best-effort 永不影响交易主流程）。

并发安全（两 worker 同时 run_once 不双发，四重机制）：
1. **SKIP LOCKED 认领**：claim 单事务内 ``SELECT ... WHERE status IN ('PENDING','RETRY')
   AND (next_attempt_at IS NULL OR <= now) ORDER BY created_at FOR UPDATE SKIP LOCKED
   LIMIT batch`` → 置 SENDING + claimed_at=now → **先 commit 再发送**（发送在事务外，
   绝不跨网络调用持有行锁）。PG 下他 worker 的同款查询直接跳过已锁行；sqlite 测试
   方言下 SKIP LOCKED 为 no-op，互斥改由 SENDING 中间态保证（二次 claim 的 WHERE
   条件天然排除 SENDING）。
2. **行级刷新/确权（touch）**：每行**发送前**先 ``UPDATE SET claimed_at=now WHERE
   delivery_id=? AND status='SENDING' AND claimed_at=<认领时刻 epoch>``：
   - rowcount=0 → 行已被他方回收+重 claim（必已改写 claimed_at）——本 worker 不再
     拥有，**跳过发送**（比回写拦截更早防双发）；
   - 成功 → 该行 claimed_at 刷新为本行处理时刻（批尾行的 5min stale 窗口从本行起算，
     不受批首时刻与批长拖累），返回的新 epoch 作为回写 CAS 基准。
3. **epoch CAS 回写**：每条结果独立小事务 ``UPDATE ... WHERE delivery_id=? AND
   status='SENDING' AND claimed_at=<touch 后的 epoch>``——他方在发送期间回收+重
   claim（改写 claimed_at 为新 epoch）则 rowcount=0、本 worker 结果作废不覆盖，
   绝不摧毁他方在途认领（防「用户收 2 条、库里记 1 条」）。
4. **stale 回收**：``status='SENDING' AND (claimed_at IS NULL OR claimed_at <
   now-5min)`` 的滞留行（worker 崩溃残留；NULL 为手工改库的理论死态，防御性一并
   回收）置回 RETRY（next_attempt_at=now、attempt 不变），计 recovered，防永久卡
   SENDING。在途（新鲜 SENDING）绝不回收，防并行双发。

模式切换语义（配置 ``telegram_notification_mode`` 只影响 enqueue 与 worker 注册）：
- directed→broadcast：worker 不再注册，滞留 PENDING/RETRY 行**停投不丢**——切回
  shadow/directed 即恢复消费；
- directed→shadow：worker 仍注册（观测模式），**已入队的滞留行会继续被发出**——
  与「shadow 只入队对照不投递」的语义只对新事件成立；如需彻底静默，切换前把滞留
  行清空或置 FAILED。

重试判定（``TelegramSendResult``，Task 6 transport）：
- ok → SENT + sent_at；
- 429 → RETRY，退避 = ``next_retry(attempt, retry_after)``（尊重服务端 retry_after）；
- 网络错误（status_code=None）/ 5xx → RETRY，指数退避 30·2^(attempt-1)；
- 其余 4xx（含 401/403：bot 被 block / chat 不存在 / token 失效）→ **FAILED 终态**
  ——Telegram 4xx（除 429）是非瞬态客户端错误，重试无意义。401/403 **不自动清用户
  绑定**（保守决策）：401 同样会在 bot token 被吊销时全局出现，自动解绑会在一次
  token 事故中误伤全部已绑定用户、且需逐个重新绑定；仅 FAILED 留痕，绑定处置留给人。
- attempt+1 ≥ ``notification_retry_max`` → FAILED(retry_exceeded)（上限次尝试后放弃）。

多渠道路由（Task 9，构造注入 ``transports`` 后生效；缺省 None = 现状 Telegram 单渠道，
``sender``/``user.telegram_chat_id`` 老路径原样保留）：
- claim 快照携带 ``delivery.channel``（表列，非 join event）；``channel_enabled``
  回调判定暂停的已知渠道在 claim WHERE 中排除——delivery 保持 PENDING **停投不丢**，
  与未知渠道（认领后 FAILED(unsupported_channel) 终态）语义严格二分；
- ``_process`` 按 channel 从 ``transports`` registry 取 transport，未知渠道绝不回退
  Telegram；收件人不再取 user.telegram_chat_id，而是发送前经
  ``binding_repository.get_active`` 读当前绑定（claim 与发送之间用户重绑则以新收件人
  为准）；无 active binding → FAILED(notification.binding_missing)，不发起网络调用；
- 渲染走 ``render_message``（RenderedMessage 双通道投影），SendResult 契约映射：
  ok→SENT；recipient_invalid→FAILED 且 finalize **同一数据库事务**内
  ``revoke_if_current(user, channel, 本次实际使用的收件人)``（用户已重绑返回 False
  时只 finalize 不撤新绑定）；retryable→RETRY（retry_after 优先于默认退避）；
  其余→FAILED 终态。

best-effort：单条的「确权+发送+回写」全程在 per-row 守卫内——任一环节异常不中断
整批，该行按 worker_error 转 RETRY（有界重试后终态）；**回写自身失败**（DB 抖）也兜
底再试一次 worker_error RETRY（防「发送成功但回写失败」滞留 SENDING 被 5min 回收重
发造成用户重复收消息），兜底再失败才留 SENDING 交 stale 回收（at-least-once 下限）。

安全铁律（对齐 outbox/transport）：
- 日志只落 delivery_id/event_id/user_id/error_code/attempt 等白名单字段，**绝不落
  完整 chat_id / token / 渲染正文 / 异常串**（transport 侧已 redact，本模块不再触及）；
- payload 只经 NotificationTemplateService.render（Task 6，html.escape 全转义）进正文。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from zhice_os_v2.application.services.notification_template_service import (
    NotificationTemplateService,
)
from zhice_os_v2.application.services.telegram_notifier import TelegramSendResult
from zhice_os_v2.domain.notification.contracts import (
    NotificationTransport,
    SendResult,
)
from zhice_os_v2.domain.notification.preference import SUPPORTED_NOTIFICATION_CHANNELS
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)

logger = logging.getLogger(__name__)

# SENDING 滞留判据：claimed_at 超过该时长视为 worker 崩溃残留，回收为 RETRY
_STALE_SENDING_AFTER = timedelta(minutes=5)

# 会话/发送器注入签名（测试用；缺省 get_session / send_telegram）
_SessionFactory = Callable[[], Session]
_Sender = Callable[[str, str], TelegramSendResult]


def next_retry(attempt: int, retry_after: int | None) -> timedelta:
    """重试退避时长（纯函数，plan Task 7 Step 3）。

    - ``retry_after``（Telegram 429 服务端指示）优先，负值钳 0（防上游脏值把
      next_attempt_at 拉回过去造成立即重 claim 空转），封顶 3600s；
    - 否则指数退避 ``30 * 2^(attempt-1)``（本次失败后的尝试序号，attempt=1 → 30s、
      2 → 60s、3 → 120s…），封顶 3600s；attempt<1 按 1 计。
    """
    if retry_after is not None:
        return timedelta(seconds=min(max(retry_after, 0), 3600))
    return timedelta(seconds=min(30 * 2 ** max(attempt - 1, 0), 3600))


@dataclass(frozen=True)
class _Claimed:
    """claim 快照：commit 前取值，发送阶段不持有 ORM 实体/会话。"""

    delivery_id: str
    event_id: str
    user_id: str
    channel: str  # 投递渠道（表列，Task 9：registry 分流与撤绑的定位键）
    attempt_count: int
    claim_epoch: datetime  # 认领时写入的 claimed_at（touch/回写 CAS 的归属基准）


@dataclass(frozen=True)
class _Outcome:
    """单条发送结果分类（run_once 据此算终态与退避）。"""

    status: str  # "SENT" | "RETRY" | "FAILED"
    error_code: str | None = None
    retry_after: int | None = None
    # recipient_invalid 终态：撤绑目标 = 本次发送实际使用的收件人（发送前读到的
    # active binding recipient_id）；revoke_if_current 以其为比对值防误撤用户新绑
    revoke_recipient: str | None = None


@dataclass(frozen=True)
class _Revoke:
    """recipient_invalid 终态附带的条件撤销载荷（与 finalize 同一事务提交）。"""

    user_id: str
    channel: str
    recipient_id: str


def _default_session_factory() -> Session:
    """生产会话工厂（函数内导入，保持可 monkeypatch/延迟加载）。"""
    from zhice_os_v2.infrastructure.db.session import get_session

    return get_session()


class NotificationWorker:
    """outbox 消费 worker：每轮 ``run_once`` = 回收滞留 → 认领一批 → 逐条发送回写。

    渠道形态（Task 9）：
    - 缺省（``transports=None``）：现状 Telegram 单渠道——收件人取
      ``user.telegram_chat_id``、发送走 ``sender``/``send_telegram``，行为逐字不变；
    - 注入 ``transports``：按 ``delivery.channel`` 分流到 registry 中的 transport，
      收件人发送前经 ``binding_repository.get_active`` 读当前绑定；
      ``channel_enabled`` 回调返回 False 的渠道 claim 时排除（PENDING 停投不丢）。
    """

    def __init__(
        self,
        *,
        session_factory: _SessionFactory | None = None,
        sender: _Sender | None = None,
        template_service: NotificationTemplateService | None = None,
        batch_size: int | None = None,
        retry_max: int | None = None,
        transports: Mapping[str, NotificationTransport] | None = None,
        binding_repository: NotificationBindingRepository | None = None,
        channel_enabled: Callable[[str], bool] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sender_override = sender
        self._templates = template_service or NotificationTemplateService()
        if transports is not None:
            # 复审 I-1 fail-fast：registry 键越界（SUPPORTED 之外）若放行到运行期，
            # get_active 的 _validate_channel 会抛 ValueError 被 per-row 守卫吞成
            # RETRY(worker_error) 空转——接线错误必须在构造期暴露
            unknown = sorted(set(transports) - SUPPORTED_NOTIFICATION_CHANNELS)
            if unknown:
                raise ValueError(
                    f"notification.unsupported_channel:{','.join(unknown)}"
                )
        self._transports: dict[str, NotificationTransport] | None = (
            dict(transports) if transports is not None else None
        )
        self._binding_repository = binding_repository
        self._channel_enabled = channel_enabled
        if batch_size is None or retry_max is None:
            from zhice_os_v2.config.settings import get_settings

            settings = get_settings()
            if batch_size is None:
                batch_size = settings.notification_worker_batch_size
            if retry_max is None:
                retry_max = settings.notification_retry_max
        self._batch_size = int(batch_size)
        self._retry_max = int(retry_max)

    # ── 主流程 ────────────────────────────────────────────────────────────────
    def run_once(self) -> dict[str, int]:
        """跑一轮，返回统计 {claimed, sent, retried, failed, recovered}。

        stale 回收与 claim 的 DB 异常向上抛（由 scheduler job 兜底 try/except）；
        单条的「确权+发送+回写」全程 per-row 守卫（含 M-1 回写失败兜底），不中断整批。
        """
        now = datetime.now(UTC)
        stats = {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}

        stats["recovered"] = self._recover_stale(now)
        claimed = self._claim(now)
        stats["claimed"] = len(claimed)

        for row in claimed:
            epoch: datetime | None = None
            try:
                epoch = self._touch(row)
                if epoch is None:
                    # 行已被他方回收/重 claim：本 worker 不再拥有 → 不发送、不改写
                    logger.warning(
                        "notification_worker row_lost delivery_id=%s event_id=%s "
                        "user_id=%s",
                        row.delivery_id,
                        row.event_id,
                        row.user_id,
                    )
                    continue
                outcome = self._process(row)
            except Exception:  # noqa: BLE001 - 单条异常不中断整批
                logger.warning(
                    "notification_worker row_error delivery_id=%s event_id=%s "
                    "user_id=%s error_code=worker_error",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                )
                outcome = _Outcome(status="RETRY", error_code="worker_error")

            attempt = row.attempt_count + 1
            status, error_code = outcome.status, outcome.error_code
            if status == "RETRY" and attempt >= self._retry_max:
                status, error_code = "FAILED", "retry_exceeded"
            if epoch is None:
                # _touch 抛错未及赋值：行未被改写，仍属认领时刻 epoch（CAS 基准回退）
                epoch = row.claim_epoch

            finalize_now = datetime.now(UTC)  # I-2：退避起点/sent_at 以回写时刻为基准
            next_attempt_at = (
                finalize_now + next_retry(attempt, outcome.retry_after)
                if status == "RETRY"
                else None
            )

            try:
                applied = self._finalize(
                    row.delivery_id,
                    epoch=epoch,
                    status=status,
                    error_code=error_code,
                    attempt=attempt,
                    next_attempt_at=next_attempt_at,
                    now=finalize_now,
                    revoke=self._revoke_for(outcome, row=row, status=status),
                )
            except Exception:  # noqa: BLE001 - M-1：回写失败兜底，防滞留 SENDING 被重发
                logger.warning(
                    "notification_worker finalize_error delivery_id=%s event_id=%s "
                    "user_id=%s error_code=worker_error",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                )
                if self._finalize_worker_error(
                    row,
                    epoch=epoch,
                    attempt=attempt,
                    now=finalize_now,
                    revoke=self._revoke_for(outcome, row=row, status=status),
                ):
                    stats["retried" if attempt < self._retry_max else "failed"] += 1
                continue
            if not applied:
                # CAS 落空（status/epoch 不匹配）：行已被他方改走，本 worker 结果作废
                logger.warning(
                    "notification_worker row_lost delivery_id=%s event_id=%s user_id=%s",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                )
                continue

            if status == "SENT":
                stats["sent"] += 1
                logger.info(
                    "notification_worker sent delivery_id=%s event_id=%s user_id=%s "
                    "attempt=%d",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                    attempt,
                )
            elif status == "RETRY":
                stats["retried"] += 1
                logger.warning(
                    "notification_worker retry delivery_id=%s event_id=%s user_id=%s "
                    "error_code=%s attempt=%d",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                    error_code,
                    attempt,
                )
            else:
                stats["failed"] += 1
                logger.warning(
                    "notification_worker failed delivery_id=%s event_id=%s user_id=%s "
                    "error_code=%s attempt=%d",
                    row.delivery_id,
                    row.event_id,
                    row.user_id,
                    error_code,
                    attempt,
                )

        logger.info(
            "notification_worker run claimed=%d sent=%d retried=%d failed=%d "
            "recovered=%d",
            stats["claimed"],
            stats["sent"],
            stats["retried"],
            stats["failed"],
            stats["recovered"],
        )
        return stats

    # ── 步骤 1：stale 回收 ────────────────────────────────────────────────────
    def _recover_stale(self, now: datetime) -> int:
        """滞留 SENDING（claimed_at 超时或 NULL 死态）→ RETRY（next=now，attempt 不变）。

        M-3：``claimed_at < cutoff`` 对 NULL 永不命中——SENDING+NULL 理论不可达
        （代码路径恒先写 claimed_at），但手工改库可造，防御性一并回收防永久滞留。
        """
        with self._open() as s:
            recovered = (
                s.query(orm.NotificationDeliveryModel)
                .filter(
                    orm.NotificationDeliveryModel.status == "SENDING",
                    or_(
                        orm.NotificationDeliveryModel.claimed_at.is_(None),
                        orm.NotificationDeliveryModel.claimed_at
                        < now - _STALE_SENDING_AFTER,
                    ),
                )
                .update(
                    {"status": "RETRY", "next_attempt_at": now, "claimed_at": None},
                    synchronize_session=False,
                )
            )
            s.commit()
            return int(recovered or 0)

    # ── 步骤 2：SKIP LOCKED 认领 ──────────────────────────────────────────────
    def _claim(self, now: datetime) -> list[_Claimed]:
        """认领到期 PENDING/RETRY 一批：单事务内锁定→SENDING→commit（发送在事务外）。

        Task 9：``channel_enabled`` 判定暂停的已知渠道在 WHERE 排除（delivery 保持
        PENDING 停投不丢）；未知渠道值不参与排除——照常认领后由 ``_process`` 终态
        FAILED(unsupported_channel)，两种语义严格二分。
        """
        conditions = [
            orm.NotificationDeliveryModel.status.in_(("PENDING", "RETRY")),
            or_(
                orm.NotificationDeliveryModel.next_attempt_at.is_(None),
                orm.NotificationDeliveryModel.next_attempt_at <= now,
            ),
        ]
        disabled = self._disabled_channels()
        if disabled:
            conditions.append(orm.NotificationDeliveryModel.channel.not_in(disabled))
        with self._open() as s:
            rows = (
                s.execute(
                    select(orm.NotificationDeliveryModel)
                    .where(*conditions)
                    .order_by(
                        orm.NotificationDeliveryModel.created_at,
                        orm.NotificationDeliveryModel.id,
                    )
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            claimed = [
                _Claimed(
                    delivery_id=r.delivery_id,
                    event_id=r.event_id,
                    user_id=r.user_id,
                    channel=str(r.channel or "telegram"),
                    attempt_count=int(r.attempt_count or 0),
                    claim_epoch=now,
                )
                for r in rows
            ]
            for r in rows:
                r.status = "SENDING"
                r.claimed_at = now
            s.commit()
            return claimed

    # ── 步骤 2.5：行级刷新/确权（I-2 + I-1）──────────────────────────────────
    def _touch(self, row: _Claimed) -> datetime | None:
        """发送前刷新该行 claimed_at 为当前时刻并返回新 epoch；行已被取走返回 None。

        - I-2：整批若共盖批首 t0，网络劣化下批尾行认领龄会破 5min stale 阈值被误
          回收（正是 epoch CAS 的放大器）；逐行刷新让阈值从「本行处理开始」起算；
        - I-1：刷新以认领时刻 epoch 为前置 CAS——他方回收+重 claim 必已改写
          claimed_at，rowcount=0 即认领丢失，调用方跳过发送（防双发的前置防线）。
        """
        t = datetime.now(UTC)
        with self._open() as s:
            res = cast(
                CursorResult[Any],
                s.execute(
                    update(orm.NotificationDeliveryModel)
                    .where(
                        orm.NotificationDeliveryModel.delivery_id == row.delivery_id,
                        orm.NotificationDeliveryModel.status == "SENDING",
                        orm.NotificationDeliveryModel.claimed_at == row.claim_epoch,
                    )
                    .values(claimed_at=t)
                ),
            )
            s.commit()
            return t if bool(res.rowcount) else None

    # ── 步骤 3：逐条处理（读 event/user → 渲染 → 发送）────────────────────────
    def _process(self, row: _Claimed) -> _Outcome:
        registry = self._transports
        if registry is None:
            return self._process_telegram(row)
        return self._process_routed(row, registry)

    def _process_telegram(self, row: _Claimed) -> _Outcome:
        """缺省单渠道路径（transports 未注入）：收件人=user.telegram_chat_id。

        Phase C Task 7 行为逐字保留——既有并发语义测试（34 个）全量回归守护本路径。
        """
        with self._open() as s:
            event = s.execute(
                select(orm.NotificationEventModel).where(
                    orm.NotificationEventModel.event_id == row.event_id
                )
            ).scalar_one_or_none()
            user = s.execute(
                select(orm.UserModel).where(orm.UserModel.user_id == row.user_id)
            ).scalar_one_or_none()
            event_type = event.event_type if event is not None else ""
            payload = dict(event.payload or {}) if event is not None else {}
            locale = (user.locale if user is not None else "") or "zh"
            chat_id = (user.telegram_chat_id if user is not None else "") or ""

        if not str(chat_id).strip():
            return _Outcome(status="FAILED", error_code="no_chat_binding")

        text = self._templates.render(event_type, locale=locale, payload=payload)
        result = self._resolve_sender()(str(chat_id), text)
        if result.ok:
            return _Outcome(status="SENT")

        # 终审 Minor #6：bot token 未配置是部署级缺配，重试不会自愈——立即终态
        # FAILED(not_configured)，不空转 6 次退避（≈31 分钟）后 FAILED。
        if result.error_code == "not_configured":
            return _Outcome(status="FAILED", error_code="not_configured")

        status_code = result.status_code
        if status_code is None or status_code == 429 or status_code >= 500:
            # 网络（无响应）/ 429 / 5xx：瞬态 → 可重试
            return _Outcome(
                status="RETRY",
                error_code=result.error_code or "network",
                retry_after=result.retry_after,
            )
        # 其余 4xx（401/403/400/404…）：非瞬态 → 终态 FAILED（见模块 docstring 决策）
        return _Outcome(status="FAILED", error_code=result.error_code or "unknown")

    def _process_routed(
        self, row: _Claimed, registry: Mapping[str, NotificationTransport]
    ) -> _Outcome:
        """多渠道路径（Task 9）：registry 分流 → 读当前 binding → 渲染 → transport。

        - 未知 channel → FAILED(unsupported_channel)，绝不回退 Telegram。双保险
          （复审 I-1）：构造已校验 registry 键 ⊆ SUPPORTED；此处再挡渠道值本身
          不在 SUPPORTED（防绕过构造的脏 registry / 脏库渠道值）——若漏挡，
          ``get_active`` 的 ``_validate_channel`` 会抛 ValueError 被 per-row 守卫
          吞成 RETRY(worker_error) 空转，违背「未知渠道=立即终态」二分；
        - 收件人 = 发送前 ``get_active`` 读到的当前绑定 recipient_id（claim 与发送之间
          用户重绑以新值为准）；无 active binding → FAILED(notification.binding_missing)，
          不发起任何网络调用；
        - 发送在会话/事务外（binding 读取事务先关闭，绝不跨网络调用持锁）。
        """
        if row.channel not in SUPPORTED_NOTIFICATION_CHANNELS:
            return _Outcome(status="FAILED", error_code="unsupported_channel")
        transport = registry.get(row.channel)
        if transport is None:
            return _Outcome(status="FAILED", error_code="unsupported_channel")

        repo = self._binding_repository or NotificationBindingRepository()
        with self._open() as s:
            event = s.execute(
                select(orm.NotificationEventModel).where(
                    orm.NotificationEventModel.event_id == row.event_id
                )
            ).scalar_one_or_none()
            user = s.execute(
                select(orm.UserModel).where(orm.UserModel.user_id == row.user_id)
            ).scalar_one_or_none()
            binding = repo.get_active(s, row.user_id, row.channel)
            event_type = event.event_type if event is not None else ""
            payload = dict(event.payload or {}) if event is not None else {}
            locale = (user.locale if user is not None else "") or "zh"
            recipient = str(binding.recipient_id) if binding is not None else ""

        if not recipient.strip():
            return _Outcome(status="FAILED", error_code="notification.binding_missing")

        message = self._templates.render_message(event_type, locale=locale, payload=payload)
        result = transport.send(recipient, message)
        return self._map_send_result(result, recipient=recipient)

    @staticmethod
    def _map_send_result(result: SendResult, *, recipient: str) -> _Outcome:
        """渠道无关 SendResult → _Outcome（Task 6 契约字段直映射）。

        recipient_invalid 优先于 retryable：收件人已被服务端验证无效是确定性终态，
        撤绑目标=本次实际使用的收件人（revoke_if_current 比对防误撤用户新绑）。
        """
        if result.ok:
            return _Outcome(status="SENT")
        if result.recipient_invalid:
            return _Outcome(
                status="FAILED",
                error_code=result.error_code or "recipient_invalid",
                revoke_recipient=recipient,
            )
        if result.retryable:
            return _Outcome(
                status="RETRY",
                error_code=result.error_code or "network",
                retry_after=result.retry_after,
            )
        return _Outcome(status="FAILED", error_code=result.error_code or "unknown")

    @staticmethod
    def _revoke_for(
        outcome: _Outcome, *, row: _Claimed, status: str
    ) -> _Revoke | None:
        """终态 FAILED 且 transport 判收件人无效时，装配与 finalize 同事务的撤绑载荷。"""
        if outcome.revoke_recipient is None or status != "FAILED":
            return None
        return _Revoke(
            user_id=row.user_id, channel=row.channel, recipient_id=outcome.revoke_recipient
        )

    # ── 步骤 4：epoch CAS 回写 ────────────────────────────────────────────────
    def _finalize(
        self,
        delivery_id: str,
        *,
        epoch: datetime,
        status: str,
        error_code: str | None,
        attempt: int,
        next_attempt_at: datetime | None,
        now: datetime,
        revoke: _Revoke | None = None,
    ) -> bool:
        """compare-and-set 落结果：仅当行仍为 SENDING 且 claimed_at=本 worker epoch。

        epoch（``_touch`` 返回的行处理时刻）不匹配即行已被他方回收/重 claim——回写
        作废绝不覆盖他方在途认领（I-1：防「用户收 2 条、库里记 1 条」）。返回是否
        真正写入。

        Task 9：``revoke``（recipient_invalid 终态）在**同一事务**内先于回写执行
        ``revoke_if_current``（锁序 user→binding→delivery），delivery 终态与条件撤绑
        原子提交；用户已重绑（返回 False）只落终态不撤新绑。撤绑在 CAS 判定前执行
        ——发送确已用旧收件人发生，即使行已被他方回收，撤掉仍指向旧收件人的绑定
        依然正确（新绑定因 recipient 比对天然免疫）。
        """
        values: dict[str, object] = {
            "status": status,
            "attempt_count": attempt,
            "claimed_at": None,  # 离开在途态即清标记（claimed_at 非空 ≈ 在途）
            "next_attempt_at": next_attempt_at,
            "last_error_code": error_code,
        }
        if status == "SENT":
            values["sent_at"] = now
        with self._open() as s:
            if revoke is not None:
                repo = self._binding_repository or NotificationBindingRepository()
                revoked = repo.revoke_if_current(
                    s, revoke.user_id, revoke.channel, revoke.recipient_id, now
                )
                # 白名单日志：绝不落收件人（WxPusher UID / chat_id 均敏感）
                logger.warning(
                    "notification_worker binding_%s user_id=%s channel=%s "
                    "delivery_id=%s",
                    "revoked" if revoked else "rebind_kept",
                    revoke.user_id,
                    revoke.channel,
                    delivery_id,
                )
            res = cast(
                CursorResult[Any],
                s.execute(
                    update(orm.NotificationDeliveryModel)
                    .where(
                        orm.NotificationDeliveryModel.delivery_id == delivery_id,
                        orm.NotificationDeliveryModel.status == "SENDING",
                        orm.NotificationDeliveryModel.claimed_at == epoch,
                    )
                    .values(**values)
                ),
            )
            s.commit()
            return bool(res.rowcount)

    def _finalize_worker_error(
        self,
        row: _Claimed,
        *,
        epoch: datetime | None,
        attempt: int,
        now: datetime,
        revoke: _Revoke | None = None,
    ) -> bool:
        """M-1 兜底：结果回写自身失败（DB 抖）后，尽力把行置 RETRY(worker_error)。

        防「发送成功但回写失败」的行滞留 SENDING、被 5min stale 回收后**重发**（用户
        重复收消息）；受 retry_max 上限约束（attempt 达上限置 FAILED/retry_exceeded，
        不无限循环）。兜底再失败（DB 仍不可用）则留 SENDING 交 stale 回收——
        at-least-once 语义下限，由下轮 worker 收敛。

        复审 S3：``revoke``（recipient_invalid 终态载荷）随兜底透传——发送确已判定
        收件人无效，撤绑不因回写抖动丢失；行即便兜底为 RETRY，下轮重投会在
        binding_missing 处收敛终态。
        """
        fallback_status = "RETRY" if attempt < self._retry_max else "FAILED"
        fallback_code = (
            "worker_error" if fallback_status == "RETRY" else "retry_exceeded"
        )
        try:
            return self._finalize(
                row.delivery_id,
                epoch=epoch if epoch is not None else row.claim_epoch,
                status=fallback_status,
                error_code=fallback_code,
                attempt=attempt,
                next_attempt_at=(
                    now + next_retry(attempt, None)
                    if fallback_status == "RETRY"
                    else None
                ),
                now=now,
                revoke=revoke,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "notification_worker finalize_fallback_failed delivery_id=%s "
                "event_id=%s user_id=%s stale_recovery_will_retry",
                row.delivery_id,
                row.event_id,
                row.user_id,
            )
            return False

    # ── 内部 ──────────────────────────────────────────────────────────────────
    def _disabled_channels(self) -> tuple[str, ...]:
        """channel_enabled 判定暂停的已知渠道（排序保 SQL 确定性）。

        仅遍历 SUPPORTED_NOTIFICATION_CHANNELS：未知渠道值不参与暂停判定，照常
        认领后由 ``_process_routed`` 终态 FAILED(unsupported_channel)——「暂停
        （不认领、PENDING 保留）」与「未知（终态失败）」语义严格二分。
        """
        enabled = self._channel_enabled
        if enabled is None:
            return ()
        return tuple(ch for ch in sorted(SUPPORTED_NOTIFICATION_CHANNELS) if not enabled(ch))

    def _open(self) -> Session:
        factory = self._session_factory or _default_session_factory
        return factory()

    def _resolve_sender(self) -> _Sender:
        if self._sender_override is not None:
            return self._sender_override
        from zhice_os_v2.application.services import telegram_notifier

        return telegram_notifier.send_telegram
