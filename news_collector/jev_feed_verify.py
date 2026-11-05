# -*- coding: utf-8 -*-
"""JevFeed-Daily 夜跑结果验证器（换机新链路护航，2026-10-16 起）。

每日 00:15 计划任务（JevFeed-Verify）验证昨晚 23:45 JevFeed-Daily 四项：
  (1) schtasks Last Run Time / Last Result（双语解析，PS 兜底）
  (2) C:\\Temp\\jev_feed_task.log 尾行 exit 码
  (3) news.db runs 最新 kind=jev_feed 记录：ts 新增（昨晚 23:40 后）、
      ok=1、detail.wm 水位较上轮推进
  (4) news 表 id LIKE 'xd:%' 各源行数 / MAX(ts) 数据水位

判定口径（2026-10-18 换机链路）：
  ✅ Last Result=0 且 exit=0 且 runs 新增 ok=1 且 new_count>0（水位未推满
     到当日属单轮截断设计内，注明"明晚补齐"）；
  ⚠️ new_count=0 → 追查 TushareSync 23:30 是否先跑成功；水位未推进 /
     任务 00:15 仍在运行（10-17 排水实证曾至 02:16，设计内）同列；
  ❌ exit≠0 或任务未跑（含 Last Result≠0），附 jev_feed_err.log 尾 5 行。

推送：系统既有飞书通道（feishu_client 全仓唯一底座，与 alert_fail_wrap
同族链路），落点量化群（push_targets 单一事实源）；critical=True 豁免
夜间静默窗——00:15 常落跨午夜静默段，成功汇总不可吞（用户要求成功也推）。

rc：推送成功即 0（❌ 发现已随汇总上报；jev_feed 23:45 失败自带
alert_fail_wrap 告警，本任务不重发）；推送失败 rc=1 → launcher 调
alert_fail_wrap 兜底告警个人。稳定两周（约 2026-11-01）后整链可撤：
  schtasks /delete /tn JevFeed-Verify
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from alert_io import append_ascii_log, sniff_read_text  # noqa: E402
import push_targets  # noqa: E402
from feishu_client import send_msg  # noqa: E402
import logging

DB = os.path.join(HERE, "news.db")
TASK = "JevFeed-Daily"
UPSTREAM = "TushareSync"
TASK_LOG = r"C:\Temp\jev_feed_task.log"
ERR_LOG = r"C:\Temp\jev_feed_err.log"
AUDIT_LOG = os.path.join(HERE, "logs", "jev_verify.log")
KIND = "jev_feed_verify"
GROUP = "量化群"  # push_targets 注册名（K-STOCK-403 单一事实源）
WAIT_RUNNING_MIN = 35  # 校验时任务仍在运行的轮询上限（分钟）
# 10-17 实证：23:45 启动、02:16 落账（存量排水），Steady state ~11min。
# 2026-11-05：feed 已改为 21:00 启动；267009 是任务仍在运行，不能当 ❌。
RUNNING_RC = 267009  # 0x41301 SCHED_S_TASK_RUNNING


def run_cmd(args, timeout=30):
    """子进程输出统一 utf-8/replace 解码（schtasks 本机为英文输出）。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + str(e)[:120]


def _ps_info(task):
    """Get-ScheduledTaskInfo/Task 兜底（locale 无关）：JSON 一行。"""
    ps = (
        "$i=Get-ScheduledTaskInfo -TaskName '%s';"
        "$t=Get-ScheduledTask -TaskName '%s';"
        "@{lr=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss');"
        "rc=$i.LastTaskResult;st=$t.State}|ConvertTo-Json -Compress" % (task, task)
    )
    return run_cmd(["powershell", "-NoProfile", "-Command", ps])


def _is_running(info):
    """任务仍在运行（State=Running 或 LastResult=267009）。"""
    return info.get("state") == "Running" or info.get("last_result") == RUNNING_RC


