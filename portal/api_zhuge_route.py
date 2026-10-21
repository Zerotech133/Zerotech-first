# -*- coding: utf-8 -*-
"""WO-OS-09 诸葛亮建议引擎只读路由（GET /api/zhuge/brief，2026-10-21）。

把 WO-OS-04 规则版引擎（api_zhuge_brief.build_brief）挂上 8091 门户 API：
六源只读聚合 → build_brief → JSON。路由层零写操作（不 open 写、不 sqlite
写、不派单、不发请求），与引擎「零副作用」语义一致。

六源 raw 映射（复用既有 api 层内部函数，/api/os 等聚合读同款）：
  os         → api_misc.build_os_cockpit()
  home       → api_misc.build_home_cockpit()
  alerts     → api_common.build_alerts()          （含 source_health）
  notify     → api_notify.notify_queue_payload()
  tasks      → api_tickets.build_tickets_payload()
  capability → api_capability.build_capability_board()

降级纪律：单源获取抛异常/返回非 dict → 按 None 处理进 gaps（与引擎
usable() 语义一致），绝不把「接口可达」当「业务健康」。

时钟：默认服务器当前 tz-aware 时间（北京时间）；可选 ?clock=ISO8601
（带时区）回放口径——坏值显式 400 拒绝，不静默吞。

红线（沿 api_brief 判例）：
- 不 import 引擎链之外的新依赖；build_brief 是纯函数，本模块不碰其源码；
- 非 GET 方法由 portal_api 统一 405（本 mixin 只提供 GET 分发方法）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict

from api_zhuge_brief import BEIJING, SOURCES, build_brief


def _default_fetchers() -> Dict[str, Callable[[], Any]]:
    """六源默认获取函数（延迟绑定：import 放函数内防循环依赖）。"""
    import api_common
    import api_misc
    import api_capability
    import api_notify
    import api_tickets

    return {
        "os": api_misc.build_os_cockpit,
        "home": api_misc.build_home_cockpit,
        "alerts": api_common.build_alerts,
        "notify": api_notify.notify_queue_payload,
        "tasks": api_tickets.build_tickets_payload,
        "capability": api_capability.build_capability_board,
    }


def build_route_payload(fetchers=None, clock=None):
    """聚合六源 → 引擎 build_brief → 载荷 dict。

    fetchers: {source: callable}（测试注入点；默认六源真实函数）。
    clock:    tz-aware datetime（默认服务器当前北京时间）。
    单源异常/坏返回按 None 进 gaps，不让整卡 500。
    """
    if clock is None:
        clock = datetime.now(BEIJING)
    fs = fetchers if fetchers is not None else _default_fetchers()
    raw: Dict[str, Any] = {}
    for key in SOURCES:
        fn = fs.get(key)
        if not callable(fn):
            raw[key] = None
            continue
        try:
            raw[key] = fn()
        except Exception:
            raw[key] = None  # 获取失败按 None 处理进 gaps（引擎语义）
    return build_brief(raw, clock)


def _parse_clock(value):
    """解析 ?clock= 参数（ISO8601 带时区）；坏值返回 None。"""
    if not value:
        return None
    text = value.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # 无时区歧义，拒绝（引擎契约：必须 tz-aware）
    return parsed


class Handler:
    """portal_api.Handler 多继承 mixin：GET /api/zhuge/brief 分发。

    零参数出口 + 唯一可选参数 clock（ISO8601 带时区，回放口径）；
    带任何其他参数走 400（api_notify/api_perf 判例）。
    """

    def _zhuge_brief(self, qs):
        qs = qs or ""
        clock = None
        for part in qs.split("&"):
            if part.startswith("clock="):
                clock = _parse_clock(part[6:])
                if clock is None:
                    self._json({"ok": False,
                                "error": "clock 参数必须是带时区的 ISO8601 "
                                         "（如 2026-10-21T09:00:00+08:00）"},
                               400)
                    return
            elif part.strip():
                self._json({"ok": False, "error": "本端点仅支持 clock 参数"},
                           400)
                return
        try:
            self._json(build_route_payload(clock=clock))
        except (TypeError, ValueError) as e:
            # 引擎对坏输入显式抛错：路由按 400 呈现，不静默不 500
            self._json({"ok": False, "error": "clock 参数非法: %r" % e}, 400)
