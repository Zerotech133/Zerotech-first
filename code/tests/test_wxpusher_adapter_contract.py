"""WxPusher 参数二维码绑定客户端与 webhook 协议适配器契约测试。

合成 fixture（SUBSCRIBE_EVENT 等）来自实施计划，绝不读生产库/真实凭据。
"""

from __future__ import annotations

import copy
import dataclasses
import hmac
import inspect
import logging
import traceback
from types import SimpleNamespace

import httpx
import pytest

from zhice_os_v2.infrastructure.notifications import wxpusher_binding_client, wxpusher_webhook
from zhice_os_v2.infrastructure.notifications.wxpusher_binding_client import (
    WxPusherBindingClient,
    WxPusherBindingError,
    WxPusherQrCode,
)
from zhice_os_v2.infrastructure.notifications.wxpusher_webhook import (
    WxPusherWebhookError,
    WxPusherWebhookEvent,
    WxPusherWebhookVerifier,
    parse_webhook_event,
)

SUBSCRIBE_EVENT: dict = {
    "action": "app_subscribe",
    "data": {
        "appId": 123,
        "appKey": "AK_synthetic",
        "appName": "Test App",
        "source": "scan",
        "userName": "Synthetic User",
        "uid": "UID_synthetic",
        "extraCode": "synthetic-challenge",
    },
}

UNSUBSCRIBE_EVENT: dict = {
    "action": "app_unsubscribe",
    "data": dict(SUBSCRIBE_EVENT["data"]),
}

_QR_CREATE_URL = "https://wxpusher.zjiecode.com/api/fun/create/qrcode"
_QR_SCAN_URL = "https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid"


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _MockHttp:
    """记录式 http 替身：last_json / last_params 供契约断言。"""

    def __init__(self, response: _Response | Exception | None = None) -> None:
        self._response = response
        self.last_json: dict | None = None
        self.last_params: dict | None = None
        self.last_url: str | None = None
        self.last_timeout: float | None = None

    def post(self, url: str, *, json: dict, timeout: float) -> _Response:
        self.last_url = url
        self.last_json = json
        self.last_timeout = timeout
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, _Response)
        return self._response

    def get(self, url: str, *, params: dict, timeout: float) -> _Response:
        self.last_url = url
        self.last_params = params
        self.last_timeout = timeout
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, _Response)
        return self._response


def _binding_client(
    response: _Response | Exception | None = None, *, app_token: str = "AT_synthetic"
) -> tuple[WxPusherBindingClient, _MockHttp]:
    mock = _MockHttp(response)
    return WxPusherBindingClient(app_token=app_token, timeout=3.0, client=mock), mock


def _verifier() -> WxPusherWebhookVerifier:
    return WxPusherWebhookVerifier(app_id=123, app_key="AK_synthetic")


def _event(action: str, **overrides: object) -> dict:
    data = dict(SUBSCRIBE_EVENT["data"])
    data.update(overrides)
    return {"action": action, "data": data}


# --- 1) 参数二维码创建：官方协议字段 ---


def test_create_qr_uses_official_fields() -> None:
    client, mock_http = _binding_client(
        _Response(
            200,
            {
                "success": True,
                "code": 1000,
                "data": {"code": "QRC_1", "shortUrl": "https://s", "url": "https://img"},
            },
        )
    )

    qr = client.create_qrcode("synthetic-challenge")

    assert isinstance(qr, WxPusherQrCode)
    assert qr.provider_code == "QRC_1"
    assert qr.image_url == "https://img"
    assert qr.short_url == "https://s"
    assert mock_http.last_json == {
        "appToken": "AT_synthetic",
        # 官方创建接口字段名是 extra（真实环境实测 extraCode 报 1001）
        "extra": "synthetic-challenge",
        "validTime": 600,
    }
    assert mock_http.last_url == _QR_CREATE_URL
    assert mock_http.last_timeout == 3.0
    assert dataclasses.is_dataclass(qr)
    with pytest.raises(dataclasses.FrozenInstanceError):
        qr.provider_code = "mutated"


# --- 2) 扫码 UID 查询：params 只带 provider code ---


def test_scan_query_uses_provider_code_and_returns_uid() -> None:
    client, mock_http = _binding_client(
        _Response(200, {"success": True, "code": 1000, "data": {"uid": "UID_synthetic"}})
    )

    uid = client.scan_qrcode_uid("QRC_1")

    assert uid == "UID_synthetic"
    assert mock_http.last_params == {"code": "QRC_1"}
    assert mock_http.last_url == _QR_SCAN_URL
    assert mock_http.last_timeout == 3.0


