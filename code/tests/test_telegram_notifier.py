"""Telegram 推送 telegram_notifier + AuthService.set_telegram_chat_id 单测。

全部 mock，不发真网络、不连真库。
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from zhice_os_v2.application.services import telegram_notifier as tn
from zhice_os_v2.application.services.auth_service import AuthService


# ── send_telegram（Task 6 起返回 TelegramSendResult；断言语义不变：成功/失败）──
def test_send_no_token(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "")
    assert tn.send_telegram("123", "hi").ok is False


def test_send_empty_chat(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    assert tn.send_telegram("", "hi").ok is False


def test_send_success(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(tn, "_proxy", lambda: None)  # 消除 .env.local TELEGRAM_PROXY 副作用
    import httpx

    class _R:
        status_code = 200

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    assert tn.send_telegram("123", "hi").ok is True


def test_send_non200(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(tn, "_proxy", lambda: None)  # 消除 .env.local TELEGRAM_PROXY 副作用
    import httpx

    class _R:
        status_code = 403

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    result = tn.send_telegram("123", "hi")
    assert result.ok is False
    assert result.retryable is False
    assert result.recipient_invalid is True


@pytest.mark.parametrize(
    ("status", "recipient_invalid"),
    [(400, False), (403, True), (404, False)],
)
def test_send_recipient_invalid_only_on_deterministic_403(
    monkeypatch, status, recipient_invalid
):
    """I-2：400 多为载荷错误（message too long / can't parse entities）、404 亦非
    确定性永久失效——误报会让正常绑定被 revoke（计划契约 12）；仅 403（bot 被
    用户屏蔽）是确定性永久失败。"""
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(tn, "_proxy", lambda: None)  # 消除 .env.local TELEGRAM_PROXY 副作用
    import httpx

    class _R:
        status_code = status

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    result = tn.send_telegram("123", "hi")
    assert result.ok is False
    assert result.recipient_invalid is recipient_invalid


def test_send_network_error_is_retryable_and_recipient_remains_valid(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(tn, "_proxy", lambda: None)
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    result = tn.send_telegram("123", "hi")
    assert result.ok is False
    assert result.retryable is True
    assert result.recipient_invalid is False


# ── broadcast_telegram ─────────────────────────────────────────────────────
def test_broadcast_no_token(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "")
    assert tn.broadcast_telegram("hi") == 0


def test_broadcast_counts(monkeypatch):
    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(
        tn, "send_telegram", lambda cid, text: tn.TelegramSendResult(ok=True)
    )
    import zhice_os_v2.config.settings as st_mod
    fake_settings = MagicMock()
    fake_settings.persistence_backend = "sql"
    monkeypatch.setattr(st_mod, "get_settings", lambda: fake_settings)
    import zhice_os_v2.infrastructure.repositories.user_repository as ur
    fake_repo = MagicMock()
    fake_repo.list_active_notification_recipients.return_value = ["1", "2", "3"]
    monkeypatch.setattr(ur, "UserRepository", lambda: fake_repo)
    assert tn.broadcast_telegram("hi") == 3


# ── notify_fill 消息格式 ────────────────────────────────────────────────────
def test_notify_fill_buy(monkeypatch):
    sent = {}
    monkeypatch.setattr(tn, "broadcast_telegram", lambda text: sent.update(text=text) or 1)
    monkeypatch.setattr(tn, "_strategy_name", lambda sv: "趋势动量v1")
    tn.notify_fill(side="BUY", ts_code="600519.SH", price=Decimal("1680"), qty=Decimal("100"),
                   amount=Decimal("168000"), market="CN", sv_id="sv1", reason="趋势动量:多头排列")
    t = sent["text"]
    assert "买入" in t and "600519.SH" in t and "趋势动量v1" in t and "多头排列" in t


def test_notify_fill_sell(monkeypatch):
    sent = {}
    monkeypatch.setattr(tn, "broadcast_telegram", lambda text: sent.update(text=text) or 1)
    monkeypatch.setattr(tn, "_strategy_name", lambda sv: "")
    tn.notify_fill(side="SELL", ts_code="AAPL", price=Decimal("175"), qty=Decimal("10"),
                   amount=Decimal("1750"), market="US", realized_pnl=Decimal("120.5"),
                   sell_reason="止损触发")
    t = sent["text"]
    assert "卖出" in t and "盈亏 +120.50" in t and "止损触发" in t


# ── AuthService.set_telegram_chat_id 校验 ──────────────────────────────────
def _svc():
    repo = MagicMock()
    return AuthService(repo=repo), repo


def test_set_telegram_valid():
    svc, repo = _svc()
    svc.set_telegram_chat_id("u1", "123456789")
    repo.update_telegram_chat_id.assert_called_once_with("u1", "123456789")


def test_set_telegram_negative_group_ok():
    svc, repo = _svc()
    svc.set_telegram_chat_id("u1", "-100200300")  # 群组负 id 合法
    repo.update_telegram_chat_id.assert_called_once_with("u1", "-100200300")


def test_set_telegram_unbind():
    svc, repo = _svc()
    svc.set_telegram_chat_id("u1", "")
    repo.update_telegram_chat_id.assert_called_once_with("u1", None)


def test_set_telegram_invalid():
    svc, _ = _svc()
    with pytest.raises(ValueError, match="auth.invalid_chat_id"):
        svc.set_telegram_chat_id("u1", "abc")
