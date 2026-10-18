"""统一通知模板与 Telegram/WxPusher transport 契约。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest

from zhice_os_v2.application.services.notification_template_service import (
    NotificationTemplateService,
)
from zhice_os_v2.application.services.telegram_notifier import TelegramSendResult
from zhice_os_v2.domain.notification.contracts import RenderedMessage
from zhice_os_v2.infrastructure.notifications import wxpusher_transport
from zhice_os_v2.infrastructure.notifications.telegram_transport import TelegramTransport
from zhice_os_v2.infrastructure.notifications.wxpusher_transport import WxPusherTransport


class _Response:
    def __init__(
        self,
        status_code: int,
        body: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Client:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict, float]] = []

    def post(self, url: str, *, json: dict, timeout: float) -> _Response:
        self.calls.append((url, json, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _message(*, url: str | None = None) -> RenderedMessage:
    return RenderedMessage(summary="摘要", text="纯文本", html="<b>正文</b>", url=url)


def _transport(response: _Response | Exception) -> tuple[WxPusherTransport, _Client]:
    client = _Client(response)
    return WxPusherTransport(app_token="AT_super-secret", timeout=2.5, client=client), client


def test_wxpusher_single_uid_payload_has_no_topics() -> None:
    recipient = "UID_current_1234"
    transport, client = _transport(
        _Response(200, {"success": True, "code": 1000, "data": [{"uid": recipient, "code": 1000}]})
    )

    result = transport.send(recipient, _message(url="https://app.example/detail/1"))

    assert result.ok is True
    _, payload, timeout = client.calls[0]
    assert payload == {
        "appToken": "AT_super-secret",
        "summary": "摘要",
        "content": "<b>正文</b>",
        "contentType": 2,
        "uids": [recipient],
        "url": "https://app.example/detail/1",
    }
    assert "topicIds" not in payload
    assert timeout == 2.5


def test_wxpusher_payload_omits_url_key_when_message_has_no_url() -> None:
    # S-4：url=None 时 payload 不得出现 "url" 键（WxPusher 空串 url 会破坏跳转）
    recipient = "UID_current_1234"
    transport, client = _transport(
        _Response(200, {"success": True, "code": 1000, "data": [{"uid": recipient, "code": 1000}]})
    )

    result = transport.send(recipient, _message())  # url=None

    assert result.ok is True
    assert "url" not in client.calls[0][1]


def test_wxpusher_uses_exact_current_uid_result_not_first_entry() -> None:
    recipient = "UID_current_1234"
    transport, _ = _transport(
        _Response(
            200,
            {
                "success": True,
                "code": 1000,
                "data": [
                    {"uid": "UID_someone_else", "code": 1000},
                    {"uid": recipient, "code": 1001},
                ],
            },
        )
    )

    result = transport.send(recipient, _message())

    assert result.ok is False
    assert result.error_code == "wxpusher_target_1001"
    assert result.retryable is True
    assert result.recipient_invalid is False


@pytest.mark.parametrize("data", [None, {}, "bad", [{"uid": "UID_other", "code": 1000}], [{}]])
def test_wxpusher_missing_or_malformed_target_fails_closed(data: object) -> None:
    transport, _ = _transport(
        _Response(200, {"success": True, "code": 1000, "data": data})
    )

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.error_code in {"invalid_response", "wxpusher_target_missing"}
    assert result.retryable is True
    assert result.recipient_invalid is False


def test_wxpusher_top_level_business_error_is_safe_and_retryable() -> None:
    transport, _ = _transport(
        _Response(200, {"success": False, "code": 2002, "msg": "AT_super-secret rejected"})
    )

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.error_code == "wxpusher_api_2002"
    assert result.retryable is True
    assert result.recipient_invalid is False
    assert "AT_super-secret" not in repr(result)


def test_wxpusher_unclassified_business_code_does_not_revoke_recipient() -> None:
    recipient = "UID_current_1234"
    transport, _ = _transport(
        _Response(
            200,
            {"success": True, "code": 1000, "data": [{"uid": recipient, "code": 987654}]},
        )
    )

    result = transport.send(recipient, _message())

    assert result.error_code == "wxpusher_target_987654"
    assert result.retryable is True
    assert result.recipient_invalid is False


def test_wxpusher_api_error_code_length_is_capped() -> None:
    # I-1：超长纯数字 code 归 invalid——last_error_code 是 String(64)，
    # "wxpusher_api_" + 80 位数字（93 字符）会撑爆列
    transport, _ = _transport(_Response(200, {"success": False, "code": "9" * 80, "msg": "boom"}))

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.error_code == "wxpusher_api_invalid"
    assert len(result.error_code) <= 64


def test_wxpusher_top_level_float_code_is_not_treated_as_success() -> None:
    # S-2：1000.0 == 1000 松散相等为 True，float 顶层码不得判成功
    transport, _ = _transport(
        _Response(
            200,
            {"success": True, "code": 1000.0, "data": [{"uid": "UID_current_1234", "code": 1000}]},
        )
    )

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.error_code == "wxpusher_api_invalid"


def test_wxpusher_per_target_float_code_is_not_treated_as_success() -> None:
    # 顶层/目标级判定一致性：data[].code=1000.0 不得因松散相等判目标成功；
    # str(1000.0)="1000.0" 非纯数字 → 归 wxpusher_target_invalid（同 _safe_code 语义）
    recipient = "UID_current_1234"
    transport, _ = _transport(
        _Response(200, {"success": True, "code": 1000, "data": [{"uid": recipient, "code": 1000.0}]})
    )

    result = transport.send(recipient, _message())

    assert result.ok is False
    assert result.error_code == "wxpusher_target_invalid"
    assert result.retryable is True


def test_wxpusher_network_error_redacts_token_recipient_and_url(caplog) -> None:
    token = "AT_super-secret"
    recipient = "UID_sensitive_1234"
    request = httpx.Request(
        "POST", f"https://wxpusher.zjiecode.com/api/send/message?token={token}&uid={recipient}"
    )
    transport, _ = _transport(httpx.ReadTimeout(str(request.url), request=request))

    with caplog.at_level(logging.WARNING):
        result = transport.send(recipient, _message(url="https://private.example/user/1234"))

    assert result.ok is False
    assert result.error_code == "network"
    assert result.retryable is True
    assert result.recipient_invalid is False
    exposed = repr(result) + caplog.text
    assert token not in exposed
    assert recipient not in exposed
    assert "private.example" not in exposed
    assert "wxpusher.zjiecode.com" not in exposed


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (404, False), (429, True), (500, True), (503, True)],
)
def test_wxpusher_http_status_classification(status: int, retryable: bool) -> None:
    transport, _ = _transport(_Response(status, {"secret": "must-not-be-used"}))

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.status_code == status
    assert result.error_code == f"http_{status}"
    assert result.retryable is retryable
    assert result.recipient_invalid is False


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("45", 45),
        ("0", None),
        ("-1", None),
        ("3601", None),
        ("1.5", None),
        ("later", None),
        ("²", None),
    ],
)
def test_wxpusher_429_retry_after_only_accepts_reasonable_integer(
    header: str, expected: int | None
) -> None:
    transport, _ = _transport(_Response(429, {}, headers={"Retry-After": header}))

    result = transport.send("UID_current_1234", _message())

    assert result.retry_after == expected
    assert result.retryable is True


def test_wxpusher_superscript_retry_after_header_never_raises() -> None:
    # I-1："²".isdigit() 为 True 但 int("²") 抛 ValueError——畸形 Retry-After 头
    # 不得击穿 transport 永不抛边界，应按无 retry_after 返回 SendResult
    transport, _ = _transport(_Response(429, {}, headers={"Retry-After": "²"}))

    result = transport.send("UID_current_1234", _message())

    assert result.ok is False
    assert result.error_code == "http_429"
    assert result.retryable is True
    assert result.retry_after is None


def test_wxpusher_uses_explicit_client_even_when_falsy(monkeypatch) -> None:
    # S-2：HttpClient Protocol 不约束 __bool__，显式传入的 falsy client 不应被
    # `or httpx.Client()` 静默换成新实例（丢了调用记录/测试替身）
    class _Forbidden:
        def post(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("must not construct a replacement client")

    monkeypatch.setattr(wxpusher_transport, "httpx", SimpleNamespace(Client=_Forbidden))

    class _FalsyClient(_Client):
        def __bool__(self) -> bool:
            return False

    client = _FalsyClient(
        _Response(
            200, {"success": True, "code": 1000, "data": [{"uid": "UID_current_1234", "code": 1000}]}
        )
    )
    transport = WxPusherTransport(app_token="AT_x", timeout=1.0, client=client)

    result = transport.send("UID_current_1234", _message())

    assert result.ok is True
    assert len(client.calls) == 1


def test_wxpusher_invalid_json_and_unsafe_code_fail_safely() -> None:
    invalid_json, _ = _transport(_Response(200, ValueError("raw token AT_super-secret")))
    unsafe_code, _ = _transport(
        _Response(200, {"success": False, "code": "2002/token=secret"})
    )

    invalid_result = invalid_json.send("UID_current_1234", _message())
    unsafe_result = unsafe_code.send("UID_current_1234", _message())

    assert invalid_result.error_code == "invalid_response"
    assert unsafe_result.error_code == "wxpusher_api_invalid"
    assert invalid_result.retryable is True
    assert unsafe_result.retryable is True


def test_template_render_message_escapes_html_but_preserves_plain_text() -> None:
    service = NotificationTemplateService()
    raw = "A&B <突破>"

    message = service.render_message(
        "fill",
        locale="zh",
        payload={"strategy": raw, "url": "https://app.example/fill/1"},
    )

    assert raw in message.text
    assert raw not in message.html
    assert "A&amp;B &lt;突破&gt;" in message.html
    assert message.url == "https://app.example/fill/1"
    assert len(message.summary) <= 100
    assert service.render("fill", locale="zh", payload={"strategy": raw}) == message.html


def test_template_summary_is_capped_at_100_characters() -> None:
    message = NotificationTemplateService().render_message(
        "user_report", locale="zh", payload={"title": "x" * 150}
    )

    assert len(message.summary) == 100
    assert message.summary == message.text.splitlines()[0][:100]


def test_telegram_adapter_maps_send_result_without_losing_semantics() -> None:
    expected = TelegramSendResult(
        ok=False,
        status_code=429,
        error_code="http_429",
        retryable=True,
        recipient_invalid=True,
        retry_after=27,
    )
    calls: list[tuple[str, str]] = []

    def sender(recipient_id: str, text: str) -> TelegramSendResult:
        calls.append((recipient_id, text))
        return expected

    result = TelegramTransport(sender=sender).send("123456789", _message())

    assert calls == [("123456789", "<b>正文</b>")]
    assert result.ok is expected.ok
    assert result.status_code == expected.status_code
    assert result.error_code == expected.error_code
    assert result.retryable is expected.retryable
    assert result.recipient_invalid is expected.recipient_invalid
    assert result.retry_after == expected.retry_after
