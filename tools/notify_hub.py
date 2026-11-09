# -*- coding: utf-8 -*-
"""notify_hub —— 健康检查家族统一红告警出口（S3，2026-11-08）。

背景：huangzhong_health_alert（flag 等转发，被动）/ intel_source_health（无出口，
纯拉模式）/ jev_risk_alert（flag 等转发）三件的红结果原本都不直接到人。
S3 收编：红结果统一走 news_collector/send_feishu.send_text 直发个人（kind=
ops:health_notify），绿不发送、去重窗口内不重发、发送结果落 state 供审计。

设计边界（对照告警链转正清单 S3 / FF09 防过度设计）：
    - 只做「红即到人」一件事：不建监控全家桶、不做聚合日报、不做风暴抑制——
      各工具自有的阈值/去重状态机保留原样，本模块只提供统一出口；
    - 各调用方各自维护 dedupe key（工具名+指纹），hub 只管发与记；
    - 发送失败不吞：返回 False 由调用方如实 rc 非 0（fail-visible 原则）。

用法（调用方三行接入）：
    from notify_hub import notify_red
    ok = notify_red("huangzhong_health", "连续 2 次 fail：黄忠工具链心跳异常", key="hz-fail")

state: news_collector/outbox/notify_hub_state.json
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
NEWS = os.path.join(STATION, "news_collector")
STATE_PATH = os.path.join(NEWS, "outbox", "notify_hub_state.json")

DEDUPE_H = 4.0  # 同 key 去重窗口（小时）——窗口内重发拦截，风暴交调用方阈值管


def _now():
    return datetime.datetime.now()


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _send(text):
    sys.path.insert(0, NEWS)
    try:
        from send_feishu import send_text
    except ImportError as e:
        print("NOTIFY_HUB_SEND_ERR import: %s" % str(e)[:100])
        return False
    try:
        return bool(send_text(text, kind="ops:health_notify"))
    except Exception as e:
        print("NOTIFY_HUB_SEND_ERR %s" % str(e)[:120])
        return False


def notify_red(tool: str, reason: str, key: str = "", dedupe_h: float = DEDUPE_H) -> bool:
    """红告警统一出口：去重窗口内同 key 只发一次。

    Args:
        tool:   调用方名（如 huangzhong_health / intel_source_health / jev_risk_alert）
        reason: 一行红原因（会进消息正文）
        key:    去重键（建议含事件指纹；空则按 tool 去重）
    Returns:
        True=已发送或窗口内已发过（视为送达）；False=发送失败（调用方应如实非 0）
    """
    now = _now()
    k = "%s|%s" % (tool, key) if key else tool
    st = _load_state()
    last = st.get(k) or {}
    last_at = last.get("at", "")
    if last_at:
        try:
            delta = (now - datetime.datetime.strptime(last_at[:16], "%Y-%m-%d %H:%M")).total_seconds() / 3600.0
            if delta < float(dedupe_h):
                print("NOTIFY_HUB_DEDUPE %s (%.1fh < %.1fh)" % (k, delta, dedupe_h))
                return True
        except ValueError:
            pass
    text = "[%s 红告警] %s —— %s（%s，经 notify_hub 直发）" % (
        tool, reason, now.strftime("%Y-%m-%d %H:%M"), "窗口内不重发" if last_at else "首报")
    ok = _send(text)
    st[k] = {"at": now.strftime("%Y-%m-%d %H:%M"), "ok": ok, "reason": reason[:200]}
    try:
        _atomic_write(STATE_PATH, st)
    except OSError as e:
        print("NOTIFY_HUB_STATE_ERR %s" % str(e)[:100])
    return ok
