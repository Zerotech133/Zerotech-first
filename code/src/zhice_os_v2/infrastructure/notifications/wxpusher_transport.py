"""WxPusher 单 UID 通知 transport。"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import httpx

from zhice_os_v2.domain.notification.contracts import RenderedMessage, SendResult, mask_recipient

logger = logging.getLogger(__name__)

_SEND_URL = "https://wxpusher.zjiecode.com/api/send/message"
_SAFE_CODE = re.compile(r"^[0-9]+$")
_MAX_SAFE_CODE_LENGTH = 8
_MAX_RETRY_AFTER = 3600


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, object], timeout: float) -> Any: ...


class WxPusherTransport:
    """通过 WxPusher 官方接口向精确单个 UID 发送 HTML 通知。"""

    def __init__(self, *, app_token: str, timeout: float, client: HttpClient | None = None) -> None:
        self._app_token = app_token
        self._timeout = timeout
        # 显式 client 恒被采用：Protocol 对象可定义 __bool__（falsy 但有效），`or` 会误换
        self._client = httpx.Client() if client is None else client

    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
        payload: dict[str, object] = {
            "appToken": self._app_token,
            "summary": message.summary,
            "content": message.html,
            "contentType": 2,
            "uids": [recipient_id],
        }
        if message.url:
            payload["url"] = message.url

        try:
            response = self._client.post(_SEND_URL, json=payload, timeout=self._timeout)
        except Exception:  # noqa: BLE001 - transport 边界不得外抛或记录异常文本
            logger.warning(
                "wxpusher send error recipient=%s error=network",
                mask_recipient(recipient_id),
            )
            return SendResult(ok=False, error_code="network", retryable=True)

        status = response.status_code
        if not 200 <= status < 300:
            retryable = status == 429 or status >= 500
            retry_after = _retry_after(response.headers.get("Retry-After")) if status == 429 else None
            error_code = f"http_{status}"
            logger.warning(
                "wxpusher send failed recipient=%s status=%s error=%s",
                mask_recipient(recipient_id),
                status,
                error_code,
            )
            return SendResult(
                ok=False,
                status_code=status,
                error_code=error_code,
                retryable=retryable,
                retry_after=retry_after,
            )

        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - response/异常文本可能含敏感数据
            return _failure(status, "invalid_response")
        if not isinstance(body, dict):
            return _failure(status, "invalid_response")

        top_code = body.get("code")
        # type is int：1000.0 == 1000 松散相等为 True，float 顶层码不得判成功
        if body.get("success") is not True or type(top_code) is not int or top_code != 1000:
            return _failure(status, f"wxpusher_api_{_safe_code(top_code)}")

        data = body.get("data")
        if not isinstance(data, list):
            return _failure(status, "invalid_response")
        for target in data:
            if not isinstance(target, dict):
                return _failure(status, "invalid_response")
            if target.get("uid") != recipient_id:
                continue
            target_code = target.get("code")
            # type is int：与顶层判定同严格度，1000.0 不得因松散相等判目标成功
            if type(target_code) is int and target_code == 1000:
                return SendResult(ok=True, status_code=status)
            return _failure(status, f"wxpusher_target_{_safe_code(target_code)}")
        return _failure(status, "wxpusher_target_missing")


def _failure(status_code: int, error_code: str) -> SendResult:
    return SendResult(
        ok=False,
        status_code=status_code,
        error_code=error_code,
        retryable=True,
        recipient_invalid=False,
    )


def _safe_code(value: object) -> str:
    if isinstance(value, bool):
        return "invalid"
    text = str(value)
    # 长度帽：错误码将落 String(64) 列（如 last_error_code），超长纯数字归 invalid 防撑爆
    if not _SAFE_CODE.fullmatch(text) or len(text) > _MAX_SAFE_CODE_LENGTH:
        return "invalid"
    return text


def _retry_after(value: object) -> int | None:
    # isdecimal 而非 isdigit：上标数字（"²"）过 isdigit 但 int() 抛 ValueError，
    # 会击穿 send() 永不抛边界；isdecimal 与 int() 可接受的数字集严格一致
    if not isinstance(value, str) or not value.isdecimal():
        return None
    seconds = int(value)
    return seconds if 1 <= seconds <= _MAX_RETRY_AFTER else None
