"""WxPusher 官方 webhook 事件校验与解析（app_subscribe / app_unsubscribe）。

verifier 只做真实性判定（appId + 常量时间 appKey），畸形输入恒 False 绝不抛；
parser 产出归一化绑定事件（subscribe/unsubscribe + uid + challenge），userName
等其余字段一律丢弃，绝不进入返回对象、绝不写日志（泄密红线）。
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_MAX_UID_LENGTH = 128
_MAX_EXTRA_CODE_LENGTH = 128


class WxPusherWebhookError(ValueError):
    """webhook 事件不可解析；消息恒为稳定错误码，绝不回显原始 payload。"""


@dataclass(frozen=True)
class WxPusherWebhookEvent:
    """归一化绑定事件：action ∈ {subscribe, unsubscribe}，challenge 仅订阅时存在。"""

    action: str
    recipient_id: str
    challenge_token: str | None


class WxPusherWebhookVerifier:
    """官方回调真实性校验：appId 精确相等 + appKey 常量时间比较。"""

    def __init__(self, *, app_id: int, app_key: str) -> None:
        self._app_id = app_id
        self._app_key = app_key

    def verify(self, event: dict) -> bool:
        try:
            if not isinstance(event, dict):
                return False
            data = event.get("data")
            if not isinstance(data, dict):
                return False
            app_id = data.get("appId")
            # bool 是 int 子类：appId=True 不得碰巧等于 app_id=1
            if isinstance(app_id, bool) or not isinstance(app_id, int):
                return False
            if app_id != self._app_id:
                return False
            app_key = data.get("appKey")
            if not isinstance(app_key, str):
                return False
            return hmac.compare_digest(app_key, self._app_key)
        except Exception:  # noqa: BLE001 - verifier 边界绝不抛（如非 ASCII str 触发 TypeError）
            return False


class _WebhookData(BaseModel):
    """官方事件 data 段：strict 类型 + 长度上限；未知字段（userName 等）直接忽略。"""

    model_config = ConfigDict(extra="ignore")

    appId: int = Field(strict=True)
    appKey: str = Field(strict=True)
    uid: str = Field(strict=True, max_length=_MAX_UID_LENGTH)
    extraCode: str | None = Field(default=None, strict=True, max_length=_MAX_EXTRA_CODE_LENGTH)


class _WebhookEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(strict=True)
    data: _WebhookData


def parse_webhook_event(event: dict) -> WxPusherWebhookEvent:
    """解析官方回调为归一化绑定事件；畸形/未知 action 抛稳定错误码异常。

    pydantic ValidationError 会回显输入值，故就地吞掉并 from None 断开异常链，
    只抛 WxPusherWebhookError("wxpusher_webhook_invalid_payload")。
    """
    try:
        parsed = _WebhookEvent.model_validate(event)
    except ValidationError:
        raise WxPusherWebhookError("wxpusher_webhook_invalid_payload") from None

    if parsed.action == "app_subscribe":
        return WxPusherWebhookEvent(
            action="subscribe",
            recipient_id=parsed.data.uid,
            challenge_token=parsed.data.extraCode,
        )
    if parsed.action == "app_unsubscribe":
        # 退订无 challenge：即使官方透传 extraCode 也强制丢弃
        return WxPusherWebhookEvent(
            action="unsubscribe",
            recipient_id=parsed.data.uid,
            challenge_token=None,
        )
    raise WxPusherWebhookError("wxpusher_webhook_unknown_action")
