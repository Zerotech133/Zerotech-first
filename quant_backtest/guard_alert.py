# -*- coding: utf-8 -*-
"""守卫拦截飞书告警器（alert-loop P0-2 2026-10-14：正确的拦截不再静默）。

判例（今晨事件）：StrongEod 06:30 freshness_guard 正确拦截（10-13 TdxSync
崩溃→板块仓停 10-12），但拦截只落 stdout exit 3——守卫行为对，告警缺位，
没人知道名单没出。本模块把「拦截出口」接成「告警闭环」：

  出口调 guard_alert(任务名, 原因) → 内容四要素拼装（任务/原因/双锚/建议）
  → 12h 去重（data/guard_alert_state.json，同 key 只发 1 次，原子写）
  → runner.push_feishu 推送；推送失败只落日志不炸主流程（告警不能反噬守卫）。

设计口径：
  · 去重键 = "guard_" + sha1(任务名|原因)[:16]——原因文本含数据锚日期，
    日期变了即新告警（同因 12h 内重复拦截不打扰，跨日复发可见）；
  · 状态文件损坏/缺失 fail-open（视为空状态照发）——宁可重发不可静默；
  · send=False 为 dry 路径（只拼装不推不写状态，供测试与演练）；
  · alert_once(key, text) 为通用去重告警口，cross 源健康告警（任务 B）
    复用同一状态文件，键前缀 "cross_src_" 区分策略。

用法:
  guard_alert("StrongEod", "个股锚 600519.SH=2026-10-12 ≠ 板块锚 ...")
"""

import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATE_PATH = os.path.join(HERE, "data", "guard_alert_state.json")
LOG_PATH = os.path.join(HERE, "logs", "guard_alert.log")
DEDUP_WINDOW_H = 12  # 同 key 去重窗口（小时）
_LOCK_STALE_S = 75  # 锁文件超龄视为残留（持锁进程已消亡）强破
_LOCK_TIMEOUT_S = 80  # 等锁上限（> runner.push_feishu 60s 子进程超时）


def log(msg):
    """告警动作留痕（推送/失败/去重跳过），诊断不反噬主流程。"""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/guard_alert.py", exc)


_last_fail_reason = ""  # 最近一次 _push 失败原因（SEND_FAIL 原因行/stderr
# 摘要，≤120 字符），供 push fail 日志取证


def _push(text):
    """复用 runner.push_feishu 文本通道（与 alert_fail.py 同一发送底座）。

    A4 20261019：失败时把底座落下的原因（runner.LAST_PUSH_FAIL，
    send_feishu 的 SEND_FAIL 原因行/stderr 摘要）取回 _last_fail_reason，
    供 alert_once 的 fail 日志带出——推送失败不再只留 key 不留因。
    """
    global _last_fail_reason
    _last_fail_reason = ""
    import runner

    ok = bool(runner.push_feishu(text))
    if not ok:
        reason = (getattr(runner, "LAST_PUSH_FAIL", "") or "").strip()
        _last_fail_reason = (
            reason[:120] if reason else "push_feishu False（底座未落原因）"
        )
    return ok


@contextmanager
def _locked(path, timeout_s=_LOCK_TIMEOUT_S):
    """状态文件互斥锁：O_EXCL 锁文件（Windows/POSIX 通用，纯标准库）。

    A4 20261019（push fail 诊断 §五-5）：check-then-write 竞争窗口收敛——
    并发进程对 guard_alert_state.json 的「判定+推送+写状态」串行化，
    杜绝同 key 重复发送与旧快照全量覆写（丢 key）。
    · 超时让步（告警可用性优先，宁重发不静默）：等锁超时 proceed 不持锁；
    · 陈旧锁强破：锁文件 mtime 超 _LOCK_STALE_S 视为持锁进程已消亡残留。
    yield 是否真正持锁。
    """
    lock_path = path + ".lock"
    t0 = time.time()
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock_path) > _LOCK_STALE_S:
                    os.remove(lock_path)  # 陈旧锁强破
                    continue
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/guard_alert.py", exc)
            if time.time() - t0 > timeout_s:
                log("state lock timeout (proceed unlocked) %s" % os.path.basename(path))
                break
            time.sleep(0.05)
    try:
        yield fd is not None
    finally:
        if fd is not None:
            try:
                os.close(fd)
                os.remove(lock_path)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/guard_alert.py", exc)


