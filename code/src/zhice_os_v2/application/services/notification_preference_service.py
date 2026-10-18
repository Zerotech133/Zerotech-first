"""用户级通知偏好服务（Phase C Task 4）。

口径与安全属性：
- get_or_create：缺省创建 system_alerts=True、timezone=Asia/Shanghai、locale 取用户
  locale（无用户行回退 zh）、market_reports_enabled={}（**等价全市场关**，opt-in：
  收件人计算对缺键/无行一律视为关，fail-closed）；幂等（并发创建由
  unique(user_id,channel) 兜底 + 重读）；
- update：owner-scoped（user_id 只能来自 JWT），仅应用非 None 字段（None=不改，与
  /my PATCH 范式一致——显式传 null 与不传同等不改，故本任务不支持经 PATCH 清空
  quiet_hours）；market_reports_enabled 为**按键合并**（只改提供的市场，保留其余）；
- report_recipients(market)：市场报告收件人 = 有该市场启用实例的用户 ∩ 该市场
  market_reports_enabled=True（按启用实例创建序去重，稳定序）。

strict 校验（FastAPI 下 422）：market_reports_enabled 键必须 ∈ SUPPORTED_MARKETS、
值必须 bool；quiet_hours 必须恰为 {"start","end"} 且 HH:MM（24h 补零）、start!=end
（跨午夜 start>end 合法）；timezone 须经 zoneinfo 校验；locale ∈ {zh,en}；未知字段
拒绝（extra="forbid"）。
"""
from __future__ import annotations

import re
import secrets
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator

from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.domain.notification.preference import (
    NOTIFICATION_CHANNEL_TELEGRAM,
    SUPPORTED_NOTIFICATION_CHANNELS,
    UserNotificationPreference,
)
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)
from zhice_os_v2.infrastructure.repositories.user_strategy_repository import (
    UserStrategyRepository,
)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class QuietHours(BaseModel):
    """静音时段：{"start": "HH:MM", "end": "HH:MM"}，跨午夜合法，start==end 非法。"""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @model_validator(mode="after")
    def _validate_range(self) -> QuietHours:
        for name in ("start", "end"):
            if not _HHMM_RE.match(getattr(self, name)):
                raise ValueError(f"quiet_hours.{name} must be HH:MM (00:00-23:59)")
        if self.start == self.end:
            raise ValueError("quiet_hours.start must differ from quiet_hours.end")
        return self


class NotificationPreferenceBody(BaseModel):
    """PATCH /my/notification-preferences 请求体（strict：未知字段拒绝；None=不改）。"""

    model_config = ConfigDict(extra="forbid")

    # StrictBool：拒字符串强转（"yes"/"off" 等一律 422，开关只认真布尔）。
    market_reports_enabled: dict[str, StrictBool] | None = None
    signal_enabled: StrictBool | None = None
    fill_enabled: StrictBool | None = None
    morning_report_enabled: StrictBool | None = None
    evening_report_enabled: StrictBool | None = None
    system_alerts_enabled: StrictBool | None = None
    quiet_hours: QuietHours | None = None
    timezone: str | None = None
    locale: Literal["zh", "en"] | None = None

    @field_validator("market_reports_enabled")
    @classmethod
    def _validate_markets(
        cls, value: dict[str, bool] | None
    ) -> dict[str, bool] | None:
        if value is None:
            return None
        unknown = set(value) - SUPPORTED_MARKETS
        if unknown:
            raise ValueError(f"unknown market(s): {sorted(unknown)}")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"invalid timezone: {value!r}") from exc
        return value


