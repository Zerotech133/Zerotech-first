"""WxPusher 绑定服务：参数二维码 challenge 生命周期、webhook 语义与轮询闭环。

安全铁律：
- challenge token 用 ``secrets.token_urlsafe(32)`` 生成，DB 只落 SHA-256 哈希；token
  原文仅在创建后保留在**进程内存**（challenge_id → (token, expires_at)）供轮询闭环
  复用，绝不落库、绝不写日志、绝不返回前端；
- provider_code（官方二维码 code）只存服务端、只用于 scan 查询，不返回前端、不写日志；
- 事务边界：每个用例自开短事务，外部 HTTP（二维码创建/扫码查询/发送）恒在事务外，
  绝不跨 HTTP 持锁/持事务；
- 外部二维码失败：按 challenge_id 精确条件作废本次 challenge（不波及他人或随后创建
  的新 challenge），随后抛稳定错误码（``wxpusher.qrcode_failed``，HTTP 由 router 映射）；
- 轮询闭环：webhook 丢失时 GET bindings 兜底——未消费、未过期、已存 provider_code 且
  当前未绑定时查官方扫码结果；返回 UID 走与 webhook 完全相同的 consume_and_bind 事务
  （同事务初始化渠道默认 preference）；每个 challenge 最快 3 秒查一次官方（内存节流）。
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.notification.contracts import RenderedMessage, mask_recipient
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.notifications.wxpusher_binding_client import (
    WxPusherBindingClient,
    WxPusherBindingError,
)
from zhice_os_v2.infrastructure.notifications.wxpusher_transport import WxPusherTransport
from zhice_os_v2.infrastructure.notifications.wxpusher_webhook import parse_webhook_event
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    ConsumeStatus,
    NotificationBindingRepository,
)
from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)

_CHANNEL_WXPUSHER = "wxpusher"
_POLL_THROTTLE_SECONDS = 3.0
_TEST_SUMMARY = "ZHICE·OS 绑定测试"

#: 稳定业务错误码（router 映射 HTTP 状态；消息绝不携带 token/uid/响应原文）
BINDING_DISABLED_CODE = "wxpusher.binding_disabled"
QRCODE_FAILED_CODE = "wxpusher.qrcode_failed"
BINDING_REQUIRED_CODE = "notification.binding_required"


def _utc(dt: datetime) -> datetime:
    """读回的 datetime 统一为 aware-UTC（sqlite 丢 tzinfo，PG 保留）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class BindingChallengeView:
    """challenge 创建结果：只有二维码图片与过期时刻，无 token/provider_code。"""

    qr_image_url: str
    expires_at: datetime


@dataclass(frozen=True)
class BindingView:
    """绑定状态视图：recipient 只给掩码。"""

    channel: str
    status: str
    recipient_masked: str | None
    bound_at: datetime | None


