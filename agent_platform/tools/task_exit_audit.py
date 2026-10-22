"""task_exit_audit.py — 计划任务退出码告警闭环（决策书 D1，P0 新能力）。

背景：全项目审计实测 30 个计划任务 13 个非零退出且零告警闭环——
失败只写 stderr，计划任务不采集输出，任何人都不知情。本工具是
告警闭环的「账本 + 摘要」最小实现：扫描计划任务实态 → 统一 JSONL
账本（append-only）→ 当日摘要 JSON（供 OS 建议引擎与飞书卡片消费）。

设计纪律（判例集）：
- 只读扫描：不 Start/Stop/改任何计划任务（改任务须五要素，本工具不碰）；
- LastTaskResult=0 只证明正常退出不证明有产出（P3-4 判例），摘要单独列
  「零产出嫌疑」提示位，由人核对，不自动判定故障；
- 空态诚实：任务数为零/查询失败时如实报，不产出空摘要冒充健康；
- 账本原子写：tmp → os.replace，防半写损坏；
- 策略性状态不告警：Disabled 任务不进 failures（它没失败，只是没开）。

用法：
    python task_exit_audit.py --scan            # 扫描并落账本+摘要
    python task_exit_audit.py --scan --json     # 额外打印摘要 JSON
    python task_exit_audit.py --summary-only    # 只读已有账本出摘要
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from typing import Any

# 退出码语义表（来自 2026-10-22 全项目审计实测口径）
EXIT_SEMANTICS: dict[int, str] = {
    0: "正常退出",
    1: "一般失败",
    2: "一般错误/文件找不到/degraded",
    267009: "任务正在运行(0x41001)",
    267011: "任务尚未运行(0x41303)",
    267014: "任务被强制终止(0x41306)",
    3221225786: "Ctrl-C 类强杀(0xC000013A)",
}

KNOWN_SCHED_FAMILIES = ("Legion", "Agent", "Quant", "Alert", "Xueqiu", "Hermes",
                        "KgChain", "Space", "KG-", "Ollama", "CnOpenData", "Daily",
                        # 2026-10-22 补：这两个任务当天都被 ExecutionTimeLimit 杀掉
                        # （Event ID 329 实证），却因前缀不在名单而从未入账。
                        "AI工作站门户", "Notice")

# 落盘到 portal/data（与 result_evidence 同域）：nginx 静态直服 /data/task_exit_audit/*，
# OS 首页零后端依赖可消费（D5 地基状态灯数据源）；不走 8091（避免 SYSTEM 进程重启滞后）。
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "portal", "data", "task_exit_audit")
LEDGER_NAME = "task_exit_ledger.jsonl"
SUMMARY_NAME = "task_exit_summary.json"


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _append_ledger(path: str, record: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def query_tasks_via_powershell() -> list[dict[str, Any]]:
    """通过 PowerShell 只读查询计划任务实态。

    不用 schtasks.exe（黑名单判例）；不用 Get-ScheduledTask 的 -TaskName
    参数注入面；输出 JSON 由本进程解析，避免文本列对齐歧义。
    """
    import subprocess

    ps = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Get-ScheduledTask | ForEach-Object {"
        "  $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue;"
        "  $lr = -1;"
        "  if ($null -ne $i.LastTaskResult) { $lr = [int64]$i.LastTaskResult };"
        "  [PSCustomObject]@{"
        "    name=$_.TaskName; state=[string]$_.State;"
        "    last_result=$lr;"
        "    last_run=if($i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''};"
        "    next_run=if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}"
        "  }"
        "} | ConvertTo-Json -Compress"
    )
    # 中文 Windows 下 PowerShell 输出可能是 GBK（实测 byte 0xce 报 UTF-8 解码失败）；
    # 命令内已强制 [Console]::OutputEncoding=UTF8，这里再以 errors=replace 兜底防个别环境回退。
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("gbk", errors="replace")
        raise RuntimeError(f"PowerShell 查询失败 rc={proc.returncode}: {err[:300]}")
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [x for x in data if isinstance(x, dict)]


def scan(audit_family_only: bool = True) -> dict[str, Any]:
    """扫描一次并落账本与摘要。返回摘要。"""
    tasks = query_tasks_via_powershell()
    if audit_family_only:
        tasks = [t for t in tasks
                 if any(str(t.get("name", "")).startswith(f) for f in KNOWN_SCHED_FAMILIES)]

    ledger_path = os.path.join(DATA_DIR, LEDGER_NAME)
    summary_path = os.path.join(DATA_DIR, SUMMARY_NAME)

    failures: list[dict[str, Any]] = []
    ok_count = 0
    disabled_count = 0
    never_run = 0

    for t in tasks:
        name = str(t.get("name", ""))
        code = int(t.get("last_result", -1))
        state = str(t.get("state", ""))
        rec = {
            "at": _now_iso(),
            "name": name,
            "state": state,
            "last_result": code,
            "exit_semantics": EXIT_SEMANTICS.get(code, "未知退出码"),
            "last_run": t.get("last_run", ""),
            "next_run": t.get("next_run", ""),
        }
        _append_ledger(ledger_path, rec)

        if state.lower() == "disabled":
            disabled_count += 1
            continue
        if code == 0:
            ok_count += 1
        elif code == 267011:  # 尚未运行：不是失败
            never_run += 1
        else:
            failures.append(rec)

    summary = {
        "generated_at": _now_iso(),
        "scope": f"军团系计划任务（前缀 {', '.join(KNOWN_SCHED_FAMILIES)}）" if audit_family_only else "全部计划任务",
        "total_scanned": len(tasks),
        "ok": ok_count,
        "failed": len(failures),
        "disabled": disabled_count,
        "never_run": never_run,
        "failures": [
            {"name": f["name"], "last_result": f["last_result"],
             "exit_semantics": f["exit_semantics"], "last_run": f["last_run"],
             "next_run": f["next_run"]}
            for f in failures
        ],
        "note": "LastTaskResult=0 只证明正常退出不证明有产出；产出核对仍需人工或产物探针。"
                "Disabled 任务未计失败（它没失败，只是没开）。账本 append-only 可回放。",
        "ledger": os.path.relpath(ledger_path, os.path.join(DATA_DIR, "..", "..", "..")),
    }
    _atomic_write_json(summary_path, summary)
    return summary


def summary_only() -> dict[str, Any]:
    path = os.path.join(DATA_DIR, SUMMARY_NAME)
    if not os.path.isfile(path):
        return {"error": "尚无摘要文件，请先 --scan", "path": path}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="计划任务退出码告警闭环（只读扫描+账本+摘要）")
    ap.add_argument("--scan", action="store_true", help="执行扫描并落账本与摘要")
    ap.add_argument("--summary-only", action="store_true", help="只读已有摘要")
    ap.add_argument("--all-families", action="store_true", help="扫描全部计划任务（默认仅军团系前缀）")
    ap.add_argument("--json", action="store_true", help="打印摘要 JSON")
    args = ap.parse_args(argv)

    if not args.scan and not args.summary_only:
        ap.print_help()
        return 2

    try:
        result = scan(audit_family_only=not args.all_families) if args.scan else summary_only()
    except RuntimeError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "error" in result:
            print(result["error"])
            return 1
        print(f"扫描 {result['total_scanned']} 任务：正常 {result['ok']} / 失败 {result['failed']} / "
              f"禁用 {result['disabled']} / 未跑 {result['never_run']}")
        for f in result["failures"]:
            print(f"  [FAIL] {f['name']} exit={f['last_result']} ({f['exit_semantics']}) last={f['last_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
