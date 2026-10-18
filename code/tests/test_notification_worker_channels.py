"""通知 worker 多渠道分流与条件撤销（多渠道改造 Task 9）。

覆盖（契约最小测试集 + 补充）：
- 分流：wxpusher delivery 只调 wxpusher transport（telegram 零调用），
  且消息体是 render_message 产出的 RenderedMessage（html 通道含业务字段）；
- 发送前读当前 binding：claim 后用户重绑（UID_A→UID_NEW）→ 发送用 UID_NEW；
- 无 active binding（已撤销）→ FAILED(notification.binding_missing)，零网络调用；
- recipient_invalid=True：
  - 正常路径 → delivery FAILED + binding 撤销同轮生效（同一数据库事务）；
  - 发送期间用户已重绑 UID_NEW → revoke_if_current 落空，新绑定原样保留；
- 未知 channel（registry 无此键）→ FAILED(unsupported_channel)，不调任何 transport，
  绝不回退 Telegram；
- 暂停渠道（channel_enabled 返回 False）→ claim WHERE 排除，delivery 保持 PENDING，
  已启用渠道照常认领；
- SendResult 映射：retryable=False → FAILED 终态；retryable=True 且带 retry_after
  → RETRY 且 retry_after 优先于默认指数退避（无 retry_after 走 30s 起步）。

复核加固（Task 9 复审 I-1/S2/S3/S5/S6）：
- registry 键越界双保险：构造即抛 ValueError（fail-fast 接线错误）+ 脏 registry/
  脏库渠道值在 _process_routed 判 FAILED(unsupported_channel) 终态——绝不落入
  get_active 的 ValueError→RETRY(worker_error) 空转第三象限；
- channel_enabled 回调抛异常 → run_once 上抛（fail-stop 交 scheduler 兜底），零
  delivery 状态变化；
- M-1 兜底（finalize DB 抖）透传 revoke：recipient_invalid 轮撤绑不因回写抖动丢失；
- next_retry 负值 retry_after 钳 0（next_attempt_at 绝不早于 now）；
- CAS 落空仍撤绑（发送确已用旧收件人发生，他方回收不撤销该裁决）。

既有 test_notification_worker.py（34 个）回归守护默认参数路径 = 纯 Telegram 现状。
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.notification_worker import (
    NotificationWorker,
    next_retry,
)
from zhice_os_v2.domain.notification.contracts import RenderedMessage, SendResult
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)

_NOW = datetime.now(UTC)
_ZERO_STATS = {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}


def _aware(dt: datetime | None) -> datetime:
    """sqlite 读回 naive 时间 → 统一补 UTC（PG 返回 aware，原样返回）。

    返回恒非 None：调用方须先断言字段非空（收窄 datetime | None → datetime）。
    """
    assert dt is not None, "调用方须先断言时间字段非 None"
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


class RecordingTransport:
    """按 NotificationTransport 协议记录调用的假 transport（默认成功）。"""

    def __init__(
        self,
        result: SendResult | None = None,
        on_send: Callable[[str, RenderedMessage], None] | None = None,
    ) -> None:
        self.calls: list[tuple[str, RenderedMessage]] = []
        self._result = result
        self._on_send = on_send

    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
        self.calls.append((recipient_id, message))
        if self._on_send is not None:
            self._on_send(recipient_id, message)
        return self._result if self._result is not None else SendResult(ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 隔离环境：内存 sqlite + 种子（user/event/delivery/binding）+ 多渠道 worker 工厂
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def channels_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def seed(
        *,
        user_id: str | None = None,
        channel: str = "wxpusher",
        recipient_id: str | None = "UID_A",
        binding_status: str | None = "active",
        locale: str = "zh",
        event_id: str | None = None,
        delivery_id: str | None = None,
        delivery_status: str = "PENDING",
    ) -> str:
        """种一对 user/event/delivery（指定渠道）+ 可选 binding 行，返回 delivery_id。"""
        sfx = uuid.uuid4().hex[:8]
        uid = user_id or f"u-ch-{sfx}"
        eid = event_id or f"evt-ch-{sfx}"
        did = delivery_id or f"dlv-ch-{sfx}"
        s = Session()
        try:
            # telegram_chat_id 留空：多渠道路径的收件人只来自 binding 表
            s.add(
                orm.UserModel(
                    user_id=uid,
                    username=uid,
                    password_hash="x",
                    role="TRADER",
                    is_active=True,
                    locale=locale,
                    created_at=_NOW,
                )
            )
            s.flush()
            s.add(
                orm.NotificationEventModel(
                    event_id=eid,
                    user_id=uid,
                    event_type="fill",
                    payload={
                        "fill_id": "f1",
                        "symbol": "AAPL",
                        "side": "BUY",
                        "price": 10,
                        "qty": 2,
                    },
                    occurred_at=_NOW,
                    created_at=_NOW,
                )
            )
            s.flush()
            s.add(
                orm.NotificationDeliveryModel(
                    delivery_id=did,
                    event_id=eid,
                    user_id=uid,
                    channel=channel,
                    status=delivery_status,
                    attempt_count=0,
                    created_at=_NOW,
                )
            )
            if binding_status is not None:
                s.add(
                    orm.UserNotificationBindingModel(
                        binding_id=f"nbind-test-{sfx}",
                        user_id=uid,
                        channel=channel,
                        recipient_id=recipient_id or "",
                        status=binding_status,
                        bound_at=_NOW,
                        created_at=_NOW,
                        updated_at=_NOW,
                    )
                )
            s.commit()
            return did
        finally:
            s.close()

    def fetch(delivery_id: str) -> SimpleNamespace:
        s = Session()
        try:
            row = (
                s.query(orm.NotificationDeliveryModel)
                .filter_by(delivery_id=delivery_id)
                .one()
            )
            return SimpleNamespace(
                status=row.status,
                attempt_count=int(row.attempt_count or 0),
                next_attempt_at=row.next_attempt_at,
                last_error_code=row.last_error_code,
                claimed_at=row.claimed_at,
                sent_at=row.sent_at,
            )
        finally:
            s.close()

    def binding(user_id: str, channel: str) -> SimpleNamespace:
        s = Session()
        try:
            row = (
                s.query(orm.UserNotificationBindingModel)
                .filter_by(user_id=user_id, channel=channel)
                .one()
            )
            return SimpleNamespace(
                recipient_id=row.recipient_id,
                status=row.status,
                revoked_at=row.revoked_at,
            )
        finally:
            s.close()

    def rebind(user_id: str, channel: str, new_recipient: str) -> None:
        """直接改绑定收件人（模拟用户重绑）。"""
        s = Session()
        try:
            s.execute(
                update(orm.UserNotificationBindingModel)
                .where(
                    orm.UserNotificationBindingModel.user_id == user_id,
                    orm.UserNotificationBindingModel.channel == channel,
                )
                .values(recipient_id=new_recipient, updated_at=datetime.now(UTC))
            )
            s.commit()
        finally:
            s.close()

    def make_worker(
        *,
        transports: dict[str, RecordingTransport] | None = None,
        channel_enabled: Callable[[str], bool] | None = None,
        batch_size: int = 50,
        retry_max: int = 6,
    ) -> NotificationWorker:
        return NotificationWorker(
            session_factory=Session,
            transports=transports
            if transports is not None
            else {"telegram": RecordingTransport(), "wxpusher": RecordingTransport()},
            binding_repository=NotificationBindingRepository(),
            channel_enabled=channel_enabled,
            batch_size=batch_size,
            retry_max=retry_max,
        )

    def user_of(delivery_id: str) -> str:
        s = Session()
        try:
            return str(
                s.query(orm.NotificationDeliveryModel.user_id)
                .filter_by(delivery_id=delivery_id)
                .one()[0]
            )
        finally:
            s.close()

    return SimpleNamespace(
        Session=Session,
        seed=seed,
        fetch=fetch,
        binding=binding,
        rebind=rebind,
        make_worker=make_worker,
        user_of=user_of,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 分流：按 claimed.channel 取 transport
# ──────────────────────────────────────────────────────────────────────────────
def test_worker_dispatches_by_claimed_channel(channels_env) -> None:
    """wxpusher delivery → 只调 wxpusher transport；消息体为 RenderedMessage(html)。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    tg = RecordingTransport()
    wx = RecordingTransport()
    w = channels_env.make_worker(transports={"telegram": tg, "wxpusher": wx})

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "sent": 1}
    assert len(wx.calls) == 1
    assert wx.calls[0][0] == "UID_A"
    message = wx.calls[0][1]
    assert isinstance(message, RenderedMessage)
    assert "AAPL" in message.html  # render_message 渲染自 event payload
    assert channels_env.fetch(did).status == "SENT"
    assert tg.calls == []  # 绝不旁路调用 Telegram


