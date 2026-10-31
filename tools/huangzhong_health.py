# -*- coding: utf-8 -*-
"""黄忠工具链自监控心跳（只读；默认不写盘）。

用途：把「黄忠工具链是否在正常运转」变成可定时执行、可告警的检查：一条命令输出
`status=ok|warn|fail` + 逐项 checks，供计划任务/巡检/告警消费。**只读**：默认不写
任何文件；仅当显式 `--out-dir` 时写一份 md 报告（默认落 C:\\项目\\deliverables）。

检查项（7 项 + 可选 exec 自检）：
  ① 最新验收单 age：work/huangzhong/acceptance_*.json，>90min 判 stale（fail）；
  ② events.jsonl 最近写入 age：缺失/空/坏行 → fail；>90min stale → fail；
  ③ 最新 review 报告 age：缺失/>24h → warn（复核靠一键流程触发，不作硬 fail）；
  ④ 证据年龄：baseline + redteam 均 ≤ --max-age-hours（默认 48h）→ 超龄/缺失 fail；
  ⑤ 计划任务健康：HuangzhongDaily / HuangzhongEvidenceRefresh（状态/上次结果/上次运行
     间隔），经模块级 `_run` 查询（PowerShell 结构化，失败回退 schtasks；**查询不可用→
     warn 不 fail**）；
  ⑥ push_config 开关：enabled=true →「enabled（B ON）」；false/缺失 →「off」，均 ok 仅记录；
  ⑦ work/huangzhong 必需文件存在性：daily.jsonl / events.jsonl / ≥1 acceptance_*.json /
     ≥1 review_*.json（缺 → fail）；演练台账/抽查记录/weekly（缺 → warn）。
  `--check-exec`：额外跑 `python tools/huangzhong_review.py --help`（经 `_run`）确认可执行。

合成：任一 fail → status=fail；否则任一 warn → warn；否则 ok。
退出码：0=ok / 1=warn / 2=fail（fail-visible：坏文件/缺文件如实标注，不编造）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_BASELINE = Path("agent_platform") / "data" / "test_baseline" / "latest.json"
DEFAULT_REDTEAM_DIR = Path("work") / "p1" / "redteam"
DEFAULT_DRAFT_DIR = Path(r"C:\项目\deliverables")
ACCEPTANCE_STALE_MINUTES = 90        # 日更 30min 节奏；连续 3 档未跑 = stale
EVENTS_STALE_MINUTES = 90
REVIEW_WARN_HOURS = 24.0
DEFAULT_MAX_AGE_HOURS = 48.0
DEFAULT_TASKS = (
    {"name": "HuangzhongDaily", "max_gap_minutes": 90, "required": True,
     "note": "黄忠日更验收（30 分钟节奏）"},
    {"name": "HuangzhongEvidenceRefresh", "max_gap_minutes": 1560,
     "required": True, "note": "黄忠证据每日刷新（每日 20:00）"},
)
TOOL_VERSION = "v1 (2026-10-31)"


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区 aware）；测试可注入 now。"""
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601/空格/斜杠格式/epoch 秒）；无时区按本机时区解释。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.upper() in ("N/A", "NA", "-", "无"):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                    "%Y/%m/%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return Path(path).read_text(encoding="utf-8-sig"), None
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    text, err = _read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_last_jsonl(path: Path) -> dict:
    """读 JSONL 最后一条可解析记录：{exists, rows, bad_lines, last, error}。"""
    text, err = _read_text(path)
    if text is None:
        return {"path": str(path), "exists": False, "rows": 0, "bad_lines": 0,
                "last": None, "error": err}
    rows, bad, last = 0, 0, None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows += 1
            last = obj
        else:
            bad += 1
    return {"path": str(path), "exists": True, "rows": rows,
            "bad_lines": bad, "last": last, "error": None}