# --- 3) 订阅事件解析：只取 uid/extraCode ---


def test_subscribe_parser_uses_uid_and_extra_code_only() -> None:
    parsed = parse_webhook_event(SUBSCRIBE_EVENT)

    assert isinstance(parsed, WxPusherWebhookEvent)
    assert parsed.action == "subscribe"
    assert parsed.recipient_id == "UID_synthetic"
    assert parsed.challenge_token == "synthetic-challenge"
    # userName 绝不进入返回对象
    assert "Synthetic User" not in repr(parsed)
    assert not hasattr(parsed, "user_name")
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.recipient_id = "mutated"


# --- 4) verifier：appId + 常量时间 appKey ---


def test_webhook_verifier_checks_app_id_and_constant_time_app_key() -> None:
    verifier = _verifier()

    assert verifier.verify(SUBSCRIBE_EVENT) is True

    wrong_key = copy.deepcopy(SUBSCRIBE_EVENT)
    wrong_key["data"]["appKey"] = "AK_wrong"
    assert verifier.verify(wrong_key) is False


# --- 5) 退订事件：challenge_token 恒为 None ---


def test_unsubscribe_has_no_challenge_token() -> None:
    # data 里故意保留 extraCode，证明 unsubscribe 强制丢弃 challenge
    assert UNSUBSCRIBE_EVENT["data"]["extraCode"] == "synthetic-challenge"

    parsed = parse_webhook_event(UNSUBSCRIBE_EVENT)

    assert parsed.action == "unsubscribe"
    assert parsed.recipient_id == "UID_synthetic"
    assert parsed.challenge_token is None


# --- 补充：verifier 畸形输入与检查顺序 ---


@pytest.mark.parametrize(
    "event",
    [None, [], "x", 123, {}, {"data": None}, {"data": "x"}, {"data": []}, {"data": {}}],
)
def test_verifier_never_raises_and_rejects_malformed(event: object) -> None:
    assert _verifier().verify(event) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "app_id",
    [999, "123", True, 123.0, None],
)
def test_verifier_rejects_wrong_or_mistyped_app_id(app_id: object) -> None:
    event = _event("app_subscribe", appId=app_id)
    assert _verifier().verify(event) is False


@pytest.mark.parametrize("app_key", [None, 123, ["AK_synthetic"]])
def test_verifier_rejects_missing_or_mistyped_app_key(app_key: object) -> None:
    event = _event("app_subscribe", appKey=app_key)
    assert _verifier().verify(event) is False


def test_verifier_checks_app_id_before_touching_app_key(monkeypatch) -> None:
    # 契约顺序：先 appId（可短路），后 appKey 常量时间比较
    calls: list[tuple[object, object]] = []
    real = hmac.compare_digest

    def spy(a: object, b: object) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert _verifier().verify(_event("app_subscribe", appId=999)) is False
    assert calls == []

    assert _verifier().verify(SUBSCRIBE_EVENT) is True
    assert calls == [("AK_synthetic", "AK_synthetic")]


def test_verifier_uses_hmac_compare_digest_in_source() -> None:
    # 源码级守护：常量时间比较不得退化为 ==
    source = inspect.getsource(wxpusher_webhook)
    assert "compare_digest" in source


# --- 补充：parser 未知 action / 畸形与超长输入 ---


def test_parser_rejects_unknown_action_with_stable_error() -> None:
    assert issubclass(WxPusherWebhookError, ValueError)

    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(_event("app_follow"))

    assert str(excinfo.value) == "wxpusher_webhook_unknown_action"
    # 错误消息不回显 payload
    assert "app_follow" not in str(excinfo.value)


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        "x",
        {},
        {"data": {}},
        {"action": "app_subscribe"},
        {"action": "app_subscribe", "data": "x"},
        {"action": "app_subscribe", "data": []},
    ],
)
def test_parser_rejects_malformed_structure_without_echoing_payload(event: object) -> None:
    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(event)  # type: ignore[arg-type]

    assert str(excinfo.value) == "wxpusher_webhook_invalid_payload"


def test_parser_rejects_oversized_uid_without_echoing_payload() -> None:
    long_uid = "UID_" + "x" * 200
    event = _event("app_subscribe", uid=long_uid)

    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(event)

    assert str(excinfo.value) == "wxpusher_webhook_invalid_payload"
    assert long_uid not in str(excinfo.value)


