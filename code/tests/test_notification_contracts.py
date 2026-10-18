"""通知渠道领域契约与 WxPusher 配置门。"""

from __future__ import annotations

from typing import Any

import pytest

from zhice_os_v2.config.settings import AppSettings
from zhice_os_v2.domain.notification.contracts import (
    BindingStatus,
    NotificationChannel,
    NotificationTransport,
    RenderedMessage,
    SendResult,
    mask_recipient,
)
from zhice_os_v2.domain.notification.preference import (
    NOTIFICATION_CHANNEL_TELEGRAM,
    NOTIFICATION_CHANNEL_WXPUSHER,
    SUPPORTED_NOTIFICATION_CHANNELS,
)


def _settings(**kwargs: Any) -> AppSettings:
    return AppSettings(_env_file=None, **kwargs)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _isolate_wxpusher_environ(monkeypatch):
    """隔离本机 .env.local 污染：scheduler_jobs 顶层 load_dotenv 会把 .env.local 的
    WXPUSHER_* 写进进程 os.environ（同 pytest 进程内所有后续测试共享），而 _settings
    的 _env_file=None 只屏蔽 .env.local 文件本身、屏蔽不了已进 environ 的同名变量，
    致「默认关闭」契约断言依赖机器真实配置。显式删除，恢复源码默认假设。"""
    import os

    for key in [k for k in os.environ if k.startswith("WXPUSHER_")]:
        monkeypatch.delenv(key, raising=False)


class _FakeTransport:
    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
        return SendResult(ok=bool(recipient_id and message.text))


def test_notification_contract_values_and_payloads() -> None:
    assert NotificationChannel.TELEGRAM.value == "telegram"
    assert NotificationChannel.WXPUSHER.value == "wxpusher"
    assert BindingStatus.ACTIVE.value == "active"
    assert BindingStatus.REVOKED.value == "revoked"

    message = RenderedMessage(
        summary="摘要",
        text="纯文本",
        html="<p>正文</p>",
        url="https://example.com/detail",
    )
    result = SendResult(
        ok=False,
        status_code=400,
        error_code="wxpusher_target_failed",
        retryable=False,
        recipient_invalid=True,
        retry_after=30,
    )

    assert message.url == "https://example.com/detail"
    assert result.retryable is False
    assert result.recipient_invalid is True
    assert result.retry_after == 30
    transport: NotificationTransport = _FakeTransport()
    assert transport.send("recipient", message).ok is True


def test_mask_recipient_never_exposes_short_or_full_identifier() -> None:
    assert mask_recipient("1234") == "****"
    assert mask_recipient("abc") == "***"
    assert mask_recipient("12345678") == "****5678"
    assert mask_recipient("UID_abcdef123456") == "UID_***3456"
    assert "abcdef" not in mask_recipient("UID_abcdef123456")


def test_supported_notification_channels_keep_telegram_compatibility() -> None:
    assert NOTIFICATION_CHANNEL_TELEGRAM == "telegram"
    assert NOTIFICATION_CHANNEL_WXPUSHER == "wxpusher"
    assert frozenset({"telegram", "wxpusher"}) == SUPPORTED_NOTIFICATION_CHANNELS


def test_wxpusher_settings_are_disabled_by_default() -> None:
    settings = _settings()

    assert settings.wxpusher_binding_enabled is False
    assert settings.wxpusher_delivery_enabled is False
    assert settings.wxpusher_app_token == ""
    assert settings.wxpusher_app_id == 0
    assert settings.wxpusher_app_key == ""
    assert settings.wxpusher_webhook_path_key == ""
    assert settings.wxpusher_challenge_ttl_seconds == 600
    assert settings.wxpusher_timeout_seconds == 8.0


def test_enabling_wxpusher_binding_requires_all_credentials() -> None:
    with pytest.raises(ValueError) as exc_info:
        _settings(wxpusher_binding_enabled=True)

    text = str(exc_info.value)
    assert "WXPUSHER_APP_TOKEN" in text
    assert "WXPUSHER_APP_ID" in text
    assert "WXPUSHER_APP_KEY" in text
    assert "WXPUSHER_WEBHOOK_PATH_KEY" in text


