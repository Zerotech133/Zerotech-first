#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周一护航只读预检（escort-premonday-check）。

用途：2026-10-19（周一）门3外部触发观察点 16:30 人工预检，一次看清四类健康面：
  1) 三计划任务（AgentShadowDaily / AgentMondayCheckpoint / AgentChiefPush）存在性、状态、下次运行时间
  2) chief_last_run.log 新鲜度（17:35 后须存在且含 SEND_OK；首跑前缺失属预期 WARN）
  3) 证据仓 evidence.db（mtime + source='strategy.runs' 覆盖行数，只读 sqlite URI 打开）
  4) automation_runs/trigger_precheck/latest.json 新鲜度与 fail 计数

红线：全只读。不 schtasks /run、不改任务、不写 data/、不触发 evidence_collect 实跑。
查询计划任务用 powershell Get-ScheduledTaskInfo（只读），不依赖被安全策略拦截的 schtasks.exe。

用法：
  python tools/escort_premonday_check.py          # 人读逐项表 + PRECHECK 总判
  python tools/escort_premonday_check.py --json   # 机器可读 JSON
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHADOW_DIR = REPO / "agent_platform" / "data" / "shadow"
EVIDENCE_DB = REPO / "agent_platform" / "data" / "evidence" / "evidence.db"
TRIGGER_PRECHECK = REPO / "automation_runs" / "trigger_precheck" / "latest.json"

TASKS = [
    ("AgentShadowDaily", "17:10"),
    ("AgentMondayCheckpoint", "17:25"),
    ("AgentChiefPush", "17:35"),
]
NEVER_RUN_RESULT = 267011  # 0x41303 任务尚未运行（首跑前预期）

_results: list[dict] = []


def add(name: str, status: str, detail: str) -> None:
    _results.append({"check": name, "status": status, "detail": detail})
    mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[status]
    print(f"  {mark} {name}  ... {status} ({detail})")