def test_parser_rejects_oversized_extra_code() -> None:
    event = _event("app_subscribe", extraCode="c" * 200)

    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(event)

    assert str(excinfo.value) == "wxpusher_webhook_invalid_payload"


@pytest.mark.parametrize("uid", [12345, None, ["UID_synthetic"]])
def test_parser_rejects_mistyped_uid(uid: object) -> None:
    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(_event("app_subscribe", uid=uid))

    assert str(excinfo.value) == "wxpusher_webhook_invalid_payload"


def test_parser_rejects_mistyped_extra_code() -> None:
    with pytest.raises(WxPusherWebhookError) as excinfo:
        parse_webhook_event(_event("app_subscribe", extraCode=123))

    assert str(excinfo.value) == "wxpusher_webhook_invalid_payload"


def test_parser_missing_extra_code_yields_none_challenge() -> None:
    event = _event("app_subscribe")
    event["data"].pop("extraCode")

    parsed = parse_webhook_event(event)

    assert parsed.challenge_token is None


def test_parser_ignores_unknown_fields_instead_of_failing() -> None:
    event = _event("app_subscribe", someFutureField="whatever")

    parsed = parse_webhook_event(event)

    assert parsed.recipient_id == "UID_synthetic"


# --- 补充：二维码接口协议失败（success false / 非 1000 / 畸形） ---


def test_create_qr_business_failure_error_code_is_stable() -> None:
    client, _ = _binding_client(
        _Response(200, {"success": False, "code": 1001, "msg": "AT_synthetic rejected"})
    )

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) == "wxpusher_qr_1001"
    exposed = str(excinfo.value)
    assert "AT_synthetic" not in exposed
    assert "rejected" not in exposed


def test_create_qr_success_true_but_non_1000_code_fails() -> None:
    client, _ = _binding_client(_Response(200, {"success": True, "code": 2002, "data": {}}))

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) == "wxpusher_qr_2002"


def test_create_qr_oversized_numeric_code_capped_to_invalid() -> None:
    # I-1：超长纯数字 code 归 invalid——错误码将落 v2_notification_delivery.last_error_code
    # String(64)，"wxpusher_qr_" + 80 位数字会撑爆列
    client, _ = _binding_client(_Response(200, {"success": False, "code": "9" * 80, "msg": "boom"}))

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) == "wxpusher_qr_invalid"
    assert len(str(excinfo.value)) <= 64


def test_create_qr_float_code_is_not_treated_as_success() -> None:
    # S-2：1000.0 == 1000 松散相等为 True，float 业务码不得判成功
    client, _ = _binding_client(
        _Response(
            200,
            {"success": True, "code": 1000.0, "data": {"code": "QRC_1", "url": "u", "shortUrl": "s"}},
        )
    )

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) == "wxpusher_qr_invalid"


@pytest.mark.parametrize(
    "body",
    [
        {"success": False},  # 缺 code → invalid
        {"success": False, "code": "1001/AT_synthetic"},  # 非纯数字 code → 不拼响应原文
        {"success": True, "code": 1000},  # 缺 data
        {"success": True, "code": 1000, "data": "x"},
        {"success": True, "code": 1000, "data": {}},  # data 缺 code/url/shortUrl
        {"success": True, "code": 1000, "data": {"code": "QRC_1", "url": "https://img"}},  # 缺 shortUrl
    ],
)
def test_create_qr_malformed_responses_fail_with_stable_code(body: dict) -> None:
    client, _ = _binding_client(_Response(200, body))

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) in {"wxpusher_qr_invalid", "wxpusher_qr_invalid_response"}
    assert "AT_synthetic" not in str(excinfo.value)


def test_create_qr_http_error_and_invalid_json_do_not_leak() -> None:
    http_client, _ = _binding_client(_Response(500, {"secret": "must-not-be-used"}))
    with pytest.raises(WxPusherBindingError) as excinfo:
        http_client.create_qrcode("synthetic-challenge")
    assert str(excinfo.value) == "wxpusher_qr_http_500"
    assert "must-not-be-used" not in str(excinfo.value)

    json_client, _ = _binding_client(_Response(200, ValueError("raw token AT_synthetic")))
    with pytest.raises(WxPusherBindingError) as excinfo:
        json_client.create_qrcode("synthetic-challenge")
    assert str(excinfo.value) == "wxpusher_qr_invalid_response"


