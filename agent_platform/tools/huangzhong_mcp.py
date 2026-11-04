# -*- coding: utf-8 -*-
"""黄忠验收 MCP server (stdio) — Hermes 可调用的**只读**验收/守门工具。

背景（2026-10-31 WO-HZ-HERMES-BRIDGE-20261031）：
  黄忠×Hermes 底层结合的工具面。Hermes 是交互/编排/控制面，黄忠保持
  「确定性验收尺子」，**不把验收逻辑迁进 Hermes、不开放写操作**。
  与 D1↔Hermes 桥（agent_platform/tools/d1_research_mcp.py）同款 stdio JSON-RPC。

Hermes config entry（~/.hermes/config.yaml，需重启 8787 后加载）:
  huangzhong:
    command: "C:\\Program Files\\Python311\\python.exe"
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\huangzhong_mcp.py]

Tools（全部只读，无写/无触发）：
  - huangzhong_status()            黄忠六条复核现状 + 最新验收 + 证据年龄（聚合）
  - huangzhong_review_latest()     最近一次 review 报告明细（work/huangzhong/review_*.json）
  - huangzhong_evidence()          验收证据年龄/新鲜度（基线+红队，≤48h 门）
  - huangzhong_acceptance_latest() 最近一张验收单（verdict/commit/reasons）
  - huangzhong_weekly_kpi()        7 天窗口 runs/merge_done/覆盖率（只读计算，不落盘）
  - huangzhong_tasks_health()      两个 Windows 计划任务 LastRun/rc（只读查询）
  - huangzhong_health()            P0 工具链心跳：跑 tools/huangzhong_health.py --json，
                                   返回 status/summary/checks 摘要（超时 180s、输出 ≤8KB）
  - huangzhong_preflight()         P1 11-04 复核 preflight：跑 tools/huangzhong_1104_preflight.py
                                   --json，返回 ready/blockers/warnings 摘要
  - huangzhong_brief()             P1 每日简报：跑 tools/huangzhong_brief.py --json，
                                   返回结论/KPI/事件计数摘要

注：huangzhong_health/preflight/brief 只读执行底层工具；preflight/brief 底层 CLI 按其
既有行为可能生成 md 报告到默认 deliverables（本 MCP 不额外写仓库/work/、不改配置）。

环境变量（测试/迁移用）：
  HUANGZHONG_REPO_ROOT  默认 C:\\项目\\老田的AI助手
  HUANGZHONG_WORK_DIR   默认 <repo>/work/huangzhong
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROTOCOL = "2024-11-05"
REPO = Path(os.environ.get("HUANGZHONG_REPO_ROOT") or r"C:\项目\老田的AI助手")
WORK = Path(os.environ.get("HUANGZHONG_WORK_DIR") or str(REPO / "work" / "huangzhong"))
AGE_LIMIT_HOURS = 48.0
MATURITY_DAYS = 7
TASKS = ("HuangzhongDaily", "HuangzhongEvidenceRefresh")
# ── P2-C：只读子工具（P0/P1 能力服务化）──────────────────────────────────
# 全部经 _run_tool_cmd 跑固定白名单脚本 + --json；超时 180s；交回 Hermes 的
# JSON 截断上限 8KB（超限标记 output_truncated，不返回半截 JSON）。
TOOL_RUN_TIMEOUT_S = 180
TOOL_OUTPUT_CAP_BYTES = 8 * 1024
READONLY_CMD_TOOLS = {
    "huangzhong_health": ("tools/huangzhong_health.py", "--json"),
    "huangzhong_preflight": ("tools/huangzhong_1104_preflight.py", "--json"),
    "huangzhong_brief": ("tools/huangzhong_brief.py", "--json"),
}

TOOLS = [
    {
        "name": "huangzhong_status",
        "description": "黄忠验收现状聚合：六条复核（passed/total、各条 status/measured）、"
                       "最新验收单（verdict/commit）、证据年龄（基线/红队）、"
                       "④满窗/跟踪计数。只读。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_review_latest",
        "description": "最近一次黄忠六条复核报告明细（generated_at/items/kpi/覆盖）。只读。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_evidence",
        "description": "黄忠验收证据新鲜度：基线 test_baseline/latest.json 与最新红队报告的 "
                       "generated_at/age_hours/是否 ≤48h（R3 门）。只读。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_acceptance_latest",
        "description": "最近一张黄忠验收单：verdict/commit/sha/时间/reasons/证据摘要。只读。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_weekly_kpi",
        "description": "黄忠 7 天窗口 KPI（只读计算）：runs、merge_done、覆盖率、PASS/REJECT/OBSERVE 计数。",
        "inputSchema": {"type": "object", "properties": {
            "window_days": {"type": "integer", "minimum": 1, "maximum": 30,
                            "description": "窗口天数，默认 7"}},
            "additionalProperties": False},
    },
    {
        "name": "huangzhong_tasks_health",
        "description": "黄忠两个计划任务（HuangzhongDaily / HuangzhongEvidenceRefresh）的 "
                       "LastRunTime/LastTaskResult/NextRunTime（只读查询 Windows Scheduler）。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_health",
        "description": "黄忠工具链心跳（P0，只读）：跑 tools/huangzhong_health.py --json，"
                       "返回 status（ok/warn/fail）、summary（ok/warn/fail 计数）、"
                       "checks 摘要（id/name/status/detail）与 warnings；超时 180s、输出 ≤8KB。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_preflight",
        "description": "黄忠 11-04 复核 preflight（P1，只读）：跑 "
                       "tools/huangzhong_1104_preflight.py --json，返回 ready、"
                       "blockers/warnings（逐条）、checks 摘要（id/name/status/result）。"
                       "ready=false 不是执行失败（数据有效）。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "huangzhong_brief",
        "description": "黄忠每日简报（P1，只读）：跑 tools/huangzhong_brief.py --json，"
                       "返回 conclusion（一行结论）、kpi（runs/merge_done/coverage 等）、"
                       "events 计数、review/acceptance/push_state 摘要。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
]

#: 受控写工具（Phase 2，老田 2026-10-31 批 A）：默认关闭（HUANGZHONG_MCP_WRITE=1 才注册）
#: 只允许两个固定动作：复核 / 当前 HEAD 验收；必须带精确 confirm 口令；限频 60s；
#: 每次调用写 dev_events code_action 审计；不接受调用方传路径/参数。
WRITE_ENABLED = str(os.environ.get("HUANGZHONG_MCP_WRITE") or "0").strip().lower() \
    in ("1", "true", "on", "yes")
WRITE_TOOLS = [
    {
        "name": "huangzhong_run_review",
        "description": "【受控写】立即跑一次黄忠六条复核（只读计算 + 写 work 报告）。"
                       "必须 confirm=\"RUN-REVIEW\"；60s 限频；仅在 HUANGZHONG_MCP_WRITE=1 时可用。",
        "inputSchema": {"type": "object", "properties": {
            "confirm": {"type": "string", "description": "必须为 RUN-REVIEW"}},
            "required": ["confirm"], "additionalProperties": False},
    },
    {
        "name": "huangzhong_run_acceptance",
        "description": "【受控写】对当前 HEAD 立即跑一次黄忠验收（写 acceptance 单 + 日更）。"
                       "必须 confirm=\"RUN-ACCEPT\"；60s 限频；仅在 HUANGZHONG_MCP_WRITE=1 时可用。",
        "inputSchema": {"type": "object", "properties": {
            "confirm": {"type": "string", "description": "必须为 RUN-ACCEPT"}},
            "required": ["confirm"], "additionalProperties": False},
    },
]
CONFIRM_REVIEW = "RUN-REVIEW"
CONFIRM_ACCEPT = "RUN-ACCEPT"
MIN_INTERVAL_S = 60.0
_WRITE_LOCK = threading.Lock()
_LAST_RUN = {"review": 0.0, "accept": 0.0}


# ── 工具实现（只读 + 受控写）──────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _latest_review() -> dict:
    files = sorted(glob.glob(str(WORK / "review_*.json")), key=os.path.getmtime)
    if not files:
        return {"ok": False, "error": "无 review_*.json（尚未跑过复核）"}
    data = _read_json(Path(files[-1])) or {}
    items = []
    for it in data.get("items") or []:
        items.append({"id": it.get("id"), "title": it.get("title"),
                      "status": it.get("status"), "measured": it.get("measured")})
    return {"ok": True, "file": Path(files[-1]).name,
            "generated_at": data.get("generated_at"),
            "summary": data.get("summary"), "kpi": data.get("kpi"),
            "items": items}


def _latest_acceptance() -> dict:
    files = sorted(glob.glob(str(WORK / "acceptance_*.json")), key=os.path.getmtime)
    if not files:
        return {"ok": False, "error": "无 acceptance_*.json"}
    data = _read_json(Path(files[-1])) or {}
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    return {"ok": True, "file": Path(files[-1]).name,
            "verdict": data.get("verdict"),
            "commit": commit.get("sha") or data.get("sha"),
            "generated_at": data.get("generated_at"),
            "reasons": data.get("reasons") or [],
            "evidence": {"baseline": ev.get("baseline"), "redteam": ev.get("redteam")}}


def _evidence():
    base = _read_json(REPO / "agent_platform" / "data" / "test_baseline" / "latest.json") or {}
    rt_files = sorted(glob.glob(str(REPO / "work" / "p1" / "redteam" / "redteam_*.json")),
                      key=os.path.getmtime)
    rt = _read_json(Path(rt_files[-1])) if rt_files else {}
    now = _now()
    out = {"ok": True, "age_limit_hours": AGE_LIMIT_HOURS}
    for name, rec in (("baseline", base), ("redteam", rt or {})):
        gen = _parse_ts(rec.get("generated_at"))
        age = round((now - gen).total_seconds() / 3600, 2) if gen else None
        out[name] = {"generated_at": rec.get("generated_at"),
                     "age_hours": age,
                     "stale": (age is not None and age > AGE_LIMIT_HOURS),
                     "summary": rec.get("summary"),
                     "pass_rate": rec.get("pass_rate")}
    out["ok"] = not (out["baseline"].get("stale") or out["redteam"].get("stale"))
    return out


def _inspect_events(rows):
    """④辅助：从 review 明细取满窗/跟踪/漏放计数（无 review 时返回 None）。"""
    rv = _latest_review()
    if not rv.get("ok"):
        return None
    for it in rv.get("items") or []:
        if it.get("id") == "④":
            return {"status": it.get("status"), "measured": it.get("measured")}
    return None


def _weekly_kpi(window_days: int = 7) -> dict:
    window_days = max(1, min(int(window_days or 7), 30))
    now = _now()
    start = now - timedelta(days=window_days)
    runs = []
    daily = WORK / "daily.jsonl"
    if daily.is_file():
        for line in daily.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts and start <= ts <= now:
                runs.append(rec)
    merge_done = 0
    ev = REPO / "portal" / "data" / "dev_events.jsonl"
    if ev.is_file():
        for line in ev.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("kind") != "merge_done":
                continue
            try:
                ts = datetime.strptime(str(rec.get("ts")), "%Y-%m-%d %H:%M:%S").astimezone()
            except ValueError:
                continue
            if start <= ts <= now:
                merge_done += 1
    counts = {v: sum(1 for r in runs if r.get("verdict") == v)
              for v in ("PASS", "REJECT", "OBSERVE")}
    return {"ok": True, "window_days": window_days, "runs": len(runs),
            "merge_done": merge_done,
            "coverage_rate": (round(len(runs) / merge_done, 4) if merge_done else None),
            "counts": counts,
            "generated_at": now.isoformat(timespec="seconds")}


def _tasks_health() -> dict:
    """只读查询两个计划任务（PowerShell Get-ScheduledTaskInfo → JSON）。"""
    ps = ("$names=@('HuangzhongDaily','HuangzhongEvidenceRefresh');"
          "$out=@();foreach($n in $names){$t=Get-ScheduledTaskInfo -TaskName $n "
          "-ErrorAction SilentlyContinue; if($t){$out+=[pscustomobject]@{name=$n;"
          "last_run=$t.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss');"
          "last_result=$t.LastTaskResult;"
          "next_run=$t.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}};"
          "$out|ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        raw = (r.stdout or "").strip()
        data = json.loads(raw) if raw else []
        if isinstance(data, dict):
            data = [data]
        return {"ok": r.returncode == 0, "tasks": data,
                "ps_error": (r.stderr or "").strip()[:300] or None}
    except Exception as exc:  # noqa: BLE001 —— 只读工具 fail-visible
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def _py() -> str:
    return sys.executable or "python"


def _audit(tool: str, detail: str) -> None:
    """写 dev_events code_action 审计（fail-open，审计失败不影响工具返回）。"""
    try:
        tools_dir = str(REPO / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import events as _events  # noqa: WPS433 —— 惰性导入，避免拖慢只读路径
        # 2026-10-31 P134：全量套件下 sys.modules["events"] 可能是他处缓存的同名模块
        # （写向主仓），导致本工具审计事件未落到当前 REPO → 全量顺序 flaky。
        # 仅当已导入模块确属 REPO/tools/events.py 时才走 emit，否则用下方直写兜底。
        _expected = (REPO / "tools" / "events.py").resolve()
        _actual = Path(str(getattr(_events, "__file__", "") or "")).resolve()
        if _actual != _expected:
            raise RuntimeError("events module path mismatch: %s != %s" % (_actual, _expected))
        _events.emit(kind="code_action", task="HUANGZHONG-MCP", agent="HERMES",
                     ide="Hermes 8787", model="huangzhong-mcp",
                     detail="%s %s" % (tool, detail))
        return
    except Exception:  # noqa: BLE001 —— fallback 直写（同 schema）
        pass
    try:
        p = REPO / "portal" / "data" / "dev_events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        now = _now()
        rec = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"),
               "day": now.date().isoformat(), "kind": "code_action",
               "task": "HUANGZHONG-MCP", "agent": "HERMES",
               "ide": "Hermes 8787", "model": "huangzhong-mcp",
               "detail": ("%s %s" % (tool, detail))[:300]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 审计永远 fail-open
        pass


def _run_tool_cmd(cmd: list, timeout: int = 180):
    """跑固定白名单命令（测试可 monkeypatch 本函数，不真跑子进程）。"""
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


# ── P2-C：只读子工具实现（health / preflight / brief）─────────────────────

def _cap_text(value, limit: int = 300):
    """字符串截断（None → None），避免大字段撑爆 MCP 输出。"""
    if value is None:
        return None
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


def _cap_issue(item):
    """preflight 的 blockers/warnings 条目（dict 或 str）统一压缩成 {check, message}。"""
    if isinstance(item, dict):
        msg = item.get("message") or item.get("detail") or item.get("note")
        return {"check": item.get("check") or item.get("id"),
                "message": _cap_text(msg if msg is not None else item, 200)}
    return {"check": None, "message": _cap_text(item, 200)}


def _finalize_readonly(summary: dict) -> dict:
    """保证交回 Hermes 的单工具 JSON ≤ TOOL_OUTPUT_CAP_BYTES（超限截断并标记）。"""
    summary = dict(summary)
    if len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) \
            <= TOOL_OUTPUT_CAP_BYTES:
        return summary
    summary["output_truncated"] = True
    summary["details"] = ("（输出超过 %dKB 上限，明细已截断；完整内容请用对应 CLI 查看）"
                          % (TOOL_OUTPUT_CAP_BYTES // 1024))
    for key in ("checks", "blockers", "warnings", "kpi", "review", "events",
                "acceptance", "incidents", "push_state", "evidence", "conclusion"):
        summary.pop(key, None)
    if len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) \
            > TOOL_OUTPUT_CAP_BYTES:
        summary = {"ok": summary.get("ok", False), "tool": summary.get("tool"),
                   "output_truncated": True,
                   "error": "工具输出超过 %dKB 上限，已截断"
                            % (TOOL_OUTPUT_CAP_BYTES // 1024)}
    return summary


def _run_readonly_tool(name: str) -> tuple[dict, dict]:
    """跑白名单只读工具（--json）：超时/异常/非 JSON/自报 ok=false → RuntimeError（isError）。"""
    script, flag = READONLY_CMD_TOOLS[name]
    cmd = [_py(), "-X", "utf8", script, flag]
    t0 = time.time()
    try:
        r = _run_tool_cmd(cmd, timeout=TOOL_RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError("%s 超时（>%ss）" % (name, TOOL_RUN_TIMEOUT_S))
    except Exception as exc:  # noqa: BLE001 —— 失败 fail-visible，不编造
        raise RuntimeError("%s 执行失败：%s: %s"
                           % (name, type(exc).__name__, str(exc)[:200]))
    raw = getattr(r, "stdout", "") or ""
    raw_bytes = len(raw.encode("utf-8", errors="replace"))
    meta = {"script": script, "rc": getattr(r, "returncode", None),
            "elapsed_s": round(time.time() - t0, 1), "raw_bytes": raw_bytes,
            "raw_truncated": raw_bytes > TOOL_OUTPUT_CAP_BYTES,
            "stderr_tail": _cap_text((getattr(r, "stderr", "") or "").strip(), 200)
            or None}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("%s 输出非合法 JSON（raw_bytes=%d, rc=%s）：%s"
                           % (name, raw_bytes, meta["rc"], str(exc)[:150]))
    if not isinstance(data, dict):
        raise RuntimeError("%s 输出 JSON 顶层不是对象（%s）"
                           % (name, type(data).__name__))
    if data.get("ok") is False:
        raise RuntimeError("%s 自报数据错误：%s"
                           % (name, _cap_text(data.get("error")
                                              or data.get("note"), 200)))
    return data, meta


def _health() -> dict:
    """P0 工具链心跳摘要（status/summary/checks）。"""
    data, meta = _run_readonly_tool("huangzhong_health")
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    out = {
        "ok": data.get("ok") is not False,
        "tool": "huangzhong_health",
        "status": data.get("status"),
        "summary": data.get("summary"),
        "checks_total": len(checks),
        "checks": [{"id": c.get("id"), "name": _cap_text(c.get("name"), 80),
                    "status": c.get("status"),
                    "detail": _cap_text(c.get("detail"), 160)}
                   for c in checks[:10] if isinstance(c, dict)],
        "warnings": [_cap_text(w, 160) for w in (data.get("warnings") or [])[:5]],
        "generated_at": data.get("generated_at"),
        "read_only": True,
        "run": meta,
    }
    return _finalize_readonly(out)


def _preflight() -> dict:
    """P1 11-04 preflight 摘要（ready/blockers/warnings）。ready=false 不是执行失败。"""
    data, meta = _run_readonly_tool("huangzhong_preflight")
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    blockers = data.get("blockers") if isinstance(data.get("blockers"), list) else []
    warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    out = {
        "ok": data.get("ok") is not False,
        "tool": "huangzhong_preflight",
        "ready": bool(data.get("ready")),
        "blockers_total": len(blockers),
        "blockers": [_cap_issue(x) for x in blockers[:20]],
        "warnings_total": len(warnings),
        "warnings": [_cap_issue(x) for x in warnings[:20]],
        "checks_total": len(checks),
        "checks": [{"id": c.get("id"), "name": _cap_text(c.get("name"), 80),
                    "status": c.get("status"),
                    "result": _cap_text(c.get("result"), 160)}
                   for c in checks[:10] if isinstance(c, dict)],
        "generated_at": data.get("generated_at"),
        "read_only": True,
        "run": meta,
    }
    return _finalize_readonly(out)


def _brief() -> dict:
    """P1 每日简报摘要（结论/KPI/事件计数/复核/验收/推送状态）。"""
    data, meta = _run_readonly_tool("huangzhong_brief")
    review = data.get("review") if isinstance(data.get("review"), dict) else {}
    rsum = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    acceptance = data.get("acceptance") if isinstance(data.get("acceptance"), dict) else {}
    push_state = data.get("push_state") if isinstance(data.get("push_state"), dict) else {}
    incidents = data.get("incidents") if isinstance(data.get("incidents"), dict) else {}
    out = {
        "ok": data.get("ok") is not False,
        "tool": "huangzhong_brief",
        "conclusion": _cap_text(data.get("conclusion"), 500),
        "generated_at": data.get("generated_at"),
        "date": data.get("date"),
        "days": data.get("days"),
        "kpi": data.get("kpi"),
        "events": {k: events.get(k) for k in
                   ("status", "total", "counts", "bad_lines", "window_days")},
        "review": {"status": review.get("status"),
                   "generated_at": review.get("generated_at"),
                   "age_hours": review.get("age_hours"),
                   "summary": {k: rsum.get(k) for k in
                               ("overall_pass", "passed", "total")}},
        "acceptance": {k: acceptance.get(k) for k in
                       ("status", "verdict", "commit", "generated_at", "age_hours")},
        "push_state": {k: push_state.get(k) for k in
                       ("enabled", "state", "source", "updated_at", "by")},
        "incidents": {k: incidents.get(k) for k in
                      ("status", "rows", "recent_window", "latest")},
        "warnings": [_cap_text(w, 160) for w in (data.get("warnings") or [])[:5]],
        "read_only": True,
        "run": meta,
    }
    return _finalize_readonly(out)


def _guard_write(kind: str, confirm, expect: str) -> None:
    if not WRITE_ENABLED:
        raise RuntimeError("write tools disabled（HUANGZHONG_MCP_WRITE=0）")
    if str(confirm or "").strip() != expect:
        raise ValueError("confirm 口令错误：需 %r" % expect)
    now = time.monotonic()
    if now - _LAST_RUN[kind] < MIN_INTERVAL_S:
        raise RuntimeError("限频：%s 刚跑过（<%.0fs），请复用上次结果"
                           % (kind, MIN_INTERVAL_S))


def _latest_daily_row() -> dict:
    p = WORK / "daily.jsonl"
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8",
                                          errors="replace").splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else {}
    except (OSError, ValueError):
        return {}


def _run_review(confirm) -> dict:
    with _WRITE_LOCK:
        _guard_write("review", confirm, CONFIRM_REVIEW)
        t0 = time.time()
        r = _run_tool_cmd([_py(), "-X", "utf8", "tools/huangzhong_review.py", "--json"])
        _LAST_RUN["review"] = time.monotonic()
        _audit("huangzhong_run_review",
               "rc=%s elapsed=%.1fs" % (r.returncode, time.time() - t0))
        review = _latest_review()
        # rc=1 = 六条未全通过（观察期正常，如 5/6）；只要报告已生成即视为已执行
        ok = bool(review.get("ok")) and r.returncode in (0, 1)
        return {"ok": ok, "rc": r.returncode,
                "note": ("rc=1 表示六条未全通过（观察期正常），非执行失败"
                         if r.returncode == 1 else None),
                "duration_s": round(time.time() - t0, 1),
                "review": review,
                "stderr_tail": ((r.stderr or "")[-300:] or None)}


def _run_acceptance(confirm) -> dict:
    with _WRITE_LOCK:
        _guard_write("accept", confirm, CONFIRM_ACCEPT)
        t0 = time.time()
        r = _run_tool_cmd([_py(), "-X", "utf8", "tools/huangzhong_daily.py",
                           "--daily", "--json"])
        _LAST_RUN["accept"] = time.monotonic()
        _audit("huangzhong_run_acceptance",
               "rc=%s elapsed=%.1fs" % (r.returncode, time.time() - t0))
        return {"ok": r.returncode == 0, "rc": r.returncode,
                "duration_s": round(time.time() - t0, 1),
                "acceptance": _latest_acceptance(),
                "daily": _latest_daily_row(),
                "stderr_tail": ((r.stderr or "")[-300:] or None)}


def call_tool(name: str, args: dict):
    if name == "huangzhong_status":
        return {"ok": True, "review": _latest_review(),
                "acceptance": _latest_acceptance(), "evidence": _evidence(),
                "leakage": _inspect_events(None), "fetched_at": _now().isoformat()}
    if name == "huangzhong_review_latest":
        return _latest_review()
    if name == "huangzhong_evidence":
        return _evidence()
    if name == "huangzhong_acceptance_latest":
        return _latest_acceptance()
    if name == "huangzhong_weekly_kpi":
        return _weekly_kpi(args.get("window_days", 7))
    if name == "huangzhong_tasks_health":
        return _tasks_health()
    if name == "huangzhong_health":
        return _health()
    if name == "huangzhong_preflight":
        return _preflight()
    if name == "huangzhong_brief":
        return _brief()
    if name == "huangzhong_run_review":
        return _run_review(args.get("confirm"))
    if name == "huangzhong_run_acceptance":
        return _run_acceptance(args.get("confirm"))
    raise KeyError("unknown tool: %s" % name)


# ── MCP stdio JSON-RPC ────────────────────────────────────────────────────

def _content(obj):
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


def handle(msg: dict) -> dict:
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "huangzhong", "version": "1.1.0",
                           "description": "黄忠验收守门·只读工具面（9 只读 + 受控写；"
                                          "不写/不触发除显式写工具外动作）"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"tools": TOOLS + (WRITE_TOOLS if WRITE_ENABLED else [])}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            obj = call_tool(name, args)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": _content(obj), "isError": False}}
        except KeyError as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": str(exc)[:200]}}
        except Exception as exc:  # noqa: BLE001 —— fail-visible，不编造结果
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text",
                                            "text": "huangzhong tool failed: %s"
                                                    % str(exc)[:300]}],
                               "isError": True}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def main() -> int:
    # WO-MCP-UTF8-GUARD-20261104（判例 K-STOCK-428；根因见 WO-MCP-ENCODING-AUDIT-20261104）：
    # Hermes 以管道拉起 MCP server，且 spawn env 白名单不含 PYTHONIOENCODING/PYTHONUTF8，
    # Windows 文本 stdout 默认 cp936/GBK → json.dumps(ensure_ascii=False) 的中文按 GBK 字节
    # 写出，客户端按 UTF-8 + errors="replace" 解码 → U+FFFD 乱码。故显式 UTF-8；
    # 流不可 reconfigure（如测试 CaptureIO）时无害跳过。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}}),
                  flush=True)
            continue
        resp = handle(msg)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