def _run(cmd, timeout: float = 30.0) -> dict:
    """执行外部命令（模块级可注入；测试用 fake）。返回 {ok, rc, stdout, stderr}。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return {"ok": getattr(r, "returncode", 1) == 0,
                "rc": getattr(r, "returncode", None),
                "stdout": getattr(r, "stdout", "") or "",
                "stderr": getattr(r, "stderr", "") or ""}
    except Exception as e:  # noqa: BLE001  fail-visible，不中断心跳
        return {"ok": False, "rc": None, "stdout": "",
                "stderr": "%s: %s" % (type(e).__name__, e)}


def _log(msg: str) -> None:
    """调试日志（默认静默；HUANGZHONG_HEALTH_DEBUG=1 时写 stderr）。"""
    try:
        if os.environ.get("HUANGZHONG_HEALTH_DEBUG"):
            sys.stderr.write("[huangzhong_health] %s\n" % msg)
    except Exception:  # noqa: BLE001  fail-open
        pass


def _check(cid: str, name: str, status: str, detail: str, **extra) -> dict:
    row = {"id": cid, "name": name, "status": status, "detail": detail}
    row.update(extra)
    return row


def _age_minutes(ts: dt.datetime | None, now: dt.datetime) -> float | None:
    if ts is None:
        return None
    return round((now - ts).total_seconds() / 60.0, 1)


# ------------------------------------------------------------------ 各检查项

def _latest_by_generated_at(files: list[Path], now: dt.datetime,
                            key: str = "generated_at") -> dict:
    """取「generated_at 最新」的文件（不可解析回退 mtime）。"""
    best = {"path": None, "ts": None, "error": None}
    for p in files:
        data, err = _read_json(p)
        ts = None
        if isinstance(data, dict):
            ts = _parse_ts(data.get(key))
        if ts is None:
            try:
                ts = dt.datetime.fromtimestamp(p.stat().st_mtime,
                                               tz=dt.timezone.utc)
            except OSError as e:
                if best["error"] is None:
                    best["error"] = "%s: %s" % (type(e).__name__, e)
                continue
        if best["ts"] is None or ts > best["ts"]:
            best = {"path": str(p), "ts": ts, "error": err}
    return best


def check_acceptance(work: Path, now: dt.datetime) -> dict:
    files = sorted(Path(work).glob("acceptance_*.json"))
    if not files:
        return _check("acceptance_latest", "最新验收单 age", "fail",
                      "work/huangzhong/acceptance_*.json 缺失（日更验收未产出）",
                      age_minutes=None, path=str(Path(work) / "acceptance_*.json"))
    latest = _latest_by_generated_at(files, now)
    age = _age_minutes(latest["ts"], now)
    path = latest["path"] or str(files[-1])
    if latest.get("error"):
        return _check("acceptance_latest", "最新验收单 age", "warn",
                      "最新验收单 JSON 不可读（%s）：%s；按 mtime 计龄 age=%.1f min"
                      % (path, latest["error"], age if age is not None else -1),
                      age_minutes=age, path=path)
    if age is None:
        return _check("acceptance_latest", "最新验收单 age", "warn",
                      "最新验收单时间不可解析：%s" % path, age_minutes=None,
                      path=path)
    stale = age > ACCEPTANCE_STALE_MINUTES
    status = "fail" if stale else "ok"
    detail = ("最新验收单 %s（%s），age=%.1f min；阈值 ≤%d min"
              % (Path(path).name, _iso(latest["ts"]), age,
                 ACCEPTANCE_STALE_MINUTES))
    return _check("acceptance_latest", "最新验收单 age", status, detail,
                  age_minutes=age, path=path,
                  threshold_minutes=ACCEPTANCE_STALE_MINUTES)


def check_events(work: Path, now: dt.datetime) -> dict:
    path = Path(work) / "events.jsonl"
    got = _read_last_jsonl(path)
    if not got["exists"]:
        return _check("events_freshness", "events.jsonl 最近写入 age", "fail",
                      "events.jsonl 缺失：%s" % path, age_minutes=None,
                      path=str(path))
    if got["rows"] == 0:
        return _check("events_freshness", "events.jsonl 最近写入 age", "fail",
                      "events.jsonl 为空或无可解析记录（bad_lines=%d）"
                      % got["bad_lines"], age_minutes=None, path=str(path))
    last = got["last"] or {}
    ts = _parse_ts(last.get("ts") or last.get("generated_at") or last.get("date"))
    age = _age_minutes(ts, now)
    if age is None:
        return _check("events_freshness", "events.jsonl 最近写入 age", "warn",
                      "events.jsonl 最后一条 ts 不可解析：%s" % str(path),
                      age_minutes=None, path=str(path))
    stale = age > EVENTS_STALE_MINUTES
    status = "fail" if stale else "ok"
    return _check("events_freshness", "events.jsonl 最近写入 age", status,
                  "最后事件 %s（rows=%d bad_lines=%d），age=%.1f min；阈值 ≤%d min"
                  % (_iso(ts), got["rows"], got["bad_lines"], age,
                     EVENTS_STALE_MINUTES),
                  age_minutes=age, path=str(path),
                  threshold_minutes=EVENTS_STALE_MINUTES)


def check_review(work: Path, now: dt.datetime) -> dict:
    files = sorted(Path(work).glob("review_*.json"))
    if not files:
        return _check("review_latest", "最新复核报告 age", "warn",
                      "work/huangzhong/review_*.json 缺失（一键复核流程未跑；不判 fail）",
                      age_hours=None, path=str(Path(work) / "review_*.json"))
    latest = _latest_by_generated_at(files, now)
    age_h = None
    if latest["ts"] is not None:
        age_h = round((now - latest["ts"]).total_seconds() / 3600.0, 2)
    path = latest["path"] or str(files[-1])
    if latest.get("error"):
        return _check("review_latest", "最新复核报告 age", "warn",
                      "最新复核报告 JSON 不可读（%s）：%s"
                      % (path, latest["error"]), age_hours=None, path=path)
    if age_h is None:
        return _check("review_latest", "最新复核报告 age", "warn",
                      "最新复核报告时间不可解析：%s" % path, age_hours=None,
                      path=path)
    stale = age_h > REVIEW_WARN_HOURS
    return _check("review_latest", "最新复核报告 age", "warn" if stale else "ok",
                  "最新复核报告 %s（%s），age=%.2f h；阈值 ≤%.0f h（超龄仅 warn）"
                  % (Path(path).name, _iso(latest["ts"]), age_h,
                     REVIEW_WARN_HOURS),
                  age_hours=age_h, path=path, threshold_hours=REVIEW_WARN_HOURS)


def _load_evidence(path: Path, kind: str, now: dt.datetime,
                   max_age_hours: float) -> dict:
    ev = {"kind": kind, "path": str(path), "ok": False, "error": None,
          "generated_at": None, "age_hours": None, "stale": None}
    data, err = _read_json(path)
    if err or not isinstance(data, dict):
        ev["error"] = err or "JSON 顶层不是对象"
        return ev
    gen = _parse_ts(data.get("generated_at"))
    age = round((now - gen).total_seconds() / 3600.0, 2) if gen is not None else None
    ev.update({"ok": True, "generated_at": data.get("generated_at"),
               "age_hours": age,
               "stale": (age > max_age_hours) if age is not None else None})
    return ev


def _resolve_redteam_file(repo_root: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            files = sorted(p.glob("redteam_*.json"),
                           key=lambda x: x.stat().st_mtime if x.exists() else 0)
            return files[-1] if files else p
        return p
    directory = Path(repo_root) / DEFAULT_REDTEAM_DIR
    if not directory.is_dir():
        return directory
    files = sorted(directory.glob("redteam_*.json"),
                   key=lambda x: x.stat().st_mtime if x.exists() else 0)
    return files[-1] if files else directory


def check_evidence(repo_root: Path, max_age_hours: float,
                   now: dt.datetime) -> dict:
    base = _load_evidence(Path(repo_root) / DEFAULT_BASELINE, "baseline", now,
                          max_age_hours)
    rt_path = _resolve_redteam_file(repo_root, None)
    rt = _load_evidence(rt_path, "redteam", now, max_age_hours)
    problems = []
    for ev in (base, rt):
        if not ev["ok"]:
            problems.append("%s 不可用（%s）" % (ev["kind"], ev["error"]))
        elif ev["age_hours"] is None:
            problems.append("%s 生成时间不可解析" % ev["kind"])
        elif ev["stale"]:
            problems.append("%s 证据超龄 %.1f h > %.0f h"
                            % (ev["kind"], ev["age_hours"], max_age_hours))
    status = "fail" if problems else "ok"
    detail = ("baseline=%s（%s，age=%s h；stale=%s）；redteam=%s（%s，age=%s h）；"
              "阈值 ≤%.0f h"
              % (Path(base["path"]).name, base.get("generated_at"),
                 base.get("age_hours"), base.get("stale"),
                 Path(str(rt["path"])).name, rt.get("generated_at"),
                 rt.get("age_hours"), max_age_hours))
    if problems:
        detail += "；问题：" + "；".join(problems)
    return _check("evidence_age", "证据年龄（baseline/redteam）", status, detail,
                  max_age_hours=max_age_hours,
                  baseline=base, redteam=rt)


# ------------------------------------------------------- ⑤ 计划任务（可注入查询）

def _ps_task_cmd(name: str) -> list:
    safe = re.sub(r"[^A-Za-z0-9_.\\ -]", "", name)
    script = (
        "$ErrorActionPreference='Stop';"
        "$n='%s';"
        "try{"
        "$t=Get-ScheduledTask -TaskName $n -ErrorAction Stop;"
        "$i=Get-ScheduledTaskInfo -TaskName $n -ErrorAction Stop;"
        "$o=[pscustomobject]@{name=[string]$t.TaskName;state=[string]$t.State;"
        "enabled=[bool]$t.Settings.Enabled;"
        "last_run=$i.LastRunTime.ToUniversalTime().ToString('o');"
        "last_result=$i.LastTaskResult;"
        "next_run=$i.NextRunTime.ToUniversalTime().ToString('o')};"
        "$o|ConvertTo-Json -Compress"
        "}catch{Write-Output ('ERR:'+$_.Exception.Message);exit 2}"
    ) % safe
    return ["powershell", "-NoProfile", "-Command", script]


def _schtasks_cmd(name: str) -> list:
    return ["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"]


_SCHTASK_KEYS = {
    "last_run": ("last run time", "上次运行时间"),
    "last_result": ("last result", "上次结果"),
    "status": ("status", "状态"),
    "state": ("scheduled task state", "计划任务状态"),
    "next_run": ("next run time", "下次运行时间"),
}


def _parse_schtasks_list(text: str) -> dict | None:
    """解析 schtasks /V /FO LIST 文本（英/中标签）；失败返回 None。"""
    fields: dict = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        for field, aliases in _SCHTASK_KEYS.items():
            if k in aliases:
                fields[field] = v
                break
    if not fields:
        return None
    last_run = _parse_ts(fields.get("last_run"))
    last_result = fields.get("last_result")
    try:
        last_result = int(str(last_result).strip())
    except (TypeError, ValueError):
        last_result = None
    return {"name": None, "state": fields.get("state"),
            "enabled": (fields.get("state") or "").lower() in ("enabled", "已启用"),
            "status": fields.get("status"),
            "last_run": _iso(last_run), "last_result": last_result,
            "next_run": fields.get("next_run")}


def _query_task(name: str) -> dict:
    """查询单个计划任务：PowerShell 结构化优先，schtasks 文本回退。"""
    out = {"name": name, "ok": False, "state": None, "enabled": None,
           "last_run": None, "last_result": None, "next_run": None,
           "error": None, "source": None}
    ps = _run(_ps_task_cmd(name))
    text = (ps.get("stdout") or "").strip()
    if ps.get("ok") and text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            last_run = _parse_ts(data.get("last_run"))
            next_run = _parse_ts(data.get("next_run"))
            out.update({"ok": True, "source": "powershell",
                        "state": data.get("state"),
                        "enabled": data.get("enabled"),
                        "last_run": _iso(last_run),
                        "last_result": data.get("last_result"),
                        "next_run": _iso(next_run)})
            return out
    # 回退：schtasks 文本
    sc = _run(_schtasks_cmd(name))
    parsed = _parse_schtasks_list(sc.get("stdout") or "") if sc.get("ok") else None
    if parsed:
        parsed["name"] = parsed.get("name") or name
        parsed.update({"ok": True, "source": "schtasks", "error": None})
        return parsed
    out["error"] = ("PowerShell 查询失败（%s）；schtasks 回退失败（rc=%s %s）"
                    % ((ps.get("stderr") or "rc=%s" % ps.get("rc"))[:200],
                       sc.get("rc"), (sc.get("stderr") or "")[:120]))
    return out


def check_scheduled_tasks(tasks, now: dt.datetime) -> dict:
    rows, problems, unavailable = [], [], []
    for t in tasks:
        name = t.get("name") or ""
        got = _query_task(name)
        row = {"name": name, "required": bool(t.get("required")),
               "max_gap_minutes": t.get("max_gap_minutes"),
               "note": t.get("note")}
        if not got.get("ok"):
            unavailable.append(name)
            row.update({"status": "warn", "error": got.get("error")})
            rows.append(row)
            continue
        last_run = _parse_ts(got.get("last_run"))
        gap_min = _age_minutes(last_run, now)
        # 计划任务"正在运行"是正常瞬态（20:00 证据刷新窗口等），不得判 fail：
        # 267009 = 0x41301 SCHED_S_TASK_RUNNING。
        is_running = (str(got.get("state") or "").lower() in ("running", "正在运行")
                      or got.get("last_result") == 267009)
        state_ok = (got.get("enabled") is not False
                    and str(got.get("state") or "").lower() in (
                        "ready", "running", "enabled", "已就绪", "正在运行",
                        "已启用", ""))
        result_ok = got.get("last_result") in (0, None) or is_running
        gap_ok = True
        max_gap = t.get("max_gap_minutes")
        if (not is_running) and _num(max_gap) and gap_min is not None and gap_min > float(max_gap):
            gap_ok = False
        row.update({"status": "ok", "state": got.get("state"),
                    "enabled": got.get("enabled"),
                    "last_run": got.get("last_run"),
                    "last_result": got.get("last_result"),
                    "next_run": got.get("next_run"),
                    "gap_minutes": gap_min, "source": got.get("source")})
        if not state_ok:
            row["status"] = "fail"
            problems.append("%s 状态异常（state=%s enabled=%s）"
                            % (name, got.get("state"), got.get("enabled")))
        if not result_ok:
            row["status"] = "fail"
            problems.append("%s 上次结果异常（last_result=%s）"
                            % (name, got.get("last_result")))
        if not gap_ok:
            row["status"] = "fail"
            problems.append("%s 上次运行距现在 %.0f min > 预期 %.0f min"
                            % (name, gap_min or 0, float(max_gap)))
        rows.append(row)
    if unavailable:
        status = "warn"
        detail = ("计划任务查询不可用（warn 不 fail）：%s；已查 %d 项"
                  % ("、".join(unavailable), len(rows)))
    elif problems:
        status = "fail"
        detail = "计划任务异常：" + "；".join(problems)
    else:
        status = "ok"
        detail = "；".join("%s：%s（last_run=%s last_result=%s gap=%s min）"
                          % (r["name"], r.get("state"), r.get("last_run"),
                             r.get("last_result"), r.get("gap_minutes"))
                          for r in rows)
    return _check("scheduled_tasks", "计划任务健康", status, detail,
                  tasks=rows, unavailable=unavailable)


# ------------------------------------------------------------ ⑥ push / ⑦ 文件

def check_push_config(work: Path) -> dict:
    path = Path(work) / "push_config.json"
    if not path.exists():
        return _check("push_config", "push 开关状态", "ok",
                      "push_config.json 缺失 → 视为 OFF（沿用「无文件即 OFF」口径）",
                      enabled=False, source=str(path), note="missing_as_off")
    data, err = _read_json(path)
    if err or not isinstance(data, dict):
        return _check("push_config", "push 开关状态", "warn",
                      "push_config.json 不可读/结构异常（%s）→ 按 OFF 记录"
                      % (err or "顶层非对象"), enabled=False, source=str(path))
    enabled = data.get("enabled")
    enabled = enabled if isinstance(enabled, bool) else None
    state_txt = {True: "enabled（B ON）", False: "off（B OFF）",
                 None: "未知（enabled 字段非 bool）"}[enabled]
    status = "ok" if enabled is not None else "warn"
    return _check("push_config", "push 开关状态", status,
                  "push_config.json：%s；updated_at=%s by=%s（enabled/off 均 OK，仅记录）"
                  % (state_txt, data.get("updated_at"), data.get("by")),
                  enabled=enabled, source=str(path),
                  updated_at=data.get("updated_at"), by=data.get("by"))


def check_required_files(work: Path) -> dict:
    work = Path(work)
    missing_required, missing_recommended = [], []
    daily = work / "daily.jsonl"
    events = work / "events.jsonl"
    if not daily.exists():
        missing_required.append("daily.jsonl")
    if not events.exists():
        missing_required.append("events.jsonl")
    acc = list(work.glob("acceptance_*.json"))
    if not acc:
        missing_required.append("acceptance_*.json（≥1）")
    recommended = {
        "review_*.json": list(work.glob("review_*.json")),
        "drills/drill_ledger_*.json": list(work.glob("drills/drill_ledger_*.json")),
        "audit_sample_3.json": ([work / "audit_sample_3.json"]
                                if (work / "audit_sample_3.json").exists() else []),
        "weekly_*.md": list(work.glob("weekly_*.md")),
    }
    for pattern, found in recommended.items():
        if not found:
            missing_recommended.append(pattern)
    if missing_required:
        status = "fail"
        detail = "必需文件缺失：%s" % "、".join(missing_required)
    elif missing_recommended:
        status = "warn"
        detail = "必需文件齐全；建议文件缺失（warn）：%s" % "、".join(missing_recommended)
    else:
        status = "ok"
        detail = ("必需文件齐全（daily/events/acceptance×%d）；建议文件齐全"
                  % len(acc))
    return _check("required_files", "work/huangzhong 必需文件", status, detail,
                  missing_required=missing_required,
                  missing_recommended=missing_recommended,
                  acceptance_count=len(acc),
                  review_count=len(list(work.glob("review_*.json"))))


def check_exec(repo_root: Path) -> dict:
    script = Path(repo_root) / "tools" / "huangzhong_review.py"
    if not script.exists():
        return _check("exec_selfcheck", "工具链可执行自检（--check-exec）", "warn",
                      "找不到 %s；跳过可执行自检" % script, path=str(script))
    cmd = [sys.executable or "python", str(script), "--help"]
    got = _run(cmd)
    if got.get("ok"):
        return _check("exec_selfcheck", "工具链可执行自检（--check-exec）", "ok",
                      "huangzhong_review.py --help rc=0（%d 字节 stdout）"
                      % len(got.get("stdout") or ""), path=str(script))
    return _check("exec_selfcheck", "工具链可执行自检（--check-exec）", "warn",
                  "huangzhong_review.py --help 执行失败（rc=%s；%s）"
                  % (got.get("rc"), (got.get("stderr") or "")[:200]),
                  path=str(script))


# ------------------------------------------------------------------ 报告渲染

def _aggregate(checks: list[dict]) -> str:
    statuses = [c.get("status") for c in checks]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _render_md(rep: dict) -> str:
    checks = rep.get("checks") or []
    summary = rep.get("summary") or {}
    md = ["# 黄忠工具链自监控心跳报告", "",
          "- 生成时间：%s" % rep.get("generated_at"),
          "- 数据根：`%s`；工作目录：`%s`" % (rep.get("repo_root"),
                                              rep.get("work_dir")),
          "- 总体状态：**%s**（ok=%s warn=%s fail=%s）"
          % (rep.get("status"), summary.get("ok"), summary.get("warn"),
             summary.get("fail")),
          "- 只读心跳：默认不写盘；由 --out-dir 触发写本报告", "",
          "## 一、检查项", "",
          "| # | 检查项 | 状态 | 明细 |", "|---|---|---|---|"]
    for i, c in enumerate(checks, 1):
        md.append("| %d | %s（%s） | %s | %s |"
                  % (i, c.get("name"), c.get("id"), c.get("status"),
                     str(c.get("detail") or "").replace("|", "\\|")))
    push = rep.get("push") or {}
    md += ["", "## 二、push 开关", "",
           "- 状态：%s（source=`%s`；updated_at=%s）"
           % (push.get("state_txt") or push.get("enabled"), push.get("source"),
              push.get("updated_at")), "",
           "## 三、计划任务", ""]
    for row in rep.get("scheduled_tasks") or []:
        md.append("- %s：status=%s state=%s enabled=%s last_run=%s "
                  "last_result=%s gap_minutes=%s（max_gap=%s）"
                  % (row.get("name"), row.get("status"), row.get("state"),
                     row.get("enabled"), row.get("last_run"),
                     row.get("last_result"), row.get("gap_minutes"),
                     row.get("max_gap_minutes")))
    md += ["", "## 四、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_health.py --json",
           "```",
           "",
           "> 退出码：0=ok / 1=warn / 2=fail；本报告为只读巡检材料，供告警输入。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(work_dir: Path | str | None = None, repo_root: Path | str | None = None,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS, check_exec_flag: bool = False,
        out_dir: Path | str | None = None, now: dt.datetime | None = None,
        tasks: list[dict] | None = None) -> dict:
    """执行心跳检查；默认不写盘（out_dir 给定时写 md 报告）。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    tasks = list(tasks) if tasks is not None else list(DEFAULT_TASKS)
    warnings: list[str] = []

    checks = [
        check_acceptance(work, now),
        check_events(work, now),
        check_review(work, now),
        check_evidence(root, float(max_age_hours), now),
        check_scheduled_tasks(tasks, now),
        check_push_config(work),
        check_required_files(work),
    ]
    if check_exec_flag:
        checks.append(check_exec(root))
    status = _aggregate(checks)
    summary = {"ok": sum(1 for c in checks if c["status"] == "ok"),
               "warn": sum(1 for c in checks if c["status"] == "warn"),
               "fail": sum(1 for c in checks if c["status"] == "fail"),
               "skipped": sum(1 for c in checks if c["status"] == "skipped"),
               "total": len(checks)}
    push_check = next((c for c in checks if c["id"] == "push_config"), {})
    rep = {
        "ok": status != "fail",
        "tool": "huangzhong_health.py",
        "version": TOOL_VERSION,
        "generated_at": _iso(now),
        "status": status,
        "repo_root": str(root),
        "work_dir": str(work),
        "read_only": True,
        "checks": checks,
        "summary": summary,
        "push": {"enabled": push_check.get("enabled"),
                 "state_txt": ("enabled（B ON）" if push_check.get("enabled") is True
                               else "off（B OFF）"
                               if push_check.get("enabled") is False else "未知"),
                 "source": push_check.get("source"),
                 "updated_at": push_check.get("updated_at"),
                 "by": push_check.get("by")},
        "scheduled_tasks": next((c.get("tasks") for c in checks
                                 if c["id"] == "scheduled_tasks"), []) or [],
        "warnings": warnings,
    }
    rep["report_md"] = None
    if out_dir:
        out = Path(out_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
            mp = out / ("2026-10-31_黄忠工具链心跳_%s.md"
                        % now.strftime("%Y%m%d_%H%M%S"))
            mp.write_text(_render_md(rep), encoding="utf-8")
            rep["report_md"] = str(mp)
        except OSError as e:
            warnings.append("md 报告写入失败：%s: %s" % (type(e).__name__, e))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠工具链自监控心跳（只读；默认不写盘）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS,
                    help="证据年龄上限（小时，默认 48）")
    ap.add_argument("--check-exec", action="store_true",
                    help="额外跑 huangzhong_review.py --help 确认工具可执行")
    ap.add_argument("--out-dir", default="",
                    help="写 md 报告到该目录（默认不写盘；常用 C:\\项目\\deliverables）")
    args = ap.parse_args(argv)
    rep = run(work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              max_age_hours=args.max_age_hours,
              check_exec_flag=args.check_exec,
              out_dir=args.out_dir or None)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("黄忠工具链心跳：status=%s（ok=%d warn=%d fail=%d）"
              % (rep["status"], rep["summary"]["ok"],
                 rep["summary"]["warn"], rep["summary"]["fail"]))
        for c in rep["checks"]:
            print("  [%s] %s：%s" % (c["status"], c["name"], c["detail"]))
        if rep.get("report_md"):
            print("报告：%s" % rep["report_md"])
    return {"ok": 0, "warn": 1, "fail": 2}.get(rep["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
