"""Task 8: WxPusher 绑定服务、本人 API 与回调的端到端契约测试。

合成数据铁律：凭据一律 AT_/AK_/UID_/QRC_/pk_ 合成前缀，官方 HTTP 用记录式替身
（_ScriptedHttp），绝不真外呼 wxpusher.zjiecode.com；DB 为隔离 sqlite。

安全属性覆盖：
- challenge 响应只含二维码图片与过期时刻，token/provider_code 绝不返回；
- webhook：错 path_key 404（不泄露存在性）、错 appKey 403、成功/重复/过期一律 204；
- user_id 一律取自 JWT，body/query 的 user_id 被无视（无跨账号标识符）；
- 轮询闭环：webhook 丢失时 GET bindings 兜底完成绑定；3 秒节流；preference 不重复初始化。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import zhice_os_v2.api.routers.integrations_wxpusher as wx_mod
import zhice_os_v2.api.routers.my_notification_bindings as my_mod
import zhice_os_v2.application.services.notification_binding_service as svc_mod
import zhice_os_v2.infrastructure.repositories.notification_binding_repository as binding_repo_mod
from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_notification_binding_service,
)
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.notification_binding_service import (
    NotificationBindingService,
)
from zhice_os_v2.config.settings import AppSettings
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.notifications.wxpusher_binding_client import (
    WxPusherBindingClient,
    WxPusherBindingError,
)
from zhice_os_v2.infrastructure.notifications.wxpusher_transport import WxPusherTransport

_PATH_KEY = "pk_" + "s" * 40
_ISOLATED_ENV_VARS = (
    "WXPUSHER_APP_TOKEN",
    "WXPUSHER_APP_ID",
    "WXPUSHER_APP_KEY",
    "WXPUSHER_WEBHOOK_PATH_KEY",
    "WXPUSHER_BINDING_ENABLED",
    "WXPUSHER_DELIVERY_ENABLED",
    "WXPUSHER_CHALLENGE_TTL_SECONDS",
    "WXPUSHER_TIMEOUT_SECONDS",
)
_ENABLED_KWARGS: dict[str, Any] = {
    "wxpusher_binding_enabled": True,
    "wxpusher_app_token": "AT_synthetic",
    "wxpusher_app_id": 123,
    "wxpusher_app_key": "AK_synthetic",
    "wxpusher_webhook_path_key": _PATH_KEY,
}

_CHALLENGE_URL = "/api/v1/auth/me/notification-bindings/wxpusher/challenge"
_BINDINGS_URL = "/api/v1/auth/me/notification-bindings"
_WXPUSHER_URL = "/api/v1/auth/me/notification-bindings/wxpusher"
_TEST_URL = "/api/v1/auth/me/notification-bindings/wxpusher/test"


def _settings(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> AppSettings:
    """隔离构造：清掉相关 env 并禁读 .env.local，仅用显式参数。"""
    for var in _ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return AppSettings(_env_file=None, **kwargs)


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


_NOT_SCANNED = {"success": True, "code": 1000, "data": None}


class _ScriptedHttp:
    """记录式 http 替身：post/get 响应可编程（队列，取尽用默认值）。

    每次二维码创建发号唯一 QRC_n（provider_code 有 unique(channel, provider_code)）。
    """

    def __init__(self) -> None:
        self.post_responses: list[_Response | Exception] = []
        self.get_responses: list[_Response | Exception] = []
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self._qr_seq = 0

    def _qr_ok(self) -> _Response:
        self._qr_seq += 1
        return _Response(
            200,
            {
                "success": True,
                "code": 1000,
                "data": {
                    "code": f"QRC_{self._qr_seq}",
                    "url": f"https://img.example/qr-{self._qr_seq}.png",
                    "shortUrl": f"https://s.example/{self._qr_seq}",
                },
            },
        )

    def post(self, url: str, *, json: dict, timeout: float):
        self.post_calls.append({"url": url, "json": json, "timeout": timeout})
        item = self.post_responses.pop(0) if self.post_responses else self._qr_ok()
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url: str, *, params, timeout: float):
        self.get_calls.append({"url": url, "params": dict(params), "timeout": timeout})
        item = (
            self.get_responses.pop(0)
            if self.get_responses
            else _Response(200, _NOT_SCANNED)
        )
        if isinstance(item, Exception):
            raise item
        return item


class _MutableClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, users):
    """隔离 sqlite + 启用绑定的 settings + mock 官方 HTTP + 可注入 clock 的 service。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    http = _ScriptedHttp()
    clock = _MutableClock(datetime(2026, 9, 17, 8, 0, tzinfo=UTC))
    settings = _settings(monkeypatch, **_ENABLED_KWARGS)
    service = NotificationBindingService(
        binding_client=WxPusherBindingClient(
            app_token="AT_synthetic", timeout=3.0, client=http
        ),
        transport=WxPusherTransport(app_token="AT_synthetic", timeout=3.0, client=http),
        clock=clock.now,
    )

    monkeypatch.setattr(svc_mod, "get_session", lambda: Session())
    monkeypatch.setattr(svc_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(my_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(wx_mod, "get_settings", lambda: settings)
    # 仓储内部 _utcnow（set_provider_code 过期判定等）与注入 clock 对齐，
    # 否则冻结时钟早于墙钟会被判"已过期"（同 test_notification_binding_repository 范式）。
    monkeypatch.setattr(binding_repo_mod, "_utcnow", clock.now)

    _seed_user(Session, users.alice)
    _seed_user(Session, users.bob)

    app.dependency_overrides[get_notification_binding_service] = lambda: service
    client = TestClient(app)

    def as_user(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user

    try:
        yield SimpleNamespace(
            Session=Session,
            http=http,
            clock=clock,
            settings=settings,
            service=service,
            client=client,
            as_user=as_user,
        )
    finally:
        app.dependency_overrides.pop(get_notification_binding_service, None)
        app.dependency_overrides.pop(get_current_user, None)


# ── 测试装配辅助 ───────────────────────────────────────────────────────────────


def _seed_user(Session, user: User) -> None:
    with Session() as session:
        if session.query(orm.UserModel).filter_by(user_id=user.user_id).first() is None:
            session.add(
                orm.UserModel(
                    user_id=user.user_id,
                    username=user.username,
                    password_hash="x",
                    role=user.role.value,
                    is_active=True,
                    locale="zh",
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()


def _seed_binding(env, user: User, recipient_id: str, *, status: str = "active") -> None:
    now = datetime.now(UTC)
    with env.Session() as session:
        session.add(
            orm.UserNotificationBindingModel(
                binding_id=f"binding-{user.user_id}-{recipient_id}",
                user_id=user.user_id,
                channel="wxpusher",
                recipient_id=recipient_id,
                status=status,
                bound_at=now,
                revoked_at=now if status == "revoked" else None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _latest_challenge(env, user_id: str):
    with env.Session() as session:
        return (
            session.query(orm.NotificationBindingChallengeModel)
            .filter_by(user_id=user_id, channel="wxpusher")
            .order_by(orm.NotificationBindingChallengeModel.id.desc())
            .first()
        )


def _active_binding_recipient(env, user_id: str) -> str | None:
    with env.Session() as session:
        row = (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id, channel="wxpusher", status="active")
            .first()
        )
        return row.recipient_id if row is not None else None


def _binding_status(env, user_id: str) -> str | None:
    with env.Session() as session:
        row = (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id, channel="wxpusher")
            .first()
        )
        return row.status if row is not None else None


def _preference_count(env, user_id: str, channel: str = "wxpusher") -> int:
    with env.Session() as session:
        return (
            session.query(orm.UserNotificationPreferenceModel)
            .filter_by(user_id=user_id, channel=channel)
            .count()
        )


def _create_challenge(env, user: User) -> str:
    """经 API 创建 challenge，返回 challenge token 原文（仅测试装配可见，响应里没有）。"""
    env.as_user(user)
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 200, resp.text
    row = _latest_challenge(env, user.user_id)
    assert row is not None
    return env.service._open_challenges[row.challenge_id][0]


def _wx_event(
    action: str,
    *,
    uid: str,
    extra_code: str | None = None,
    app_key: str = "AK_synthetic",
) -> dict:
    data: dict[str, Any] = {"appId": 123, "appKey": app_key, "uid": uid}
    if extra_code is not None:
        data["extraCode"] = extra_code
    return {"action": action, "data": data}


def _webhook_url(path_key: str = _PATH_KEY) -> str:
    return f"/api/v1/integrations/wxpusher/webhook/{path_key}"


def _post_webhook(env, event: dict, *, path_key: str = _PATH_KEY):
    return env.client.post(_webhook_url(path_key), json=event)


# ── challenge 端点 ─────────────────────────────────────────────────────────────


def test_create_challenge_returns_image_not_plain_token(env, users) -> None:
    env.as_user(users.alice)
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"qr_image_url", "expires_at"}
    assert body["qr_image_url"].startswith("https://")
    assert body["expires_at"].endswith("+00:00")
    assert resp.headers["cache-control"] == "no-store"
    # token/provider_code 绝不出现在响应
    row = _latest_challenge(env, users.alice.user_id)
    assert row is not None
    token = env.service._open_challenges[row.challenge_id][0]
    assert token not in resp.text
    assert "provider_code" not in resp.text
    assert row.provider_code not in resp.text


def test_challenge_requires_auth(env) -> None:
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 401


def test_challenge_endpoint_disabled_returns_503(
    env, monkeypatch: pytest.MonkeyPatch, users
) -> None:
    """fail-closed：binding_enabled=false → 503 稳定码，且不产生任何 challenge 行。"""
    disabled = _settings(monkeypatch)
    monkeypatch.setattr(my_mod, "get_settings", lambda: disabled)
    monkeypatch.setattr(svc_mod, "get_settings", lambda: disabled)
    env.as_user(users.alice)
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 503
    assert resp.json()["detail"] == {"code": "wxpusher.binding_disabled"}
    assert _latest_challenge(env, users.alice.user_id) is None


def test_qrcode_failure_invalidates_only_that_challenge(env, users) -> None:
    """外部二维码失败 → 本次 challenge 条件作废、无 provider_code 残留、稳定错误码；
    他人/后续新 challenge 不受影响。"""
    # bob 先有一个成功创建的 open challenge
    bob_token = _create_challenge(env, users.bob)
    assert bob_token

    env.http.post_responses.append(WxPusherBindingError("wxpusher_qr_network"))
    env.as_user(users.alice)
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == {"code": "wxpusher.qrcode_failed"}

    failed = _latest_challenge(env, users.alice.user_id)
    assert failed is not None
    assert failed.consumed_at is not None  # 被条件作废
    assert failed.provider_code is None  # 无 provider_code 残留

    # bob 的先前 challenge 不受影响（仍 open）
    bob_row = _latest_challenge(env, users.bob.user_id)
    assert bob_row is not None
    assert bob_row.consumed_at is None
    assert bob_row.provider_code is not None

    # 后续新 challenge 正常（作废不波及随后创建的新 challenge）
    retry = env.client.post(_CHALLENGE_URL)
    assert retry.status_code == 200, retry.text
    fresh = _latest_challenge(env, users.alice.user_id)
    assert fresh is not None
    assert fresh.consumed_at is None
    assert fresh.provider_code is not None


def test_set_provider_code_false_fails_closed(env, users, monkeypatch) -> None:
    """S-2：set_provider_code 返回 False（双击建码竞态）→ 死码不得当成功：
    按外部失败同语义作废 challenge 并 502 稳定码。"""
    monkeypatch.setattr(
        env.service._repo, "set_provider_code", lambda session, cid, code: False
    )
    env.as_user(users.alice)
    resp = env.client.post(_CHALLENGE_URL)
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == {"code": "wxpusher.qrcode_failed"}

    row = _latest_challenge(env, users.alice.user_id)
    assert row is not None
    assert row.consumed_at is not None  # 已条件作废
    assert row.provider_code is None
    assert env.service._open_challenges == {}  # 内存项已清理


def test_qrcode_valid_time_uses_settings_ttl(
    env, users, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-4：二维码 validTime 用 settings 的 challenge TTL，而非客户端硬编码 600。"""
    custom = _settings(
        monkeypatch, **{**_ENABLED_KWARGS, "wxpusher_challenge_ttl_seconds": 300}
    )
    monkeypatch.setattr(svc_mod, "get_settings", lambda: custom)
    env.as_user(users.alice)

    resp = env.client.post(_CHALLENGE_URL)

    assert resp.status_code == 200, resp.text
    assert env.http.post_calls[-1]["json"]["validTime"] == 300
    assert datetime.fromisoformat(resp.json()["expires_at"]) == env.clock.now().replace(
        tzinfo=UTC
    ) + timedelta(seconds=300)


# ── webhook ────────────────────────────────────────────────────────────────────


def test_subscribe_callback_binds_user_once(env, users) -> None:
    token = _create_challenge(env, users.alice)
    event = _wx_event("app_subscribe", uid="UID_alice", extra_code=token)

    first = _post_webhook(env, event)
    second = _post_webhook(env, event)

    assert first.status_code == 204
    assert first.text == ""
    assert second.status_code == 204  # 重复回调幂等，同样 204
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_alice"
    # active binding 恰 1（重放不产生第二行）
    with env.Session() as session:
        assert (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=users.alice.user_id, channel="wxpusher", status="active")
            .count()
            == 1
        )
    # 同事务初始化了渠道默认 preference
    assert _preference_count(env, users.alice.user_id) == 1


def test_wrong_path_key_or_app_key_fails_closed(env, users) -> None:
    token = _create_challenge(env, users.alice)

    wrong_path = _post_webhook(
        env,
        _wx_event("app_subscribe", uid="UID_alice", extra_code=token),
        path_key="pk_" + "w" * 40,
    )
    assert wrong_path.status_code == 404  # 不泄露存在性

    wrong_key = _post_webhook(
        env,
        _wx_event(
            "app_subscribe", uid="UID_alice", extra_code=token, app_key="AK_wrong"
        ),
    )
    assert wrong_key.status_code == 403
    assert wrong_key.json()["detail"] == {"code": "wxpusher.webhook_forbidden"}
    assert _active_binding_recipient(env, users.alice.user_id) is None


def test_webhook_non_ascii_path_key_returns_404_not_500(env) -> None:
    """I-1：非 ASCII path_key 不得让 str 版 compare_digest 抛 TypeError → 500；
    必须与未知路由同形 404（bytes 形式比较对任意内容恒不抛）。"""
    quiet = TestClient(app, raise_server_exceptions=False)
    resp = quiet.post("/api/v1/integrations/wxpusher/webhook/%E4%BD%A0%E5%A5%BD", json={})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_webhook_wrong_path_key_ignores_malformed_body(env) -> None:
    """I-2：错 path_key + 非 dict body → 404（绝不在 path_key 检查前 422 回显请求体）。"""
    resp = env.client.post(
        f"/api/v1/integrations/wxpusher/webhook/pk_{'w' * 40}", json=[1, 2]
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_webhook_non_dict_or_broken_body_stable_code_no_echo(env) -> None:
    """I-2：正确 path_key + 非 dict / 坏 JSON → 稳定 code（403 验签失败族），
    响应绝不回显请求体内容。"""
    non_dict = env.client.post(_webhook_url(), json=[1, 2])
    assert non_dict.status_code == 403
    assert non_dict.json()["detail"] == {"code": "wxpusher.webhook_forbidden"}
    assert "[1, 2]" not in non_dict.text

    broken = env.client.post(
        _webhook_url(),
        content="{not-json",
        headers={"content-type": "application/json"},
    )
    assert broken.status_code == 403
    assert broken.json()["detail"] == {"code": "wxpusher.webhook_forbidden"}
    assert "not-json" not in broken.text


def test_webhook_unknown_action_returns_400_stable_code(env) -> None:
    resp = _post_webhook(env, _wx_event("app_follow", uid="UID_alice"))
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "wxpusher_webhook_unknown_action"
    assert "UID_alice" not in resp.text


def test_expired_challenge_webhook_returns_204(env, users) -> None:
    token = _create_challenge(env, users.alice)
    with env.Session() as session:
        row = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter_by(user_id=users.alice.user_id, channel="wxpusher")
            .one()
        )
        # 过期时刻以服务端注入 clock 为基准（而非墙钟）
        row.expires_at = env.clock.now() - timedelta(seconds=1)
        session.commit()

    resp = _post_webhook(env, _wx_event("app_subscribe", uid="UID_alice", extra_code=token))

    assert resp.status_code == 204
    assert _active_binding_recipient(env, users.alice.user_id) is None


def test_unsubscribe_revokes_current_uid(env, users) -> None:
    token = _create_challenge(env, users.alice)
    assert (
        _post_webhook(
            env, _wx_event("app_subscribe", uid="UID_alice", extra_code=token)
        ).status_code
        == 204
    )

    resp = _post_webhook(env, _wx_event("app_unsubscribe", uid="UID_alice"))

    assert resp.status_code == 204
    assert _binding_status(env, users.alice.user_id) == "revoked"
    assert _active_binding_recipient(env, users.alice.user_id) is None


def test_unsubscribe_with_stale_uid_does_not_revoke_rebound_user(env, users) -> None:
    """退订旧 UID 不能误撤本人重绑后的新 UID 绑定。"""
    old_token = _create_challenge(env, users.alice)
    _post_webhook(env, _wx_event("app_subscribe", uid="UID_old", extra_code=old_token))
    new_token = _create_challenge(env, users.alice)
    _post_webhook(env, _wx_event("app_subscribe", uid="UID_new", extra_code=new_token))
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_new"

    resp = _post_webhook(env, _wx_event("app_unsubscribe", uid="UID_old"))

    assert resp.status_code == 204
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_new"  # 新绑定完好


def test_webhook_exempt_from_internal_key_auth(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设 INTERNAL_API_KEY 的部署上官方服务器不带 X-Internal-Key：webhook 前缀豁免放行，
    普通端点仍受内部键保护。"""
    import zhice_os_v2.api.main as main_mod

    monkeypatch.setattr(
        main_mod, "get_settings", lambda: SimpleNamespace(internal_api_key="ik-secret")
    )
    resp = _post_webhook(env, _wx_event("app_subscribe", uid="UID_alice", extra_code="x"))
    assert resp.status_code == 204  # 路由可达（非中间件 401）

    other = env.client.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
    assert other.status_code == 401
    assert other.json() == {"error": "unauthorized"}


# ── GET bindings 轮询闭环 ─────────────────────────────────────────────────────


def test_poll_completes_binding_when_webhook_lost(env, users) -> None:
    """webhook 从未到达：GET bindings 轮询官方扫码结果兜底完成绑定。"""
    _create_challenge(env, users.alice)
    env.http.get_responses.append(
        _Response(200, {"success": True, "code": 1000, "data": {"uid": "UID_alice"}})
    )

    resp = env.client.get(_BINDINGS_URL)

    assert resp.status_code == 200, resp.text
    entries = resp.json()
    wx_entry = next(e for e in entries if e["channel"] == "wxpusher")
    assert wx_entry["status"] == "active"
    assert wx_entry["recipient_masked"] == "UID_***lice"
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_alice"
    assert _preference_count(env, users.alice.user_id) == 1
    # 官方 send/message 从未被调用（webhook 丢失，也不发测试消息）
    assert all("send/message" not in c["url"] for c in env.http.post_calls)


def test_repeated_poll_does_not_duplicate_preference(env, users) -> None:
    token = _create_challenge(env, users.alice)
    env.http.get_responses.append(
        _Response(200, {"success": True, "code": 1000, "data": {"uid": "UID_alice"}})
    )
    env.clock.advance(timedelta(seconds=10))
    first = env.client.get(_BINDINGS_URL)
    second = env.client.get(_BINDINGS_URL)
    assert first.status_code == second.status_code == 200
    assert _preference_count(env, users.alice.user_id) == 1
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_alice"

    # S-3：轮询已消费 challenge 后，同 challenge 的 webhook 迟到再投 → 204 幂等，
    # preference 仍恰 1 行、绑定不变（两条路径共享一次初始化）
    replay = _post_webhook(env, _wx_event("app_subscribe", uid="UID_alice", extra_code=token))
    assert replay.status_code == 204
    assert _preference_count(env, users.alice.user_id) == 1
    assert _active_binding_recipient(env, users.alice.user_id) == "UID_alice"


def test_poll_throttled_to_three_seconds(env, users) -> None:
    _create_challenge(env, users.alice)  # 默认 get 响应：未扫码
    env.as_user(users.alice)

    env.client.get(_BINDINGS_URL)
    assert len(env.http.get_calls) == 1

    env.clock.advance(timedelta(seconds=1))  # 节流窗口内：不再查官方
    env.client.get(_BINDINGS_URL)
    assert len(env.http.get_calls) == 1

    env.clock.advance(timedelta(seconds=3))  # 越过 3 秒：再查一次
    env.client.get(_BINDINGS_URL)
    assert len(env.http.get_calls) == 2
    assert (
        _active_binding_recipient(env, users.alice.user_id) is None
    )  # 未扫码保持 unbound


def test_poll_skips_official_when_token_missing(env, users) -> None:
    """S-1：进程重启丢了内存 token 时，轮询不得白打官方扫码外呼。"""
    _create_challenge(env, users.alice)
    env.service._open_challenges.clear()  # 模拟进程重启
    env.http.get_responses.append(
        _Response(200, {"success": True, "code": 1000, "data": {"uid": "UID_alice"}})
    )

    resp = env.client.get(_BINDINGS_URL)

    assert resp.status_code == 200
    assert len(env.http.get_calls) == 0  # 未发任何官方请求
    assert _active_binding_recipient(env, users.alice.user_id) is None


def test_get_bindings_never_leaks_provider_code_or_token(env, users) -> None:
    _create_challenge(env, users.alice)
    row = _latest_challenge(env, users.alice.user_id)
    assert row is not None
    token = env.service._open_challenges[row.challenge_id][0]  # 轮询消费前先取 token
    env.http.get_responses.append(
        _Response(200, {"success": True, "code": 1000, "data": {"uid": "UID_alice"}})
    )
    assert env.client.get(_BINDINGS_URL).status_code == 200

    again = env.client.get(_BINDINGS_URL)
    assert "provider_code" not in again.text
    assert row.provider_code not in again.text
    assert token not in again.text
    for entry in again.json():
        assert set(entry) == {"channel", "status", "recipient_masked", "bound_at"}


# ── test / unbind 端点（身份恒取 JWT）──────────────────────────────────────────


def test_test_message_ignores_caller_supplied_identity_and_uses_current_user(
    env, users
) -> None:
    _seed_binding(env, users.alice, "UID_alice")
    _seed_binding(env, users.bob, "UID_bob")
    env.http.post_responses.append(
        _Response(
            200,
            {"success": True, "code": 1000, "data": [{"uid": "UID_alice", "code": 1000}]},
        )
    )
    env.as_user(users.alice)

    resp = env.client.post(_TEST_URL, json={"user_id": users.bob.user_id})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    # transport 只收到当前用户（alice）的 UID
    send_calls = [c for c in env.http.post_calls if "send/message" in c["url"]]
    assert len(send_calls) == 1
    assert send_calls[0]["json"]["uids"] == ["UID_alice"]
    # 响应绝不出现任何 UID（掩码也不返回）
    assert "UID_" not in resp.text


def test_test_message_without_binding_returns_409(env, users) -> None:
    env.as_user(users.alice)
    resp = env.client.post(_TEST_URL)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"code": "notification.binding_required"}


def test_delete_has_no_cross_account_identifier(env, users) -> None:
    _seed_binding(env, users.alice, "UID_alice")
    _seed_binding(env, users.bob, "UID_bob")
    env.as_user(users.alice)

    resp = env.client.delete(_WXPUSHER_URL, params={"user_id": users.bob.user_id})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert _binding_status(env, users.alice.user_id) == "revoked"  # 只撤当前用户
    assert (
        _active_binding_recipient(env, users.bob.user_id) == "UID_bob"
    )  # usr-other 不受影响
