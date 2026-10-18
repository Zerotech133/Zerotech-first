"""定向通知模板渲染（Phase C Task 6）。

纯函数服务：``render_message(...) -> RenderedMessage``（同一模板出 text/html 双
通道 + summary 截 100 + url 透传；``render(...) -> str`` 是其 HTML 视图），无 IO、
无 DB、无日志，输入确定 → 输出确定（易测，Task 7 worker 渲染正文用）。

安全铁律（send_telegram 走 parse_mode=HTML）：
- **HTML 通道所有动态值经 ``html.escape``**（text 通道同模板但不转义）——策略名/
  代码/理由/narrative 等用户可见文本含
  ``<>&`` 时必须转义（``A&B <突破>`` → ``A&amp;B &lt;突破&gt;``），否则破 HTML
  格式甚至注入标签；模板自身的少量标签（报告头 ``<b>``）是写死的静态串；
- payload 由调用方（outbox enqueue）保证不含 chat_id/token 等敏感数据。

健壮性：
- payload 缺键 → 该行整体省略（不 KeyError、不出 ``-`` 占位行）；头行恒在，
  渲染结果恒非空；
- 未知 event_type → ValueError（NotificationType 枚举校验）；未知 locale 容错
  回退 zh（通知链路不因 locale 脏值炸发送）；
- 数字格式化参照 telegram_notifier._fmt_num（``:g`` 去尾零，坏值 ``-``）；
  盈亏 ``+.2f`` 带符号——与现有 notify_fill 观感一致。

风格对齐现有广播文案：emoji 头 + 行式（🟢 买入 XXX [市场] / 价 量 额 / 盈亏 /
策略 / 理由 / 时间）；报告对齐 ``📊 <b>{market} {label}</b>\\n\\n{narrative}``。
"""
from __future__ import annotations

import html
from typing import Any

from zhice_os_v2.application.services.notification_outbox_service import NotificationType
from zhice_os_v2.domain.notification.contracts import RenderedMessage

# 报告族（无 symbol/side，头 + narrative 结构）
_REPORT_TYPES = frozenset({NotificationType.MARKET_REPORT, NotificationType.USER_REPORT})


# ── 文案包（zh/en）──────────────────────────────────────────────────────────
#
# ``side_fill``：fill 头行用完成时（Bought/Sold），对齐英文通知习惯；
# 其余交易族头行方向词用 ``side``（Buy/Sell）。
_TEXTS: dict[str, dict[str, Any]] = {
    "zh": {
        "event": {
            "signal": "信号",
            "plan": "计划",
            "approval": "审批",
            "risk": "风控",
            "order": "委托",
            "fill": "成交",
            "stop_loss": "止损",
            "take_profit": "止盈",
            "runtime_error": "运行时错误",
        },
        "side": {"BUY": "买入", "SELL": "卖出"},
        "price": "价",
        "qty": "量",
        "amount": "额",
        "pnl": "盈亏",
        "strategy": "策略",
        "reason": "理由",
        "status": "状态",
        "kind": "类型",
        "detail": "详情",
        "source": "来源",
        "error": "错误",
    },
    "en": {
        "event": {
            "signal": "Signal",
            "plan": "Plan",
            "approval": "Approval",
            "risk": "Risk",
            "order": "Order",
            "fill": "Fill",
            "stop_loss": "Stop Loss",
            "take_profit": "Take Profit",
            "runtime_error": "Runtime Error",
        },
        "side": {"BUY": "Buy", "SELL": "Sell"},
        "side_fill": {"BUY": "Bought", "SELL": "Sold"},
        "price": "Price",
        "qty": "Qty",
        "amount": "Amount",
        "pnl": "PnL",
        "strategy": "Strategy",
        "reason": "Reason",
        "status": "Status",
        "kind": "Type",
        "detail": "Detail",
        "source": "Source",
        "error": "Error",
    },
}

