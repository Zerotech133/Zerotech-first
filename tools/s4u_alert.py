# -*- coding: utf-8 -*-
r"""S4U 守卫失败告警出口。

背景（2026-11-05）
    s4u_guard.ps1 每 5 分钟把 "action 里含 aios" 的计划任务转成 S4U 隐藏模式。
    加了 JEV 白名单后仍有 58 个任务每轮转换失败，而旧版只往 s4u_guard.log 写一行
    ``noop matched=164 failed=58`` —— 没人看、没告警，等于静默空转。
    守卫自己的注释也承认过：日志里的 "noop (all S4U)" 曾经是假的。

为什么出口放在这里而不是 PS 里
    守卫跑在 SYSTEM 下（s4u_guard.log 属主 = NT AUTHORITY\SYSTEM），解不了用户级
    DPAPI 的 jev-gate.env.enc；而 C:\Users\t\.lark-cli\config.json 是**明文静态
    app 凭证**（ACL 里 SYSTEM = FullControl），所以把 USERPROFILE 指到真实用户目录
    后，send_feishu 的 OpenAPI 直连通道就能在 SYSTEM 下工作，不依赖 lark-cli 的
    profile / 用户态事件总线。

去重策略
    失败集合（排序后）变化 → 立刻发；
    集合没变但距上次告警超过 INTERVAL → 再发一次（长期失败不许沉底）；
    失败清零且上次有失败 → 发一条"已恢复"。

退出码
    0 已发送 / 3 无需发送（去重）/ 4 发送失败 / 5 状态文件缺失或不可读
"""

import json
import os
import sys
from datetime import datetime

STATE = r"C:\aios\state\s4u_guard_state.json"
SHOT = r"C:\aios\state\s4u_guard_alert.json"
AUDIT = r"C:\aios\state\s4u_guard_alert.jsonl"
SENDER_DIR = r"C:\项目\老田的AI助手\news_collector"
REAL_HOME = r"C:\Users\t"
INTERVAL = 6 * 3600
RETRY_AFTER_FAIL = 15 * 60
MAX_LIST = 12


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_audit(rec):
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def build_text(state, recovered=False):
    failed = list(state.get("failed") or [])
    head = state.get("stamp") or stamp()
    matched = state.get("matched")
    exempt = state.get("exempt")
    parent = (state.get("parent") or "unknown")[:220]
    if recovered:
        return (
            "[S4U-GUARD] 已恢复\n"
            "时间: %s\n"
            "matched=%s exempt=%s，转换失败已清零（上一轮失败项均已转成 S4U 或已从匹配集移除）。\n"
            "宿主: %s" % (stamp(), matched, exempt, parent)
        )
    show = failed[:MAX_LIST]
    more = len(failed) - len(show)
    lines = [
        "[S4U-GUARD] 计划任务 S4U 转换失败 %d 个" % len(failed),
        "守卫轮次: %s" % head,
        "matched=%s exempt=%s converted=%s" % (
            matched, exempt, len(state.get("converted") or [])),
        "失败示例: " + ", ".join(show) + (" ...(另 %d 个)" % more if more > 0 else ""),
        "宿主: %s" % parent,
        "说明: 守卫每 5 分钟重试一次，失败项不会自动消失；要么补白名单",
        "      要么认领这些任务为何转不成 S4U。明细见 C:\\aios\\state\\s4u_guard_state.json",
    ]
    return "\n".join(lines)


def send(text):
    """走 send_feishu 的 OpenAPI 直连通道（静态凭证，不依赖 lark-cli profile）。"""
    os.environ["USERPROFILE"] = REAL_HOME
    os.environ["HOME"] = REAL_HOME
    if SENDER_DIR not in sys.path:
        sys.path.insert(0, SENDER_DIR)
    try:
        import send_feishu
    except Exception as exc:
        return False, "import send_feishu 失败: %s" % exc
    try:
        ok = bool(send_feishu.send_text(text))
    except Exception as exc:
        return False, "send_text 异常: %s" % exc
    return ok, "openapi"


def main():
    state = load_json(STATE)
    if not isinstance(state, dict):
        return 5
    failed = sorted(str(x) for x in (state.get("failed") or []))
    sig = "|".join(failed)
    prev = load_json(SHOT, {}) or {}
    last_epoch = int(prev.get("last_alert_epoch") or 0)
    now_epoch = int(datetime.now().timestamp())
    prev_sig = prev.get("sig") or ""

    # 发送失败不能把整个 6 小时窗口吃掉：失败只推迟 15 分钟再试，
    # 否则"网抖一次"就等于"这条告警六小时内不存在"。
    def next_epoch(ok):
        return now_epoch if ok else now_epoch - INTERVAL + RETRY_AFTER_FAIL

    if not failed:
        if prev_sig:
            ok, why = send(build_text(state, recovered=True))
            write_json_atomic(SHOT, {"sig": "", "last_alert_epoch": next_epoch(ok),
                                     "last_result": ok, "last_reason": why})
            append_audit({"ts": stamp(), "event": "recovered", "ok": ok, "reason": why})
            return 0 if ok else 4
        return 3

    changed = sig != prev_sig
    due = (now_epoch - last_epoch) >= INTERVAL
    if not (changed or due):
        return 3

    ok, why = send(build_text(state))
    write_json_atomic(SHOT, {"sig": sig, "last_alert_epoch": next_epoch(ok),
                             "last_result": ok, "last_reason": why,
                             "last_stamp": stamp(), "failed_count": len(failed)})
    append_audit({"ts": stamp(), "event": "alert", "ok": ok, "reason": why,
                  "failed_count": len(failed), "changed": changed,
                  "failed": failed[:60]})
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
