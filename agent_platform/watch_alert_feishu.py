# -*- coding: utf-8 -*-
"""Feishu sink for watch_alerts（W6 test-channel only）.

只把 `watch_alert/v1` 事件格式化为文本，经 news_collector 已存在的
`send_content_to(..., kind="watch:alert")` 发送到 push_targets 指定目标。
默认目标 `内部测试`；不打印 token，不做网络调用以外的业务写。

边界：
- 只在调用方显式传入 target 且 alert ledger 已配置时由 runner 使用；
- 发送失败返回 status=failed，由 watch_alerts.dispatch 记 failed，不抛。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEWS_DIR = REPO / "news_collector"


def _target_id(name):
    if str(NEWS_DIR) not in sys.path:
        sys.path.insert(0, str(NEWS_DIR))
    import push_targets  # noqa: E402
    return push_targets.target_id(name)


def _format(event):
    return (
        "【盯盘告警】%s %s %s\n"
        "观测=%s 阈值=%s\n"
        "计划=%s\n"
        "paper_only=true / not_a_trade=true\n"
        "quote_as_of=%s"
        % (
            event.get("symbol") or "-",
            event.get("action") or "-",
            event.get("plan_id") or "-",
            event.get("observed") or "-",
            event.get("threshold") or "-",
            event.get("plan_id") or "-",
            event.get("quote_as_of") or "-",
        )
    )


def make_feishu_sink(target_name=None):
    """Return a callable sink(event)->mapping; target default env / 内部测试."""
    name = (target_name or os.environ.get("WATCH_ALERT_FEISHU_TARGET") or "内部测试").strip()
    def sink(event):
        tid = _target_id(name)
        if not tid:
            return {"status": "failed", "channel": "feishu", "target": name,
                    "reason": "target_not_found"}
        import send_feishu  # noqa: E402
        ok = send_feishu.send_content_to(
            tid, "chat_id", _format(event), "text", kind="watch:alert"
        )
        return {"status": "sent" if ok else "failed", "channel": "feishu",
                "target": name}
    sink.__name__ = "feishu_sink"
    return sink


__all__ = ["make_feishu_sink"]
