"""Phase C Task 8 接线：成交与报告通知的三态路由（broadcast|shadow|directed）。

把「要不要广播 / 要不要定向」的判定从调用方（workflow_service / scheduler_jobs）
收拢到本模块，调用方只保留三行分发；模式每次现读 settings（``notify_mode``），
不改 settings 本身。

语义矩阵（安全铁律）：

===========  ===========================  ====================================
模式         原广播路径                   定向路径（本模块）
===========  ===========================  ====================================
broadcast    一字不变（notify_fill /      不进入（防御性 no-op）
             broadcast_telegram）
shadow       原广播照发（新旧并行观察，   outbox enqueue（shadow 语义：event 落库
             仅 owner 已解析的成交）      + 路由日志，不写 delivery）
directed     **绝不广播**                 outbox enqueue（event + delivery，worker
                                          认领发送）
===========  ===========================  ====================================

directed 收件人**只**经 OwnershipResolver→RuntimeOwner（成交）或
``NotificationPreferenceService.report_recipients``（市场报告）解析；解析不出
owner（legacy/断链）→ **绝不广播兜底**，只落 ``notification.owner_missing`` 系统
告警（directed；shadow 只记日志）。

一切 best-effort：try/except 永不抛，通知失败绝不影响交易/报告主流程。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from enum import StrEnum

from sqlalchemy.orm import Session

from zhice_os_v2.application.services.ownership_resolver import RuntimeOwner
from zhice_os_v2.domain.ontology.objects import utc_now

logger = logging.getLogger(__name__)

_OWNER_MISSING_ALERT = "notification.owner_missing"


class ReportKind(StrEnum):
    """市场报告种类（晨/晚）：幂等键与渠道偏好（morning/evening_report_enabled）的定位键。"""

    MORNING = "morning"
    EVENING = "evening"


def report_event_id(
    market: str, report_date: date, report_kind: ReportKind, user_id: str
) -> str:
    """报告幂等键（Task 11）：``mreport-{market}-{YYYYMMDD}-{kind}-{user_digest}``。

    - user_id 哈希成 16 hex：同日同用户晨/晚两键互异、同类重跑幂等（outbox event_id
      唯一 + ON CONFLICT DO NOTHING 沿用 fill 族机制）；不落裸 user_id 防键过长；
    - 长度上界：8 前缀 + 市场 + 1 + 8 日期 + 1 + 7 kind + 1 + 16 digest ≤ 64
      （``v2_notification_event.event_id`` String(64)）。
    """
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return f"mreport-{market}-{report_date:%Y%m%d}-{report_kind.value}-{digest}"


def notify_mode() -> str:
    """当前通知模式（broadcast|shadow|directed）；读失败回退 broadcast（安全默认）。"""
    try:
        from zhice_os_v2.config.settings import get_settings

        return str(get_settings().telegram_notification_mode)
    except Exception:  # noqa: BLE001
        return "broadcast"


def _fill_event_id(fill_id: str) -> str:
    """成交幂等键（计划口径 ``fill-{fill_id}``）：fill_id 本身已带 ``fill-`` 前缀
    （``_record_fill._fit("fill-")`` / ``fit_id("fill-", …)``），沿用不重复叠加；
    裸 id 才补前缀。"""
    return fill_id if fill_id.startswith("fill-") else f"fill-{fill_id}"


def resolve_fill_owner(
    *, position=None, approval=None, fill_id: str = ""
) -> RuntimeOwner | None:
    """解析成交归属（上下文优先，resolver 兜底；任何失败 → None）。

    - 上下文：Phase B 起 approval/position 域对象带 ``user_strategy_id``——持仓行
      是权威归属锚点（``save_position`` 在 ``_record_fill`` 之前已落库），approval
      次之；
    - 兜底：``OwnershipResolver.for_fill``（fill→execution→request→intent 链；
      intent 自本任务起写入上下文归属）；
    - 绝不用 strategy_version_id 猜 owner（同一版本可被多用户共享，猜必错）。
    """
    try:
        from zhice_os_v2.application.services.ownership_resolver import OwnershipResolver

        resolver = OwnershipResolver()
        for ctx in (position, approval):
            if not getattr(ctx, "user_strategy_id", None):
                continue
            if ctx is position:
                owner = resolver.for_position(str(ctx.position_id))
            else:
                owner = resolver.for_approval(str(ctx.request_id))
            if owner is not None:
                return owner
        if fill_id:
            return resolver.for_fill(fill_id)
        return None
    except Exception:  # noqa: BLE001
        return None


def route_fill(
    session: Session | None = None,
    *,
    fill_id: str,
    side: str,
    ts_code: str,
    price,
    qty,
    amount,
    market: str,
    sv_id: str = "",
    reason: str | None = None,
    sell_reason: str | None = None,
    realized_pnl=None,
    fill_time=None,
    exit_kind: str | None = None,
    position=None,
    approval=None,
) -> RuntimeOwner | None:
    """shadow/directed 成交接线：与成交同事务 enqueue outbox，返回解析出的 owner。

    - broadcast 模式防御性 no-op（调用方约定：broadcast 走原 notify_fill，不进本函数）；
    - owner 缺失（legacy/断链）：不写任何 event——directed 落 owner_missing 告警
      （绝不广播兜底），shadow 只记日志；
    - 平仓按 ``exit_kind`` 映射 STOP_LOSS/TAKE_PROFIT 事件（对齐 workflow
      ``_EXIT_KIND_TEXT`` 语义），其余离场与建仓（BUY）为 FILL。

    best-effort：永不抛；返回值供调用方判定 shadow 并行广播。
    """
    owner: RuntimeOwner | None = None
    try:
        mode = notify_mode()
        if mode == "broadcast":
            return None
        owner = resolve_fill_owner(position=position, approval=approval, fill_id=fill_id)
        if owner is None:
            _owner_missing(mode, fill_id=fill_id, market=market, ts_code=ts_code, side=side)
            return None

        from zhice_os_v2.application.services.notification_outbox_service import (
            NotificationOutboxService,
            NotificationType,
        )

        event_type = NotificationType.FILL
        if str(side).upper() == "SELL":
            if exit_kind == "STOP_LOSS":
                event_type = NotificationType.STOP_LOSS
            elif exit_kind == "TAKE_PROFIT":
                event_type = NotificationType.TAKE_PROFIT
        NotificationOutboxService().enqueue(
            session,
            event_id=_fill_event_id(fill_id),
            owner=owner,
            event_type=event_type,
            payload=_fill_payload(
                fill_id=fill_id, side=side, ts_code=ts_code, market=market,
                price=price, qty=qty, amount=amount, realized_pnl=realized_pnl,
                sv_id=sv_id, reason=reason, sell_reason=sell_reason, fill_time=fill_time,
            ),
            occurred_at=fill_time or utc_now(),
        )
    except Exception:  # noqa: BLE001
        logger.debug("route_fill failed: fill_id=%s", fill_id, exc_info=True)
    return owner


def route_market_report(
    *,
    market: str,
    label: str,
    narrative: str,
    report_kind: ReportKind | str,
    report_date: str | None = None,
) -> None:
    """shadow/directed 市场报告接线（Task 11 起按晨/晚 kind 路由）。

    - 收件人 = telegram/wxpusher 两渠道 ``report_recipients(market, channel, kind)``
      **并集**（binding active + 市场订阅 + 该 kind 开关，逐渠道合格即收）——
      wxpusher-only 订阅者不被 Telegram 口径过滤；并集后每用户只 enqueue 一个
      event，投递渠道由 outbox 按该用户 active bindings 逐渠道决定；
    - event_id=``report_event_id``（同日同 kind 重跑幂等；晨/晚互异）；
    - payload 必含 ``report_kind``（outbox 报告族偏好门按其选晨/晚开关；缺失时
      的过渡语义=晨晚双开才投，本函数恒写入，不依赖过渡分支）；
    - **升级兼容**：当日旧 key ``mreport-{market}-{iso}-{uid}``（Task 11 前格式）
      已存在（旧版当日已投过）→ 同用户新 key 不补发，防升级重发；次日起旧 key
      天然不可能再命中（日期不同），检查自然失效；
    - broadcast 模式防御性 no-op（调用方约定：broadcast 走原 broadcast_telegram）；
      无收件人跳过。best-effort：永不抛。
    """
    try:
        mode = notify_mode()
        if mode == "broadcast":
            return
        from datetime import UTC, datetime

        from zhice_os_v2.application.services.notification_outbox_service import (
            NotificationOutboxService,
            NotificationType,
        )
        from zhice_os_v2.application.services.notification_preference_service import (
            NotificationPreferenceService,
        )
        from zhice_os_v2.domain.notification.preference import (
            NOTIFICATION_CHANNEL_TELEGRAM,
            NOTIFICATION_CHANNEL_WXPUSHER,
        )

        # 未知 kind：fail-closed 不投递，但必须留 warning——否则调用方传错 kind
        # 时整场报告无痕蒸发（S-1）。白名单字段：market/kind，无敏感数据。
        try:
            kind = ReportKind(report_kind)
        except ValueError:
            logger.warning(
                "market_report_unknown_kind market=%s report_kind=%s skipped",
                market,
                report_kind,
            )
            return
        d = _resolve_report_date(report_date)
        iso = d.isoformat()

        # 双渠道合格用户并集（保序去重：telegram 口径在前，wxpusher 增量在后）。
        preference_service = NotificationPreferenceService()
        recipients: list[str] = []
        seen: set[str] = set()
        for channel in (NOTIFICATION_CHANNEL_TELEGRAM, NOTIFICATION_CHANNEL_WXPUSHER):
            for uid in preference_service.report_recipients(market, channel, kind.value):
                if uid not in seen:
                    seen.add(uid)
                    recipients.append(uid)

        outbox = NotificationOutboxService()
        now = datetime.now(UTC)
        enqueued = 0
        for uid in recipients:
            # 升级当天防重发：旧 key 事件存在 = 旧版当日已为该用户投过本市场报告
            if outbox.event_exists(f"mreport-{market}-{iso}-{uid}"):
                continue
            outbox.enqueue(
                event_id=report_event_id(market, d, kind, uid),
                # 报告族无实例归属：user_id 级路由（空串 = 无实例，outbox 侧
                # ``or None`` 归一；类型上 RuntimeOwner 字段为 str）。
                owner=RuntimeOwner(
                    user_id=uid, user_strategy_id="", account_id="", strategy_version_id=""
                ),
                event_type=NotificationType.MARKET_REPORT,
                payload={
                    "market": market,
                    "title": label,
                    "narrative": narrative,
                    "date": iso,
                    "report_kind": kind.value,
                },
                occurred_at=now,
            )
            enqueued += 1
        logger.info(
            "market_report_routed market=%s type=%s kind=%s date=%s mode=%s "
            "recipients=%d enqueued=%d",
            market, label, kind.value, iso, mode, len(recipients), enqueued,
        )
    except Exception:  # noqa: BLE001
        logger.debug("route_market_report failed: market=%s", market, exc_info=True)


def _resolve_report_date(report_date: str | None) -> date:
    """报告日（幂等键成分）：优先用报告自带日期（ISO），缺失/不可解析回退当天（UTC）。"""
    from datetime import UTC, datetime

    if report_date:
        try:
            return date.fromisoformat(str(report_date))
        except ValueError:
            logger.debug("report_date unparseable: %r, fallback to today", report_date)
    return datetime.now(UTC).date()


def route_subscription_signal(
    session: Session | None = None,
    *,
    fill_id: str,
    sv_id: str,
    side: str,
    ts_code: str,
    price,
    market: str,
    exit_kind: str | None = None,
    reason: str | None = None,
    fill_time=None,
) -> int:
    """内置策略成交 → 向其活跃订阅者扇出入场/出场 SIGNAL（P2）。

    仅内置策略（``v2_strategy.owner_user_id IS NULL``）触发；用户自建策略的信号由
    ``route_fill`` 定向给 owner，不进本函数。payload **只含信号要素**（方向/标的/
    价格/时间/理由/离场类型），**绝不含**持仓量、成本、账户、盈亏明细（合规红线：
    订阅者永远看不到内置策略的持仓与成交）。broadcast 模式 no-op。返回入队订阅者数。

    与成交同事务入队（传入调用方 session）；best-effort：永不抛。
    """
    try:
        mode = notify_mode()
        if mode == "broadcast" or not sv_id or session is None:
            return 0
        from zhice_os_v2.infrastructure.db import models as orm

        row = (
            session.query(
                orm.StrategyModel.owner_user_id, orm.StrategyModel.strategy_id
            )
            .join(
                orm.StrategyVersionModel,
                orm.StrategyVersionModel.strategy_id == orm.StrategyModel.strategy_id,
            )
            .filter(orm.StrategyVersionModel.strategy_version_id == sv_id)
            .first()
        )
        # 未知 sv 或非内置（owner 非 NULL）→ 不扇出（owner 信号走 route_fill 定向）。
        if row is None or row[0] is not None:
            return 0
        strategy_id = row[1]

        from zhice_os_v2.application.services.membership_service import (
            SubscriptionService,
        )

        subscribers = SubscriptionService().list_active_subscriber_ids(strategy_id)
        if not subscribers:
            return 0

        from zhice_os_v2.application.services.notification_outbox_service import (
            NotificationOutboxService,
            NotificationType,
        )

        signal = "entry" if str(side).upper() == "BUY" else "exit"
        payload = {
            "signal": signal,  # entry=入场 / exit=出场
            "strategy_id": strategy_id,
            "ts_code": ts_code,
            "market": market,
            "price": (float(price) if price is not None else None),
            "exit_kind": exit_kind,  # STOP_LOSS/TAKE_PROFIT（出场时）
            "reason": reason,
            "signal_time": (
                fill_time.isoformat() if fill_time else utc_now().isoformat()
            ),
        }
        outbox = NotificationOutboxService()
        now = fill_time or utc_now()
        n = 0
        for uid in subscribers:
            outbox.enqueue(
                session,
                event_id=f"signal-{fill_id}-{uid}",
                owner=RuntimeOwner(
                    user_id=uid,
                    user_strategy_id="",
                    account_id="",
                    strategy_version_id=sv_id,
                ),
                event_type=NotificationType.SIGNAL,
                payload=payload,
                occurred_at=now,
            )
            n += 1
        logger.info(
            "subscription_signal_routed sv=%s strategy=%s subs=%d mode=%s",
            sv_id, strategy_id, n, mode,
        )
        return n
    except Exception:  # noqa: BLE001
        logger.debug("route_subscription_signal failed sv=%s", sv_id, exc_info=True)
        return 0


# ── 内部 ────────────────────────────────────────────────────────────────────
def _owner_missing(
    mode: str, *, fill_id: str, market: str, ts_code: str, side: str
) -> None:
    """directed 成交解析不出 owner：落系统告警（仓内 alert_service 轻量范式），
    绝不广播兜底；shadow 只记日志。每笔 fill_id 均入日志便于追踪。"""
    if mode == "directed":
        try:
            from zhice_os_v2.application.services.alert_service import send_alert

            send_alert(
                "WARN",
                _OWNER_MISSING_ALERT,
                f"fill_id={fill_id} market={market} ts_code={ts_code} side={side}",
            )
        except Exception:  # noqa: BLE001
            logger.debug("owner_missing alert failed: fill_id=%s", fill_id, exc_info=True)
    logger.info(
        "notification_owner_missing fill_id=%s market=%s side=%s mode=%s",
        fill_id, market, side, mode,
    )


def _fill_payload(
    *,
    fill_id: str,
    side: str,
    ts_code: str,
    market: str,
    price,
    qty,
    amount,
    realized_pnl,
    sv_id: str,
    reason: str | None,
    sell_reason: str | None,
    fill_time,
) -> dict:
    """定向成交 payload（对齐现有 notify_fill 字段与 Task 6 模板键）。

    payload 落 ``v2_notification_event.payload`` JSON 列：数值转 float、时间转
    ISO 串（Decimal/datetime 不可 JSON 序列化）；不含 chat_id/token 等敏感数据。
    """
    from zhice_os_v2.application.services.telegram_notifier import _strategy_name

    def _num(x):
        try:
            return None if x is None else float(x)
        except (TypeError, ValueError):
            return None

    return {
        "fill_id": fill_id,
        "side": side,
        "ts_code": ts_code,
        "market": market,
        "price": _num(price),
        "qty": _num(qty),
        "amount": _num(amount),
        "realized_pnl": _num(realized_pnl),
        "strategy": _strategy_name(sv_id or ""),
        "reason": reason,
        "sell_reason": sell_reason,
        "fill_time": str(fill_time) if fill_time is not None else None,
    }