# 头行 emoji（fill 随方向 🟢/🔴，缺方向回退 🔔；报告族另走 _render_report）
_EMOJI: dict[NotificationType, str] = {
    NotificationType.SIGNAL: "⚡",
    NotificationType.PLAN: "📋",
    NotificationType.APPROVAL: "✅",
    NotificationType.RISK: "⚠️",
    NotificationType.ORDER: "📤",
    NotificationType.FILL: "🟢",
    NotificationType.STOP_LOSS: "🛑",
    NotificationType.TAKE_PROFIT: "🎯",
    NotificationType.RUNTIME_ERROR: "🛑",
    NotificationType.MARKET_REPORT: "📊",
    NotificationType.USER_REPORT: "📈",
}
_SIDE_EMOJI = {"BUY": "🟢", "SELL": "🔴"}

# 每事件头行是否带方向词（交易族带；风控/错误/报告不带）
_SIDE_IN_HEAD = frozenset(
    {
        NotificationType.SIGNAL,
        NotificationType.PLAN,
        NotificationType.APPROVAL,
        NotificationType.ORDER,
        NotificationType.FILL,
        NotificationType.STOP_LOSS,
        NotificationType.TAKE_PROFIT,
    }
)

# 价/量/额合并行事件（notify_fill 风格）；其余事件价单独成行
_PQA_EVENTS = frozenset(
    {
        NotificationType.ORDER,
        NotificationType.FILL,
        NotificationType.STOP_LOSS,
        NotificationType.TAKE_PROFIT,
    }
)


def _get(payload: dict, *keys: str) -> Any:
    """按别名序列取第一个非空值（缺键容错的取值底座）。"""
    for k in keys:
        v = payload.get(k)
        if v is not None and v != "":
            return v
    return None


def _esc(value: Any) -> str:
    """动态值 → HTML 安全文本（安全铁律：用户可见串必经此处）。"""
    return html.escape(str(value))


def _fmt_num(x: Any) -> str:
    """参照 telegram_notifier._fmt_num：``:g`` 去尾零，坏值 ``-``。"""
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return "-"


def _fmt_time(value: Any) -> str:
    """时间 → ``YYYY-MM-DD HH:MM:SS``（对齐 notify_fill 的截断规则）。"""
    return str(value)[:19].replace("T", " ")


