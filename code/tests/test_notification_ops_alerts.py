"""企业微信运维告警隔离与 rollout 配置（多渠道改造 Task 12）。

红线：企业微信群机器人 webhook（``ALERT_WEBHOOK_URL``）只承载**运维**告警——
普通用户业务正文（止损/风控拒绝/标的行情/成交明细等）绝不进 webhook，只落
v2_alert；运维告警正文只含白名单字段（kind/channel/counts/duration/job id）。

覆盖：
- ``send_notification_ops_alert``：未知 kind 拒绝；正文白名单字段渲染 + 业务字段
  （uid/chat_id/symbol/私有正文）在渲染前即被丢弃（泄漏红线）；
- 同 kind+channel 复用 alert_service 300s 频控（不同渠道不互相抑制）；
- 业务告警（风控拒绝/止损，workflow_service 调用点同款出口）→ webhook 零调用；
- ``send_alert`` 传非白名单 kind → 整条拒绝（不推 webhook 也不落库）；
- worker 每轮后的投递健康检查：超阈值渠道发 delivery_backlog；WxPusher 停用
  （delivery_enabled=False）时其 PENDING 滞留"停投不丢"，保持 PENDING 且**不算
  backlog 误报**；新鲜队列不告警；
- ``run_notification_worker_job`` 接线：每轮后调用健康检查（传 disabled 渠道）。

webhook 捕获在 httpx.post 层——断言的是**全量上送正文**（含 emoji/时间戳包装），
泄漏红线不放过任何一层包装。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services import alert_service
from zhice_os_v2.application.services.alert_service import (
    alert_risk_rejected,
    alert_stop_loss,
)
from zhice_os_v2.application.services.notification_health_service import (
    _ALLOWED_OPS_KINDS,
    check_delivery_backlog,
    collect_notification_health,
    send_notification_ops_alert,
)
from zhice_os_v2.application.services.notification_worker import NotificationWorker
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)

_NOW = datetime.now(UTC)


# ──────────────────────────────────────────────────────────────────────────────
# 工具：httpx.post 层捕获全量 webhook 正文 + 清 300s 频控缓存
# ──────────────────────────────────────────────────────────────────────────────
class _WebhookCapture(list[str]):
    """捕获列表 + 可配置响应（企业微信出错也返 200，须按 body errcode 判定）。"""

    def __init__(self) -> None:
        super().__init__()
        self.responses = SimpleNamespace(status_code=200, json_body={"errcode": 0})


@pytest.fixture
def ops_webhook(monkeypatch: pytest.MonkeyPatch) -> _WebhookCapture:
    """配置 ALERT_WEBHOOK_URL 并在 httpx.post 层捕获企业微信全量上送正文。"""
    captured = _WebhookCapture()
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.invalid/hook")

    def fake_post(url: str, timeout: float | None = None, json: dict | None = None):
        assert json is not None and json["msgtype"] == "text"
        captured.append(json["text"]["content"])
        return SimpleNamespace(
            status_code=captured.responses.status_code,
            json=lambda: captured.responses.json_body,
        )

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    alert_service._throttle_cache.clear()
    return captured


# ──────────────────────────────────────────────────────────────────────────────
# 白名单 kind 与正文泄漏红线
# ──────────────────────────────────────────────────────────────────────────────
def test_allowed_ops_kinds_exact() -> None:
    """契约白名单：恰好四类运维 kind，多一类少一类都算漂移。"""
    assert (
        frozenset(
            {"channel_auth_failed", "delivery_backlog", "worker_stalled", "terminal_failure_rate"}
        )
        == _ALLOWED_OPS_KINDS
    )


def test_ops_alert_drops_user_business_fields(ops_webhook: list[str]) -> None:
    """泄漏红线：counts 里夹带的业务字段（symbol/uid/chat_id/私有正文）渲染前即被丢弃。

    正文只保留白名单字段：kind/channel/白名单计数值/duration/worker_job_id——
    断言在 httpx.post 全量正文层面做（含企业微信 text 包装与时间戳行）。
    """
    sent = send_notification_ops_alert(
        kind="terminal_failure_rate",
        channel="wxpusher",
        counts={
            "failed": 7,
            "symbol": "AAPL",  # 业务标的 → 越界键，丢弃
            "uid": "UID_secret",  # 用户收件人 → 越界键，丢弃
            "chat_id": 12345678,  # 电报 chat_id → 越界键，丢弃
            "body": "private fill body",  # 任意正文 → 越界键，丢弃
            "pending": "UID_secret",  # 白名单键 + 字符串值 → 值不合法，丢弃
        },
        duration_seconds=120,
        worker_job_id="notification_worker",
    )
    assert sent is True
    assert len(ops_webhook) == 1
    text = ops_webhook[0]
    # 白名单字段照常渲染
    assert "channel=wxpusher" in text
    assert "failed=7" in text
    assert "kind=terminal_failure_rate" in text
    assert "duration_seconds=120" in text
    assert "worker_job_id=notification_worker" in text
    # 稳定前缀：运维告警一眼可辨、可被运维侧按前缀路由
    assert "[ops] notification terminal_failure_rate channel=wxpusher" in text
    assert "[WARN]" in text
    # 业务字段绝不出现（泄漏红线）
    for secret in ("UID_secret", "12345678", "AAPL", "private fill body"):
        assert secret not in text, f"运维告警正文泄漏业务字段: {secret}"


def test_ops_alert_rejects_unknown_kind(ops_webhook: list[str]) -> None:
    """未知 kind（含伪装成 kind 的业务语义）→ 记日志拒绝，零 webhook 调用。"""
    assert (
        send_notification_ops_alert(kind="stop_loss", channel="wxpusher", counts={"failed": 1})
        is False
    )
    assert send_notification_ops_alert(kind="user_message") is False
    assert ops_webhook == []


def test_send_alert_rejects_non_whitelist_kind(ops_webhook: list[str]) -> None:
    """send_alert 直连层同样只接受白名单 kind：非白名单整条拒绝（webhook+落库都不做）。"""
    assert alert_service.send_alert("WARN", "x", "y", kind="daily_report") is False
    assert ops_webhook == []


# ──────────────────────────────────────────────────────────────────────────────
# 300s 频控（同 kind+channel）
# ──────────────────────────────────────────────────────────────────────────────
def test_ops_alert_throttled_per_kind_and_channel(
    ops_webhook: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 kind+channel 300s 内去重；不同渠道不互相抑制。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(alert_service.time, "monotonic", lambda: clock["t"])

    first = send_notification_ops_alert(
        kind="delivery_backlog", channel="wxpusher", counts={"pending": 12}
    )
    dup = send_notification_ops_alert(
        kind="delivery_backlog", channel="wxpusher", counts={"pending": 13}
    )
    other = send_notification_ops_alert(
        kind="delivery_backlog", channel="telegram", counts={"pending": 1}
    )
    assert first is True
    assert dup is False  # 同 kind+channel 频控内静默
    assert other is True  # 不同渠道不受 wxpusher 频控影响
    assert len(ops_webhook) == 2


def test_ops_alert_throttle_expires_after_300s(
    ops_webhook: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """频控窗口 300s：299s 仍抑制，跨过 300s 后放行。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(alert_service.time, "monotonic", lambda: clock["t"])

    assert send_notification_ops_alert(kind="worker_stalled", channel="telegram") is True
    clock["t"] += 299
    assert send_notification_ops_alert(kind="worker_stalled", channel="telegram") is False
    clock["t"] += 2  # 距首次 301s
    assert send_notification_ops_alert(kind="worker_stalled", channel="telegram") is True
    assert len(ops_webhook) == 2