def task_info(task):
    """(1) schtasks /v /fo list 双语解析 Last Run Time / Last Result /
    Status；解析失败回落 PS。返回 dict(last_run, last_result, state, raw)。"""
    out = run_cmd(["schtasks", "/query", "/tn", task, "/v", "/fo", "list"])
    info = {"last_run": None, "last_result": None, "state": "", "raw": ""}
    lr = re.search(r"(?:Last Run Time|上次运行时间)\s*:\s*(.+)", out)
    rc = re.search(r"(?:Last Result|上次结果)\s*:\s*(\S+)", out)
    st = re.search(r"^\s*(?:Status|状态)\s*:\s*(.+)$", out, re.M)
    if lr:
        info["raw"] = lr.group(1).strip()
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                info["last_run"] = datetime.strptime(info["raw"], fmt)
                break
            except ValueError:
                continue
    if rc:
        try:
            info["last_result"] = int(rc.group(1), 0)
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/jev_feed_verify.py", exc)
    if st:
        info["state"] = st.group(1).strip()
    if info["last_run"] is None or info["last_result"] is None:
        try:  # PS 兜底（解析失败/非英文非中文 locale）
            j = json.loads(_ps_info(task))
            if info["last_run"] is None:
                info["raw"] = str(j.get("lr") or "")
                info["last_run"] = datetime.strptime(info["raw"], "%Y-%m-%d %H:%M:%S")
            if info["last_result"] is None:
                info["last_result"] = int(j.get("rc") or 0)
            if not info["state"]:
                info["state"] = str(j.get("st") or "")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/jev_feed_verify.py", exc)
    return info


def wait_finish(task, max_min=WAIT_RUNNING_MIN):
    """任务 Running 时每分钟轮询至多 max_min 分钟，返回最终 state。"""
    for _ in range(max_min):
        time.sleep(60)
        try:
            j = json.loads(_ps_info(task))
            state = str(j.get("st") or "")
        except Exception:
            state = ""
        if state and state != "Running":
            return state
    return "Running"


def task_log_exit():
    """(2) launcher 任务日志尾行 'jev_feed exit=N <ISO>'。"""
    if not os.path.isfile(TASK_LOG):
        return None, "", "log缺失"
    lines = [l for l in sniff_read_text(TASK_LOG).splitlines() if "exit=" in l]
    if not lines:
        return None, "", "无exit行"
    m = re.search(r"exit=(-?\d+)", lines[-1])
    ts = lines[-1].rsplit(" ", 1)[-1]
    return (int(m.group(1)) if m else None), ts, ""


def runs_latest():
    """(3) runs 表最新两条（id 序）：返回 (latest, prev)，行可为 None。"""
    db = sqlite3.connect(DB, timeout=10)
    try:
        rows = db.execute(
            "SELECT ts,ok,new_count,push_count,detail FROM runs "
            "WHERE kind='jev_feed' ORDER BY id DESC LIMIT 2"
        ).fetchall()
    finally:
        db.close()
    return (rows[0] if rows else None), (rows[1] if len(rows) > 1 else None)


def xd_stats():
    """(4) xd 两源行数与数据水位：{source: (count, max_ts)}。"""
    db = sqlite3.connect(DB, timeout=10)
    try:
        return {
            s: (c, t or "")
            for s, c, t in db.execute(
                "SELECT source,COUNT(*),MAX(ts) FROM news "
                "WHERE id LIKE 'xd:%' GROUP BY source"
            )
        }
    finally:
        db.close()


def err_tail(n=5):
    """jev_feed_err.log 尾 n 行（BOM 嗅探兼容 UTF-16LE/UTF-8）。"""
    if not os.path.isfile(ERR_LOG):
        return "(err log 缺失)"
    ls = [l for l in sniff_read_text(ERR_LOG).splitlines() if l.strip()]
    return "\n".join(ls[-n:]) or "(空)"


def _wm(detail):
    try:
        return json.loads(detail or "{}").get("wm") or {}
    except Exception:
        return {}


def tushare_note(now):
    """new_count=0 时追查上游 TushareSync 23:30。"""
    ti = task_info(UPSTREAM)
    ran = ti["last_run"] and ti["last_run"] >= now - timedelta(days=1)
    if ran and ti["last_result"] == 0:
        return "TushareSync 昨晚已跑 rc=0（上游无增量或降级为空）"
    return "TushareSync 异常 rc=%s 上次=%s" % (ti["last_result"], ti["raw"] or "?")


