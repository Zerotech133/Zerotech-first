"""Telegram 推送：晨晚报 + 交易成交，广播给所有已绑定 telegram_chat_id 的用户。

仿 alert_service.send_alert 范式：模块级 best-effort，读 TELEGRAM_BOT_TOKEN，httpx 发送，
try/except 永不抛（不影响交易/报告主流程）。未配 token 静默跳过。
token 优先取 settings.telegram_bot_token(.env.local)，回退 os.getenv(真环境变量)。

Phase C Task 6 起 ``send_telegram`` 是**纯 transport**：只向给定 chat_id 发送、
返回结构化 :class:`TelegramSendResult`（429 带 retry_after），不查收件人、不做路由
（收件人解析归 outbox/worker，Task 7-8）。日志只落脱敏 chat_id（尾 4 位）与稳定
error_code——绝不落完整 chat_id、token、响应正文或异常串（httpx 异常消息可能含
带 token 的请求 URL）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from zhice_os_v2.domain.notification.contracts import mask_recipient

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass(frozen=True)
class TelegramSendResult:
    """send_telegram 结构化结果（worker 判定重试/终态用，Task 7）。

    - ``ok``：HTTP 200 即成功；
    - ``status_code``：HTTP 状态码（网络错误无响应为 None）；
    - ``error_code``：稳定错误码（``not_configured``/``empty_chat``/``network``/
      ``http_{status}``），绝不携带响应正文/token/chat 数据；
    - ``retry_after``：Telegram 429 ``parameters.retry_after``（秒），无则 None。
    """

    ok: bool
    status_code: int | None = None
    error_code: str | None = None
    retryable: bool = False
    recipient_invalid: bool = False
    retry_after: int | None = None


def _bot_token() -> str:
    try:
        from zhice_os_v2.config.settings import get_settings
        return get_settings().telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    except Exception:  # noqa: BLE001
        return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _proxy() -> str | None:
    """代理（国内直连 api.telegram.org 被墙）。TELEGRAM_PROXY 优先，回退 HTTPS_PROXY。"""
    try:
        from zhice_os_v2.config.settings import get_settings
        p = getattr(get_settings(), "telegram_proxy", "")
        if p:
            return p
    except Exception:  # noqa: BLE001
        pass
    return (os.getenv("TELEGRAM_PROXY") or os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy") or None)


def _redact_chat_id(chat_id: str) -> str:
    """Compatibility helper delegating to the shared recipient privacy contract."""
    return mask_recipient(chat_id)


def send_telegram(chat_id: str, text: str) -> TelegramSendResult:
    """向单个 chat 发消息（纯 transport）。best-effort，永不抛。

    不查收件人、不做任何路由判定；失败返回 ok=False + 稳定 error_code。
    """
    token = _bot_token()
    if not token:
        return TelegramSendResult(ok=False, error_code="not_configured")
    if not chat_id:
        return TelegramSendResult(ok=False, error_code="empty_chat")
    try:
        import httpx

        url = _API.format(token=token)
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        proxy = _proxy()
        if proxy:
            with httpx.Client(proxy=proxy, timeout=8.0) as c:
                resp = c.post(url, json=payload)
        else:
            resp = httpx.post(url, timeout=8.0, json=payload)
    except Exception:  # noqa: BLE001
        # 异常串（httpx URL 等）可能含 token，只落稳定 error_code + 脱敏 chat
        logger.warning("telegram send error chat=%s error=network", _redact_chat_id(chat_id))
        return TelegramSendResult(ok=False, error_code="network", retryable=True)
    if resp.status_code == 200:
        return TelegramSendResult(ok=True, status_code=200)
    error_code = f"http_{resp.status_code}"
    retry_after: int | None = None
    if resp.status_code == 429:
        try:
            retry_after = int(resp.json().get("parameters", {}).get("retry_after"))
        except Exception:  # noqa: BLE001 - body 不可解析时按无 retry_after 处理
            retry_after = None
    logger.warning(
        "telegram send failed chat=%s status=%s error=%s",
        _redact_chat_id(chat_id),
        resp.status_code,
        error_code,
    )
    return TelegramSendResult(
        ok=False,
        status_code=resp.status_code,
        error_code=error_code,
        retryable=resp.status_code == 429 or resp.status_code >= 500,
        # 仅 403（bot 被用户屏蔽）是确定性永久失效；400 多为载荷错误（message too
        # long / can't parse entities）、404 非确定——误报会永久错杀正常绑定
        recipient_invalid=resp.status_code == 403,
        retry_after=retry_after,
    )


def broadcast_telegram(text: str) -> int:
    """广播给所有已绑定 telegram 的 active 用户，返回成功发送数。best-effort，永不抛。"""
    if not _bot_token():
        return 0
    try:
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend != "sql":
            return 0
        from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

        chat_ids = UserRepository().list_active_notification_recipients("telegram")
    except Exception:
        logger.debug("telegram broadcast: list chat_ids failed", exc_info=True)
        return 0
    sent = sum(1 for cid in chat_ids if send_telegram(cid, text).ok)
    logger.info("telegram broadcast recipients=%d sent=%d", len(chat_ids), sent)
    return sent


def notify_admins(text: str) -> int:
    """只发给绑定 telegram 的 active ADMIN（运维告警，不广播给普通用户）。

    best-effort，永不抛：token 空/非 sql 后端/无绑定管理员均静默返回 0。
    """
    if not _bot_token():
        return 0
    try:
        from zhice_os_v2.config.settings import get_settings

        if get_settings().persistence_backend != "sql":
            return 0
        from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

        chat_ids = UserRepository().list_active_notification_recipients(
            "telegram", role="ADMIN"
        )
    except Exception:
        logger.debug("notify_admins: list admin chat_ids failed", exc_info=True)
        return 0
    sent = sum(1 for cid in chat_ids if send_telegram(cid, text).ok)
    logger.info("telegram notify_admins recipients=%d sent=%d", len(chat_ids), sent)
    return sent


_SIDE = {"BUY": "🟢 买入", "SELL": "🔴 卖出"}


def _strategy_name(sv_id: str) -> str:
    """strategy_version_id → 策略名（去 [V1] 前缀）；查不到回退 sv_id。best-effort。"""
    if not sv_id:
        return ""
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        s = get_session()
        try:
            row = (
                s.query(orm.StrategyModel.strategy_name)
                .join(orm.StrategyVersionModel, orm.StrategyVersionModel.strategy_id == orm.StrategyModel.strategy_id)
                .filter(orm.StrategyVersionModel.strategy_version_id == sv_id)
                .first()
            )
            if row and row[0]:
                n = row[0].strip()
                return n[5:] if n.startswith("[V1] ") else n
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        pass
    return sv_id


def _fmt_num(x) -> str:
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return "-"


def notify_fill(*, side: str, ts_code: str, price, qty, amount, market: str,
                sv_id: str = "", reason: str | None = None,
                sell_reason: str | None = None, realized_pnl=None, fill_time=None) -> None:
    """交易成交回报 → 广播 Telegram。best-effort，永不抛（不影响交易主流程）。"""
    try:
        head = _SIDE.get(side, side)
        lines = [f"{head} {ts_code} [{market}]",
                 f"价 {_fmt_num(price)} · 量 {_fmt_num(qty)} · 额 {_fmt_num(amount)}"]
        if realized_pnl is not None:
            try:
                lines.append(f"盈亏 {float(realized_pnl):+.2f}")
            except (TypeError, ValueError):
                pass
        strat = _strategy_name(sv_id)
        if strat:
            lines.append(f"策略 {strat}")
        r = reason or sell_reason
        if r:
            lines.append(f"理由 {r}")
        if fill_time is not None:
            lines.append(str(fill_time)[:19].replace("T", " "))
        broadcast_telegram("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.debug("notify_fill failed", exc_info=True)