# ──────────────────────────────────────────────────────────────────────────────
# 企业微信响应语义（S-2）：出错也返 HTTP 200，须按 body errcode==0 判定成功
# ──────────────────────────────────────────────────────────────────────────────
def test_wecom_webhook_errcode_nonzero_is_failure(ops_webhook: _WebhookCapture) -> None:
    """HTTP 200 + errcode=40001（key 失效）→ 失败：运维通道信号不得失真。"""
    ops_webhook.responses.json_body = {"errcode": 40001, "errmsg": "invalid webhook url"}
    assert (
        send_notification_ops_alert(
            kind="delivery_backlog", channel="wxpusher", counts={"pending": 1}
        )
        is False
    )


def test_wecom_webhook_errcode_zero_is_success(ops_webhook: _WebhookCapture) -> None:
    """HTTP 200 + errcode=0 → 成功送达。"""
    ops_webhook.responses.json_body = {"errcode": 0, "errmsg": "ok"}
    assert (
        send_notification_ops_alert(
            kind="delivery_backlog", channel="telegram", counts={"pending": 1}
        )
        is True
    )
    assert len(ops_webhook) == 1


# ──────────────────────────────────────────────────────────────────────────────
# channel / worker_job_id 白名单（S-3）：自由字符串不得直渲染正文
# ──────────────────────────────────────────────────────────────────────────────
def test_ops_alert_drops_invalid_channel_and_job_id(ops_webhook: _WebhookCapture) -> None:
    """channel ∉ SUPPORTED 渠道 / worker_job_id 不合 ^[A-Za-z0-9_.-]{1,64}$ → 字段丢弃。

    告警仍发出（可用性优先：脏渠道值不能静默掉整条运维告警），但正文/标题绝不
    出现脏字符串（注入红线），channel 回退 '-'。
    """
    sent = send_notification_ops_alert(
        kind="delivery_backlog",
        channel='wecom"><svg onload=alert(1)>',
        worker_job_id="job id with spaces!",
        counts={"pending": 3},
    )
    assert sent is True
    assert len(ops_webhook) == 1
    text = ops_webhook[0]
    assert "pending=3" in text
    assert "channel=-" in text  # 非法渠道被丢弃，标题回退 '-'
    for dirty in ("svg", "onload", "job id with spaces"):
        assert dirty not in text, f"运维告警正文出现未白名单的自由字符串: {dirty}"
    assert "worker_job_id=" not in text  # 非法 job id 整字段丢弃


