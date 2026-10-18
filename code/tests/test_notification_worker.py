"""通知 outbox worker（Phase C Task 7）测试。

覆盖（plan Task 7 Step 1）：
- next_retry 纯函数：429 用 retry_after（封顶 3600）/ 指数 30·60·120…（封顶 3600）；
- 单条生命周期：PENDING→claim(SENDING)→SENT；429→RETRY+attempt+1+next_attempt_at；
  网络错误→RETRY 指数退避；5xx→RETRY；401→FAILED；attempt 达 max→FAILED(retry_exceeded)；
- stale SENDING（claimed_at 超时）回收 RETRY（recovered+1）且本轮即可重投；
  新鲜 SENDING 不回收不重投（防在途双发）；未到期 RETRY 不认领；
- 并发安全：SENDING 中间态互斥（sqlite SKIP LOCKED 为 no-op，用二次 claim 条件排除法）、
  已完成 SENT 不重投、batch_size 限额、CAS 回写不覆盖他方判定、
  claim 先 commit 后 send（发送前状态已提交可见）；
- no chat_id → FAILED(no_chat_binding) 不发起发送；用户 locale 参与渲染；
- best-effort：单条发送异常不中断整批（异常行 worker_error 重试）；
- 安全铁律：worker 日志绝不落完整 chat_id（caplog 断言）；
- scheduler job：best-effort 返回统计 / 异常吞掉不外抛；
- real_pg 并发：两线程同时 run_once，SKIP LOCKED 真锁下每条 delivery 只发一次。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.telegram_notifier import TelegramSendResult
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime.now(UTC)
_WORKER_LOGGER = "zhice_os_v2.application.services.notification_worker"

_ZERO_STATS = {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}


def _aware(dt: datetime | None) -> datetime | None:
    """sqlite 读回 naive 时间 → 统一补 UTC（PG 返回 aware，原样返回）。"""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


# ──────────────────────────────────────────────────────────────────────────────
# 隔离环境：内存 sqlite + 可注入 sender 的 worker 工厂 + 种子/读取辅助
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def worker_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    calls: list[tuple[str, str]] = []  # (chat_id, text) 发送记录
    lock = threading.Lock()

    def make_worker(*, sender=None, result=None, batch_size=50, retry_max=6):
        from zhice_os_v2.application.services.notification_worker import (
            NotificationWorker,
        )

        def _send(chat_id: str, text: str) -> TelegramSendResult:
            with lock:
                calls.append((chat_id, text))
            if sender is not None:
                return sender(chat_id, text)
            if result is not None:
                return result
            return TelegramSendResult(ok=True, status_code=200)

        return NotificationWorker(
            session_factory=Session,
            sender=_send,
            batch_size=batch_size,
            retry_max=retry_max,
        )

    def seed(
        *,
        user_id: str | None = None,
        chat_id: str | None = "5550001111",
        locale: str = "zh",
        event_id: str | None = None,
        delivery_id: str | None = None,
        status: str = "PENDING",
        attempt_count: int = 0,
        next_attempt_at: datetime | None = None,
        claimed_at: datetime | None = None,
        event_type: str = "fill",
        payload: dict | None = None,
    ) -> str:
        sfx = uuid.uuid4().hex[:8]
        uid = user_id or f"u-w-{sfx}"
        eid = event_id or f"evt-{sfx}"
        did = delivery_id or f"dlv-{sfx}"
        s = Session()
        try:
            s.add(
                orm.UserModel(
                    user_id=uid,
                    username=uid,
                    password_hash="x",
                    role="TRADER",
                    is_active=True,
                    locale=locale,
                    telegram_chat_id=chat_id,
                    created_at=_NOW,
                )
            )
            s.flush()
            s.add(
                orm.NotificationEventModel(
                    event_id=eid,
                    user_id=uid,
                    event_type=event_type,
                    payload=payload
                    if payload is not None
                    else {"fill_id": "f1", "symbol": "AAPL", "side": "BUY", "price": 10, "qty": 2},
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
                    channel="telegram",
                    status=status,
                    attempt_count=attempt_count,
                    next_attempt_at=next_attempt_at,
                    claimed_at=claimed_at,
                    created_at=_NOW,
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

    return SimpleNamespace(
        Session=Session, seed=seed, fetch=fetch, calls=calls, make_worker=make_worker
    )


# ──────────────────────────────────────────────────────────────────────────────
# next_retry 纯函数
# ──────────────────────────────────────────────────────────────────────────────
def test_next_retry_uses_retry_after_when_present() -> None:
    from zhice_os_v2.application.services.notification_worker import next_retry

    assert next_retry(1, 12) == timedelta(seconds=12)
    assert next_retry(5, 45) == timedelta(seconds=45)


def test_next_retry_retry_after_capped_at_3600() -> None:
    from zhice_os_v2.application.services.notification_worker import next_retry

    assert next_retry(1, 99_999) == timedelta(seconds=3600)


def test_next_retry_exponential_backoff() -> None:
    from zhice_os_v2.application.services.notification_worker import next_retry

    assert next_retry(0, None) == timedelta(seconds=30)  # max(attempt-1,0)=0
    assert next_retry(1, None) == timedelta(seconds=30)
    assert next_retry(2, None) == timedelta(seconds=60)
    assert next_retry(3, None) == timedelta(seconds=120)
    assert next_retry(4, None) == timedelta(seconds=240)


def test_next_retry_exponential_capped_at_3600() -> None:
    from zhice_os_v2.application.services.notification_worker import next_retry

    assert next_retry(8, None) == timedelta(seconds=3600)  # 30*2^7=3840 → 封顶


# ──────────────────────────────────────────────────────────────────────────────
# 生命周期：claim → send → 回写
# ──────────────────────────────────────────────────────────────────────────────
def test_run_once_empty_outbox_returns_zero_stats(worker_env) -> None:
    stats = worker_env.make_worker().run_once()
    assert stats == _ZERO_STATS


def test_pending_claimed_and_sent(worker_env) -> None:
    did = worker_env.seed()
    stats = worker_env.make_worker().run_once()
    assert stats == {**_ZERO_STATS, "claimed": 1, "sent": 1}
    row = worker_env.fetch(did)
    assert row.status == "SENT"
    assert row.sent_at is not None
    assert row.claimed_at is None  # 终态清掉在途标记
    assert len(worker_env.calls) == 1
    chat_id, text = worker_env.calls[0]
    assert chat_id == "5550001111"
    assert "AAPL" in text  # 模板渲染自 event payload


def test_claim_commits_sending_before_send_starts(worker_env) -> None:
    """claim 事务先提交：sender 执行时行已是已提交的 SENDING（发送在事务外）。"""
    did = worker_env.seed()
    seen: list[str] = []

    def spy(chat_id: str, text: str) -> TelegramSendResult:
        s = worker_env.Session()
        try:
            r = (
                s.query(orm.NotificationDeliveryModel)
                .filter_by(delivery_id=did)
                .one()
            )
            seen.append(r.status)
        finally:
            s.close()
        return TelegramSendResult(ok=True, status_code=200)

    worker_env.make_worker(sender=spy).run_once()
    assert seen == ["SENDING"]


def test_user_locale_used_for_rendering(worker_env) -> None:
    worker_env.seed(locale="en")
    worker_env.make_worker().run_once()
    text = worker_env.calls[0][1]
    assert "Bought" in text  # en fill 头行（zh 为「买入」）


def test_rate_limited_429_schedules_retry_with_retry_after(worker_env) -> None:
    did = worker_env.seed()
    before = datetime.now(UTC)
    w = worker_env.make_worker(
        result=TelegramSendResult(
            ok=False, status_code=429, error_code="http_429", retry_after=12
        )
    )
    stats = w.run_once()
    assert stats["retried"] == 1 and stats["sent"] == 0 and stats["failed"] == 0
    row = worker_env.fetch(did)
    assert row.status == "RETRY"
    assert row.attempt_count == 1
    assert row.last_error_code == "http_429"
    assert row.next_attempt_at is not None
    delay = (_aware(row.next_attempt_at) - before).total_seconds()  # type: ignore[arg-type]
    assert 8 <= delay <= 20, f"next_attempt_at 应约 +12s，实为 +{delay}s"


def test_network_error_retries_with_exponential_backoff(worker_env) -> None:
    did = worker_env.seed()
    before = datetime.now(UTC)
    w = worker_env.make_worker(
        result=TelegramSendResult(ok=False, error_code="network")
    )
    stats = w.run_once()
    assert stats["retried"] == 1
    row = worker_env.fetch(did)
    assert row.status == "RETRY"
    assert row.attempt_count == 1
    assert row.last_error_code == "network"
    assert row.next_attempt_at is not None
    delay = (_aware(row.next_attempt_at) - before).total_seconds()  # type: ignore[arg-type]
    assert 25 <= delay <= 40, f"首次退避应 30s（30*2^0），实为 +{delay}s"


def test_server_error_5xx_retries(worker_env) -> None:
    did = worker_env.seed()
    w = worker_env.make_worker(
        result=TelegramSendResult(ok=False, status_code=502, error_code="http_502")
    )
    stats = w.run_once()
    assert stats["retried"] == 1
    row = worker_env.fetch(did)
    assert row.status == "RETRY"
    assert row.last_error_code == "http_502"
    assert row.next_attempt_at is not None


@pytest.mark.parametrize("code", [401, 403])
def test_unauthorized_fails_terminal(worker_env, code: int) -> None:
    """401/403（bot 被 block/token 失效/chat 被禁）→ 终态 FAILED，不再重试（保守：不动用户绑定）。"""
    did = worker_env.seed()
    w = worker_env.make_worker(
        result=TelegramSendResult(
            ok=False, status_code=code, error_code=f"http_{code}"
        )
    )
    stats = w.run_once()
    assert stats["failed"] == 1 and stats["retried"] == 0
    row = worker_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == f"http_{code}"
    assert row.next_attempt_at is None


def test_not_configured_fails_terminal(worker_env) -> None:
    """终审 Minor #6：bot token 未配置 → 终态 FAILED(not_configured)，不空转重试。

    not_configured 是部署级缺配（settings/env 未配 token），重试 6 次约 31 分钟
    不会自愈——与 no_chat_binding 同为立即终态。
    """
    did = worker_env.seed()
    w = worker_env.make_worker(
        result=TelegramSendResult(ok=False, error_code="not_configured")
    )
    stats = w.run_once()
    assert stats["failed"] == 1 and stats["retried"] == 0
    row = worker_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "not_configured"
    assert row.attempt_count == 1
    assert row.next_attempt_at is None
    assert len(worker_env.calls) == 1  # 发送器已真实尽力（transport 返回 not_configured）


def test_retry_exceeded_after_max_attempts(worker_env) -> None:
    """attempt+1 达 notification_retry_max（6）→ FAILED(retry_exceeded)。"""
    did = worker_env.seed(attempt_count=5)  # 本次发送是第 6 次尝试
    w = worker_env.make_worker(
        result=TelegramSendResult(ok=False, error_code="network"), retry_max=6
    )
    stats = w.run_once()
    assert stats["failed"] == 1 and stats["retried"] == 0
    row = worker_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "retry_exceeded"
    assert row.attempt_count == 6


def test_attempt_below_max_still_retries(worker_env) -> None:
    did = worker_env.seed(attempt_count=4)  # 第 5 次尝试仍 < 6 → RETRY
    w = worker_env.make_worker(
        result=TelegramSendResult(ok=False, error_code="network"), retry_max=6
    )
    stats = w.run_once()
    assert stats["retried"] == 1
    assert worker_env.fetch(did).status == "RETRY"


def test_missing_chat_binding_fails_without_sending(worker_env) -> None:
    did = worker_env.seed(chat_id=None)
    stats = worker_env.make_worker().run_once()
    assert stats["failed"] == 1 and stats["sent"] == 0
    row = worker_env.fetch(did)
    assert row.status == "FAILED"
    assert row.last_error_code == "no_chat_binding"
    assert worker_env.calls == []  # 未发起网络发送


# ──────────────────────────────────────────────────────────────────────────────
# stale 回收 + 认领条件
# ──────────────────────────────────────────────────────────────────────────────
def test_stale_sending_recovered_and_reprocessed(worker_env) -> None:
    """claimed_at 超 5 分钟的滞留 SENDING → 回收 RETRY（recovered+1）→ 本轮即可重投。"""
    did = worker_env.seed(
        status="SENDING", claimed_at=datetime.now(UTC) - timedelta(minutes=6)
    )
    stats = worker_env.make_worker().run_once()
    assert stats["recovered"] == 1
    assert stats["claimed"] == 1
    assert stats["sent"] == 1
    assert worker_env.fetch(did).status == "SENT"


def test_fresh_sending_not_recovered_not_reclaimed(worker_env) -> None:
    """在途（新鲜）SENDING：不回收、不重投（防 worker 并行双发）。"""
    did = worker_env.seed(status="SENDING", claimed_at=datetime.now(UTC))
    stats = worker_env.make_worker().run_once()
    assert stats == _ZERO_STATS
    assert worker_env.calls == []
    assert worker_env.fetch(did).status == "SENDING"


def test_future_next_attempt_not_claimed(worker_env) -> None:
    worker_env.seed(
        status="RETRY",
        attempt_count=1,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )
    stats = worker_env.make_worker().run_once()
    assert stats["claimed"] == 0


def test_due_retry_is_claimed(worker_env) -> None:
    did = worker_env.seed(
        status="RETRY",
        attempt_count=1,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    stats = worker_env.make_worker().run_once()
    assert stats["claimed"] == 1 and stats["sent"] == 1
    assert worker_env.fetch(did).status == "SENT"


# ──────────────────────────────────────────────────────────────────────────────
# 并发安全：不双发 / CAS / 批次
# ──────────────────────────────────────────────────────────────────────────────
def test_sending_row_not_reclaimed_by_second_worker(worker_env) -> None:
    """sqlite 下 SKIP LOCKED 为 no-op：互斥靠 SENDING 中间态 + 二次 claim 条件排除。"""
    did = worker_env.seed()
    s = worker_env.Session()
    try:
        s.execute(
            update(orm.NotificationDeliveryModel)
            .where(orm.NotificationDeliveryModel.delivery_id == did)
            .values(status="SENDING", claimed_at=datetime.now(UTC))
        )
        s.commit()
    finally:
        s.close()

    stats = worker_env.make_worker().run_once()  # 模拟第二个 worker
    assert stats["claimed"] == 0
    assert worker_env.calls == []


def test_completed_delivery_not_reprocessed(worker_env) -> None:
    did = worker_env.seed()
    worker_env.make_worker().run_once()
    stats = worker_env.make_worker().run_once()  # 第二轮
    assert stats == _ZERO_STATS
    assert len(worker_env.calls) == 1
    assert worker_env.fetch(did).status == "SENT"


def test_batch_size_limits_claim(worker_env) -> None:
    for i in range(5):
        worker_env.seed(
            user_id=f"u-b-{i}",
            chat_id=f"70000{i}",
            event_id=f"evt-b-{i}",
            delivery_id=f"dlv-b-{i}",
        )
    stats = worker_env.make_worker(batch_size=2).run_once()
    assert stats["claimed"] == 2


def test_finalize_cas_does_not_overwrite_moved_row(worker_env) -> None:
    """CAS：发送期间行被他人改走（如 stale 回收）→ 回写落空，不覆盖他方判定。"""
    did = worker_env.seed()

    def racing_recovery(chat_id: str, text: str) -> TelegramSendResult:
        s = worker_env.Session()
        try:
            s.execute(
                update(orm.NotificationDeliveryModel)
                .where(
                    orm.NotificationDeliveryModel.delivery_id == did,
                    orm.NotificationDeliveryModel.status == "SENDING",
                )
                .values(
                    status="RETRY",
                    next_attempt_at=datetime.now(UTC),
                    claimed_at=None,
                )
            )
            s.commit()
        finally:
            s.close()
        return TelegramSendResult(ok=True, status_code=200)

    stats = worker_env.make_worker(sender=racing_recovery).run_once()
    assert stats["sent"] == 0  # CAS 落空不计 sent
    row = worker_env.fetch(did)
    assert row.status == "RETRY"  # 他方结果保留
    assert row.sent_at is None


def test_row_exception_does_not_break_batch(worker_env) -> None:
    """单条异常（sender 抛错）不中断整批：异常行 worker_error 转重试，其余照发。"""
    worker_env.seed(user_id="u-x", chat_id="111", event_id="evt-x", delivery_id="dlv-x")
    worker_env.seed(user_id="u-y", chat_id="222", event_id="evt-y", delivery_id="dlv-y")

    def flaky(chat_id: str, text: str) -> TelegramSendResult:
        if chat_id == "111":
            raise RuntimeError("boom")
        return TelegramSendResult(ok=True, status_code=200)

    stats = worker_env.make_worker(sender=flaky).run_once()
    assert stats["sent"] == 1
    assert stats["retried"] == 1
    row_x = worker_env.fetch("dlv-x")
    assert row_x.status == "RETRY"
    assert row_x.last_error_code == "worker_error"
    assert worker_env.fetch("dlv-y").status == "SENT"


# ──────────────────────────────────────────────────────────────────────────────
# 复核加固（Important I-1/I-2、Minor M-1/M-3）
# ──────────────────────────────────────────────────────────────────────────────
def test_finalize_cas_misses_when_reclaimed_with_new_epoch(worker_env) -> None:
    """I-1：他方回收后重 claim（SENDING+新 claimed_at）→ 旧 worker 回写落空不覆盖。

    场景：A 认领整批→批尾行认领龄超时→B 回收并重 claim（claimed_at 改写为 B 的
    epoch）→ A 的 CAS 若只匹配 status='SENDING' 会命中 B 的在途行，覆写 SENT 并
    摧毁 B 的认领（用户收 2 条、库里记 1 条）。修复后 A 的回写以自己的 epoch 为
    CAS 前置 → 落空，B 的在途状态原样保留。
    """
    did = worker_env.seed()
    intruder_epoch = datetime.now(UTC) + timedelta(seconds=7)  # 必不等于本 worker 任何时刻

    def reclaimed_by_other(chat_id: str, text: str) -> TelegramSendResult:
        s = worker_env.Session()
        try:
            s.execute(
                update(orm.NotificationDeliveryModel)
                .where(orm.NotificationDeliveryModel.delivery_id == did)
                .values(status="SENDING", claimed_at=intruder_epoch)
            )
            s.commit()
        finally:
            s.close()
        return TelegramSendResult(ok=True, status_code=200)

    stats = worker_env.make_worker(sender=reclaimed_by_other).run_once()
    assert stats["sent"] == 0  # 回写落空不计
    row = worker_env.fetch(did)
    assert row.status == "SENDING"  # B 的在途认领不被摧毁
    assert _aware(row.claimed_at) == intruder_epoch  # B 的 epoch 不被覆写
    assert row.sent_at is None


def test_lost_row_not_sent(worker_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """I-1 前置防线：行在发送前已被他方取走（touch 落空）→ 不再发送（防双发）。"""
    did = worker_env.seed()
    w = worker_env.make_worker()
    monkeypatch.setattr(w, "_touch", lambda row: None)  # 模拟认领已被回收/改走
    stats = w.run_once()
    assert stats == {**_ZERO_STATS, "claimed": 1}
    assert worker_env.calls == []  # 绝不发起发送
    assert worker_env.fetch(did).status == "SENDING"  # 不改写他方在途状态


def test_claimed_at_refreshed_per_row_before_send(worker_env) -> None:
    """I-2：批内逐行刷新 claimed_at——后行发送时刻的 claimed_at 晚于前行（含前行发送耗时）。

    整批若共用批首 t0，网络劣化下批尾行认领龄会破 5min 阈值触发误回收（I-1 放大器）。
    """
    for i in range(2):
        worker_env.seed(
            user_id=f"u-r-{i}",
            chat_id=f"71000{i}",
            event_id=f"evt-r-{i}",
            delivery_id=f"dlv-r-{i}",
        )
    chat_to_user = {"710000": "u-r-0", "710001": "u-r-1"}
    captured: dict[str, datetime] = {}

    def spy_first_sleeps(chat_id: str, text: str) -> TelegramSendResult:
        s = worker_env.Session()
        try:
            row = (
                s.query(orm.NotificationDeliveryModel)
                .filter_by(user_id=chat_to_user[chat_id])
                .one()
            )
            captured[chat_id] = row.claimed_at
        finally:
            s.close()
        if chat_id == "710000":
            time.sleep(0.3)
        return TelegramSendResult(ok=True, status_code=200)

    worker_env.make_worker(sender=spy_first_sleeps).run_once()
    c0 = _aware(captured["710000"])
    c1 = _aware(captured["710001"])
    assert c0 is not None and c1 is not None
    assert c1 != c0, "两行 claimed_at 应各自刷新（整批共盖批首 t0 的旧行为）"
    assert (c1 - c0).total_seconds() >= 0.2, (
        f"第二行刷新时刻应晚于第一行（首行发送 0.3s），实差 {(c1 - c0).total_seconds()}s"
    )


def test_finalize_failure_falls_back_to_worker_error(worker_env) -> None:
    """M-1：finalize 自身失败（DB 抖）→ worker_error 兜底重试，不滞留 SENDING、不中断整批。

    若只把发送放进守卫而 finalize 裸奔，「发送成功但回写失败」的行会滞留 SENDING，
    5min 后被回收重发 → 用户重复收消息；兜底 RETRY(worker_error) 受 retry_max 上限约束。
    """
    worker_env.seed(user_id="u-m1a", chat_id="81001", event_id="evt-m1a", delivery_id="dlv-m1a")
    worker_env.seed(user_id="u-m1b", chat_id="81002", event_id="evt-m1b", delivery_id="dlv-m1b")
    w = worker_env.make_worker()
    real_finalize = w._finalize
    seen: set[str] = set()

    def flaky_finalize(delivery_id: str, **kwargs: object) -> bool:
        first = delivery_id not in seen
        seen.add(delivery_id)
        if delivery_id == "dlv-m1a" and first:
            raise RuntimeError("db hiccup")
        return real_finalize(delivery_id, **kwargs)  # type: ignore[arg-type]

    w._finalize = flaky_finalize  # type: ignore[assignment]
    stats = w.run_once()  # 不外抛
    assert stats["sent"] == 1 and stats["retried"] == 1
    row_a = worker_env.fetch("dlv-m1a")
    assert row_a.status == "RETRY"  # 兜底回写成功：不滞留 SENDING
    assert row_a.last_error_code == "worker_error"
    assert worker_env.fetch("dlv-m1b").status == "SENT"  # 整批未中断


def test_null_claimed_at_sending_recovered(worker_env) -> None:
    """M-3：SENDING + claimed_at NULL（手工改库的理论死态）也被回收，不永久滞留。"""
    did = worker_env.seed(status="SENDING", claimed_at=None)
    stats = worker_env.make_worker().run_once()
    assert stats["recovered"] == 1
    assert stats["claimed"] == 1 and stats["sent"] == 1
    assert worker_env.fetch(did).status == "SENT"


# ──────────────────────────────────────────────────────────────────────────────
# 安全铁律：日志不落 chat_id
# ──────────────────────────────────────────────────────────────────────────────
def test_worker_logs_never_contain_chat_id(
    worker_env, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "998877665544SECRETCHAT"
    worker_env.seed(chat_id=secret)
    worker_env.seed(
        user_id="u-l2",
        chat_id="111122223333",
        event_id="evt-l2",
        delivery_id="dlv-l2",
    )
    w = worker_env.make_worker(
        result=TelegramSendResult(
            ok=False, status_code=429, error_code="http_429", retry_after=5
        )
    )
    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        w.run_once()
    full = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records, "worker 应有日志输出"
    assert secret not in full
    assert "111122223333" not in full
    assert "chat_id" not in full
    # 白名单字段照常落（delivery_id/user_id/error_code/attempt）
    assert "error_code=http_429" in full


# ──────────────────────────────────────────────────────────────────────────────
# scheduler job 包装
# ──────────────────────────────────────────────────────────────────────────────
def test_notification_worker_job_returns_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhice_os_v2 import scheduler_jobs
    from zhice_os_v2.application.services import notification_worker as nw_mod

    class _Stub:
        # Task 11：run_notification_worker_job 以 transports/binding_repository/
        # channel_enabled 构造 worker——stub 接受并忽略装配参数
        def __init__(self, **kwargs) -> None:
            pass

        def run_once(self) -> dict:
            return {**_ZERO_STATS, "claimed": 1, "sent": 1}

    monkeypatch.setattr(nw_mod, "NotificationWorker", _Stub)
    stats = scheduler_jobs.run_notification_worker_job()
    assert stats["claimed"] == 1 and stats["sent"] == 1


def test_notification_worker_job_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 崩溃（如 DB 不可用）不外抛：记 error 返回零统计（不影响调度器）。"""
    from zhice_os_v2 import scheduler_jobs
    from zhice_os_v2.application.services import notification_worker as nw_mod

    class _Boom:
        def __init__(self, **kwargs) -> None:  # Task 11：装配参数（见 _Stub 注）
            pass

        def run_once(self) -> dict:
            raise RuntimeError("db down")

    monkeypatch.setattr(nw_mod, "NotificationWorker", _Boom)
    stats = scheduler_jobs.run_notification_worker_job()  # 不抛即通过
    assert stats["error"] == "worker_exception"
    assert stats["sent"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# real_pg 并发：两线程同时 run_once，SKIP LOCKED 真锁下每条只发一次
# ──────────────────────────────────────────────────────────────────────────────
def _pg_available() -> bool:
    try:
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session

        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.mark.real_pg
def test_real_pg_concurrent_run_once_sends_each_delivery_once() -> None:
    """两线程同时 run_once（batch=3、10 条 PENDING）：认领集互不相交、每条只发一次。

    PG 的 FOR UPDATE SKIP LOCKED 让两线程各自锁住不同子集（各 3 条），剩余 4 条
    留待下一轮；随后单线程补跑清空，总发送数 == delivery 数（无一条双发）。
    """
    if not (os.getenv("REAL_PG") == "1" and _pg_available()):
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用")

    from zhice_os_v2.application.services.notification_worker import NotificationWorker
    from zhice_os_v2.infrastructure.db.session import get_session

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    n = 10
    user_ids = [f"zztest_nw_user_{sfx}_{i}" for i in range(n)]
    chat_ids = [f"90000{sfx}{i}" for i in range(n)]
    now = datetime.now(UTC)

    setup = get_session()
    try:
        for uid, cid in zip(user_ids, chat_ids, strict=True):
            setup.add(
                orm.UserModel(
                    user_id=uid,
                    username=uid,
                    password_hash="x",
                    role="TRADER",
                    is_active=True,
                    locale="zh",
                    telegram_chat_id=cid,
                    created_at=now,
                )
            )
            setup.flush()
            setup.add(
                orm.NotificationEventModel(
                    event_id=f"evt-nw-{uid}",
                    user_id=uid,
                    event_type="fill",
                    payload={"fill_id": "f1", "symbol": "AAPL"},
                    occurred_at=now,
                    created_at=now,
                )
            )
            setup.flush()
            setup.add(
                orm.NotificationDeliveryModel(
                    delivery_id=f"dlv-nw-{uid}",
                    event_id=f"evt-nw-{uid}",
                    user_id=uid,
                    channel="telegram",
                    status="PENDING",
                    attempt_count=0,
                    created_at=now,
                )
            )
        setup.commit()
    finally:
        setup.close()

    sends: list[str] = []
    sends_lock = threading.Lock()

    def slow_ok(chat_id: str, text: str) -> TelegramSendResult:
        time.sleep(0.15)  # 拉长在途窗口，逼出真并发认领
        with sends_lock:
            sends.append(chat_id)
        return TelegramSendResult(ok=True, status_code=200)

    worker = NotificationWorker(
        session_factory=get_session, sender=slow_ok, batch_size=3
    )
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait()
        results.append(worker.run_once())

    try:
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 第一轮：两线程各认领 3 条（SKIP LOCKED 互斥），共 6 条、无重叠双发
        claimed_total = sum(r["claimed"] for r in results)
        assert claimed_total == 6, f"两线程应共认领 6 条，实为 {claimed_total}：{results}"
        assert sum(r["sent"] for r in results) == 6
        assert len(sends) == 6
        assert len(set(sends)) == 6, "存在同一 chat 被两个 worker 各发一次（双发）"

        # 第二轮：单线程批次兜清剩余 4 条（batch=3 需两轮）；总发送 == delivery 数
        drained = 0
        for _ in range(10):  # 安全上限：正常两轮即空
            st = worker.run_once()
            drained += st["claimed"]
            if st["claimed"] == 0:
                break
        assert drained == 4, f"第二轮应清空剩余 4 条，实为 {drained}"
        assert len(sends) == n
        assert len(set(sends)) == n, "同一 delivery 被发送超过一次"

        check = get_session()
        try:
            rows = (
                check.query(orm.NotificationDeliveryModel)
                .filter(
                    orm.NotificationDeliveryModel.delivery_id.like(
                        f"dlv-nw-zztest_nw_user_{sfx}%"
                    )
                )
                .all()
            )
            assert len(rows) == n
            for row in rows:
                assert row.status == "SENT"
                assert row.sent_at is not None
                assert row.attempt_count == 1  # 一次成功发送只算一次尝试
        finally:
            check.close()
    finally:
        cleanup = get_session()
        try:
            from sqlalchemy import delete

            pat = f"zztest_nw_user_{sfx}%"
            cleanup.execute(
                delete(orm.NotificationDeliveryModel).where(
                    orm.NotificationDeliveryModel.user_id.like(pat)
                )
            )
            cleanup.execute(
                delete(orm.NotificationEventModel).where(
                    orm.NotificationEventModel.user_id.like(pat)
                )
            )
            cleanup.execute(delete(orm.UserModel).where(orm.UserModel.user_id.like(pat)))
            cleanup.commit()
        finally:
            cleanup.close()
