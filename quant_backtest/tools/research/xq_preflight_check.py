# -*- coding: utf-8 -*-
"""六环 T-2h 只读终检（preflight-dev，2026-10-18）。

纯只读：COM 直读计划任务 State/NextRunTime/触发器/LastTaskResult，校验 wrapper
与被调 py 存在性 + py_compile，产物目录可写，心跳日志新鲜度。绝不启停任务、
不改触发器、不手动跑任何 wrapper。

用法（venv 裸跑）:
  python tools/research/xq_preflight_check.py [--json OUT] [--date YYYY-MM-DD]

退出码: 0=全 PASS, 1=有 FAIL, 2=仅 UNKNOWN（无 FAIL）。
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
import logging

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNS = r"C:\aios\automation_runs"
HEARTBEAT_LOG = os.path.join(RUNS, "xq_classify", "xq_classify_status.log")
DEFAULT_JSON = os.path.join(RUNS, "xq_report", "preflight_2026-10-18.json")

VENV_PY = r"C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

# 任务名 -> (期望 NextRunTime 时分, 是否断言每日触发器)
WATCH_TASK = "XueqiuTimelineWatch"
TASK_SPECS = [
    ("XueqiuCrawl", (18, 0), False),
    ("XueqiuDailyReport", (19, 0), False),
    ("XueqiuReportVerify", (19, 5), False),
    ("RiskGuardShadowDaily", (19, 10), False),
    (WATCH_TASK, (19, 35), True),
    ("XueqiuBackfillSprint", (13, 30), False),  # 明日 13:30，偏移 +1 天
]

# wrapper -> 被调 py（相对 REPO；绝对路径直接用）
# zerobound-kg 内文件只查存在性，不 py_compile（该目录不在本工位写权内）
SCRIPT_MAP = [
    (
        os.path.join(REPO, "start_daily_report.ps1"),
        [os.path.join(REPO, "xq_daily_report.py")],
        True,
    ),
    (
        os.path.join(REPO, "start_verify_report.ps1"),
        [os.path.join(REPO, "tools", "verify_daily_report.py")],
        True,
    ),
    (
        os.path.join(REPO, "start_timeline_watch.ps1"),
        [os.path.join(REPO, "tools", "xq_timeline_watch.py")],
        True,
    ),
    (
        os.path.join(REPO, "start_backfill_sprint.ps1"),
        [os.path.join(REPO, "tools", "backfill_sprint.py")],
        True,
    ),
    (
        r"C:\aios\quant_backtest\start_xueqiu_crawl.ps1",
        [r"C:\aios\quant_backtest\xueqiu_crawl.py"],
        True,
    ),
    (
        r"C:\aios\zerobound-kg\riskguard_shadow_daily.ps1",
        [
            r"C:\aios\zerobound-kg\kg_riskguard.py",
            r"C:\aios\zerobound-kg\kg_fourstate.py",
        ],
        False,
    ),
    (
        os.path.join(REPO, "xq_classify.py"),
        [],
        True,
    ),  # 只读+py_compile，内容写权在 xq-quality-dev
]

PRODUCT_DIRS = [
    os.path.join(RUNS, "xq_report", "daily"),
    os.path.join(RUNS, "xq_timeline"),
    os.path.join(RUNS, "xq_classify"),
]

TASK_TRIGGER_TIME = 1  # 一次性
TASK_TRIGGER_DAILY = 2  # 每日
SCHED_HAS_NOT_RUN = 267011  # 0x41303

# 任务 XML 触发器节点 -> 形态名
_XML_TRIGGER_KINDS = [
    ("ScheduleByDay", "daily"),
    ("ScheduleByWeek", "weekly"),
    ("ScheduleByMonth", "monthly"),
    ("TimeTrigger", "once"),
    ("LogonTrigger", "logon"),
    ("BootTrigger", "boot"),
]


def trigger_kinds_from_xml(xml):
    """从任务 XML 提取触发器形态列表。COM 的 ITrigger.TriggerType 在部分
    pywin32 动态派发下不可读，改走 XML 判定（权威）。"""
    if not xml:
        return []
    kinds = []
    for m in re.finditer(r"<(\w+Trigger)[ >].*?</\1>", xml, re.S):
        block = m.group(0)
        kind = (
            "once"
            if m.group(1) == "TimeTrigger"
            else m.group(1).replace("Trigger", "").lower()
        )
        for marker, name in _XML_TRIGGER_KINDS:
            if marker in block:
                kind = name
                break
        kinds.append(kind)
    return kinds


# ---------------- 纯判定逻辑（可注入假数据单测） ----------------


def parse_sched_time(s):
    """解析 COM NextRunTime 字符串。COM 给的是本地时间但可能带误导性 '+00:00'
    尾巴，故取 naive 部分。支持 'YYYY-MM-DD HH:MM:SS[+tz]' 与 ISO 'T' 分隔。"""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})", s.strip())
    if not m:
        return None
    try:
        return datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def judge_next_run(actual_str, expected, tolerance_min=0):
    """NextRunTime 容差比对。actual_str 为 COM 原始字符串。"""
    actual = parse_sched_time(actual_str)
    if actual is None:
        return {
            "status": "UNKNOWN",
            "detail": "NextRunTime 不可解析: %r" % (actual_str,),
        }
    delta = abs((actual - expected).total_seconds()) / 60.0
    if delta <= tolerance_min:
        return {
            "status": "PASS",
            "detail": "NextRunTime=%s 期望=%s" % (actual, expected),
        }
    return {
        "status": "FAIL",
        "detail": "NextRunTime=%s 期望=%s (偏差 %.0f 分钟)" % (actual, expected, delta),
    }


def judge_last_result(code):
    """LastTaskResult: 0=PASS；0x41303(尚未运行)=PASS（首跑前正常形态）；其余 FAIL 只报不修。"""
    if code == 0:
        return {"status": "PASS", "detail": "LastTaskResult=0"}
    if code == SCHED_HAS_NOT_RUN:
        return {
            "status": "PASS",
            "detail": "LastTaskResult=0x41303 (任务尚未运行过, 首跑前正常)",
        }
    return {
        "status": "FAIL",
        "detail": "LastTaskResult=0x%X (%d) 非零, 只报不修" % (code, code),
    }


def judge_watch_form(trigger_kind, next_run_str, expected):
    """守望任务每日化形态：每日触发器 且 NextRunTime=今日 19:35。
    trigger_kind 双口径：XML 形态名 "daily" 或 COM TASK_TRIGGER_DAILY(2)。"""
    nxt = judge_next_run(next_run_str, expected)
    if trigger_kind not in ("daily", TASK_TRIGGER_DAILY):
        return {
            "status": "FAIL",
            "detail": "触发器非每日(kind=%s), 每日化改动形态不达标; %s"
            % (trigger_kind, nxt["detail"]),
        }
    if nxt["status"] != "PASS":
        return {"status": "FAIL", "detail": "每日触发器达标但 " + nxt["detail"]}
    return {
        "status": "PASS",
        "detail": "每日触发器(ScheduleByDay/DaysInterval=1)且 " + nxt["detail"],
    }


_FAIL_MARKERS = ("RETRY-HARD-FAIL", "FAIL")
_HARD_FAIL_RE = re.compile(r"hard_fail=([1-9]\d*)")


def judge_heartbeat(last_line, mtime, now, stale_minutes=30):
    """心跳面：mtime 新鲜且末行无 FAIL 标记=PASS；停滞且末行含 RETRY-HARD-FAIL
    或停滞超时=UNKNOWN（注明 16:00 班待观察）；新鲜但有 FAIL 标记=FAIL。"""
    if mtime is None:
        return {"status": "UNKNOWN", "detail": "心跳日志不可读"}
    age_min = (now - mtime).total_seconds() / 60.0
    fresh = age_min <= stale_minutes
    if fresh:
        if "RETRY-HARD-FAIL" in (last_line or "") or _HARD_FAIL_RE.search(
            last_line or ""
        ):
            return {
                "status": "FAIL",
                "detail": "末行含 FAIL 标记: %s" % last_line.strip()[:120],
            }
        return {
            "status": "PASS",
            "detail": "mtime=%s ( %.0f 分钟前), 末行: %s"
            % (mtime, age_min, (last_line or "").strip()[:100]),
        }
    if "RETRY-HARD-FAIL" in (last_line or ""):
        return {
            "status": "UNKNOWN",
            "detail": "停在 RETRY-HARD-FAIL (mtime %s, %.0f 分钟前)——16:00 班待观察"
            % (mtime, age_min),
        }
    return {
        "status": "UNKNOWN",
        "detail": "心跳停滞 mtime=%s (%.0f 分钟前)——16:00 班待观察" % (mtime, age_min),
    }


# ---------------- 只读收集 ----------------


def collect_schedule(today, tolerance_min=0):
    """COM 直读任务定义与状态，逐项判定。"""
    items = []
    try:
        import win32com.client
    except ImportError:
        return [{"item": "调度面", "status": "UNKNOWN", "detail": "pywin32 不可用"}]
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        root = sched.GetFolder("\\")
    except Exception as exc:  # noqa: BLE001
        return [
            {"item": "调度面", "status": "UNKNOWN", "detail": "COM 连接失败: %s" % exc}
        ]

    for name, (hh, mm), is_watch in TASK_SPECS:
        expected = datetime(today.year, today.month, today.day, hh, mm)
        if name == "XueqiuBackfillSprint":
            expected += timedelta(days=1)  # 明日 13:30 首跑（每日化）
        row = {"item": "task:%s" % name}
        try:
            task = root.GetTask(name)
            row["detail"] = "State=%s enabled=%s" % (task.State, task.Enabled)
            if not task.Enabled:
                row["status"] = "FAIL"
                row["detail"] += "; 任务被禁用"
            else:
                nr = judge_next_run(str(task.NextRunTime), expected, tolerance_min)
                lr = judge_last_result(task.LastTaskResult)
                if is_watch:
                    kinds = trigger_kinds_from_xml(task.XML)
                    kind = kinds[0] if kinds else None
                    if kind is None:
                        # XML 判定失败时回退 COM tr.Type（int，实测 pywin32 动态派发可读）
                        try:
                            trigs = list(task.Definition.Triggers)
                            if trigs:
                                kind = trigs[0].Type
                        except Exception:  # noqa: BLE001
                            kind = None
                    form = judge_watch_form(kind, str(task.NextRunTime), expected)
                    row["status"] = form["status"]
                    row["detail"] += "; 触发器数=%d 形态=%s; %s; %s" % (
                        len(kinds),
                        kinds,
                        form["detail"],
                        lr["detail"],
                    )
                else:
                    kinds = trigger_kinds_from_xml(task.XML)
                    row["status"] = (
                        "PASS"
                        if (nr["status"] == "PASS" and lr["status"] == "PASS")
                        else (
                            "FAIL"
                            if "FAIL" in (nr["status"], lr["status"])
                            else "UNKNOWN"
                        )
                    )
                    row["detail"] += "; 触发器数=%d 形态=%s; %s; %s" % (
                        len(kinds),
                        kinds,
                        nr["detail"],
                        lr["detail"],
                    )
        except Exception as exc:  # noqa: BLE001
            row["status"] = "UNKNOWN"
            row["detail"] = "读取失败: %s" % exc
        items.append(row)
    return items


def collect_scripts():
    """wrapper 与被调 py 存在性 + py_compile（cfile 落临时目录，不写源目录）。"""
    items = []
    for wrapper, pys, do_compile in SCRIPT_MAP:
        row = {"item": "script:%s" % os.path.basename(wrapper)}
        if not os.path.isfile(wrapper):
            row["status"] = "FAIL"
            row["detail"] = "wrapper 缺失: %s" % wrapper
            items.append(row)
            continue
        ok, bad = [], []
        for py in pys:
            if not os.path.isfile(py):
                bad.append("%s 缺失" % os.path.basename(py))
                continue
            if not do_compile:
                ok.append("%s 存在(只查不编译)" % os.path.basename(py))
                continue
            try:
                import py_compile

                fd, cfile = tempfile.mkstemp(suffix=".pyc")
                os.close(fd)
                try:
                    py_compile.compile(py, cfile=cfile, doraise=True)
                    ok.append("%s py_compile 通过" % os.path.basename(py))
                finally:
                    try:
                        os.remove(cfile)
                    except OSError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/research/xq_preflight_check.py", exc)
            except Exception as exc:  # noqa: BLE001
                bad.append("%s py_compile 失败: %s" % (os.path.basename(py), exc))
        row["status"] = "FAIL" if bad else "PASS"
        row["detail"] = "; ".join(ok + bad)
        items.append(row)
    return items


def collect_products(now):
    items = []
    for d in PRODUCT_DIRS:
        row = {"item": "product:%s" % os.path.relpath(d, RUNS)}
        if not os.path.isdir(d):
            row["status"] = "FAIL"
            row["detail"] = "目录不存在: %s" % d
        elif not os.access(d, os.W_OK):
            row["status"] = "FAIL"
            row["detail"] = "目录不可写: %s" % d
        else:
            row["status"] = "PASS"
            row["detail"] = "存在且可写"
        items.append(row)
    return items


def collect_heartbeat(now):
    last_line, mtime = None, None
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(HEARTBEAT_LOG))
        with open(HEARTBEAT_LOG, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        last_line = lines[-1] if lines else ""
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/research/xq_preflight_check.py", exc)
    row = judge_heartbeat(last_line, mtime, now)
    row["item"] = "heartbeat:xq_classify_status.log"
    return [row]


def main(argv=None):
    ap = argparse.ArgumentParser(description="六环 T-2h 只读终检")
    ap.add_argument("--json", default=DEFAULT_JSON, help="结果 JSON 落盘路径")
    ap.add_argument(
        "--date", default=None, help="以该日期为「今日」(YYYY-MM-DD)，默认系统当天"
    )
    ap.add_argument(
        "--tolerance-min", type=int, default=0, help="NextRunTime 容差分钟数"
    )
    args = ap.parse_args(argv)

    if args.date:
        today = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        today = datetime.now().date()
    now = datetime.now()

    sections = {
        "schedule": collect_schedule(today, args.tolerance_min),
        "scripts": collect_scripts(),
        "products": collect_products(now),
        "heartbeat": collect_heartbeat(now),
    }
    all_items = [it for its in sections.values() for it in its]
    n_pass = sum(1 for it in all_items if it["status"] == "PASS")
    n_fail = sum(1 for it in all_items if it["status"] == "FAIL")
    n_unknown = sum(1 for it in all_items if it["status"] == "UNKNOWN")

    report = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "for_date": today.isoformat(),
        "mode": "read-only preflight (T-2h)",
        "summary": {"pass": n_pass, "fail": n_fail, "unknown": n_unknown},
        "sections": sections,
    }
    try:
        os.makedirs(os.path.dirname(args.json), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        json_written = args.json
    except OSError as exc:
        json_written = "落盘失败: %s" % exc

    print("== 六环 T-2h 只读终检 (for %s) ==" % today)
    for sec, its in sections.items():
        print("-- %s" % sec)
        for it in its:
            print("  [%-7s] %-42s %s" % (it["status"], it["item"], it["detail"]))
    print(
        "== 汇总: PASS=%d FAIL=%d UNKNOWN=%d | JSON: %s =="
        % (n_pass, n_fail, n_unknown, json_written)
    )
    if n_fail:
        return 1
    return 2 if n_unknown else 0


if __name__ == "__main__":
    sys.exit(main())