def test_create_qr_network_exception_raises_sanitized_error() -> None:
    # 异常对象自带含 token 的 URL：不得经消息或异常链外泄
    request = httpx.Request("POST", f"{_QR_CREATE_URL}?token=AT_synthetic")
    client, _ = _binding_client(httpx.ReadTimeout(str(request.url), request=request))

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.create_qrcode("synthetic-challenge")

    assert str(excinfo.value) == "wxpusher_qr_network"
    formatted = "".join(traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.value.__traceback__))
    assert "AT_synthetic" not in str(excinfo.value) + formatted
    assert _QR_CREATE_URL not in formatted


# --- 补充：扫码查询协议分支 ---


@pytest.mark.parametrize("data", [None, {}, "", 0])
def test_scan_query_empty_data_returns_none(data: object) -> None:
    client, _ = _binding_client(_Response(200, {"success": True, "code": 1000, "data": data}))

    assert client.scan_qrcode_uid("QRC_1") is None


def test_scan_query_dict_without_uid_returns_none_not_string_none() -> None:
    # data 为 dict 但缺 uid 键：str(None)="None" 与真 UID 类型层面不可区分，
    # 统一归一为未扫码哨兵 None，防 Task 8 的 `if not uid` 判空被击穿
    client, _ = _binding_client(_Response(200, {"success": True, "code": 1000, "data": {"foo": "bar"}}))

    assert client.scan_qrcode_uid("QRC_1") is None


def test_scan_query_scalar_data_returns_string_form() -> None:
    # 契约：data 为 dict → str(data.get("uid"))；其他 → str(data)
    client, _ = _binding_client(_Response(200, {"success": True, "code": 1000, "data": "UID_direct"}))

    assert client.scan_qrcode_uid("QRC_1") == "UID_direct"


def test_scan_query_business_failure_error_code_is_stable() -> None:
    client, _ = _binding_client(
        _Response(200, {"success": False, "code": 1002, "msg": "AT_synthetic rejected"})
    )

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.scan_qrcode_uid("QRC_1")

    assert str(excinfo.value) == "wxpusher_scan_1002"
    assert "AT_synthetic" not in str(excinfo.value)


def test_scan_query_network_exception_raises_sanitized_error() -> None:
    request = httpx.Request("GET", f"{_QR_SCAN_URL}?token=AT_synthetic")
    client, _ = _binding_client(httpx.ConnectError(str(request.url), request=request))

    with pytest.raises(WxPusherBindingError) as excinfo:
        client.scan_qrcode_uid("QRC_1")

    assert str(excinfo.value) == "wxpusher_scan_network"
    assert "AT_synthetic" not in str(excinfo.value)


# --- 补充：构造器注入纪律（同 transport 模式） ---


def test_client_uses_explicit_client_even_when_falsy(monkeypatch) -> None:
    # Protocol 对象可定义 __bool__（falsy 但有效），`or` 会误换——必须 client is None 才新建
    class _Forbidden:
        def post(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("must not construct a replacement client")

        def get(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("must not construct a replacement client")

    monkeypatch.setattr(wxpusher_binding_client, "httpx", SimpleNamespace(Client=_Forbidden))

    class _FalsyMock(_MockHttp):
        def __bool__(self) -> bool:
            return False

    mock = _FalsyMock(
        _Response(
            200,
            {
                "success": True,
                "code": 1000,
                "data": {"code": "QRC_1", "url": "https://img", "shortUrl": "https://s"},
            },
        )
    )
    client = WxPusherBindingClient(app_token="AT_x", timeout=1.0, client=mock)

    assert client.create_qrcode("synthetic-challenge").provider_code == "QRC_1"
    assert mock.last_json is not None


def test_adapter_modules_do_not_import_global_settings() -> None:
    for module in (wxpusher_binding_client, wxpusher_webhook):
        source = inspect.getsource(module)
        assert "zhice_os_v2.config" not in source
        assert "get_settings" not in source


def test_webhook_adapter_writes_no_logs_on_verifier_path(caplog) -> None:
    # userName/挑战串/appKey 绝不写日志：verifier/parser 全程静默
    with caplog.at_level(logging.DEBUG):
        _verifier().verify(SUBSCRIBE_EVENT)
        parse_webhook_event(SUBSCRIBE_EVENT)
        try:
            parse_webhook_event(_event("app_follow"))
        except WxPusherWebhookError:
            pass

    assert "Synthetic User" not in caplog.text
    assert "synthetic-challenge" not in caplog.text
    assert "AK_synthetic" not in caplog.text