def build(now):
    """四项检查 → (icon, 一行汇总, 异常摘要)。"""
    yday = now - timedelta(days=1)
    d1 = yday.strftime("%m-%d")
    d1c = yday.strftime("%Y%m%d")  # 水位"满当日"基准
    fresh_from = yday.strftime("%Y-%m-%d 20:30")

    ti = task_info(TASK)
    ran = bool(
        ti["last_run"]
        and ti["last_run"]
        >= datetime.combine(yday, datetime.min.time()).replace(hour=20, minute=30)
    )

    # 当前 JevFeed 21:00 启动、可能跨 2h；校验时仍在运行 → 轮询等待，
    # 超时降级 ⚠️，绝不能把 267009 当失败。
    still_running = False
    if ran and _is_running(ti):
        state = wait_finish(TASK)
        still_running = state == "Running" or ti["last_result"] == RUNNING_RC
        ti = task_info(TASK)  # 完成后刷新 Last Result
        still_running = still_running or _is_running(ti)

    exit_code, log_ts, log_err = task_log_exit()
    latest, prev = runs_latest()
    xd = xd_stats()
    xd_total = sum(c for c, _ in xd.values())

    wm_new, wm_prev = _wm(latest[4] if latest else ""), _wm(prev[4] if prev else "")
    advanced = (
        any(str(v) > str(wm_prev.get(k, "")) for k, v in wm_new.items())
        if wm_new
        else False
    )
    runs_fresh = bool(latest and latest[0] >= fresh_from)
    ok1 = bool(latest and latest[1] == 1)
    new_count = latest[2] if latest else None
    log_fresh = log_ts >= yday.strftime("%Y-%m-%dT20:30")

    wm_txt = "/".join("%s=%s" % (k, wm_new.get(k, "-")) for k in ("news", "notice"))
    lag = [
        k for k in ("news", "notice") if str(wm_new.get(k, "")) < d1c
    ]  # 未推满到昨日的源
    xd_txt = (
        " ".join(
            "%s=%d(至%s)" % (s, c, (t or "?")[5:10]) for s, (c, t) in sorted(xd.items())
        )
        or "无"
    )
    nums = "exit=%s new=%s 水位%s xd共%d行[%s] 任务rc=%s/%s" % (
        "进行中" if still_running else exit_code,
        new_count,
        wm_txt,
        xd_total,
        xd_txt,
        ti["last_result"],
        (ti["raw"] or "?")[5:16],
    )

    if not ran:
        return (
            "❌",
            "[%s夜检❌] 任务未跑（上次 %s rc=%s）｜%s\n%s"
            % (d1, (ti["raw"] or "?")[:19], ti["last_result"], nums, err_tail()),
            "任务未跑 LastRun=%s rc=%s" % (ti["raw"], ti["last_result"]),
        )
    if still_running:
        return (
            "⚠️",
            "[%s夜检⚠️] 校验时仍在运行(已轮询%d分钟,10-17排水曾至02:16属设计内)"
            "｜%s\n最终exit见C:\\Temp\\jev_feed_task.log尾行"
            % (d1, WAIT_RUNNING_MIN, nums),
            "still_running",
        )
    if ti["last_result"] != 0 or exit_code != 0 or not log_fresh:
        return (
            "❌",
            "[%s夜检❌] exit=%s 任务rc=%s log_ts=%s｜%s\n%s"
            % (d1, exit_code, ti["last_result"], log_ts or log_err, nums, err_tail()),
            "exit=%s rc=%s" % (exit_code, ti["last_result"]),
        )
    if not runs_fresh or not ok1:
        why = tushare_note(now) if (runs_fresh and ok1 is False) else ""
        return (
            "⚠️",
            "[%s夜检⚠️] runs未落账或ok≠1(latest=%s ok=%s)｜%s%s"
            % (
                d1,
                latest[0] if latest else "无",
                latest[1] if latest else "?",
                nums,
                ("\n" + why) if why else "",
            ),
            "runs_latest=%s ok=%s"
            % (latest[0] if latest else "无", latest[1] if latest else "?"),
        )
    if not new_count:
        return (
            "⚠️",
            "[%s夜检⚠️] new_count=0｜%s\n%s" % (d1, nums, tushare_note(now)),
            "new_count=0",
        )
    if not advanced:
        return (
            "⚠️",
            "[%s夜检⚠️] 水位未推进(上轮%s)｜%s" % (d1, wm_prev or "无", nums),
            "wm未推进 %s→%s" % (wm_prev, wm_new),
        )
    note = "（%s未满当日,明晚补齐）" % "/".join(lag) if lag else ""
    return ("✅", "[%s夜检✅] %s%s" % (d1, nums, note), "")


def push(msg):
    gid = push_targets.target_id(GROUP)
    if not gid:
        return False, "no group id"
    ok, why = send_msg(
        json.dumps({"text": msg}, ensure_ascii=False),
        "text",
        gid,
        "chat_id",
        kind=KIND,
        critical=True,
    )
    return ok, why


def main():
    now = datetime.now()
    icon, msg, why = build(now)
    print(msg)
    if why:
        print("reason: " + why)
    ok, why2 = push(msg)
    print("PUSH_%s %s" % ("OK" if ok else "FAIL " + why2, ""))
    append_ascii_log(
        AUDIT_LOG,
        "%s | icon=%s | push=%s | %s"
        % (
            now.strftime("%Y-%m-%dT%H:%M:%S"),
            icon,
            "OK" if ok else "FAIL",
            (why or why2 or "-")[:200],
        ),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
