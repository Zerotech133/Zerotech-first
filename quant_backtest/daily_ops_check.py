# -*- coding: utf-8 -*-
"""daily_ops_check.py — 每日晨检（05:30）：任务/数据/告警三域巡检 + 飞书报告

巡检域（按责任域标注）：
  [数据同步] KgDailySync/QuantMetaSync/TdxSync_* 昨日结果 + kg/news 数据新鲜度
  [舆情采集] GubaCrawl/_B/_C、NewsCollect 昨日结果 + guba 目录新鲜度
  [量化研究] DkTailScan/DkTailWatch、RpsDaily、StrongEod 等结果
  [告警审计] 昨日 alert_send.log 全量（哪些失败真实发生、谁被抑制）

判级：
  P0 立即处理：核心链失败（KgDailySync fail）或核心数据 stale（news/kg 超 48h）
  P1 当日处理：次级任务失败、同任务连续 2 天失败
  P2 观察：单次失败、已被去重抑制的重复告警

发送：全部正常 → 静默（只落 status log）；有任何 P0/P1 → 飞书 P0 头部 + 责任域分节
      全 P2 或纯观察 → 飞书简报（daily:report 流水，不打扰式）
失败兜底：巡检器自身异常 → 飞书 P0（本文件不可用则 status log 兜底）
状态日志：logs/daily_ops_check_status.log（ASCII）
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))

from send_feishu import send_text  # noqa: E402

STATUS_LOG = os.path.join(HERE, "logs", "daily_ops_check_status.log")
ALERT_SEND_LOG = os.path.join(os.path.dirname(HERE), "news_collector",
                              "logs", "alert_send.log")
DATA_DIR = os.path.join(HERE, "data")

# 任务 → 责任域映射（P0 核心 / P1 次级）
TASKS = [
    # (name, domain, core)
    ("KgDailySync", "数据同步", True),
    ("QuantMetaSync", "数据同步", False),
    ("TdxSync_A_高频", "数据同步", False),
    ("TdxSync_B_财务", "数据同步", False),
    ("TdxSync_C_低频", "数据同步", False),
    ("TdxSync_D_缺口", "数据同步", False),
    ("NewsCollect", "舆情采集", True),
    ("GubaCrawl", "舆情采集", False),
    ("GubaCrawl_B", "舆情采集", False),
    ("GubaCrawl_C", "舆情采集", False),
    ("XueqiuSeedAuto", "舆情采集", False),
    ("DkTailScan", "量化研究", False),
    ("DkTailWatch", "量化研究", False),
    ("RpsDaily", "量化研究", False),
    ("StrongEod", "量化研究", False),
]
# 数据新鲜度：(path, 域, 描述, max_age_h)
DATA_FRESH = [
    (os.path.join(DATA_DIR, "semantica", "events.jsonl"), "数据同步", "事件图谱jsonl", 48),
    (os.path.join(DATA_DIR, "guba"), "舆情采集", "guba目录(mtime最新)", 30),
]
HOURS_STALE_D1 = 48  # 连续 2 天失败升级 P1


def log_status(msg):
    os.makedirs(os.path.dirname(STATUS_LOG), exist_ok=True)
    with open(STATUS_LOG, "a", encoding="ascii", errors="replace") as f:
        f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), msg))


def query_task(tname):
    """Get-ScheduledTaskInfo → (last_run:dt|None, last_rc:int|None)。"""
    ps = (
        "$i = Get-ScheduledTask -TaskName '%s' | Get-ScheduledTaskInfo; "
        "'LR=' + $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') + ' RC=' + $i.LastTaskResult"
    ) % tname
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return None, None
    m = re.search(r"LR=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) RC=(-?\d+)", out)
    if not m:
        return None, None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), int(m.group(2))
    except ValueError:
        return None, None


def check_tasks(now):
    """→ (findings:[(域,级,task,desc)], ok_n, fail_map task->rc)"""
    findings, fails = [], {}
    ok = 0
    for name, dom, core in TASKS:
        lr, rc = query_task(name)
        if lr is None:
            findings.append((dom, "P1", name, "查询失败（任务缺失？）"))
            continue
        # 只看昨日 00:00 至今的运行（05:30 晨检覆盖昨日全天）
        since = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if lr.year < 2000:
            findings.append((dom, "P2", name, "从未运行（等首次触发 %s）"
                             % "新任务" if core is False else "从未运行（核心任务缺失！）"))
            if core:
                findings[-1] = (dom, "P1", name, "核心任务从未运行！")
            continue
        if lr < since:
            findings.append((dom, "P1" if core else "P2", name,
                             "近24h未运行（last=%s）" % lr.strftime("%m-%d %H:%M")))
            continue
        if rc == 0:
            ok += 1
        elif rc == 267009:  # 0x41301 running
            findings.append((dom, "P2", name, "仍在运行中（05:30 未结束）"))
        elif rc == 267011:  # 0x41306 terminated（非失败桶，项目记忆口径）
            findings.append((dom, "P1" if core else "P2", name, "被终止 0x41306（超时/睡眠）"))
        else:
            fails[name] = rc
            findings.append((dom, "P0" if core else "P1", name, "失败 rc=%d" % rc))
    return findings, ok, fails


def check_data(now):
    findings = []
    for path, dom, desc, maxh in DATA_FRESH:
        try:
            if os.path.isdir(path):
                mt = max(os.path.getmtime(os.path.join(path, f))
                         for f in os.listdir(path)) if os.listdir(path) else 0
            else:
                mt = os.path.getmtime(path)
        except OSError:
            findings.append((dom, "P1", desc, "路径不可读"))
            continue
        age_h = (now.timestamp() - mt) / 3600
        if age_h > maxh:
            findings.append((dom, "P0", desc, "数据stale %.1fh（阈 %dh）" % (age_h, maxh)))
    return findings


def check_alerts(ydate):
    """昨日 alert_send.log → [(task, rc, 抑制?)]。"""
    if not os.path.exists(ALERT_SEND_LOG):
        return []
    ystr = ydate.strftime("%Y-%m-%d")
    out = []
    try:
        with open(ALERT_SEND_LOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if not ln.startswith(ystr):
                    continue
                m = re.match(r"^(\S+ \S+) \| task=(\S+) .*task_rc=(\d+)", ln)
                if m:
                    supp = "suppressed" in ln
                    out.append((m.group(2), int(m.group(3)), supp))
    except OSError:
        pass
    return out


def build_report(findings, ok, alerts, now):
    p0 = [f for f in findings if f[1] == "P0"]
    p1 = [f for f in findings if f[1] == "P1"]
    p2 = [f for f in findings if f[1] == "P2"]
    if not p0 and not p1 and not p2:
        return None  # 全绿静默
    head = "📋 每日晨检 %s" % now.strftime("%Y-%m-%d %H:%M")
    if p0:
        head += " 【P0 立即处理】"
    lines = [head, "正常任务 %d/%d" % (ok, len(TASKS)), ""]
    for lvl, tag in (("P0", "🔴 P0 立即处理"), ("P1", "🟠 P1 当日处理"), ("P2", "⚪ P2 观察")):
        items = [f for f in findings if f[1] == lvl]
        if not items:
            continue
        lines.append(tag)
        bydom = {}
        for dom, _, task, desc in items:
            bydom.setdefault(dom, []).append("  %s：%s" % (task, desc))
        for dom in sorted(bydom):
            lines.append("【%s】" % dom)
            lines.extend(bydom[dom])
        lines.append("")
    if alerts:
        sup_n = sum(1 for _, _, s in alerts if s)
        lines.append("昨日告警 %d 条（去重抑制 %d）" % (len(alerts), sup_n))
    return "\n".join(lines)


def main():
    now = datetime.now()
    ydate = (now - timedelta(days=1)).date()
    try:
        tf, ok, fails = check_tasks(now)
        df = check_data(now)
        af = check_alerts(ydate)
        findings = tf + df
        report = build_report(findings, ok, af, now)
        if report:
            # 审计副本（本地留痕，与飞书同文）
            rp = os.path.join(HERE, "reports", "daily_ops_check_last.txt")
            os.makedirs(os.path.dirname(rp), exist_ok=True)
            with open(rp, "w", encoding="utf-8") as f:
                f.write(report)
            r = send_text(report, kind="daily:opscheck")
            log_status("sent ok=%d findings=%d p0=%d send=%s"
                       % (ok, len(findings), sum(1 for f in findings if f[1] == "P0"),
                          "OK" if r else "FAIL"))
        else:
            log_status("all green ok=%d findings=0" % ok)
    except Exception as e:
        log_status("checker error: %r" % e)
        try:
            send_text("🔴 晨检器自身故障：%r\n（daily_ops_check 异常，需人工查看 %s）"
                      % (e, STATUS_LOG), kind="daily:opscheck")
        except Exception:
            pass  # status log 已留痕
        sys.exit(1)


if __name__ == "__main__":
    main()
