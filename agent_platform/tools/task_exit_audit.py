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
    # 2026-10-22 新增：脚本**主动拒跑**，因为上游证据不足（不是它自己坏了）。
    # 缘起 AlertWatch 连续 4 天 rc=1——它每次都正确拒绝基于"无法证明完成"的
    # 上游数据报警，但账本把这种"正确拒跑"和"脚本崩溃"记成同一个 1，
    # 于是静默 4 天没人分得出来。约定：exit 3 = 上游不可信，不计失败、单列。
    3: "上游不可信(主动拒跑，非故障)",
    267009: "任务正在运行(0x41001)",
    267011: "任务尚未运行(0x41303)",
    267014: "任务被强制终止(0x41306)",
    # 2026-10-22 实测：AlertWatch 在 15:45 / 16:16 两轮都返回这个码。
    # 0x80070003 = ERROR_PATH_NOT_FOUND，含义是**任务的动作根本没起来**
    # （进程创建阶段就失败），不是脚本内部的错。常见成因：S4U 登录会话
    # 读不到 action 的 WorkingDirectory / 可执行文件（本例两者都在 junction
    # 路径 C:\aios\... 下）。它与 rc=1（脚本跑了但失败）性质完全不同，
    # 必须能分开看，否则会被误当成"业务逻辑报错"。
    2147942403: "进程未能启动(0x80070003 路径不存在)",
    # 2026-10-25 EXEC-R7（B3 口径修正）：它不是"业务失败"，单列不按 failure 计。
    # 0x800710E0 = HRESULT_FROM_WIN32(4320)=ERROR_REQUEST_REFUSED「启动请求被拒」：
    # 任务 MultipleInstances=IgnoreNew 时实例已在跑，再次 Start 即被拒——设计语义
    # ="启动器已在运行/已存在"，不代表服务死亡（N1 曾据它误判 instance hung）。
    2147946720: "启动器已在运行/请求被拒(0x800710E0，非失败)",
    # 0xC000013A = STATUS_CONTROL_C_EXIT：任务被 Cancel/强杀（含关机/注销打断、
    # ExecutionTimeLimit 强杀），属"被取消"而非服务失败。
    3221225786: "任务被取消/强杀(0xC000013A，非服务失败)",
}

