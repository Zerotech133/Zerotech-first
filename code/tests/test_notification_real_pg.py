"""Task 16 real_pg：通知绑定/投递并发正确性（PostgreSQL）。

整文件 @real_pg，需 REAL_PG=1 且真 PG（已迁移到 0033）。只跑专用测试库
（createdb -O zhice 建空库后 alembic upgrade 0033），绝不指向共享/生产库——
worker 的 stale 回收会 UPDATE 全局 SENDING 行。三个测试（计划锁定语义）：

1. 并发 create_challenge（同 user+channel，两线程各持独立会话）：
   user 行锁串行化「作废旧 open challenge + 建新」，终态只剩最新一条可消费——
   两 token consume 结果恰为 {bound, expired_or_used}，open challenge 数为 0；
2. telegram + wxpusher 各一 PENDING delivery，两 worker 并发 run_once
   （batch=1 + barrier + 慢 transport 拉长在途窗口）：FOR UPDATE SKIP LOCKED
   真锁下认领集不相交，每事件恰好发送一次（渠道分流正确、无跨渠道双发）；
3. 旧 UID 发送失败 recipient_invalid 不能撤销重绑后的新 UID：bind UID_OLD →
   worker 认领 → 发送期间用户重绑 UID_NEW → finalize 以「本次实际使用的收件人」
   做 revoke_if_current 比对 → 落空，active 绑定仍为 UID_NEW（真 PG 行锁下的
   发送期间重绑竞态）。

数据纪律：全部合成行用 ``test-notification-<sfx>`` 前缀，fixture finally 按
FK 顺序（delivery → event → binding → challenge → user）只删本测试创建的行。

跑法（本机 Homebrew PG16，用完 dropdb）：
  createdb -O zhice zhice_notif_realpg_test
  DB_MAIN_URL=postgresql://zhice:<pass>@127.0.0.1:5432/zhice_notif_realpg_test \
    alembic upgrade 0033
  REAL_PG=1 DB_MAIN_URL=postgresql://zhice:<pass>@127.0.0.1:5432/zhice_notif_realpg_test \
    PERSISTENCE_BACKEND=sql PYTHONPATH=src uv run pytest -o addopts="" \
    -m real_pg tests/test_notification_real_pg.py tests/test_notification_worker.py -q
  dropdb zhice_notif_realpg_test
"""
from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from zhice_os_v2.application.services.notification_worker import NotificationWorker
from zhice_os_v2.domain.notification.contracts import RenderedMessage, SendResult
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)

pytestmark = pytest.mark.real_pg

_NOW = datetime.now(UTC)


def _pg_available() -> bool:
    try:
        from sqlalchemy import text

        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.fixture
def notif_prefix() -> str:
    """专用前缀 + finally 只清理本前缀行（绝不触碰库中其它数据）。"""
    if not (os.getenv("REAL_PG") == "1" and _pg_available()):
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用（专用库，已迁移到 0033）")
    prefix = f"test-notification-{uuid.uuid4().hex[:10]}"
    try:
        yield prefix
    finally:
        _cleanup(prefix)


def _cleanup(prefix: str) -> None:
    s = get_session()
    try:
        pat = f"{prefix}%"
        s.execute(
            delete(orm.NotificationDeliveryModel).where(
                orm.NotificationDeliveryModel.user_id.like(pat)
            )
        )
        s.execute(
            delete(orm.NotificationEventModel).where(
                orm.NotificationEventModel.user_id.like(pat)
            )
        )
        s.execute(
            delete(orm.UserNotificationBindingModel).where(
                orm.UserNotificationBindingModel.user_id.like(pat)
            )
        )
        s.execute(
            delete(orm.NotificationBindingChallengeModel).where(
                orm.NotificationBindingChallengeModel.user_id.like(pat)
            )
        )
        s.execute(delete(orm.UserModel).where(orm.UserModel.user_id.like(pat)))
        s.commit()
    finally:
        s.close()


def _seed_user(user_id: str) -> None:
    s = get_session()
    try:
        s.add(
            orm.UserModel(
                user_id=user_id,
                username=user_id,
                password_hash="x",
                role="USER",
                is_active=True,
                locale="zh",
                created_at=_NOW,
            )
        )
        s.commit()
    finally:
        s.close()