class NotificationBindingService:
    """WxPusher 绑定用例（短事务 + 外部 HTTP 恒在事务外；依赖均可注入覆盖）。"""

    def __init__(
        self,
        *,
        repo: NotificationBindingRepository | None = None,
        binding_client: WxPusherBindingClient | None = None,
        transport: WxPusherTransport | None = None,
        preference_repo: NotificationPreferenceRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        poll_throttle_seconds: float = _POLL_THROTTLE_SECONDS,
    ) -> None:
        self._repo = repo or NotificationBindingRepository()
        self._injected_binding_client = binding_client
        self._injected_transport = transport
        self._preference_repo = preference_repo or NotificationPreferenceRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_throttle_seconds = poll_throttle_seconds
        # challenge_id -> (token 原文, expires_at)：token 只留内存供轮询消费，绝不落库/日志
        self._open_challenges: dict[str, tuple[str, datetime]] = {}
        # challenge_id -> 上次官方扫码查询时刻（节流）
        self._last_scan: dict[str, datetime] = {}

    # ---------- 依赖装配（生产按 settings 惰性构造，测试构造器注入）----------

    def _now(self) -> datetime:
        return self._clock()

    def _binding_client(self) -> WxPusherBindingClient:
        if self._injected_binding_client is not None:
            return self._injected_binding_client
        settings = get_settings()
        return WxPusherBindingClient(
            app_token=settings.wxpusher_app_token,
            timeout=settings.wxpusher_timeout_seconds,
        )

    def _transport(self) -> WxPusherTransport:
        if self._injected_transport is not None:
            return self._injected_transport
        settings = get_settings()
        return WxPusherTransport(
            app_token=settings.wxpusher_app_token,
            timeout=settings.wxpusher_timeout_seconds,
        )

    # ---------- challenge 生命周期 ----------

    def create_wxpusher_challenge(self, user_id: str) -> BindingChallengeView:
        """创建参数二维码 challenge（顺序固定：短事务落 challenge → 外部建码 → 短事务存
        provider_code）。fail-closed：binding 未开启抛 ``wxpusher.binding_disabled``。"""
        settings = get_settings()
        if not settings.wxpusher_binding_enabled:
            raise ValueError(BINDING_DISABLED_CODE)
        now = self._now()
        expires_at = now + timedelta(seconds=settings.wxpusher_challenge_ttl_seconds)
        token = secrets.token_urlsafe(32)

        session = get_session()
        try:
            row = self._repo.create_challenge(
                session,
                user_id=user_id,
                channel=_CHANNEL_WXPUSHER,
                token=token,
                expires_at=expires_at,
            )
            session.commit()
            challenge_id = row.challenge_id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        self._prune_open_challenges(now)
        self._open_challenges[challenge_id] = (token, expires_at)

        try:
            qr = self._binding_client().create_qrcode(
                token, valid_time=settings.wxpusher_challenge_ttl_seconds
            )
        except WxPusherBindingError:
            # 外部失败：仅按 challenge_id 精确条件作废本次 challenge（DB 二次失败不掩盖
            # 稳定业务错误），随后抛稳定错误码
            self._fail_challenge(challenge_id)
            raise ValueError(QRCODE_FAILED_CODE) from None

        session = get_session()
        try:
            stored = self._repo.set_provider_code(session, challenge_id, qr.provider_code)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        if not stored:
            # 双击建码竞态：challenge 已被作废/过期或 provider_code 已被占用——死码
            # 不得当成功返回，按外部失败同语义条件作废 + 稳定错误码
            self._fail_challenge(challenge_id)
            raise ValueError(QRCODE_FAILED_CODE)
        return BindingChallengeView(qr_image_url=qr.image_url, expires_at=expires_at)

    def _fail_challenge(self, challenge_id: str) -> None:
        """外部失败收尾：按 challenge_id 精确条件作废并清理内存项（不波及任何其他行）。"""
        with suppress(Exception):
            self._invalidate_challenge(challenge_id)
        self._open_challenges.pop(challenge_id, None)
        self._last_scan.pop(challenge_id, None)

    def _invalidate_challenge(self, challenge_id: str) -> None:
        """按 challenge_id 精确条件作废（consumed_at 仍为空才作废，不波及任何其他行）。"""
        session = get_session()
        try:
            row = (
                session.query(orm.NotificationBindingChallengeModel)
                .filter(
                    orm.NotificationBindingChallengeModel.challenge_id == challenge_id,
                    orm.NotificationBindingChallengeModel.consumed_at.is_(None),
                )
                .with_for_update()
                .first()
            )
            if row is not None:
                row.consumed_at = self._now()
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _prune_open_challenges(self, now: datetime) -> None:
        for challenge_id in [
            cid for cid, (_, expires_at) in self._open_challenges.items() if expires_at <= now
        ]:
            self._open_challenges.pop(challenge_id, None)
            self._last_scan.pop(challenge_id, None)

    # ---------- 查询（含轮询闭环）----------

    def list_bindings(self, user_id: str) -> list[BindingView]:
        """本人全部渠道绑定（recipient 恒掩码）；触发 wxpusher 轮询兜底。"""
        self._poll_wxpusher_challenge(user_id)
        session = get_session()
        try:
            rows = self._repo.list_for_user(session, user_id)
            return [
                BindingView(
                    channel=row.channel,
                    status=row.status,
                    recipient_masked=(
                        mask_recipient(row.recipient_id) if row.recipient_id else None
                    ),
                    bound_at=_utc(row.bound_at) if row.bound_at else None,
                )
                for row in rows
            ]
        finally:
            session.close()

    def _poll_wxpusher_challenge(self, user_id: str) -> None:
        """webhook 丢失的兜底轮询：best-effort，任何失败绝不影响 GET 的成功返回。"""
        try:
            now = self._now()
            session = get_session()
            try:
                if self._repo.get_active(session, user_id, _CHANNEL_WXPUSHER) is not None:
                    return
                challenge = (
                    session.query(orm.NotificationBindingChallengeModel)
                    .filter(
                        orm.NotificationBindingChallengeModel.user_id == user_id,
                        orm.NotificationBindingChallengeModel.channel == _CHANNEL_WXPUSHER,
                        orm.NotificationBindingChallengeModel.consumed_at.is_(None),
                        orm.NotificationBindingChallengeModel.provider_code.isnot(None),
                    )
                    .order_by(orm.NotificationBindingChallengeModel.id.desc())
                    .first()
                )
                if challenge is None or _utc(challenge.expires_at) <= now:
                    return
                challenge_id = challenge.challenge_id
                provider_code = challenge.provider_code
            finally:
                session.close()
            if provider_code is None:  # pragma: no cover - 查询条件已保证非空
                return
            entry = self._open_challenges.get(challenge_id)
            if entry is None:
                # 进程重启丢了 token 原文：webhook 仍是权威绑定路径，先放弃再谈外呼
                # （不得白打官方扫码查询）
                return

            last = self._last_scan.get(challenge_id)
            if (
                last is not None
                and now - last < timedelta(seconds=self._poll_throttle_seconds)
            ):
                return
            self._last_scan[challenge_id] = now

            uid = self._binding_client().scan_qrcode_uid(provider_code)
            if not uid:
                return  # 未扫码：保持 unbound
            self._consume_and_init_preference(token=entry[0], recipient_id=uid)
        except (WxPusherBindingError, ValueError, LookupError):
            return

    # ---------- webhook 语义 ----------

    def handle_webhook_event(self, event: dict) -> None:
        """处理已验签的官方回调（parser 归一化后按 action 分派）。"""
        parsed = parse_webhook_event(event)
        if parsed.action == "subscribe":
            if not parsed.challenge_token:
                return  # 无 challenge 可消费：与过期同等对待（幂等）
            try:
                self._consume_and_init_preference(
                    token=parsed.challenge_token, recipient_id=parsed.recipient_id
                )
            except ValueError:
                # 收件人已被他人绑定（recipient_conflict）：权威状态不变，按已处理对待
                return
        elif parsed.action == "unsubscribe":
            self._revoke_wxpusher_by_uid(parsed.recipient_id)

    def _consume_and_init_preference(self, *, token: str, recipient_id: str) -> ConsumeStatus:
        """消费 challenge → 绑定 → 同事务初始化渠道默认 preference（webhook/轮询共用）。"""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        session = get_session()
        try:
            result = self._repo.consume_and_bind(
                session,
                token=token,
                channel=_CHANNEL_WXPUSHER,
                recipient_id=recipient_id,
                now=self._now(),
            )
            if result == "expired_or_used":
                session.rollback()
                return result
            challenge = (
                session.query(orm.NotificationBindingChallengeModel)
                .filter_by(token_hash=token_hash, channel=_CHANNEL_WXPUSHER)
                .first()
            )
            if challenge is not None:
                user = (
                    session.query(orm.UserModel)
                    .filter_by(user_id=challenge.user_id)
                    .first()
                )
                locale = (getattr(user, "locale", None) or "zh") if user is not None else "zh"
                # 同一事务初始化渠道默认偏好（并发已建行由唯一约束幂等吸收）
                self._preference_repo.create(
                    f"npref-{secrets.token_hex(8)}",
                    challenge.user_id,
                    channel=_CHANNEL_WXPUSHER,
                    locale=locale,
                    session=session,
                )
                session.commit()
                self._open_challenges.pop(challenge.challenge_id, None)
                self._last_scan.pop(challenge.challenge_id, None)
            else:  # pragma: no cover - 理论不可达（绑定成功必有 challenge 行）
                session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _revoke_wxpusher_by_uid(self, recipient_id: str) -> None:
        """按 uid 精确撤销当前 active 绑定（stale uid 查不到 active 行即 no-op）。"""
        session = get_session()
        try:
            owner = (
                session.query(orm.UserNotificationBindingModel)
                .filter_by(
                    channel=_CHANNEL_WXPUSHER, status="active", recipient_id=recipient_id
                )
                .first()
            )
            if owner is None:
                return
            self._repo.revoke_if_current(
                session, owner.user_id, _CHANNEL_WXPUSHER, recipient_id, now=self._now()
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ---------- 测试消息 / 解绑 ----------

    def send_test_message(self, user_id: str) -> dict:
        """向本人 active 绑定发测试消息；响应只有 {ok, reason?}，绝无 recipient。"""
        session = get_session()
        try:
            binding = self._repo.get_active(session, user_id, _CHANNEL_WXPUSHER)
            recipient = binding.recipient_id if binding is not None else None
        finally:
            session.close()
        if not recipient:
            raise ValueError(BINDING_REQUIRED_CODE)
        message = RenderedMessage(
            summary=_TEST_SUMMARY,
            text="✅ ZHICE·OS：WxPusher 绑定成功，这是一条测试消息。",
            html="<p>✅ ZHICE·OS：WxPusher 绑定成功，这是一条测试消息。</p>",
        )
        result = self._transport().send(recipient, message)
        payload: dict = {"ok": result.ok}
        if not result.ok and result.error_code:
            payload["reason"] = result.error_code
        return payload

    def unbind(self, user_id: str) -> None:
        """本人解绑：revoke active 绑定并作废全部未消费 challenge（repo.revoke 一并完成）。"""
        session = get_session()
        try:
            self._repo.revoke(
                session, user_id=user_id, channel=_CHANNEL_WXPUSHER, now=self._now()
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