def _fresh(ent, now, window_h):
    """窗口内已发过（ent 非空且 ts 距 now 不足 window_h 小时）。"""
    if not ent:
        return False
    prev = datetime.strptime(str(ent.get("ts"))[:19], "%Y-%m-%d %H:%M:%S")
    return now - prev < timedelta(hours=window_h)


def _load_state(path):
    """读去重状态；文件缺失/损坏 fail-open 返回 {}（宁重发不静默）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(path, state):
    """原子写（tmp + os.replace，仓库同款），写失败只落日志。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log("state save error: %s" % str(e)[:120])


def alert_once(
    key, text, send=True, state_path=None, now=None, window_h=DEDUP_WINDOW_H
):
    """通用去重告警：同 key 窗口内只发 1 次。返回 (是否发送, 文本)。

    · 窗口内已发 → (False, text)，不重复打扰；
    · send=False → dry：不推不写状态（测试/演练路径）；
    · 推送失败 → 落日志返回 (False, text)，不写状态（下轮重试），
      任何异常不上抛（告警不能反噬调用方主流程）。
    """
    now = now or datetime.now()
    path = state_path or STATE_PATH
    try:
        state = _load_state(path)
        if _fresh(state.get(key), now, window_h):
            log("dedup skip key=%s (sent %s)" % (key[:24], state[key].get("ts")))
            return False, text
        if not send:
            return False, text  # dry：只拼装
        with _locked(path) as got:
            if got:
                # 锁内重读：等锁期间并发进程可能已发出同 key 告警
                state = _load_state(path)
                if _fresh(state.get(key), now, window_h):
                    log(
                        "dedup skip key=%s (sent %s)" % (key[:24], state[key].get("ts"))
                    )
                    return False, text
            ok = _push(text)
            if ok:
                state[key] = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "key": key[:64]}
                _save_state(path, state)
            else:
                reason = (_last_fail_reason or "unknown").strip() or "unknown"
                log(
                    "push fail key=%s reason=%s（不写状态，下轮重试）"
                    % (key[:24], reason[:120])
                )
        return ok, text
    except Exception as e:
        log("alert_once error key=%s: %s" % (key[:24], str(e)[:120]))
        return False, text


def guard_alert(
    task, reason, anchors=None, suggest=None, send=True, state_path=None, now=None
):
    """守卫拦截告警（四要素卡）：任务名 / 拦截原因 / 双数据锚 / 建议动作。

    anchors 缺省时从原因文本提取 ISO 日期兜底（freshness_guard 的
    mismatch/count 两条失败路径 msg 内均含锚日期；无日期路径标「无」）。
    返回 (是否发送, 文本)。
    """
    task, reason = str(task), str(reason)
    if anchors is None:
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", reason)
        anchors = {"数据锚": "、".join(dates) if dates else "无（原因未含日期）"}
    if suggest is None:
        suggest = "人工核查上游同步/补数任务；数据补齐后按任务手册强制重跑"
    lines = [
        "⛔ 守卫拦截告警 · %s" % (now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "任务: %s" % task,
        "拦截原因: %s" % reason,
        "数据锚: %s" % "；".join("%s=%s" % (k, v) for k, v in anchors.items()),
        "建议动作: %s" % suggest,
    ]
    text = "\n".join(lines)
    key = (
        "guard_"
        + hashlib.sha1(("%s|%s" % (task, reason)).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    )
    return alert_once(key, text, send=send, state_path=state_path, now=now)


if __name__ == "__main__":
    # CLI 口径（人工演练）：python guard_alert.py <task> <reason...>
    r = guard_alert(sys.argv[1], " ".join(sys.argv[2:]))
    print("sent=%s" % r[0])
    print(r[1])
