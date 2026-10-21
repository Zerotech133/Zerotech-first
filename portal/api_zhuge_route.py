# -*- coding: utf-8 -*-
"""WO-OS-09 诸葛亮建议引擎只读路由（GET /api/zhuge/brief，2026-10-21）。
WO-OS-20 扩第七源 pushflow：当日通知投递成败（读 portal/work/push_flow.jsonl）。

把 WO-OS-04 规则版引擎（api_zhuge_brief.build_brief）挂上 8091 门户 API：
七源只读聚合 → build_brief → JSON。路由层零写操作（不 open 写、不 sqlite
写、不派单、不发请求），与引擎「零副作用」语义一致。

七源 raw 映射（复用既有 api 层内部函数，/api/os 等聚合读同款）：
  os         → api_misc.build_os_cockpit()
  home       → api_misc.build_home_cockpit()
  alerts     → api_common.build_alerts()          （含 source_health）
  notify     → api_notify.notify_queue_payload()
  tasks      → api_tickets.build_tickets_payload()
  capability → api_capability.build_capability_board()
  pushflow   → _pushflow_stats()                  （只读投递账本当日行，见下）

pushflow 口径（WO-OS-20）：按北京日期筛 work/push_flow.jsonl 当日行，统计
ok_count/fail_count/fail_rate 与分通道成败；文件缺失、读失败、当日无记录一律
返回 None → 引擎 gaps（待核），绝不推断「没失败就是正常」。

降级纪律：单源获取抛异常/返回非 dict → 按 None 处理进 gaps（与引擎
usable() 语义一致），绝不把「接口可达」当「业务健康」。

时钟：默认服务器当前 tz-aware 时间（北京时间）；可选 ?clock=ISO8601
（带时区）回放口径——坏值显式 400 拒绝，不静默吞。

红线（沿 api_brief 判例）：
- 不 import 引擎链之外的新依赖；build_brief 是纯函数，本模块不碰其源码；
- 非 GET 方法由 portal_api 统一 405（本 mixin 只提供 GET 分发方法）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from api_zhuge_brief import BEIJING, SOURCES, build_brief

# 投递账本（只读）：portal/work/push_flow.jsonl，一行一次投递
PUSHFLOW_LEDGER = Path(__file__).resolve().parent / "work" / "push_flow.jsonl"


# WO-OS-22：政策行词表——制造端用这些词标记"按策略不发送"，均非投递失败。
# 覆盖：闸门静默窗口(|deny:)、配额拦截(quota_block)、策略延后(policy/hold)、
# 夜间静默(silent)。新增制造端政策词时必须同步本表（见运维手册红线）。
POLICY_KIND_WORDS = ("deny:", "quota", "policy", "hold", "silent")


def _is_policy_suppressed(row):
    """是否为策略性拦截（非投递失败）。

    WO-OS-20-FIX（2026-10-21 实测口径纠错）：push_flow 里 channel=hub-gate
    或 kind 带政策词的，是推送闸门/配额按策略主动不发送，属设计内行为。
    若算成失败，会把静默窗口抑制虚报成故障（实测 282 行中 103 行为抑制，
    虚报 53.9% vs 真实 27.4%）。
    WO-OS-22：判据从「hub-gate + |deny:」扩为政策词表，堵住配额拦截外溢
    （km_feishu_hub.quota_block:km_card 当前未触发，但一旦触发不得算失败）。
    沿 api_notify.py:205 is_gate 判例保持同源口径。
    """
    if not isinstance(row, dict):
        return False
    if row.get("channel") == "hub-gate":
        return True
    kind = str(row.get("kind") or "").lower()
    return any(word in kind for word in POLICY_KIND_WORDS)


def _pushflow_stats(path=None, day=None):
    """统计当日（北京日期）真实投递成败：{ok, today, attempted, ok_count,
    fail_count, fail_rate, policy_suppressed, channels}。
    文件缺失/读失败/当日无记录/当日全为策略抑制 → None（引擎记待核）。

    纯只读：只 read_text，不写账本、不改行。path/day 为测试注入点。
    """
    target = Path(path) if path is not None else PUSHFLOW_LEDGER
    if day is None:
        day = datetime.now(BEIJING).strftime("%Y-%m-%d")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    total = ok_count = fail_count = suppressed = 0
    channels: Dict[str, Dict[str, int]] = {}
    # WO-OS-25：按日累计真实成败，供"近 4 日趋势"（政策行一律不进）
    per_day: Dict[str, list] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 半行/坏行跳过，不拿残缺行推断成败
        if not isinstance(row, dict):
            continue
        if _is_policy_suppressed(row):
            if row.get("date") == day:
                suppressed += 1  # 策略抑制：不进分母、不进通道成败
            continue
        row_day = row.get("date")
        passed = row.get("ok") is True
        bucket_day = per_day.setdefault(str(row_day), [0, 0])  # [ok, fail]
        bucket_day[0 if passed else 1] += 1
        if row_day != day:
            continue
        total += 1
        if passed:
            ok_count += 1
        else:
            fail_count += 1
        name = row.get("channel")
        name = name if isinstance(name, str) and name else "unknown"
        bucket = channels.setdefault(name, {"ok": 0, "fail": 0})
        bucket["ok" if passed else "fail"] += 1

    # 当日总行数需含抑制行（today 是"账本行数"，与 attempted/suppressed 自洽）
    total += suppressed

    attempted = ok_count + fail_count
    if attempted == 0:
        return None  # 当日无真实投递尝试：不可推断正常，交引擎记待核

    recent = []
    for day_key in sorted(per_day)[-4:]:
        day_ok, day_fail = per_day[day_key]
        day_attempted = day_ok + day_fail
        if day_attempted <= 0:
            continue
        recent.append({
            "day": day_key,
            "attempted": day_attempted,
            "fail_count": day_fail,
            "fail_rate": round(day_fail / day_attempted, 4),
        })

    return {
        "ok": True,
        "today": total,
        "attempted": attempted,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "fail_rate": round(fail_count / attempted, 4),
        "policy_suppressed": suppressed,
        "recent": recent,
        "channels": channels,
    }


def _default_fetchers() -> Dict[str, Callable[[], Any]]:
    """七源默认获取函数（延迟绑定：import 放函数内防循环依赖）。"""
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
        "pushflow": _pushflow_stats,
    }


def build_route_payload(fetchers=None, clock=None):
    """聚合七源 → 引擎 build_brief → 载荷 dict。

    fetchers: {source: callable}（测试注入点；默认七源真实函数）。
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
