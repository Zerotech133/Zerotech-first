"""WxPusher 参数二维码绑定客户端（官方 create/qrcode 与 scan-qrcode-uid 协议）。

与 wxpusher_transport 的差异：transport 发通知失败返回 SendResult（best-effort，
不抛）；绑定客户端在协议失败时抛 WxPusherBindingError（由上层 service 决定回滚）。
异常消息恒为稳定错误码，绝不携带 appToken/响应原文/msg（泄密红线）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

_QR_CREATE_URL = "https://wxpusher.zjiecode.com/api/fun/create/qrcode"
_QR_SCAN_URL = "https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid"
_QR_VALID_TIME = 600
_SAFE_CODE = re.compile(r"^[0-9]+$")
_MAX_SAFE_CODE_LENGTH = 8


class WxPusherBindingError(Exception):
    """绑定协议失败；消息恒为稳定错误码（如 wxpusher_qr_1001 / wxpusher_scan_network）。"""


@dataclass(frozen=True)
class WxPusherQrCode:
    """参数二维码创建结果：provider_code 用于扫码查询，image_url/short_url 用于展示。"""

    provider_code: str
    image_url: str
    short_url: str


class BindingHttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, object], timeout: float) -> Any: ...

    def get(self, url: str, *, params: Mapping[str, str], timeout: float) -> Any: ...


class WxPusherBindingClient:
    """WxPusher 官方参数二维码创建与扫码 UID 查询（绑定流程专用）。"""

    def __init__(
        self, *, app_token: str, timeout: float, client: BindingHttpClient | None = None
    ) -> None:
        self._app_token = app_token
        self._timeout = timeout
        # 显式 client 恒被采用：Protocol 对象可定义 __bool__（falsy 但有效），`or` 会误换
        self._client = httpx.Client() if client is None else client

    def create_qrcode(
        self, challenge: str, *, valid_time: int = _QR_VALID_TIME
    ) -> WxPusherQrCode:
        """创建参数二维码；challenge 经 extra 原样透传，validTime 由调用方
        （服务层传 challenge TTL）指定，缺省官方推荐 600 秒。

        官方请求字段名是 ``extra``（2026-09-18 真实环境实测：传 ``extraCode``
        返回 code=1001 "extra不能为空"）；回调事件**回传**时才叫 ``extraCode``。"""
        payload: dict[str, object] = {
            "appToken": self._app_token,
            "extra": challenge,
            "validTime": valid_time,
        }
        body = self._request(
            "wxpusher_qr",
            lambda: self._client.post(_QR_CREATE_URL, json=payload, timeout=self._timeout),
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise WxPusherBindingError("wxpusher_qr_invalid_response")
        try:
            return WxPusherQrCode(
                provider_code=str(data["code"]),
                image_url=str(data["url"]),
                short_url=str(data["shortUrl"]),
            )
        except KeyError:
            raise WxPusherBindingError("wxpusher_qr_invalid_response") from None

    def scan_qrcode_uid(self, provider_code: str) -> str | None:
        """查询扫码 UID；未扫码（data 空/None）返回 None，其余按官方结构取 uid。"""
        params: dict[str, str] = {"code": provider_code}
        body = self._request(
            "wxpusher_scan",
            lambda: self._client.get(_QR_SCAN_URL, params=params, timeout=self._timeout),
        )
        data = body.get("data")
        if not data:
            return None
        if isinstance(data, dict):
            # 缺 uid 键归一为未扫码哨兵 None：str(None)="None" 与真 UID 类型层面不可区分
            uid = data.get("uid")
            return None if uid is None else str(uid)
        return str(data)

    def _request(self, prefix: str, call: Callable[[], Any]) -> dict[str, Any]:
        """统一请求边界：网络/HTTP/JSON/业务码失败 → 稳定错误码异常，绝不外泄响应内容。"""
        try:
            response = call()
        except Exception:
            # from None：底层异常（如 httpx 超时）自带含 token 的 URL，不得进异常链
            raise WxPusherBindingError(f"{prefix}_network") from None

        status = response.status_code
        if not 200 <= status < 300:
            raise WxPusherBindingError(f"{prefix}_http_{status}")

        try:
            body = response.json()
        except Exception:
            raise WxPusherBindingError(f"{prefix}_invalid_response") from None
        if not isinstance(body, dict):
            raise WxPusherBindingError(f"{prefix}_invalid_response")

        code = body.get("code")
        # type is int：1000.0 == 1000 松散相等为 True，float 业务码不得判成功
        if body.get("success") is not True or type(code) is not int or code != 1000:
            # 只透传纯数字业务码；缺失/非数字一律 invalid，绝不拼接响应原文
            raise WxPusherBindingError(f"{prefix}_{_safe_code(code)}")
        return body


def _safe_code(value: object) -> str:
    if isinstance(value, bool):
        return "invalid"
    text = str(value)
    # 长度帽：错误码将落 String(64) 列（如 last_error_code），超长纯数字归 invalid 防撑爆
    if not _SAFE_CODE.fullmatch(text) or len(text) > _MAX_SAFE_CODE_LENGTH:
        return "invalid"
    return text