class NotificationTemplateService:
    """定向通知模板：zh/en × 11 类事件 → HTML 安全 Telegram 文案。"""

    def render(
        self,
        event_type: NotificationType | str,
        *,
        locale: str,
        payload: dict,
    ) -> str:
        """渲染通知正文（纯函数，HTML 通道）。

        - ``event_type``：NotificationType 或其 value 字符串；未知值 ValueError；
        - ``locale``：``zh``/``en``（用户 locale）；未知值容错回退 zh；
        - ``payload``：业务展示字段（缺键省行，绝不 KeyError）。

        契约不变（worker 等既有调用方仍得 HTML 字符串），内部委托
        ``render_message(...).html``。
        """
        return self.render_message(event_type, locale=locale, payload=payload).html

    def render_message(
        self,
        event_type: NotificationType | str,
        *,
        locale: str,
        payload: dict,
    ) -> RenderedMessage:
        """渲染渠道无关消息体（纯函数，同一模板两种投影）。

        - ``text``：纯文本——动态值原样不转义（WxPusher 等非 HTML 通道可读）；
        - ``html``：Telegram parse_mode=HTML 正文——动态值全转义（安全铁律）；
        - ``summary``：``text`` 首行截 100 字符（WxPusher summary 上限）；
        - ``url``：payload ``url`` deep link 原样透传——模板正文不消费该键，
          同一 payload 增减 url 不影响 text/html。
        """
        p = dict(payload or {})
        message_text = self._compose(event_type, locale=locale, payload=p, escape=False)
        return RenderedMessage(
            summary=message_text.splitlines()[0][:100],
            text=message_text,
            html=self._compose(event_type, locale=locale, payload=p, escape=True),
            url=_get(p, "url"),
        )

    def _compose(
        self,
        event_type: NotificationType | str,
        *,
        locale: str,
        payload: dict,
        escape: bool,
    ) -> str:
        """单通道渲染底座：escape=True 走 HTML 铁律，False 出纯文本。"""
        et = NotificationType(event_type)
        texts = _TEXTS.get(locale, _TEXTS["zh"])
        p = dict(payload or {})
        if et in _REPORT_TYPES:
            return self._render_report(et, p, escape)
        return "\n".join(self._event_lines(et, texts, p, escape))

    # ── 交易/风控/错误族：emoji 头 + 行式明细 ─────────────────────────────
    def _event_lines(
        self, et: NotificationType, texts: dict, p: dict, escape: bool
    ) -> list[str]:
        lines: list[str] = [self._head(et, texts, p, escape)]
        lines.extend(self._detail_lines(et, texts, p, escape))
        return lines

    def _head(self, et: NotificationType, texts: dict, p: dict, escape: bool) -> str:
        esc = _esc if escape else str
        side = _get(p, "side")
        emoji = _EMOJI[et]
        parts: list[str]
        if et is NotificationType.FILL and side is not None:
            # fill 头行即方向词本身（notify_fill 风格：🟢 买入 XXX [CN]）
            emoji = _SIDE_EMOJI.get(str(side), "🔔")
            label = texts.get("side_fill", texts["side"]).get(str(side), str(side))
            parts = [f"{emoji} {esc(label)}"]
        else:
            parts = [f"{emoji} {esc(texts['event'][et.value])}"]
            if et in _SIDE_IN_HEAD and side is not None:
                parts.append(esc(texts["side"].get(str(side), str(side))))
        symbol = _get(p, "symbol", "ts_code")
        if symbol is not None:
            parts.append(esc(symbol))
        market = _get(p, "market")
        if market is not None:
            parts.append(f"[{esc(market)}]")
        return " ".join(parts)

    def _detail_lines(
        self, et: NotificationType, texts: dict, p: dict, escape: bool
    ) -> list[str]:
        esc = _esc if escape else str
        lines: list[str] = []

        def kv(label: str, value: Any, *, num: bool = False, pnl: bool = False) -> None:
            if value is None:
                return
            if pnl:
                try:
                    lines.append(f"{label} {float(value):+.2f}")
                except (TypeError, ValueError):
                    pass
            elif num:
                lines.append(f"{label} {_fmt_num(value)}")
            else:
                lines.append(f"{label} {esc(value)}")

        if et in _PQA_EVENTS:
            # 价/量/额 合并行（notify_fill 风格；三键全缺 → 整行省略）
            if any(_get(p, k) is not None for k in ("price", "qty", "amount")):
                segs = [
                    f"{texts[key]} {_fmt_num(_get(p, key))}"
                    for key in ("price", "qty", "amount")
                ]
                lines.append(" · ".join(segs))
        else:
            kv(texts["price"], _get(p, "price"), num=True)

        kv(texts["pnl"], _get(p, "realized_pnl"), pnl=True)
        kv(texts["status"], _get(p, "status"))
        kv(texts["kind"], _get(p, "kind", "risk_type"))
        kv(texts["detail"], _get(p, "detail"))
        kv(texts["source"], _get(p, "source"))
        kv(texts["error"], _get(p, "error", "message"))
        kv(texts["strategy"], _get(p, "strategy", "strategy_name"))
        kv(texts["reason"], _get(p, "reason", "sell_reason"))

        t = _get(p, "time", "fill_time", "occurred_at")
        if t is not None:
            lines.append(_fmt_time(t))
        return lines

    # ── 报告族：📊 <b>头</b> + 空行 + narrative（对齐现有广播格式）─────────
    def _render_report(self, et: NotificationType, p: dict, escape: bool) -> str:
        esc = _esc if escape else str
        emoji = _EMOJI[et]
        market = _get(p, "market") if et is NotificationType.MARKET_REPORT else None
        title = _get(p, "title")
        if market is not None or title is not None:
            head_parts = [x for x in (market, title) if x is not None]
            body = " ".join(esc(x) for x in head_parts)
        else:
            body = "Report"
        # <b> 是写死的静态标签，仅 HTML 通道输出；纯文本通道只留 emoji + 头文本
        head = f"{emoji} <b>{body}</b>" if escape else f"{emoji} {body}"
        parts = [head]
        narrative = _get(p, "narrative", "report")
        if narrative is not None:
            parts.append(esc(narrative))
        t = _get(p, "time", "occurred_at")
        if t is not None:
            parts.append(_fmt_time(t))
        return "\n\n".join(parts)