# ---------------------------------------------------------------- 计划任务
def query_tasks() -> dict[str, dict] | None:
    """只读查询三计划任务；返回 {name: info}，查询失败返回 None。"""
    ps_script = (
        "$out = Get-ScheduledTask -TaskName "
        "@('AgentShadowDaily','AgentMondayCheckpoint','AgentChiefPush') "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "$i = $_ | Get-ScheduledTaskInfo; "
        "[PSCustomObject]@{ name=$_.TaskName; state=[string]$_.State; "
        "next=$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'); "
        "last=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss'); "
        "result=[int64]$i.LastTaskResult } }; "
        "@($out) | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    data = json.loads(proc.stdout)
    if isinstance(data, dict):  # 单对象时归一为数组
        data = [data]
    return {item["name"]: item for item in data}


def check_tasks() -> None:
    infos = query_tasks()
    today_str = date.today().strftime("%Y-%m-%d")
    for name, expect_hm in TASKS:
        label = f"计划任务 {name}"
        if infos is None:
            # 只读兜底：直读 Tasks 注册目录做存在性校验（SOP 手册同款手法）
            registered = Path(r"C:\Windows\System32\Tasks", name).is_file()
            status = "WARN" if registered else "FAIL"
            detail = (
                "无法查询运行信息，注册件存在=注册未失效"
                if registered
                else "注册件缺失且运行信息不可查"
            )
            add(label, status, detail)
            continue
        info = infos.get(name)
        if info is None:
            add(label, "FAIL", "任务未注册")
            continue
        state = info.get("state", "?")
        nxt = info.get("next", "")
        result = info.get("result")
        parts = [f"State={state}", f"NextRun={nxt}"]
        if result == NEVER_RUN_RESULT:
            parts.append("从未运行（首跑前预期）")
        else:
            parts.append(f"LastResult={result}")
        # 状态就绪 + 下次运行落在今天预期时刻
        if state != "Ready":
            add(label, "FAIL", "；".join(parts) + "——任务未就绪，报 lead")
            continue
        # 下次运行须落在预期时刻（HH:MM 一致）且日期为今天或未来（周日预跑时指向周一属正常）
        if nxt.endswith(f"{expect_hm}:00") and nxt[:10] >= today_str:
            add(label, "PASS", "；".join(parts))
        else:
            add(label, "WARN", "；".join(parts) + f"——下次运行与预期 {expect_hm} 不符，人工核对")


# ------------------------------------------------------- chief_last_run.log
def check_chief_log() -> None:
    label = "chief_last_run.log 新鲜度"
    log = SHADOW_DIR / "chief_last_run.log"
    now = datetime.now()
    if not log.is_file():
        # 首跑时点已过且今天是观察日（周一）→ FAIL；其余（周日预跑等）缺失属预期 WARN
        past_first_run = now.hour >= 18 or (now.hour == 17 and now.minute >= 45)
        if now.weekday() == 0 and past_first_run:
            add(label, "FAIL", "周一 17:45 已过但日志不存在——AgentChiefPush 未触发，转异常矩阵 #4")
        else:
            add(label, "WARN", "文件不存在——AgentChiefPush 首跑前属预期；17:35 后须存在且含 SEND_OK")
        return
    mtime = datetime.fromtimestamp(log.stat().st_mtime)
    text = log.read_text(encoding="utf-8", errors="replace")
    if "SEND_OK" in text:
        add(label, "PASS", f"mtime={mtime:%Y-%m-%d %H:%M:%S}，含 SEND_OK")
    elif "SEND_FAIL" in text:
        add(label, "FAIL", f"mtime={mtime:%Y-%m-%d %H:%M:%S}，含 SEND_FAIL——读日志全文取报错行，报 lead")
    else:
        add(label, "WARN", f"mtime={mtime:%Y-%m-%d %H:%M:%S}，无 SEND_OK/SEND_FAIL 标记——人工读日志全文")


# --------------------------------------------------------------- 证据仓
def check_evidence() -> None:
    label = "evidence 仓 strategy.runs 覆盖"
    if not EVIDENCE_DB.is_file():
        add(label, "FAIL", f"{EVIDENCE_DB} 不存在——判据 e 必 FAIL，报 lead")
        return
    mtime = datetime.fromtimestamp(EVIDENCE_DB.stat().st_mtime)
    try:
        conn = sqlite3.connect(f"file:{EVIDENCE_DB.as_posix()}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM evidence WHERE source='strategy.runs'"
            )
            n = cur.fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        add(label, "WARN", f"mtime={mtime:%Y-%m-%d %H:%M:%S}，只读查询失败：{exc}")
        return
    if n > 0:
        add(label, "PASS", f"mtime={mtime:%Y-%m-%d %H:%M:%S}，strategy.runs 证据 {n} 行")
    else:
        add(
            label, "WARN",
            f"mtime={mtime:%Y-%m-%d %H:%M:%S}，strategy.runs 0 行——16:40 证据收编前属预期；"
            "17:10 落账后收编仍为 0 则判据 e 会真实 FAIL",
        )


# --------------------------------------------------- trigger_precheck 产物
def check_trigger_precheck() -> None:
    label = "automation_runs/trigger_precheck"
    if not TRIGGER_PRECHECK.is_file():
        add(label, "WARN", "latest.json 不存在——16:40 AgentTriggerChainPrecheck 触发前属预期；之后仍缺则报 lead")
        return
    mtime = datetime.fromtimestamp(TRIGGER_PRECHECK.stat().st_mtime)
    try:
        data = json.loads(TRIGGER_PRECHECK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        add(label, "WARN", f"latest.json 读取失败：{exc}")
        return
    fail_n = data.get("fail")
    d = data.get("date", "?")
    if fail_n == 0:
        add(label, "PASS", f"date={d}，fail=0（mtime={mtime:%Y-%m-%d %H:%M:%S}）")
    else:
        add(label, "FAIL", f"date={d}，fail={fail_n}——链路未就绪，按 latest.json 指引处置")


def main() -> int:
    print(f"周一护航只读预检  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  仓根: {REPO}")
    print("逐项结果:")
    check_tasks()
    check_chief_log()
    check_evidence()
    check_trigger_precheck()

    counts = {s: sum(1 for r in _results if r["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    verdict = "FAIL" if counts["FAIL"] else "PASS"
    print(f"PRECHECK: {verdict}  ({counts['PASS']} PASS / {counts['WARN']} WARN / {counts['FAIL']} FAIL)")
    if counts["WARN"]:
        print("  注：WARN 需人工按 detail 核对，首跑前多数属预期形态。")

    if "--json" in sys.argv[1:]:
        print(json.dumps(
            {"verdict": verdict, "ts": datetime.now().isoformat(timespec="seconds"),
             "counts": counts, "checks": _results},
            ensure_ascii=False, indent=1,
        ))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