def _seed_pending_delivery(
    *, user_id: str, event_id: str, delivery_id: str, channel: str
) -> None:
    """一对 event + PENDING delivery（收件人恒来自 binding 表，多渠道路径）。"""
    s = get_session()
    try:
        s.add(
            orm.NotificationEventModel(
                event_id=event_id,
                user_id=user_id,
                event_type="fill",
                payload={"fill_id": "f1", "symbol": "AAPL", "side": "BUY", "price": 10, "qty": 2},
                occurred_at=_NOW,
                created_at=_NOW,
            )
        )
        s.flush()
        s.add(
            orm.NotificationDeliveryModel(
                delivery_id=delivery_id,
                event_id=event_id,
                user_id=user_id,
                channel=channel,
                status="PENDING",
                attempt_count=0,
                created_at=_NOW,
            )
        )
        s.commit()
    finally:
        s.close()


def _delivery(delivery_id: str) -> SimpleNamespace:
    s = get_session()
    try:
        row = s.query(orm.NotificationDeliveryModel).filter_by(delivery_id=delivery_id).one()
        return SimpleNamespace(status=row.status, last_error_code=row.last_error_code)
    finally:
        s.close()


def _binding(user_id: str, channel: str) -> SimpleNamespace:
    s = get_session()
    try:
        row = (
            s.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id, channel=channel)
            .one()
        )
        return SimpleNamespace(status=row.status, recipient_id=row.recipient_id)
    finally:
        s.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. 并发 create_challenge：只剩最新一条可消费
# ──────────────────────────────────────────────────────────────────────────────
def test_concurrent_challenge_creation_leaves_only_newest_consumable(
    notif_prefix: str,
) -> None:
    uid = f"{notif_prefix}-cc-user"
    _seed_user(uid)
    repo = NotificationBindingRepository()
    tokens = [f"tok-{secrets.token_urlsafe(18)}-{i}" for i in range(2)]
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def create(token: str) -> None:
        try:
            barrier.wait(timeout=10)
            s = get_session()
            try:
                repo.create_challenge(
                    s, user_id=uid, channel="wxpusher", token=token, expires_at=expires_at
                )
                s.commit()
            finally:
                s.close()
        except Exception as exc:  # noqa: BLE001 - 线程异常必须带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(t,)) for t in tokens]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"并发 create_challenge 不应失败：{errors}"

    # 两 token 逐一 consume（顺序任意）：恰一个 bound、一个 expired_or_used。
    results: set[str] = set()
    recipient = f"UID_{notif_prefix}-cc"
    for token in tokens:
        s = get_session()
        try:
            results.add(
                repo.consume_and_bind(
                    s,
                    token=token,
                    channel="wxpusher",
                    recipient_id=recipient,
                    now=datetime.now(UTC),
                )
            )
            s.commit()
        finally:
            s.close()
    assert results == {"bound", "expired_or_used"}, (
        f"应恰一条可消费（新）、一条已被作废（旧），实为 {results}"
    )

    # 终态：该用户该渠道无任何 open challenge（旧的已作废、新的已消费）。
    s = get_session()
    try:
        open_count = (
            s.query(orm.NotificationBindingChallengeModel)
            .filter(
                orm.NotificationBindingChallengeModel.user_id == uid,
                orm.NotificationBindingChallengeModel.channel == "wxpusher",
                orm.NotificationBindingChallengeModel.consumed_at.is_(None),
            )
            .count()
        )
    finally:
        s.close()
    assert open_count == 0, f"并发创建后 open challenge 应为 0，实为 {open_count}"


# ──────────────────────────────────────────────────────────────────────────────
# 2. 两 worker 并发 run_once：跨渠道不双发
# ──────────────────────────────────────────────────────────────────────────────
class _RecordingTransport:
    """按 NotificationTransport 协议记录收件人（线程安全；慢发送拉长在途窗口）。"""

    def __init__(self) -> None:
        self.recipients: list[str] = []
        self._lock = threading.Lock()

    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
        assert isinstance(message, RenderedMessage)
        time.sleep(0.15)
        with self._lock:
            self.recipients.append(recipient_id)
        return SendResult(ok=True)