def test_unknown_channel_fails_unsupported_without_transport_call(channels_env) -> None:
    """registry 无此渠道键 → FAILED(unsupported_channel)，零 transport 调用，不回退 Telegram。"""
    did = channels_env.seed(channel="sms", binding_status=None)
    tg = RecordingTransport()
    wx = RecordingTransport()
    w = channels_env.make_worker(transports={"telegram": tg, "wxpusher": wx})

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "failed": 1}
    row = channels_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "unsupported_channel"
    assert row.attempt_count == 1
    assert tg.calls == [] and wx.calls == []


# ──────────────────────────────────────────────────────────────────────────────
# 发送前读当前 binding
# ──────────────────────────────────────────────────────────────────────────────
def test_worker_reads_current_binding_after_claim(channels_env) -> None:
    """claim 后用户重绑（UID_A→UID_NEW）→ 发送用重绑后的 UID_NEW。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    wx = RecordingTransport()
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )
    uid = channels_env.user_of(did)
    real_touch = w._touch

    def touch_after_rebind(row: Any) -> datetime | None:
        # 认领已 commit、_process 读 binding 之前：模拟用户此刻完成重绑
        channels_env.rebind(uid, "wxpusher", "UID_NEW")
        return real_touch(row)

    w._touch = touch_after_rebind

    stats = w.run_once()

    assert stats["sent"] == 1
    assert len(wx.calls) == 1
    assert wx.calls[0][0] == "UID_NEW"


def test_revoked_binding_fails_without_network_call(channels_env) -> None:
    """无 active binding（已撤销）→ FAILED(notification.binding_missing)，零网络调用。"""
    did = channels_env.seed(channel="wxpusher", binding_status="revoked")
    tg = RecordingTransport()
    wx = RecordingTransport()
    w = channels_env.make_worker(transports={"telegram": tg, "wxpusher": wx})

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "failed": 1}
    row = channels_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "notification.binding_missing"
    assert tg.calls == [] and wx.calls == []


# ──────────────────────────────────────────────────────────────────────────────
# recipient_invalid：条件撤销
# ──────────────────────────────────────────────────────────────────────────────
def test_recipient_invalid_fails_and_revokes_binding(channels_env) -> None:
    """recipient_invalid=True 正常路径：delivery FAILED + binding 撤销同轮都生效（同事务）。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    wx = RecordingTransport(
        result=SendResult(ok=False, error_code="uid_invalid_verified", recipient_invalid=True)
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )
    uid = channels_env.user_of(did)

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "failed": 1}
    row = channels_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "uid_invalid_verified"
    binding = channels_env.binding(uid, "wxpusher")
    assert binding.status == "revoked"
    assert binding.revoked_at is not None