# ──────────────────────────────────────────────────────────────────────────────
# 业务告警与企业微信隔离（workflow_service 调用点同款出口）
# ──────────────────────────────────────────────────────────────────────────────
def test_business_risk_and_stop_loss_do_not_use_ops_webhook(
    ops_webhook: list[str],
) -> None:
    """风控拒绝/止损（workflow_service.run_pre_market/run_intraday 调用点）只落库。

    这些场景函数正是 workflow 的企业微信历史调用出口——改造后业务正文绝不再
    进 webhook（运维群零调用），落 v2_alert 的语义由 alert_service 落库路径守护。
    """
    alert_risk_rejected("plan-ops-1", "持仓数超上限")
    alert_stop_loss("AAPL", 9.5, 10.0)
    assert ops_webhook == []  # webhook 零调用


def test_send_alert_business_body_never_reaches_webhook(ops_webhook: list[str]) -> None:
    """send_alert 无 kind（业务告警）→ 只频控+落库路径，绝不推 webhook。"""
    assert alert_service.send_alert("WARN_HIGH", "止损触发 AAPL", "现价 9.5 ≤ 止损价 10.0") is False
    assert alert_service.send_alert("CRITICAL", "组合暴露超限", "detail") is False
    assert ops_webhook == []


# ──────────────────────────────────────────────────────────────────────────────
# 投递健康快照 + backlog 判定（worker 每轮后）
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def backlog_env():
    """内存 sqlite：seed 任意状态的 delivery 行 + 快照/worker 只读验证。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def seed(
        *,
        channel: str = "wxpusher",
        status: str = "PENDING",
        next_attempt_at: datetime | None = None,
        created_at: datetime | None = None,
    ) -> str:
        sfx = uuid.uuid4().hex[:8]
        did = f"dlv-ops-{sfx}"
        s = Session()
        try:
            if s.query(orm.UserModel).filter_by(user_id="u-ops").count() == 0:
                s.add(
                    orm.UserModel(
                        user_id="u-ops",
                        username="u-ops",
                        password_hash="x",
                        role="TRADER",
                        is_active=True,
                        locale="zh",
                        created_at=_NOW,
                    )
                )
                s.flush()
            s.add(
                orm.NotificationEventModel(
                    event_id=f"evt-ops-{sfx}",
                    user_id="u-ops",
                    event_type="fill",
                    payload={},
                    occurred_at=_NOW,
                    created_at=_NOW,
                )
            )
            s.flush()
            s.add(
                orm.NotificationDeliveryModel(
                    delivery_id=did,
                    event_id=f"evt-ops-{sfx}",
                    user_id="u-ops",
                    channel=channel,
                    status=status,
                    attempt_count=0,
                    next_attempt_at=next_attempt_at,
                    created_at=created_at or _NOW,
                )
            )
            s.commit()
            return did
        finally:
            s.close()

    def fetch_status(delivery_id: str) -> str:
        s = Session()
        try:
            return str(
                s.query(orm.NotificationDeliveryModel.status)
                .filter_by(delivery_id=delivery_id)
                .one()[0]
            )
        finally:
            s.close()

    def make_worker(channel_enabled: Any) -> NotificationWorker:
        class _ForbiddenTransport:
            def send(self, recipient_id: str, message: Any) -> Any:
                raise AssertionError("停用渠道不应发起任何发送")

        return NotificationWorker(
            session_factory=Session,
            transports={
                "telegram": _ForbiddenTransport(),
                "wxpusher": _ForbiddenTransport(),
            },
            binding_repository=NotificationBindingRepository(),
            channel_enabled=channel_enabled,
        )

    return SimpleNamespace(
        Session=Session, seed=seed, fetch_status=fetch_status, make_worker=make_worker
    )


def test_collect_notification_health_counts(backlog_env) -> None:
    """快照口径：只统计到期（due）PENDING/RETRY；未到期 RETRY 与 SENT 不计；FAILED 记入 failed_last_hour。"""
    now = datetime.now(UTC)
    old = now - timedelta(seconds=7200)
    env = backlog_env
    env.seed(channel="telegram", status="PENDING", created_at=old)  # due（next NULL）
    env.seed(channel="telegram", status="RETRY", next_attempt_at=old, created_at=old)  # due
    env.seed(
        channel="telegram",
        status="RETRY",
        next_attempt_at=now + timedelta(seconds=300),
        created_at=old,
    )  # 未到期 → 不计
    env.seed(channel="telegram", status="FAILED", created_at=now - timedelta(minutes=5))
    env.seed(channel="telegram", status="SENT", created_at=old)

    with env.Session() as s:
        snaps = collect_notification_health(s, now=now)
    assert [snap.channel for snap in snaps] == ["telegram"]
    snap = snaps[0]
    assert snap.pending == 1
    assert snap.retry == 1
    assert snap.failed_last_hour == 1
    assert snap.oldest_due_seconds >= 7000  # 最老 due 行已等 2h


def test_backlog_alerts_for_enabled_channel(backlog_env, ops_webhook: list[str]) -> None:
    """启用渠道积压（到期工作滞留 ≥ 阈值）→ delivery_backlog 告警，正文含渠道与计数。"""
    env = backlog_env
    env.seed(channel="telegram", status="PENDING", created_at=_NOW - timedelta(seconds=7200))

    snaps = check_delivery_backlog(session_factory=env.Session)

    assert [s.channel for s in snaps] == ["telegram"]
    assert len(ops_webhook) == 1
    text = ops_webhook[0]
    assert "delivery_backlog" in text
    assert "channel=telegram" in text
    assert "pending=1" in text


def test_fresh_queue_no_backlog_alert(backlog_env, ops_webhook: list[str]) -> None:
    """新鲜队列（到期仅 10s、量 1）不触发告警。"""
    env = backlog_env
    env.seed(
        channel="telegram", status="PENDING", created_at=datetime.now(UTC) - timedelta(seconds=10)
    )
    check_delivery_backlog(session_factory=env.Session)
    assert ops_webhook == []


def test_disabled_wxpusher_keeps_pending_deliveries(backlog_env, ops_webhook: list[str]) -> None:
    """WxPusher 停用：run_once 后 delivery 保持 PENDING（停投不丢），滞留不算 backlog 误报。"""
    env = backlog_env
    did = env.seed(
        channel="wxpusher", status="PENDING", created_at=_NOW - timedelta(seconds=7200)
    )  # 若误计入必超阈值

    stats = env.make_worker(channel_enabled=lambda ch: ch != "wxpusher").run_once()

    assert stats["claimed"] == 0
    assert env.fetch_status(did) == "PENDING"
    snaps = check_delivery_backlog(session_factory=env.Session, disabled_channels=("wxpusher",))
    assert snaps == []  # 停用渠道整体从快照排除
    assert ops_webhook == []  # 不产生 backlog 误报


# ──────────────────────────────────────────────────────────────────────────────
# scheduler job 接线：worker 每轮后跑健康检查
# ──────────────────────────────────────────────────────────────────────────────
def test_worker_job_runs_health_check_after_round(
    monkeypatch: pytest.MonkeyPatch, ops_webhook: _WebhookCapture
) -> None:
    """run_notification_worker_job 每轮后调用 check_delivery_backlog（disabled 渠道透传）。

    settings 显式密闭构造（S-5）：不依赖进程环境/.env.local——本地开发机开着
    WXPUSHER_DELIVERY_ENABLED=true 时旧断言会翻车。worker 本体 stub 成零统计
    （本测试只锁接线）；健康检查 monkeypatch 成记录器。
    """
    import zhice_os_v2.application.services.notification_health_service as health_mod
    import zhice_os_v2.application.services.notification_worker as worker_mod
    import zhice_os_v2.config.settings as settings_mod
    from zhice_os_v2 import scheduler_jobs

    settings = SimpleNamespace(
        persistence_backend="memory",
        telegram_notification_mode="directed",
        wxpusher_app_token="tok-hermetic",
        wxpusher_timeout_seconds=8.0,
        wxpusher_delivery_enabled=False,
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: settings)

    class _StubWorker:
        def __init__(self, **kwargs) -> None:
            pass

        def run_once(self) -> dict:
            return {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}

    monkeypatch.setattr(worker_mod, "NotificationWorker", _StubWorker)
    checked: list[dict] = []
    monkeypatch.setattr(
        health_mod,
        "check_delivery_backlog",
        lambda **kwargs: checked.append(kwargs) or [],
    )

    stats = scheduler_jobs.run_notification_worker_job()

    assert stats["claimed"] == 0
    assert len(checked) == 1
    # 密闭 settings 显式 wxpusher_delivery_enabled=False → 该渠道按停用排除
    assert checked[0]["disabled_channels"] == ("wxpusher",)

    # 逐次现读：翻 True 后下一轮即不再排除（同 worker channel_enabled 语义）
    settings.wxpusher_delivery_enabled = True
    scheduler_jobs.run_notification_worker_job()
    assert len(checked) == 2
    assert checked[1]["disabled_channels"] == ()


# ──────────────────────────────────────────────────────────────────────────────
# 健康检查异常可见性（S-1）：首次 warning、300s 内后续 debug
# ──────────────────────────────────────────────────────────────────────────────
def test_health_check_failure_warns_once_then_debugs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """健康检查自身失败不能全员 debug 静默：首次 warning（运维可见），300s 窗口内
    后续降级 debug（防 DB 长不可用时每 15s 刷屏）。返回 [] 语义不变。"""
    import zhice_os_v2.application.services.notification_health_service as health_mod

    monkeypatch.setattr(health_mod, "_last_health_warn_monotonic", 0.0)

    def _broken_factory():
        raise RuntimeError("db down")

    with caplog.at_level(logging.DEBUG):
        first = health_mod.check_delivery_backlog(session_factory=_broken_factory)
        second = health_mod.check_delivery_backlog(session_factory=_broken_factory)

    assert first == [] and second == []  # best-effort 语义不变
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(warns) == 1, "健康检查失败首次必须 warning（否则运维永不可见）"
    assert len(debugs) == 1, "300s 窗口内后续应降级 debug（防刷屏）"


def test_worker_job_health_check_failure_does_not_break_job(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """健康检查自身崩溃（如 DB 不可用）只记日志，worker job 照常返回统计。"""
    import zhice_os_v2.application.services.notification_health_service as health_mod
    import zhice_os_v2.application.services.notification_worker as worker_mod
    from zhice_os_v2 import scheduler_jobs

    class _StubWorker:
        def __init__(self, **kwargs) -> None:
            pass

        def run_once(self) -> dict:
            return {"claimed": 1, "sent": 1, "retried": 0, "failed": 0, "recovered": 0}

    def _boom(**kwargs: object) -> list:
        raise RuntimeError("health db down")

    monkeypatch.setattr(worker_mod, "NotificationWorker", _StubWorker)
    monkeypatch.setattr(health_mod, "check_delivery_backlog", _boom)

    with caplog.at_level(logging.DEBUG):
        stats = scheduler_jobs.run_notification_worker_job()

    assert stats["claimed"] == 1 and stats["sent"] == 1
    assert not any(r.levelname == "ERROR" for r in caplog.records)  # 降级为 debug 噪音