class NotificationPreferenceService:
    def __init__(
        self,
        repository: NotificationPreferenceRepository | None = None,
        strategy_repository: UserStrategyRepository | None = None,
    ) -> None:
        self._repo = repository or NotificationPreferenceRepository()
        # 只读用（list_enabled_for_market）；不持有写路径，行为纯 additive。
        self._strategy_repo = strategy_repository or UserStrategyRepository()

    @staticmethod
    def _validate_channel(channel: str) -> None:
        if channel not in SUPPORTED_NOTIFICATION_CHANNELS:
            raise ValueError("notification.channel_invalid")

    # ---------- 读 / 缺省创建 ----------
    def get_or_create(
        self, user_id: str, channel: str = NOTIFICATION_CHANNEL_TELEGRAM
    ) -> UserNotificationPreference:
        self._validate_channel(channel)
        row = self._repo.get(user_id, channel)
        if row is not None:
            return row
        locale = self._repo.user_locale(user_id) or "zh"
        created = self._repo.create(
            f"npref-{secrets.token_hex(8)}", user_id, channel=channel, locale=locale
        )
        if created is not None:
            return created
        # 并发创建：唯一约束兜底，重读即本人行（幂等）。
        row = self._repo.get(user_id, channel)
        if row is None:  # pragma: no cover - 理论不可达（create 失败必有竞态行）
            raise RuntimeError("notification_preference.create_conflict")
        return row

    # ---------- 更新（owner-scoped）----------
    def update(
        self,
        user_id: str,
        patch: NotificationPreferenceBody,
        channel: str = NOTIFICATION_CHANNEL_TELEGRAM,
    ) -> UserNotificationPreference:
        self._validate_channel(channel)
        event_fields = (
            "signal_enabled",
            "fill_enabled",
            "morning_report_enabled",
            "evening_report_enabled",
        )
        if any(getattr(patch, name) is True for name in event_fields):
            if not self._repo.has_active_binding(user_id, channel):
                raise ValueError("notification.binding_required")
        current = self.get_or_create(user_id, channel)
        fields: dict = {}
        if patch.market_reports_enabled is not None:
            # 按键合并：只改提供的市场开关，保留其余（单市场 toggle 免读改写整表）。
            merged = dict(current.market_reports_enabled)
            merged.update(patch.market_reports_enabled)
            fields["market_reports_enabled"] = merged
        for name in event_fields:
            value = getattr(patch, name)
            if value is not None:
                fields[name] = value
        if patch.system_alerts_enabled is not None:
            fields["system_alerts_enabled"] = patch.system_alerts_enabled
        if patch.quiet_hours is not None:
            fields["quiet_hours"] = patch.quiet_hours.model_dump()
        if patch.timezone is not None:
            fields["timezone"] = patch.timezone
        if patch.locale is not None:
            fields["locale"] = patch.locale
        updated = self._repo.update(user_id, channel=channel, fields=fields)
        return updated if updated is not None else current

    # ---------- 便捷方法 ----------
    def set_market_report(
        self, user: User, market: str, enabled: bool
    ) -> UserNotificationPreference:
        """单市场报告开关（调度/测试用；strict 市场校验）。"""
        if market not in SUPPORTED_MARKETS:
            raise ValueError("notification_preference.unknown_market")
        return self.update(
            user.user_id,
            NotificationPreferenceBody(market_reports_enabled={market: enabled}),
        )

    # ---------- 收件人解析 ----------
    def channel_event_enabled(
        self, user_id: str, channel: str, event_type: str
    ) -> bool:
        """Gate channel event families through binding state and channel preference."""
        self._validate_channel(channel)
        field = {
            "signal": "signal_enabled",
            "fill": "fill_enabled",
            "stop_loss": "fill_enabled",
            "take_profit": "fill_enabled",
        }.get(event_type)
        if field is None or not self._repo.has_active_binding(user_id, channel):
            return False
        pref = self.get_or_create(user_id, channel)
        return bool(getattr(pref, field))

    def report_enabled(
        self, user_id: str, channel: str, market: str, report_kind: str
    ) -> bool:
        """Require an active binding, market opt-in, and matching report-kind switch."""
        self._validate_channel(channel)
        field = {
            "morning": "morning_report_enabled",
            "evening": "evening_report_enabled",
        }.get(report_kind)
        if (
            field is None
            or market not in SUPPORTED_MARKETS
            or not self._repo.has_active_binding(user_id, channel)
        ):
            return False
        pref = self.get_or_create(user_id, channel)
        return bool(pref.market_reports_enabled.get(market)) and bool(
            getattr(pref, field)
        )

    def report_recipients(
        self,
        market: str,
        channel: str = NOTIFICATION_CHANNEL_TELEGRAM,
        report_kind: str | None = None,
    ) -> list[str]:
        """Resolve enabled-instance owners through optional channel report gates.

        ``report_kind=None`` preserves the legacy Telegram recipient behavior. When a
        kind is supplied, active binding and the corresponding kind switch are required.
        """
        self._validate_channel(channel)
        instances = self._strategy_repo.list_enabled_for_market(market)
        ordered: list[str] = []
        seen: set[str] = set()
        for inst in instances:
            if inst.user_id not in seen:
                seen.add(inst.user_id)
                ordered.append(inst.user_id)
        if not ordered:
            return []
        prefs = self._repo.get_many(ordered, channel)
        recipients: list[str] = []
        for uid in ordered:
            pref = prefs.get(uid)
            if pref is None or not bool(pref.market_reports_enabled.get(market)):
                continue
            if report_kind is None or self.report_enabled(
                uid, channel, market, report_kind
            ):
                recipients.append(uid)
        return recipients