@pytest.mark.parametrize("path_key", ["", "x" * 31])
def test_wxpusher_binding_requires_path_key_of_at_least_32_characters(
    path_key: str,
) -> None:
    with pytest.raises(ValueError, match="WXPUSHER_WEBHOOK_PATH_KEY"):
        _settings(
            wxpusher_binding_enabled=True,
            wxpusher_app_token="AT_test",
            wxpusher_app_id=123,
            wxpusher_app_key="app-key",
            wxpusher_webhook_path_key=path_key,
        )


def test_complete_wxpusher_binding_config_is_accepted() -> None:
    settings = _settings(
        wxpusher_binding_enabled=True,
        wxpusher_app_token="AT_test",
        wxpusher_app_id=123,
        wxpusher_app_key="app-key",
        wxpusher_webhook_path_key="x" * 32,
    )

    assert settings.wxpusher_binding_enabled is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wxpusher_app_token", "   "),
        ("wxpusher_app_token", " AT_test"),
        ("wxpusher_app_key", "\t"),
        ("wxpusher_app_key", "app-key "),
        ("wxpusher_webhook_path_key", " " * 32),
        ("wxpusher_webhook_path_key", "x" * 31 + " "),
    ],
)
def test_wxpusher_credentials_reject_blank_or_surrounding_whitespace(
    field: str, value: str
) -> None:
    with pytest.raises(ValueError, match="must not contain surrounding whitespace"):
        _settings(**{field: value})


def test_wxpusher_path_key_requires_32_non_whitespace_characters() -> None:
    with pytest.raises(ValueError, match="WXPUSHER_WEBHOOK_PATH_KEY"):
        _settings(
            wxpusher_binding_enabled=True,
            wxpusher_app_token="AT_test",
            wxpusher_app_id=123,
            wxpusher_app_key="app-key",
            wxpusher_webhook_path_key="x" * 30 + "\ty",
        )


@pytest.mark.parametrize("ttl", [0, -1, True, 1.0, 1.5, "1.0"])
def test_wxpusher_challenge_ttl_must_be_positive_integer(ttl: object) -> None:
    with pytest.raises(ValueError):
        _settings(wxpusher_challenge_ttl_seconds=ttl)


def test_wxpusher_challenge_ttl_accepts_integer_environment_string() -> None:
    settings = _settings(wxpusher_challenge_ttl_seconds="600")
    assert settings.wxpusher_challenge_ttl_seconds == 600


@pytest.mark.parametrize(
    "timeout", [True, False, 0.0, -0.1, float("nan"), float("inf"), float("-inf")]
)
def test_wxpusher_timeout_must_be_finite_and_positive(timeout: object) -> None:
    with pytest.raises(ValueError):
        _settings(wxpusher_timeout_seconds=timeout)


@pytest.mark.parametrize("timeout", [1, "2.5"])
def test_wxpusher_timeout_accepts_positive_number_inputs(timeout: int | str) -> None:
    settings = _settings(wxpusher_timeout_seconds=timeout)
    assert settings.wxpusher_timeout_seconds == float(timeout)


def test_wxpusher_delivery_requires_app_token() -> None:
    with pytest.raises(ValueError, match="WXPUSHER_APP_TOKEN"):
        _settings(
            wxpusher_delivery_enabled=True,
            telegram_notification_mode="directed",
            multi_user_execution_mode="enforced",
        )


def test_wxpusher_delivery_requires_directed_enforced_mode() -> None:
    with pytest.raises(ValueError, match="directed notifications require enforced ownership"):
        _settings(
            wxpusher_delivery_enabled=True,
            wxpusher_app_token="AT_test",
            telegram_notification_mode="broadcast",
            multi_user_execution_mode="legacy",
        )

    settings = _settings(
        wxpusher_delivery_enabled=True,
        wxpusher_app_token="AT_test",
        telegram_notification_mode="directed",
        multi_user_execution_mode="enforced",
    )
    assert settings.wxpusher_delivery_enabled is True