def test_invalid_old_uid_does_not_revoke_new_binding(channels_env) -> None:
    """发送期间用户已重绑 UID_NEW → revoke_if_current(UID_A) 落空，新绑定原样保留。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    uid = channels_env.user_of(did)

    def rebind_during_send(recipient_id: str, message: RenderedMessage) -> None:
        # worker 已读到 UID_A 并发起发送；撤绑判定前用户完成重绑
        channels_env.rebind(uid, "wxpusher", "UID_NEW")

    wx = RecordingTransport(
        result=SendResult(ok=False, error_code="uid_invalid_verified", recipient_invalid=True),
        on_send=rebind_during_send,
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats["failed"] == 1
    assert channels_env.fetch(did).status == "FAILED"
    binding = channels_env.binding(uid, "wxpusher")
    assert binding.status == "active"  # 新绑定不被误撤
    assert binding.recipient_id == "UID_NEW"


# ──────────────────────────────────────────────────────────────────────────────
# 暂停渠道：claim WHERE 排除
# ──────────────────────────────────────────────────────────────────────────────
def test_disabled_channel_is_not_claimed(channels_env) -> None:
    """channel_enabled(wxpusher)=False → 不 claim（delivery 保持 PENDING），零调用。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    tg = RecordingTransport()
    wx = RecordingTransport()
    w = channels_env.make_worker(
        transports={"telegram": tg, "wxpusher": wx},
        channel_enabled=lambda ch: ch != "wxpusher",
    )

    stats = w.run_once()

    assert stats == _ZERO_STATS
    assert channels_env.fetch(did).status == "PENDING"
    assert tg.calls == [] and wx.calls == []


