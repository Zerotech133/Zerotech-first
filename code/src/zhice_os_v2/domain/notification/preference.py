"""用户级通知偏好领域对象（v2_user_notification_preference）。

每用户每渠道一行（Phase C 当前唯一渠道 telegram）。口径约定：
- ``market_reports_enabled`` 缺省 **{} 等价全市场关**（opt-in）：收件人计算对缺键
  一律视为关（fail-closed），与 ORM server_default '{}' 一致；
- ``quiet_hours`` 形如 {"start": "22:00", "end": "07:00"}，允许跨午夜（start>end），
  start==end 非法（strict 校验在 NotificationPreferenceBody，422）；
- ``timezone`` 用 IANA 名（ZoneInfo 校验）；``locale`` ∈ {zh, en}。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from zhice_os_v2.domain.ontology.objects import utc_now

#: 当前支持的通知渠道。
NOTIFICATION_CHANNEL_TELEGRAM = "telegram"
NOTIFICATION_CHANNEL_WXPUSHER = "wxpusher"
SUPPORTED_NOTIFICATION_CHANNELS = frozenset(
    {NOTIFICATION_CHANNEL_TELEGRAM, NOTIFICATION_CHANNEL_WXPUSHER}
)

#: 偏好缺省时区（与 ORM server_default 一致）。
DEFAULT_NOTIFICATION_TIMEZONE = "Asia/Shanghai"


@dataclass
class UserNotificationPreference:
    preference_id: str
    user_id: str
    channel: str = NOTIFICATION_CHANNEL_TELEGRAM
    market_reports_enabled: dict = field(default_factory=dict)
    signal_enabled: bool = True
    fill_enabled: bool = True
    morning_report_enabled: bool = True
    evening_report_enabled: bool = True
    # Legacy system-wide alerts flag; this is not the WeCom/WxPusher preference.
    system_alerts_enabled: bool = True
    quiet_hours: dict | None = None
    timezone: str = DEFAULT_NOTIFICATION_TIMEZONE
    locale: str = "zh"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
