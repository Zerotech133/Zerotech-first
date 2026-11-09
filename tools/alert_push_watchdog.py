# -*- coding: utf-8 -*-
"""S2 断链探针（2026-11-08 告警链转正清单）：alert_push 停摆即飞书直发。

背景（判例）：2026-10-26 ~ 11-08 推送链停摆 13 天无人知——outbox 一直
ok=false 也无人看。缺的不是推送，是「推送链自身的断链告警」。本探针补齐：
    读 news_collector/outbox/alert_push_last.json 的 ts/ok：
      - 超 STALE_H 小时无成功推送（ok=true 的 ts 过旧或无 ok=true 记录）
      - 或 ok 连续 false 持续 FAIL_H 小时（ts 还在动但一直失败）
    → 调 send_feishu 直发「告警链断链」文本到 push_targets 首目标（个人）。
    熔断：同一断链事件只发一次（state 文件记录），恢复后自动复位。

与 alert_watch/alert_push 的分工：
    alert_push  定时定量全量日报（被监控对象）
    alert_watch 30 分钟阈值监听（业务层）
    本探针      监控「推送链自身」——outbox 不刷新/持续失败才出手

用法:
    python tools/alert_push_watchdog.py            # 常规巡检
    python tools/alert_push_watchdog.py --test     # 自检：强制发一条测试消息
状态: news_collector/outbox/alert_push_watchdog_state.json（恢复自动复位）
调度: 计划任务每日 08:00（注册命令见告警链转正清单 S2 段；schtasks 需本人执行）
退出码: 0=链路正常或告警已发；2=断链且告警发送失败（此时需人工介入）
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
NEWS = os.path.join(STATION, "news_collector")
OUTBOX = os.path.join(NEWS, "outbox")
PUSH_LAST = os.path.join(OUTBOX, "alert_push_last.json")
STATE_PATH = os.path.join(OUTBOX, "alert_push_watchdog_state.json")

STALE_H = 24  # 超 24h 无成功推送 → 断链
FAIL_H = 6    # ts 还在动但 ok=false 持续 6h → 断链（比 STALE_H 更早发现）


def _now():
    return datetime.datetime.now()


def _parse_ts(ts):
    """outbox ts 形如 '2026-11-08 06:57'（本地时间，分钟精度）。"""
    try:
        return datetime.datetime.strptime(str(ts)[:16], "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def _atomic_write(path, obj):
    """与 alert_io.atomic_write_json 同语义（独立实现避免跨目录 import 依赖）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_push_last():
    try:
        with open(PUSH_LAST, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return {"_corrupt": True}


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def send_alert(text):
    """直发飞书：走 send_feishu 通道（OpenAPI 直连 + lark-cli 回退）。
    目标固定为 push_targets 首目标（个人）——断链告警不进群，先到人。"""
    sys.path.insert(0, NEWS)
    try:
        from send_feishu import send_text
    except ImportError as e:
        print("WATCHDOG_SEND_ERR import send_feishu: %s" % str(e)[:100])
        return False
    try:
        return bool(send_text(text, kind="ops:alert_push_watchdog"))
    except Exception as e:
        print("WATCHDOG_SEND_ERR %s" % str(e)[:120])
        return False


def diagnose(push_last, now):
    """返回 (is_broken, reason)。三态：正常 / 断链 / 无基线。"""
    if push_last is None:
        return True, "outbox/alert_push_last.json 不存在——alert_push 从未运行或 outbox 被清"
    if push_last.get("_corrupt"):
        return True, "outbox/alert_push_last.json 损坏（JSON 解析失败）"
    ts = _parse_ts(push_last.get("ts"))
    if ts is None:
        return True, "outbox ts 无法解析: %r" % push_last.get("ts")
    age_h = (now - ts).total_seconds() / 3600.0
    ok = push_last.get("ok") is True
    if ok:
        if age_h <= STALE_H:
            return False, ""
        return True, "上次成功推送 %s 已 %.1fh（阈值 %dh）" % (push_last.get("ts"), age_h, STALE_H)
    # ok=false：ts 还在动说明计划任务活着但推送一直失败
    if age_h <= FAIL_H:
        return False, ""  # 失败但仍在窗口内，给重试留时间
    return True, (
        "推送持续失败：最后记录 %s ok=false 已 %.1fh（失败阈值 %dh），状态 %r"
        % (push_last.get("ts"), age_h, FAIL_H, push_last.get("status"))
    )


def main():
    test_mode = "--test" in sys.argv
    now = _now()
    state = load_state()

    if test_mode:
        text = "[探针自检] alert_push 断链探针通道验证 %s——收到本条说明直发通道正常，无需处理。" % now.strftime("%Y-%m-%d %H:%M")
        ok = send_alert(text)
        print("WATCHDOG_TEST %s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 2

    push_last = load_push_last()
    broken, reason = diagnose(push_last, now)

    if not broken:
        if state.get("alerted"):
            # 恢复后自动复位 + 发一条恢复通知
            text = "[告警链恢复] alert_push 已恢复成功推送（%s）。断链告警自动复位。" % (
                (push_last or {}).get("ts", "?")
            )
            send_alert(text)
        _atomic_write(STATE_PATH, {"alerted": False, "ts": now.strftime("%Y-%m-%d %H:%M")})
        print("WATCHDOG_OK last=%s" % ((push_last or {}).get("ts", "none")))
        return 0

    # 断链：熔断——同一事件只发一次
    if state.get("alerted"):
        print("WATCHDOG_BROKEN (已告警，熔断) %s" % reason)
        return 0
    text = (
        "[告警链断链] %s——预警日报已停发，需人工检查 alert_push（"
        "python -X utf8 news_collector/alert_push.py 手动复跑定位）。原因: %s"
    ) % (now.strftime("%Y-%m-%d %H:%M"), reason)
    sent = send_alert(text)
    _atomic_write(
        STATE_PATH,
        {"alerted": sent, "ts": now.strftime("%Y-%m-%d %H:%M"), "reason": reason},
    )
    if not sent:
        # 断链 + 告警通道也挂 → 退出码 2，交给更外层（计划任务历史/健康卡）兜底
        print("WATCHDOG_BROKEN_AND_SEND_FAIL %s" % reason)
        return 2
    print("WATCHDOG_ALERT_SENT %s" % reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