def test_enabled_channel_still_claimed_under_filter(channels_env) -> None:
    """同一 channel_enabled 下，启用渠道照常认领发送（WHERE 不误伤全量）。"""
    did_tg = channels_env.seed(
        channel="telegram",
        recipient_id="5550001111",
        event_id="evt-tg-en",
        delivery_id="dlv-tg-en",
    )
    channels_env.seed(
        channel="wxpusher",
        recipient_id="UID_A",
        event_id="evt-wx-dis",
        delivery_id="dlv-wx-dis",
    )
    tg = RecordingTransport()
    wx = RecordingTransport()
    w = channels_env.make_worker(
        transports={"telegram": tg, "wxpusher": wx},
        channel_enabled=lambda ch: ch != "wxpusher",
    )

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "sent": 1}
    assert len(tg.calls) == 1 and tg.calls[0][0] == "5550001111"
    assert wx.calls == []
    assert channels_env.fetch(did_tg).status == "SENT"
    assert channels_env.fetch("dlv-wx-dis").status == "PENDING"


# ──────────────────────────────────────────────────────────────────────────────
# SendResult 映射：终态/重试/retry_after 优先
# ──────────────────────────────────────────────────────────────────────────────
def test_non_retryable_failure_is_terminal_multichannel(channels_env) -> None:
    """not ok 且 not retryable → FAILED 终态（不再退避重试）。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    wx = RecordingTransport(
        result=SendResult(ok=False, status_code=403, error_code="http_403", retryable=False)
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "failed": 1}
    row = channels_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "http_403"
    assert row.next_attempt_at is None
    # 非 recipient_invalid 的失败不动绑定
    binding = channels_env.binding(channels_env.user_of(did), "wxpusher")
    assert binding.status == "active"


def test_retry_after_preferred_over_default_backoff(channels_env) -> None:
    """retryable=True + retry_after=12 → RETRY 且 next_attempt_at≈+12s（优先于 30s 默认退避）。"""
    did = channels_env.seed(
        channel="wxpusher",
        recipient_id="UID_A",
        event_id="evt-ra",
        delivery_id="dlv-ra",
    )
    before = datetime.now(UTC)
    wx = RecordingTransport(
        result=SendResult(
            ok=False, status_code=429, error_code="http_429", retryable=True, retry_after=12
        )
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats["retried"] == 1
    row = channels_env.fetch(did)
    assert row.status == "RETRY"
    assert row.attempt_count == 1
    assert row.last_error_code == "http_429"
    assert row.next_attempt_at is not None
    delay = (_aware(row.next_attempt_at) - before).total_seconds()
    assert 8 <= delay <= 20, f"retry_after=12 应得 +12s，实为 +{delay}s"


def test_retryable_without_retry_after_uses_default_backoff(channels_env) -> None:
    """retryable=True 无 retry_after → 默认指数退避（首次 30s）。"""
    did = channels_env.seed(
        channel="wxpusher",
        recipient_id="UID_A",
        event_id="evt-def",
        delivery_id="dlv-def",
    )
    before = datetime.now(UTC)
    wx = RecordingTransport(result=SendResult(ok=False, error_code="network", retryable=True))
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats["retried"] == 1
    row = channels_env.fetch(did)
    assert row.status == "RETRY"
    assert row.next_attempt_at is not None
    delay = (_aware(row.next_attempt_at) - before).total_seconds()
    assert 25 <= delay <= 40, f"默认首次退避应 30s，实为 +{delay}s"


# ──────────────────────────────────────────────────────────────────────────────
# 复核加固（I-1/S2/S3/S5/S6）
# ──────────────────────────────────────────────────────────────────────────────
def test_registry_with_unsupported_key_fails_fast_at_construction(channels_env) -> None:
    """I-1：transports registry 含 SUPPORTED 之外的键 → 构造即抛 ValueError。

    不 fail-fast 时该键对应的 delivery 会落入 get_active 的 _validate_channel
    ValueError → 被 per-row 守卫吞成 RETRY(worker_error) 空转 6 次后
    FAILED(retry_exceeded)，违背「未知渠道=立即终态」的二分语义。
    """
    with pytest.raises(ValueError, match="notification.unsupported_channel"):
        channels_env.make_worker(
            transports={"telegram": RecordingTransport(), "sms": RecordingTransport()}
        )


def test_dirty_registry_channel_fails_terminal_not_worker_error(channels_env) -> None:
    """I-1 双保险：绕过构造注入的脏 registry 键 → FAILED(unsupported_channel) 终态。

    模拟绕过 __init__ 校验的脏 registry / 脏库渠道值——绝无 RETRY(worker_error) 空转、
    绝不发起网络调用。
    """
    did = channels_env.seed(channel="sms", binding_status=None)
    tg = RecordingTransport()
    rogue = RecordingTransport()
    w = channels_env.make_worker(transports={"telegram": tg, "wxpusher": rogue})
    assert w._transports is not None
    w._transports["sms"] = RecordingTransport()  # 模拟脏注入

    stats = w.run_once()

    assert stats == {**_ZERO_STATS, "claimed": 1, "failed": 1}
    row = channels_env.fetch(did)
    assert row.status == "FAILED"  # 立即终态，非 RETRY 空转
    assert row.last_error_code == "unsupported_channel"
    assert row.attempt_count == 1
    assert tg.calls == [] and rogue.calls == []


def test_channel_enabled_callback_exception_propagates_without_touching_rows(
    channels_env,
) -> None:
    """S2：channel_enabled 回调抛异常 → run_once 上抛（fail-stop，scheduler 兜底）。

    异常发生在 claim 事务开启前：零 delivery 状态变化、零 transport 调用。
    """
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    tg = RecordingTransport()
    wx = RecordingTransport()

    def broken_enabled(channel: str) -> bool:
        raise RuntimeError("paused-source-down")

    w = channels_env.make_worker(
        transports={"telegram": tg, "wxpusher": wx},
        channel_enabled=broken_enabled,
    )

    with pytest.raises(RuntimeError, match="paused-source-down"):
        w.run_once()

    row = channels_env.fetch(did)
    assert row.status == "PENDING"  # 零状态变化
    assert tg.calls == [] and wx.calls == []


def test_revoke_survives_finalize_db_hiccup_via_fallback(channels_env) -> None:
    """S3：主 finalize DB 抖 → M-1 兜底仍透传 revoke，撤绑不因回写抖动丢失。"""
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    wx = RecordingTransport(
        result=SendResult(ok=False, error_code="uid_invalid_verified", recipient_invalid=True)
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )
    uid = channels_env.user_of(did)
    real_finalize = w._finalize
    raised: list[str] = []

    def flaky_finalize(delivery_id: str, **kwargs: object) -> bool:
        if delivery_id == did and delivery_id not in raised:
            raised.append(delivery_id)
            raise RuntimeError("db hiccup")
        return real_finalize(delivery_id, **kwargs)

    w._finalize = flaky_finalize

    stats = w.run_once()  # 不外抛

    assert stats["retried"] == 1  # 兜底 RETRY(worker_error)，attempt=1 < 6
    row = channels_env.fetch(did)
    assert row.status == "RETRY"
    assert row.last_error_code == "worker_error"
    binding = channels_env.binding(uid, "wxpusher")
    assert binding.status == "revoked"  # 撤绑未丢失（兜底路径透传 revoke）
    assert binding.revoked_at is not None


def test_cas_miss_still_revokes_stale_recipient(channels_env) -> None:
    """S6：发送期间他方改写 claimed_at（CAS 落空）→ 撤绑裁决仍生效。

    发送确已用旧收件人发生且被判无效——即使 delivery 终态回写被 CAS 拦下（他方
    在途认领原样保留），仍指向旧收件人的绑定照撤（新绑定因 recipient 比对免疫）。
    """
    did = channels_env.seed(channel="wxpusher", recipient_id="UID_A")
    uid = channels_env.user_of(did)
    intruder_epoch = datetime.now(UTC) + timedelta(seconds=7)  # 必不等于本 worker 任何时刻

    def steal_row_during_send(recipient_id: str, message: RenderedMessage) -> None:
        s = channels_env.Session()
        try:
            s.execute(
                update(orm.NotificationDeliveryModel)
                .where(orm.NotificationDeliveryModel.delivery_id == did)
                .values(status="SENDING", claimed_at=intruder_epoch)
            )
            s.commit()
        finally:
            s.close()

    wx = RecordingTransport(
        result=SendResult(ok=False, error_code="uid_invalid_verified", recipient_invalid=True),
        on_send=steal_row_during_send,
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats["failed"] == 0 and stats["sent"] == 0  # CAS 落空不计任何终态
    row = channels_env.fetch(did)
    assert row.status == "SENDING"  # 他方在途认领不被摧毁
    assert _aware(row.claimed_at) == intruder_epoch
    binding = channels_env.binding(uid, "wxpusher")
    assert binding.status == "revoked"  # 撤绑裁决不受 CAS 落空影响
    assert binding.recipient_id == "UID_A"


# ──────────────────────────────────────────────────────────────────────────────
# next_retry 负值防御（S5）
# ──────────────────────────────────────────────────────────────────────────────
def test_next_retry_negative_retry_after_clamped_to_zero() -> None:
    """S5：负值 retry_after 钳 0——绝不产出负退避（next_attempt_at 早于 now）。"""
    assert next_retry(1, -5) == timedelta(seconds=0)
    assert next_retry(3, -100) == timedelta(seconds=0)


def test_negative_retry_after_yields_next_attempt_not_in_past(channels_env) -> None:
    """S5 端到端：transport 回 retry_after=-5 → RETRY 且 next_attempt_at >= 采集时刻。"""
    did = channels_env.seed(
        channel="wxpusher",
        recipient_id="UID_A",
        event_id="evt-neg",
        delivery_id="dlv-neg",
    )
    before = datetime.now(UTC)
    wx = RecordingTransport(
        result=SendResult(ok=False, status_code=429, error_code="http_429",
                          retryable=True, retry_after=-5)
    )
    w = channels_env.make_worker(
        transports={"telegram": RecordingTransport(), "wxpusher": wx}
    )

    stats = w.run_once()

    assert stats["retried"] == 1
    row = channels_env.fetch(did)
    assert row.status == "RETRY"
    assert row.next_attempt_at is not None
    delay = (_aware(row.next_attempt_at) - before).total_seconds()
    assert delay >= 0, f"负 retry_after 不得把 next_attempt_at 拉回过去，实为 {delay}s"