def test_two_workers_do_not_double_send_cross_channel_deliveries(
    notif_prefix: str,
) -> None:
    uid = f"{notif_prefix}-w2-user"
    tg_recipient = f"{notif_prefix}-tg-chat"
    wx_recipient = f"UID_{notif_prefix}-wx"
    _seed_user(uid)
    _seed_pending_delivery(
        user_id=uid,
        event_id=f"evt-{notif_prefix}-tg",
        delivery_id=f"dlv-{notif_prefix}-tg",
        channel="telegram",
    )
    _seed_pending_delivery(
        user_id=uid,
        event_id=f"evt-{notif_prefix}-wx",
        delivery_id=f"dlv-{notif_prefix}-wx",
        channel="wxpusher",
    )
    s = get_session()
    try:
        for channel, recipient in (("telegram", tg_recipient), ("wxpusher", wx_recipient)):
            s.add(
                orm.UserNotificationBindingModel(
                    binding_id=f"nbind-{notif_prefix}-{channel}",
                    user_id=uid,
                    channel=channel,
                    recipient_id=recipient,
                    status="active",
                    bound_at=_NOW,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        s.commit()
    finally:
        s.close()

    transports = {"telegram": _RecordingTransport(), "wxpusher": _RecordingTransport()}
    worker = NotificationWorker(
        session_factory=get_session,
        transports=transports,
        binding_repository=NotificationBindingRepository(),
        batch_size=1,  # 两行两 worker：SKIP LOCKED 下认领集必不相交
    )
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[Exception] = []

    def run() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(worker.run_once())
        except Exception as exc:  # noqa: BLE001 - 线程异常必须带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"并发 run_once 不应失败：{errors}"

    assert sum(r["claimed"] for r in results) == 2, f"两行应各被认领一次：{results}"
    assert sum(r["sent"] for r in results) == 2, f"两行应各发送成功一次：{results}"

    # 每事件恰发一次，且走对渠道（telegram→chat、wxpusher→UID，互不串道）。
    assert transports["telegram"].recipients == [tg_recipient]
    assert transports["wxpusher"].recipients == [wx_recipient]

    for did in (f"dlv-{notif_prefix}-tg", f"dlv-{notif_prefix}-wx"):
        row = _delivery(did)
        assert row.status == "SENT", f"{did} 应终态 SENT，实为 {row.status}"


# ──────────────────────────────────────────────────────────────────────────────
# 3. 旧 UID 发送失败不能撤销重绑后的新 UID
# ──────────────────────────────────────────────────────────────────────────────
def test_old_uid_failure_cannot_revoke_rebound_uid(notif_prefix: str) -> None:
    uid = f"{notif_prefix}-rb-user"
    uid_old = f"UID_{notif_prefix}-old"
    uid_new = f"UID_{notif_prefix}-new"
    _seed_user(uid)
    did = f"dlv-{notif_prefix}-rb"
    _seed_pending_delivery(
        user_id=uid,
        event_id=f"evt-{notif_prefix}-rb",
        delivery_id=did,
        channel="wxpusher",
    )

    repo = NotificationBindingRepository()
    s = get_session()
    try:
        repo.bind_verified(
            s, user_id=uid, channel="wxpusher", recipient_id=uid_old, now=datetime.now(UTC)
        )
        s.commit()
    finally:
        s.close()

    class _RebindDuringSend:
        """发送期间用户在另一会话重绑 UID_NEW；本次发送判旧 UID 无效。"""

        def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
            assert recipient_id == uid_old, "发送应使用发送前读到的旧 UID"
            s2 = get_session()
            try:
                repo.bind_verified(
                    s2,
                    user_id=uid,
                    channel="wxpusher",
                    recipient_id=uid_new,
                    now=datetime.now(UTC),
                )
                s2.commit()
            finally:
                s2.close()
            return SendResult(
                ok=False, error_code="uid_invalid_verified", recipient_invalid=True
            )

    worker = NotificationWorker(
        session_factory=get_session,
        transports={"wxpusher": _RebindDuringSend()},
        binding_repository=repo,
        batch_size=5,
    )
    stats = worker.run_once()

    assert stats["failed"] == 1 and stats["sent"] == 0, stats
    row = _delivery(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "uid_invalid_verified"
    binding = _binding(uid, "wxpusher")
    assert binding.status == "active", "重绑后的新绑定不得被旧 UID 的失败误撤"
    assert binding.recipient_id == uid_new