# exit 3 的语义：拒跑不是故障。单列出来，既不进 failures，也不假装成功。
UPSTREAM_UNTRUSTED_EXIT = 3
# 2026-10-25 EXEC-R7：新增两类"非失败"退出码（只记账、单列，不进 failures）。
#   2147946720 = 0x800710E0 启动器已在运行/启动请求被拒
#   3221225786 = 0xC000013A 任务被取消/强杀
LAUNCHER_REFUSED_EXIT = 2147946720
CANCELLED_EXIT = 3221225786
# 窗口对比（window_diff）里也按同一口径排除：非失败码不参与"新增/持续失败"差集。
NON_FAILURE_RC = frozenset({0, 267011, UPSTREAM_UNTRUSTED_EXIT,
                            LAUNCHER_REFUSED_EXIT, CANCELLED_EXIT})

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
    upstream_hold: list[dict[str, Any]] = []
    launcher_refused: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
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
        elif code == UPSTREAM_UNTRUSTED_EXIT:  # 主动拒跑：不是失败，单独记账
            upstream_hold.append(rec)
        elif code == LAUNCHER_REFUSED_EXIT:  # 0x800710E0 启动器已在运行/被拒
            launcher_refused.append(rec)
        elif code == CANCELLED_EXIT:  # 0xC000013A 任务被取消/强杀
            cancelled.append(rec)
        else:
            failures.append(rec)

    summary = {
        "generated_at": _now_iso(),
        "scope": f"军团系计划任务（前缀 {', '.join(KNOWN_SCHED_FAMILIES)}）" if audit_family_only else "全部计划任务",
        "total_scanned": len(tasks),
        "ok": ok_count,
        "failed": len(failures),
        "upstream_hold": len(upstream_hold),
        "launcher_refused": len(launcher_refused),
        "cancelled": len(cancelled),
        "disabled": disabled_count,
        "never_run": never_run,
        "failures": [
            {"name": f["name"], "last_result": f["last_result"],
             "exit_semantics": f["exit_semantics"], "last_run": f["last_run"],
             "next_run": f["next_run"]}
            for f in failures
        ],
        "upstream": [
            {"name": f["name"], "last_result": f["last_result"],
             "exit_semantics": f["exit_semantics"], "last_run": f["last_run"]}
            for f in upstream_hold
        ],
        "launcher_refused_tasks": [
            {"name": f["name"], "last_result": f["last_result"],
             "exit_semantics": f["exit_semantics"], "last_run": f["last_run"]}
            for f in launcher_refused
        ],
        "cancelled_tasks": [
            {"name": f["name"], "last_result": f["last_result"],
             "exit_semantics": f["exit_semantics"], "last_run": f["last_run"]}
            for f in cancelled
        ],
        "note": "LastTaskResult=0 只证明正常退出不证明有产出；产出核对仍需人工或产物探针。"
                "Disabled 任务未计失败（它没失败，只是没开）。"
                "exit 3 = 脚本主动拒跑（上游证据不足），不计失败、单列 upstream；"
                "它的处置方向在数据管线，不在脚本本身。"
                "0x800710E0 = 启动器已在运行/启动请求被拒（IgnoreNew），单列 launcher_refused；"
                "0xC000013A = 任务被取消/强杀，单列 cancelled——两者均非服务失败、不计 failed。"
                "账本 append-only 可回放。",
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


def window_diff(hours: float = 24.0) -> dict[str, Any]:
    """基于账本历史做窗口对比：新增失败 / 持续失败 / 已修复。

    P3-2（2026-10-22 审计「快照→窗口统计」）：快照只答「现在几个失败」，
    窗口对比才能答「变好还是变坏」。取窗口起点前后各一次扫描的失败集合
    做差集；账本不足两次扫描时如实报 insufficient，不硬算。
    """
    ledger_path = os.path.join(DATA_DIR, LEDGER_NAME)
    if not os.path.isfile(ledger_path):
        return {"error": "账本不存在，请先 --scan"}
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(hours=hours)
    scans: list[tuple[dt.datetime, dict[str, int]]] = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = rec.get("at", "")
            try:
                ts = dt.datetime.fromisoformat(at)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.astimezone()
            scans.append((ts, rec))
    if len(scans) < 2:
        return {"error": f"账本仅 {len(scans)} 条，不足两次扫描，无法窗口对比"}

    # 窗口内最后一次 vs 窗口外最后一次（各按任务名取末次退出码）
    def failures_upto(limit_ts: dt.datetime) -> set[str]:
        last: dict[str, int] = {}
        state: dict[str, str] = {}
        for ts, rec in scans:
            if ts > limit_ts:
                break
            name = str(rec.get("name", ""))
            last[name] = int(rec.get("last_result", -1))
            state[name] = str(rec.get("state", ""))
        return {n for n, code in last.items()
                if state.get(n, "").lower() != "disabled"
                and code not in NON_FAILURE_RC}

    now = dt.datetime.now().astimezone()
    in_window = [(ts, r) for ts, r in scans if ts > cutoff]
    if not in_window:
        return {"error": f"窗口 {hours}h 内无扫描记录"}
    before = [(ts, r) for ts, r in scans if ts <= cutoff]
    if not before:
        return {"error": "窗口外无更早扫描，无法对比"}

    latest_fail = failures_upto(max(ts for ts, _ in scans))
    prior_fail = failures_upto(max(ts for ts, _ in before))
    return {
        "window_hours": hours,
        "compared": {
            "latest_scan_at": str(max(ts for ts, _ in scans)),
            "prior_scan_at": str(max(ts for ts, _ in before)),
        },
        "new_failures": sorted(latest_fail - prior_fail),
        "persisting_failures": sorted(latest_fail & prior_fail),
        "fixed_in_window": sorted(prior_fail - latest_fail),
        "note": "new=窗口内新失败；fixed=上窗口失败现已恢复；persisting=两窗口都失败。",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="计划任务退出码告警闭环（只读扫描+账本+摘要）")
    ap.add_argument("--scan", action="store_true", help="执行扫描并落账本与摘要")
    ap.add_argument("--summary-only", action="store_true", help="只读已有摘要")
    ap.add_argument("--window", type=float, metavar="HOURS", nargs="?", const=24.0,
                    help="窗口对比：新增/持续/已修复失败（默认 24h，可传小时数）")
    ap.add_argument("--all-families", action="store_true", help="扫描全部计划任务（默认仅军团系前缀）")
    ap.add_argument("--json", action="store_true", help="打印摘要 JSON")
    args = ap.parse_args(argv)

    if not args.scan and not args.summary_only and args.window is None:
        ap.print_help()
        return 2

    try:
        if args.scan:
            result = scan(audit_family_only=not args.all_families)
        elif args.window is not None:
            result = window_diff(hours=args.window)
        else:
            result = summary_only()
    except RuntimeError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "error" in result:
            print(result["error"])
            return 1
        if "window_hours" in result:
            print(f"窗口 {result['window_hours']}h 对比（{result['compared']['prior_scan_at']} → {result['compared']['latest_scan_at']}）")
            print(f"  新增失败 {len(result['new_failures'])}：{'、'.join(result['new_failures']) or '无'}")
            print(f"  持续失败 {len(result['persisting_failures'])}：{'、'.join(result['persisting_failures']) or '无'}")
            print(f"  窗口内修复 {len(result['fixed_in_window'])}：{'、'.join(result['fixed_in_window']) or '无'}")
        else:
            print(f"扫描 {result['total_scanned']} 任务：正常 {result['ok']} / 失败 {result['failed']} / "
                  f"上游拒跑 {result.get('upstream_hold', 0)} / 启动器已在跑 {result.get('launcher_refused', 0)} / "
                  f"被取消 {result.get('cancelled', 0)} / 禁用 {result['disabled']} / 未跑 {result['never_run']}（旧摘要无新字段时按 0 显示）")
            for f in result["failures"]:
                print(f"  [FAIL] {f['name']} exit={f['last_result']} ({f['exit_semantics']}) last={f['last_run']}")
            for f in result.get("launcher_refused_tasks", []):
                print(f"  [RUNNING] {f['name']} exit={f['last_result']} ({f['exit_semantics']}) last={f['last_run']}")
            for f in result.get("cancelled_tasks", []):
                print(f"  [CANCELLED] {f['name']} exit={f['last_result']} ({f['exit_semantics']}) last={f['last_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
