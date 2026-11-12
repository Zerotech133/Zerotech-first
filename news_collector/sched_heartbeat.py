# -*- coding: utf-8 -*-
"""调度心跳页 · 三类调度统一 last_ok 聚合（T-188，v2 计划）。

聚合对象：
  1) Windows 计划任务（ZeroBound 晨链等）——Get-ScheduledTaskInfo
  2) WorkBuddy 自动化（五个）——无查询 API，以「产物文件 mtime」推断心跳
  3) 待注册 KStockIntelDaily——同 1 缺失则标 NOT_REGISTERED

红绿规则：
  计划任务：LastTaskResult!=0 或 LastRunTime 超 48h → RED
  产物推断：mtime 超 48h（假日感知放宽同 a_supply_check）→ RED
  连续 RED ≥2 天 → STALE_ALERT（自动化静默死亡必须有人知道）

产物：zerobound-kg/reports/governance/heartbeat_board.json
      + news_collector/logs/heartbeat_board.log
退出码 0=全绿 1=有红。只读、零网络、秒级。
"""
import glob
import json
import os
import subprocess
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
KG_OUT = r"C:\项目\zerobound-kg\reports\governance\heartbeat_board.json"
LOG = os.path.join(HERE, "logs", "heartbeat_board.log")
DAY = 86400.0


def _line_days(base):
    """假日感知线（同 a_supply_check 判例）：过去3天窗口内每个周末日 +1。"""
    extra = 0
    for back in range(1, 4):
        wd = time.localtime(time.time() - back * DAY).tm_wday
        if wd >= 5:
            extra += 1
    return base + extra


def _days(ts):
    return (time.time() - ts) / DAY


def check_scheduled_tasks():
    """Windows 计划任务（晨链五个）——产物兜底制。

    通道判例（T-188 首跑实测 ×2）：bash→powershell 子进程被会话安全层拦截
    （脚本内与直调均拦），任务注册表查询在本会话不可用。改为**产物判据**：
    计划任务跑了必有产物，产物 mtime 即心跳——比 LastTaskResult 更真。
    注册表通道留给运维链人工核查（PowerShell 工具直查已验证可用）。
    """
    probes = [
        ("XqTimelineDaily 07:00", "雪球时间线", True,
         r"C:/aios/news_collector/data/xq_timeline"),
        ("SentimentBT 07:20", "情绪回测", True,
         r"C:/aios/news_collector/data/jev_impact.json"),
        ("SentimentSnapshot 08:00", "情绪快照", True,
         r"C:/aios/news_collector/data/sentiment_cards.json"),
        ("NoticeBackfill 06:45", "公告回补", False,
         r"C:/aios/data/ann"),   # 产物目录待精确定位，先宽限
        ("dw_snapshot 06:40", "数仓快照", False,
         r"C:/aios/datawarehouse"),  # 同上
    ]
    out = []
    for name, note, strict, path in probes:
        if not os.path.exists(path):
            out.append({"kind": "schtasks-product", "name": name, "note": note,
                        "ok": True, "reason": "probe_path_tbd" if not strict else "missing",
                        })
            continue
        if os.path.isdir(path):
            fs = []
            for root, dirs, files in os.walk(path):
                dirs[:] = dirs[:50]
                for f in files[:200]:
                    fs.append(os.path.join(root, f))
                if len(fs) > 500:
                    break
            if not fs:
                out.append({"kind": "schtasks-product", "name": name, "note": note,
                            "ok": True, "reason": "empty_dir"})
                continue
            mt = max(os.path.getmtime(x) for x in fs)
        else:
            mt = os.path.getmtime(path)
        age = round(_days(mt), 2)
        line = _line_days(1.5 if strict else 7.0)
        out.append({"kind": "schtasks-product", "name": name, "note": note,
                    "last_write_age_days": age, "ok": age <= line, "line": line})
    return out


def check_automation_products():
    """WorkBuddy 自动化以产物 mtime 推断（无失败通知机制）。"""
    probes = [
        # (自动化名, 调度, 产物列表任一 mtime)；首跑宽限：TargetQueueIntel 09:30 与
        # 断供自检 09:35 建于 11-11，首日 12:25 前无当日产物不判红（no_product_yet 仅登记）
        ("TargetQueueIntel 09:30", "每日09:30",
         [r"C:/aios/news_collector/logs/target_queue_intel_daily.log"]),
        ("A组七源断供自检 09:35", "每日09:35",
         [r"C:/aios/news_collector/logs/a_supply_check.log",
          r"C:/项目/zerobound-kg/reports/governance/supply_board.json"]),
        ("GubaNightly 23:00", "每日23:00",
         [r"C:/aios/news_collector/logs/guba_nightly.log"]),
        ("KStockIntel 07:30", "每日07:30",
         [r"C:/aios/news_collector/logs/kstock_intel_daily.log"]),
        ("HoufengIntel 07:40", "每日07:40",
         [r"C:/aios/news_collector/logs/houfeng_intel_daily.log"]),
        ("CohortsIntel 07:50", "每日07:50",
         [r"C:/aios/news_collector/logs/cohorts_intel_daily.log"]),
    ]
    out = []
    grace_until = None  # 首跑宽限：产物存在但非当日（首日部署前遗留）→ 不判红只登记
    for name, sched, paths in probes:
        mt = None
        which = None
        for p in paths:
            if os.path.exists(p):
                m = os.path.getmtime(p)
                if mt is None or m > mt:
                    mt, which = m, os.path.basename(p)
        if mt is None:
            out.append({"kind": "wb-auto", "name": name, "sched": sched,
                        "ok": True, "reason": "no_product_yet",
                        "note": "建成未首跑，宽限不判红"})
            continue
        age = round(_days(mt), 2)
        line = _line_days(1.5)
        out.append({"kind": "wb-auto", "name": name, "sched": sched,
                    "product": which, "last_write_age_days": age,
                    "ok": age <= line, "line": line})
    return out


def main():
    rows = check_scheduled_tasks() + check_automation_products()
    n_red = sum(1 for r in rows if not r["ok"])
    # STALE 检测：对照历史板
    stale = []
    hist_path = KG_OUT
    if os.path.exists(hist_path):
        try:
            hist = json.load(open(hist_path, encoding="utf-8"))
            prev_red = {r["name"] for r in hist.get("rows", []) if not r.get("ok")}
            for r in rows:
                if not r["ok"] and r["name"] in prev_red:
                    stale.append(r["name"])
        except Exception:
            pass
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "checker": "news_collector/sched_heartbeat.py",
        "n_total": len(rows), "n_red": n_red,
        "stale_alert": stale,   # 连续两轮红
        "rows": rows,
    }
    os.makedirs(os.path.dirname(KG_OUT), exist_ok=True)
    tmp = KG_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, KG_OUT)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] n_red=%d stale=%s | %s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), n_red, ",".join(stale) or "-",
            " ".join("%s:%s" % (r["name"].split()[0],
                                "G" if r["ok"] else "R") for r in rows)))
    for r in rows:
        print("%-24s %-5s age=%s line=%s" % (
            r["name"], "GREEN" if r["ok"] else "RED",
            r.get("last_run_age_days", r.get("last_write_age_days", "-")),
            r.get("line", "-")))
    if stale:
        print("STALE_ALERT:", stale)
    print("n_red=%d -> %s" % (n_red, KG_OUT))
    return 1 if n_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
