# -*- coding: utf-8 -*-
"""G9 融合失败可观测性桥：signals_combined 写 PG 失败 → E1 告警链路。

口径对齐（不另立标准）：直接复用 E1 现役实现
news_collector/alert_fail_wrap.py 的原语——
  pending_path          → outbox 落点（pending_alert_<task>.txt，同 E1 布局）
  parse_send_result     → SEND_OK / SEND_FAIL 判定（同 alert_fail 口径）
  handle_send_result    → logs/alert_send.log ASCII 追加 + 成功后
                          pending→.sent 同目录原子改名（保留审计）
  exception_class       → 稳定异常分类
发送复用 news_collector/send_feishu.py（alert_fail.py 同款子进程调用）。

落点布局与 E1 完全一致：
  news_collector/outbox/pending_alert_SignalsCombined.txt       ← pending 落痕
  news_collector/outbox/pending_alert_SignalsCombined.txt.sent  ← 发送成功后改名
  news_collector/logs/alert_send.log                            ← ASCII 状态追加

进程内 import 而非 subprocess 调 wrap 的理由：E1 原语是纯函数，测试可
对 wrap.OUTBOX / wrap.SEND_LOG / 本模块 _send_feishu 注入沙箱 mock，
全程不触网、不读写真实 outbox（同 news_collector/test_alert_fail.py 红线）。

纪律：本模块任何异常都不外抛（告警观测层不能反噬聚合主流程）；
本模块不做任何聚合判定，只消费 aggregator 传入的失败事实。
"""

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_NEWS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "news_collector"))
if _NEWS_DIR not in sys.path:
    sys.path.insert(0, _NEWS_DIR)

import alert_fail_wrap as _wrap  # noqa: E402  E1 口径单一事实源

SEND_FEISHU_PY = os.path.join(_NEWS_DIR, "send_feishu.py")
TASK = "SignalsCombined"  # outbox 文件名与 alert_send.log 的 task 字段
SEND_TIMEOUT = 60  # send_feishu.py 子进程秒数上限（alert_fail 同量级）


def build_message(trade_date: str, rows_count: int, error=None) -> str:
    """失败事实 → 待发消息文本（UTF-8，收件人可读、可定位重跑）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    err = str(error if error is not None else "").strip() or "unknown"
    return (
        "[QUANT ALERT] signals_combined 写库失败（G9）\n"
        "task=%s trade_date=%s rows=%d ts=%s\n"
        "error=%s\n"
        "hint: 内存聚合结果已返回、PG strategy.signals_combined 无当日账；"
        "排查 quant-pg(5442) 后重跑 strategy_layer.run_daily"
        % (TASK, trade_date, rows_count, ts, err[:400])
    )


def _send_feishu(pending: str):
    """经 send_feishu.py 发送 pending 文件，返回 (ok, reason)（E1 口径判定）。"""
    try:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", SEND_FEISHU_PY, pending],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SEND_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — 子进程不可用也走 FAIL 落痕
        return False, "send_err " + str(exc)[:100]
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    return _wrap.parse_send_result(out, r.returncode)


def alert_persist_failure(trade_date: str, rows_count: int, error) -> None:
    """写库失败 → E1 告警链：outbox pending 落痕 → send_feishu 发送 →
    alert_send.log 追加 → 成功则 pending→.sent。绝不抛异常。"""
    try:
        pending = _wrap.pending_path(TASK)
        os.makedirs(os.path.dirname(pending), exist_ok=True)
        with open(pending, "w", encoding="utf-8") as f:
            f.write(build_message(trade_date, rows_count, error))
        ok, reason = _send_feishu(pending)
        try:
            action = _wrap.handle_send_result(
                pending,
                TASK,
                ok,
                reason,
                task_rc="1",
                result_summary=reason,
                exception_kind=_wrap.exception_class("1", ok, reason),
            )
        except Exception as exc:  # noqa: BLE001 — 改名失败不吞发送事实
            _wrap.log_send(
                TASK,
                "feishu",
                ok,
                reason + " rename_err " + str(exc)[:60],
                task_rc="1",
                result_summary=reason,
                exception_kind=_wrap.exception_class(
                    "1", ok, reason, rename_error=True
                ),
            )
            action = "rename_err"
        print(
            "[aggregator][ALERT] task=%s ok=%s action=%s reason=%s"
            % (TASK, ok, action, reason[:80])
        )
    except Exception as exc:  # noqa: BLE001 — 告警链自身失败不反噬主流程
        print(
            "[aggregator][ALERT][WARN] 告警链自身失败(结果已留运行日志): %s"
            % str(exc)[:120]
        )
