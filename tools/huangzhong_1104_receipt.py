# -*- coding: utf-8 -*-
"""黄忠 11-04 观察期复核回执自动生成器 v1（只读编排，草案非最终回执）。

用途：2026-11-04 10:00 用一条命令生成「观察期复核回执草案」，减少人工拼装：
  1) tools/huangzhong_review.py --json                       六条复核（必跑）
  2) tools/huangzhong_1104_preflight.py --json               09:30 前置检查（若存在）
  3) tools/huangzhong_go_no_go.py --json                     Go/No-Go 决策草案（若存在）
  4) tools/huangzhong_coverage_trend.py --days 14 --json     覆盖率趋势与 11-04 投影（若存在）
  5) tools/huangzhong_incidents.py verify --json             事故台账校验（若存在）

产物：
  · 中文回执草案（九段结构，对齐 runbook v6 §十 / 一键流程 v1.1 §六）：
    <out-dir>/<date>_黄忠观察期复核回执_草案_<ts>.md
  · stdout JSON 摘要（--json）

边界：
  · 只读编排：不改 work/、不触发验收、不改仓库文件；唯一写盘 = 回执草案 md
    （以及子工具自身在 out-dir 落的报告文件）。
  · fail-visible：子工具缺失/坏 JSON/命令失败如实标注，不编造数据。
  · 退出码：0=草案生成成功（GO/条件化 GO/NO-GO 均 0，建议写在内容里）；
    2=数据错误（review 报告缺失/不可读 → 不出草案）。

设计约定（与并行工具一致）：
  · 所有外部命令经模块级 `_run(cmd, cwd=...)` 执行，测试可 monkeypatch；
  · 所有路径可用 CLI 覆盖：--repo-root / --work-dir / --out-dir / --date / --now。
· 目标日期参数化（HZ-FINAL-DATE-20261104）：默认 --date 2026-11-04，行为与 v1 完全
  一致；显式 --target-date 2026-11-10（优先于 --date）时，草案标题/九段内日期引用/
  文件名/preflight 子调用一并切到目标日期，用于 11-10 最终验收复算。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_DATE = "2026-11-04"
TARGET_HOUR = 10
COVERAGE_THRESHOLD = 0.10

REVIEW_TOOL = "tools/huangzhong_review.py"
PREFLIGHT_TOOL = "tools/huangzhong_1104_preflight.py"
GO_NO_GO_TOOL = "tools/huangzhong_go_no_go.py"
TREND_TOOL = "tools/huangzhong_coverage_trend.py"
INCIDENTS_TOOL = "tools/huangzhong_incidents.py"

REVIEW_TIMEOUT = 180.0
DEFAULT_TIMEOUT = 300.0
MAX_LIST = 20

TOOL_NAME = "huangzhong_1104_receipt.py"
VERSION = "v1 (2026-10-31)"

OK, FAIL, SKIP = "✓", "✗", "skip"

EVIDENCE_HINTS = {
    "①": "work/huangzhong/acceptance_*.json（PASS 按 commit 去重）+ drills 台账",
    "②": "work/huangzhong/drills/drill_ledger_*.json + 受控演练记录",
    "③": "work/huangzhong/reject_review*.json（逐单人工复核）",
    "④": "PASS 单 generated_at+7d 满窗；work/huangzhong/incidents.jsonl",
    "⑤": "各验收单 evidence 段（基线/红队 generated_at/age/stale）",
    "⑥": "work/huangzhong/audit_sample_3.json（赵云抽查 3 单）",
}

STATUS_TEXT = {
    "✓": "✓ 达标",
    "✗": "✗ 未达标",
    "pending": "pending 待补证",
    "tracking": "tracking 跟踪中",
}


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts is not None else None


def _parse_ts(raw) -> dt.datetime | None:
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=_tz())
    if not isinstance(raw, (str, int, float)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    if isinstance(raw, (int, float)) or text.replace(".", "", 1).isdigit():
        try:
            return dt.datetime.fromtimestamp(float(text), tz=_tz())
        except (OverflowError, OSError, ValueError):
            return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def _fmt_pct(value) -> str:
    return "%.2f%%" % (value * 100) if _num(value) else "—"


def _fmt_hours(value) -> str:
    return "%.1fh" % value if _num(value) else "—"


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return list(rows[:limit])


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _log(msg: str) -> None:
    """进度日志走 stderr（stdout 留给 --json 摘要；测试可 monkeypatch 静音）。"""
    print("[receipt] " + str(msg), file=sys.stderr)


def _run(cmd: list, cwd: Path | str | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> dict:
    """执行外部命令（模块级；测试可 monkeypatch）。不抛异常，失败如实返回。"""
    cmd = [str(c) for c in cmd]
    started = _now()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "cmd": cmd, "cwd": str(cwd) if cwd else None,
                "elapsed_s": round((_now() - started).total_seconds(), 2)}
    return {"ok": True, "rc": proc.returncode,
            "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "error": None, "cmd": cmd,
            "cwd": str(cwd) if cwd else None,
            "elapsed_s": round((_now() - started).total_seconds(), 2)}


def _extract_json(text) -> tuple[object | None, str | None]:
    """从子进程 stdout 抽取 JSON（容忍前导日志行）。"""
    raw = (text or "").strip()
    if not raw:
        return None, "stdout 为空"
    try:
        return json.loads(raw), None
    except ValueError:
        pass
    start = raw.find("{")
    if start < 0:
        return None, "stdout 无 JSON 对象"
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[start:])
        return obj, None
    except ValueError as exc:
        return None, "stdout JSON 解析失败：%s" % exc


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _tool_path(repo: Path, rel: str) -> Path:
    return Path(repo) / rel


def _has_tool(repo: Path, rel: str) -> bool:
    return _tool_path(repo, rel).is_file()


def _py() -> str:
    return sys.executable or "python"


def _latest_review(work: Path) -> Path | None:
    work = Path(work)
    if not work.is_dir():
        return None
    files = [p for p in work.glob("review_*.json")
             if p.is_file() and p.name != "review_latest.json"]
    if not files:
        return None
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return files[-1]


def _item_status_ok(status) -> bool:
    return str(status or "").strip() in ("✓", "OK", "ok", "pass")


def _item_status_tracking(status) -> bool:
    return str(status or "").strip() == "tracking"


def _parse_target(date_value: str | None) -> dt.datetime:
    raw = str(date_value or DEFAULT_DATE).strip()
    try:
        if "T" in raw or " " in raw:
            target = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            day = dt.date.fromisoformat(raw)
            target = dt.datetime(day.year, day.month, day.day,
                                 TARGET_HOUR, 0)
    except ValueError as exc:
        raise ValueError(
            "--target-date/--date 无法解析：%r（支持 2026-11-10 或 ISO 日期时间）"
            % raw
        ) from exc
    return target if target.tzinfo else target.replace(tzinfo=_tz())


def _target_label(target: dt.datetime) -> str:
    """目标日期短标签：默认 '11-04'，显式 11-10 → '11-10'。"""
    return target.strftime("%m-%d")


def _target_label_hm(target: dt.datetime) -> str:
    """目标日期+时刻标签：默认 '11-04 10:00'。"""
    return "%s %s" % (target.strftime("%m-%d"), target.strftime("%H:%M"))


def _target_label_from_iso(raw) -> str:
    """从 rep 的 target_time（ISO）反推短标签；解析失败回退默认 '11-04'。"""
    target = _parse_ts(raw)
    return _target_label(target if isinstance(target, dt.datetime)
                         else _parse_target(None))


# ------------------------------------------------------------------ 五个子步骤

def _step(tool: str, cmd: list, cwd: Path, timeout: float,
          name: str) -> dict:
    """执行一个子工具并返回统一结果结构。"""
    run = _run(cmd, cwd=cwd, timeout=timeout)
    _log("%s rc=%s elapsed=%ss" % (name, run.get("rc"), run.get("elapsed_s")))
    return {"name": name, "tool": tool, "cmd": run.get("cmd") or cmd,
            "rc": run.get("rc"), "ok": run.get("ok"),
            "error": run.get("error"),
            "elapsed_s": run.get("elapsed_s"),
            "stdout": run.get("stdout") or "",
            "stderr_tail": (run.get("stderr") or "")[-800:]}


def _mark(step: dict, status: str, note: str | None = None) -> dict:
    step["status"] = status
    if note:
        step["note"] = note
    return step


def step_review(repo: Path, work: Path, custom_work: bool) -> dict:
    """① 六条复核（必跑；报告缺失/不可读 = 数据错误 → 不出草案）。"""
    tool = REVIEW_TOOL
    if not _has_tool(repo, tool):
        step = {"name": "review", "tool": tool, "cmd": [], "rc": None,
                "ok": False, "status": "missing",
                "error": "复核工具缺失：%s" % tool}
        return step
    cmd = [_py(), "-X", "utf8", tool, "--json"]
    if custom_work:
        cmd += ["--work-dir", str(work)]
    step = _step(tool, cmd, repo, REVIEW_TIMEOUT, "review")
    parsed, parse_err = _extract_json(step.get("stdout"))
    report_path = None
    if isinstance(parsed, dict):
        raw_path = parsed.get("report_json")
        if isinstance(raw_path, str) and raw_path.strip():
            candidate = Path(raw_path.strip())
            if candidate.is_file():
                report_path = candidate
    if report_path is None:
        report_path = _latest_review(work)
    if report_path is None:
        kind = "缺少" if (step.get("rc") in (0, 1, None)) else "执行异常（rc=%s）" % step.get("rc")
        _mark(step, "error", "review 报告%s；work=%s（数据错误，不出草案）"
              % (kind, work))
        step["data"] = None
        step["path"] = None
        return step
    data, read_err = _read_json(report_path)
    if read_err is not None or not isinstance(data, dict):
        _mark(step, "error", "review 报告不可读：%s（%s）"
              % (report_path.name, read_err or "顶层不是对象"))
        step["data"] = None
        step["path"] = str(report_path)
        return step
    for field in ("items", "summary"):
        if field not in data:
            _mark(step, "error", "review 报告缺 %s 字段（fail-visible）" % field)
            step["data"] = None
            step["path"] = str(report_path)
            return step
    _mark(step, "ok", "六条复核 %d/%d ✓；报告 %s"
          % (int(data["summary"].get("passed") or 0),
             int(data["summary"].get("total") or 0), report_path.name))
    step["data"] = data
    step["path"] = str(report_path)
    return step


def step_preflight(repo: Path, work: Path, out: Path,
                   custom_work: bool, target: dt.datetime | None = None,
                   now: dt.datetime | None = None) -> dict:
    tool = PREFLIGHT_TOOL
    if not _has_tool(repo, tool):
        return {"name": "preflight", "tool": tool, "cmd": [], "rc": None,
                "ok": False, "status": "missing", "data": None,
                "error": "preflight 工具缺失（已跳过）"}
    cmd = [_py(), "-X", "utf8", tool, "--json", "--out-dir", str(out)]
    # 仅显式目标日期/时钟时下发（默认路径保持 v1 命令行完全一致）
    if isinstance(target, dt.datetime):
        cmd += ["--target-date", target.strftime("%Y-%m-%dT%H:%M")]
    if isinstance(now, dt.datetime):
        cmd += ["--now", _iso(now)]
    if custom_work:
        cmd += ["--work-dir", str(work)]
    step = _step(tool, cmd, repo, DEFAULT_TIMEOUT, "preflight")
    data, err = _extract_json(step.get("stdout"))
    if err is not None or not isinstance(data, dict):
        _mark(step, "error", "preflight JSON 不可用：%s（按跳过处理，不阻塞草案）" % err)
        step["data"] = None
        return step
    _mark(step, "ok", "ready=%s blockers=%d warnings=%d"
          % (data.get("ready"), len(data.get("blockers") or []),
             len(data.get("warnings") or [])))
    step["data"] = data
    return step


def step_go_no_go(repo: Path, work: Path, out: Path,
                  custom_work: bool) -> dict:
    tool = GO_NO_GO_TOOL
    if not _has_tool(repo, tool):
        return {"name": "go_no_go", "tool": tool, "cmd": [], "rc": None,
                "ok": False, "status": "missing", "data": None,
                "error": "Go/No-Go 工具缺失（已跳过，建议按 review 推导）"}
    cmd = [_py(), "-X", "utf8", tool, "--json", "--draft-dir", str(out)]
    if custom_work:
        cmd += ["--work-dir", str(work)]
    step = _step(tool, cmd, repo, DEFAULT_TIMEOUT, "go_no_go")
    data, err = _extract_json(step.get("stdout"))
    if err is not None or not isinstance(data, dict):
        _mark(step, "error", "Go/No-Go JSON 不可用：%s（按跳过处理）" % err)
        step["data"] = None
        return step
    if data.get("ok") is False:
        _mark(step, "error", "Go/No-Go 数据错误：%s"
              % (data.get("error") or "ok=false"))
        step["data"] = None
        return step
    decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}
    _mark(step, "ok", "决策=%s（rc=%s；rc=1/2 属正常业务返回码）"
          % (decision.get("label") or decision.get("code") or "?",
             step.get("rc")))
    step["data"] = data
    return step


def step_coverage_trend(repo: Path, work: Path, out: Path,
                        target: dt.datetime, custom_work: bool) -> dict:
    tool = TREND_TOOL
    if not _has_tool(repo, tool):
        return {"name": "coverage_trend", "tool": tool, "cmd": [], "rc": None,
                "ok": False, "status": "missing", "data": None,
                "error": "覆盖率趋势工具缺失（已跳过）"}
    target_text = target.strftime("%Y-%m-%dT%H:%M")
    cmd = [_py(), "-X", "utf8", tool, "--days", "14",
           "--target", target_text, "--json", "--out-dir", str(out)]
    if custom_work:
        cmd += ["--work-dir", str(work)]
    step = _step(tool, cmd, repo, DEFAULT_TIMEOUT, "coverage_trend")
    data, err = _extract_json(step.get("stdout"))
    if err is not None or not isinstance(data, dict):
        _mark(step, "error", "覆盖率趋势 JSON 不可用：%s（按跳过处理）" % err)
        step["data"] = None
        return step
    proj = data.get("projection") if isinstance(data.get("projection"), dict) else {}
    _mark(step, "ok", "近 7 日 runs=%s merge=%s；投影 %s"
          % ((data.get("last7") or {}).get("runs"),
             (data.get("last7") or {}).get("merge_done"),
             _fmt_pct(proj.get("projected_coverage"))))
    step["data"] = data
    return step


def step_incidents(repo: Path, work: Path, custom_work: bool) -> dict:
    tool = INCIDENTS_TOOL
    if not _has_tool(repo, tool):
        return {"name": "incidents", "tool": tool, "cmd": [], "rc": None,
                "ok": False, "status": "missing", "data": None,
                "error": "事故台账工具缺失（已跳过）"}
    cmd = [_py(), "-X", "utf8", tool, "verify", "--json"]
    if custom_work:
        cmd += ["--work-dir", str(work)]
    step = _step(tool, cmd, repo, DEFAULT_TIMEOUT, "incidents")
    data, err = _extract_json(step.get("stdout"))
    if err is not None or not isinstance(data, dict):
        _mark(step, "error", "事故台账 JSON 不可用：%s（按跳过处理）" % err)
        step["data"] = None
        return step
    if data.get("ok") is False:
        _mark(step, "error", "事故台账不可读：%s"
              % (data.get("error") or "ok=false"))
        step["data"] = None
        return step
    if data.get("exists") is False:
        _mark(step, "ok", "台账缺失（空台账返回；需人工确认无已知事故）")
    else:
        _mark(step, "ok", "台账 %s 行、issues=%s"
              % (data.get("rows"), data.get("issue_count")))
    step["data"] = data
    return step


# ------------------------------------------------------------------ 建议推导

def _derive_suggestion(review: dict, gng: dict | None,
                       trend: dict | None,
                       target: dt.datetime | None = None) -> dict:
    """优先用 go_no_go 工具决策；缺工具/坏数据时按六条+覆盖率兜底推导。"""
    date_label = (_target_label(target) if isinstance(target, dt.datetime)
                  else DEFAULT_DATE[5:])
    if gng and isinstance(gng.get("decision"), dict):
        d = gng["decision"]
        return {"code": d.get("code") or "UNKNOWN",
                "label": d.get("label") or "（未提供）",
                "suggestion": d.get("suggestion") or "",
                "rationale": list(d.get("rationale") or []),
                "gaps": list(d.get("gaps") or []),
                "conditions": list(d.get("conditions") or []),
                "tracking_note": d.get("tracking_note"),
                "l3_required": bool(d.get("l3_required", True)),
                "source": "tools/huangzhong_go_no_go.py"}
    summary = review.get("summary") or {}
    kpi = review.get("kpi") or {}
    items = [i for i in (review.get("items") or []) if isinstance(i, dict)]
    all_ok = bool(summary.get("overall_pass"))
    passed = int(summary.get("passed") or 0)
    coverage = kpi.get("coverage_rate") if _num(kpi.get("coverage_rate")) else None
    non_ok = [i for i in items if not _item_status_ok(i.get("status"))]
    only_tracking = bool(non_ok) and all(_item_status_tracking(i.get("status"))
                                         for i in non_ok)
    gaps = [{"id": i.get("id"), "title": i.get("title"),
             "status": i.get("status"), "measured": i.get("measured")}
            for i in non_ok]
    rationale: list[str] = []
    conditions: list[str] = []
    tracking_note = None
    if only_tracking:
        tracking_note = ("仅时间项待满窗：%s（%s 满窗后重跑复核，0 事故即 ④ 转 ✓）"
                         % ("、".join(i.get("id") for i in non_ok), date_label))
        rationale.append(tracking_note)
    if all_ok and coverage is not None and coverage >= COVERAGE_THRESHOLD:
        code, label = "GO", "建议 GO"
        suggestion = ("六条全 ✓ 且覆盖率 %.2f%% ≥ %.0f%%：建议接入「merge 前置·观察不阻断」；"
                      "需老田 L3 单独批准后执行（本草案不构成批准）"
                      % (coverage * 100, COVERAGE_THRESHOLD * 100))
        rationale.insert(0, "六条全 ✓（%d/%d），kpi 覆盖率 %.2f%% ≥ %.0f%%"
                         % (passed, len(items), coverage * 100,
                            COVERAGE_THRESHOLD * 100))
    elif all_ok:
        code, label = "CONDITIONAL_GO", "建议 条件化 GO"
        suggestion = ("六条已全 ✓，但覆盖率数据不足/低于 %.0f%%：建议「条件化 GO」——"
                      "先批覆盖率提升方案，达标后再接 merge 前置；需老田 L3 单独批准"
                      % (COVERAGE_THRESHOLD * 100))
        rationale.insert(0, "六条全 ✓（%d/%d）但覆盖率 %s"
                         % (passed, len(items), _fmt_pct(coverage)))
        gaps.append({"id": "COV", "title": "覆盖率不足/不可判定",
                     "status": "gap", "measured": _fmt_pct(coverage)})
    else:
        code, label = "NO_GO", "建议 NO-GO"
        if only_tracking:
            suggestion = ("六条仅剩 ④ 时间项待满窗（当前 %d/%d ✓）：建议按「继续观察」处理，"
                          "11-05~11-06 满窗后二次复核；本次不接 merge 前置（Go 需老田 L3 单独批准）"
                          % (passed, len(items)))
        else:
            suggestion = ("六条未全 ✓（%d/%d）：按缺项补证后重跑复核与本草案；"
                          "本次不接 merge 前置（Go 需老田 L3 单独批准）"
                          % (passed, len(items)))
        rationale.insert(0, "六条未全 ✓（%d/%d）" % (passed, len(items)))
        for gap in gaps:
            rationale.append("%s %s：%s（%s）" % (gap.get("id"), gap.get("title"),
                                                 gap.get("measured"),
                                                 STATUS_TEXT.get(
                                                     str(gap.get("status") or ""),
                                                     gap.get("status"))))
    if code == "NO_GO" and isinstance(trend, dict):
        proj = trend.get("projection") or {}
        if _num(proj.get("projected_coverage")):
            rationale.append("覆盖率趋势投影（%s）：%.2f%%（口径见覆盖率趋势报告）"
                             % (str(proj.get("target") or "")[:16],
                                proj["projected_coverage"] * 100))
    return {"code": code, "label": label, "suggestion": suggestion,
            "rationale": rationale, "gaps": gaps, "conditions": conditions,
            "tracking_note": tracking_note, "l3_required": True,
            "source": "按 review 六条 + kpi 覆盖率兜底推导（go_no_go 工具不可用）"}


# ------------------------------------------------------------------ 报告渲染

def _review_items(review: dict) -> list[dict]:
    rows = []
    for item in review.get("items") or []:
        if not isinstance(item, dict):
            continue
        rows.append({"id": item.get("id"),
                     "title": item.get("title") or "",
                     "status": item.get("status"),
                     "status_text": STATUS_TEXT.get(str(item.get("status") or ""),
                                                    item.get("status")),
                     "measured": item.get("measured"),
                     "target": item.get("target")})
    return rows


def _coverage_view(review: dict, gng: dict | None, trend: dict | None) -> dict:
    kpi = review.get("kpi") or {}
    last7 = (trend or {}).get("last7") or {}
    proj = (trend or {}).get("projection") or {}
    gng_cov = (gng or {}).get("coverage") or {}
    runs = kpi.get("runs")
    if not _num(runs):
        runs = last7.get("runs")
    merge_done = kpi.get("merge_done")
    if not _num(merge_done):
        merge_done = last7.get("merge_done")
    coverage = kpi.get("coverage_rate")
    if not _num(coverage):
        coverage = last7.get("coverage")
    unique_commit = gng_cov.get("unique_commit")
    if not _num(unique_commit):
        unique_commit = last7.get("unique_commit")
    unique_coverage = gng_cov.get("unique_coverage")
    if not _num(unique_coverage):
        unique_coverage = last7.get("unique_coverage")
    return {"runs": runs, "merge_done": merge_done, "coverage": coverage,
            "unique_commit": unique_commit, "unique_coverage": unique_coverage,
            "projection": proj,
            "threshold_pass_now": _num(coverage) and coverage >= COVERAGE_THRESHOLD,
            "threshold_pass_projected":
                proj.get("threshold_pass") if isinstance(proj, dict) else None,
            "threshold": (trend or {}).get("threshold") or COVERAGE_THRESHOLD}


def _maturity_view(preflight: dict | None, gng: dict | None,
                   review: dict) -> dict:
    if preflight and isinstance(preflight.get("maturity"), dict):
        return dict(preflight["maturity"])
    if gng and isinstance(gng.get("tracking"), dict):
        return dict(gng["tracking"])
    detail = {}
    for item in review.get("items") or []:
        if isinstance(item, dict) and str(item.get("id")) == "④":
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            break
    return {"mature_count": detail.get("mature_count"),
            "tracking_count": detail.get("tracking_count"),
            "mature_at_1030": detail.get("mature_at_1030"),
            "mature_samples": detail.get("mature_samples"),
            "expected_mature_by_target": detail.get("expected_mature_by_target"),
            "earliest_window_end": detail.get("earliest_window_end"),
            "next_window_end": detail.get("next_window_end"),
            "note": detail.get("note") or "（maturity 明细不可得）"}


def _incidents_view(step_incidents: dict, preflight: dict | None) -> dict:
    data = step_incidents.get("data") if isinstance(step_incidents.get("data"), dict) else None
    if data is not None:
        return {"source": "tools/huangzhong_incidents.py verify",
                "exists": data.get("exists"), "rows": data.get("rows"),
                "bad_lines": data.get("bad_lines"),
                "issue_count": data.get("issue_count"),
                "issues": _cap(data.get("issues") or []),
                "path": data.get("path")}
    pre = (preflight or {}).get("incidents") if isinstance(preflight, dict) else None
    if isinstance(pre, dict):
        return {"source": "tools/huangzhong_1104_preflight.py",
                "exists": pre.get("exists"), "rows": pre.get("rows"),
                "bad_lines": pre.get("bad_lines"), "issue_count": None,
                "issues": [], "path": pre.get("path"),
                "note": pre.get("note")}
    return {"source": None, "exists": None, "rows": None, "bad_lines": None,
            "issue_count": None, "issues": [], "path": None,
            "note": "事故台账状态不可得（工具缺失且 preflight 未提供）"}


def _latest_confirm(work: Path) -> dict | None:
    """读取最近一份 incidents 人工确认记录（work/incidents_confirm_*.md）。

    返回 {path, confirmer, time, conclusion}；无可读记录 → None（不编造）。
    """
    try:
        files = sorted(Path(work).glob("incidents_confirm_*.md"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        info = {"path": str(f), "confirmer": None, "time": None,
                "conclusion": None}
        for line in text.splitlines():
            s = line.strip().lstrip("-").strip()
            if s.startswith("确认人："):
                info["confirmer"] = s.split("：", 1)[1].strip()
            elif s.startswith("确认时间："):
                info["time"] = s.split("：", 1)[1].strip()
            elif s.startswith("结论："):
                info["conclusion"] = s.split("：", 1)[1].strip()
        if info["confirmer"] or info["conclusion"]:
            return info
    return None


def _evidence_view(preflight: dict | None, review: dict) -> dict:
    ev = (preflight or {}).get("evidence") if isinstance(preflight, dict) else None
    out = {"baseline": None, "redteam": None, "max_age_hours": None}
    if isinstance(ev, dict):
        out["baseline"] = ev.get("baseline")
        out["redteam"] = ev.get("redteam")
        out["max_age_hours"] = ev.get("max_age_hours")
        out["status"] = ev.get("status")
        return out
    detail = {}
    for item in review.get("items") or []:
        if isinstance(item, dict) and str(item.get("id")) == "⑤":
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            break
    out["note"] = "证据年龄明细不可得（preflight 缺失）；以 review ⑤ 实测为准：%s" % (
        detail.get("note") or "—")
    return out


def _collect_warnings(steps: list[dict], review: dict, preflight: dict | None,
                      gng: dict | None, trend: dict | None,
                      suggestion: dict) -> list[str]:
    def _text(item) -> str:
        if isinstance(item, dict):
            name = item.get("check") or item.get("name") or item.get("kind") or "告警"
            detail = item.get("message") or item.get("detail") or item.get("note") \
                or json.dumps(item, ensure_ascii=False)
            return "%s：%s" % (name, detail)
        return str(item)

    warnings: list[str] = []
    for step in steps:
        if step.get("status") in ("missing", "error") and step.get("note"):
            warnings.append("%s：%s" % (step.get("name"), step["note"]))
    for name, data in (("review", review), ("preflight", preflight),
                       ("go_no_go", gng), ("coverage_trend", trend)):
        if isinstance(data, dict):
            for w in data.get("warnings") or []:
                warnings.append("%s：%s" % (name, _text(w)))
    for cond in suggestion.get("conditions") or []:
        warnings.append("建议条件：%s" % _text(cond))
    if not warnings:
        warnings.append("无（子工具均正常返回；仍需人工核对具体证据）")
    return warnings


def _evidence_paths(review_step: dict, preflight: dict | None,
                    gng: dict | None, trend: dict | None,
                    incidents: dict | None) -> dict:
    """汇总各子工具报告路径（供 §七 证据清单渲染）。"""
    trend_paths = (trend or {}).get("paths") or {}
    return {
        "review": review_step.get("path"),
        "preflight": (preflight or {}).get("report_md"),
        "go_no_go": ((gng or {}).get("draft") or {}).get("path"),
        "coverage_trend_md": trend_paths.get("md"),
        "coverage_trend_json": trend_paths.get("json"),
        "incidents": (incidents or {}).get("path"),
    }


def _commands_table(steps: list[dict]) -> list[str]:
    lines = ["| 步骤 | 工具 | 返回码 | 状态 | 说明 |", "|---|---|---|---|---|"]
    for step in steps:
        status = {"ok": "✓ 已执行", "missing": "skip 缺失", "error": "✗ 异常"}.get(
            str(step.get("status")), str(step.get("status")))
        lines.append("| %s | `%s` | %s | %s | %s |"
                     % (_cell(step.get("name")), _cell(step.get("tool")),
                        _cell(step.get("rc")),
                        _cell(status),
                        _cell(step.get("note") or step.get("error") or "—")))
    return lines


def render_md(rep: dict) -> str:
    """渲染九段中文回执草案（结构对齐 runbook v6 §十 / 一键流程 v1.1 §六）。"""
    target = rep.get("target_time") or ""
    date_text = rep.get("date") or DEFAULT_DATE
    target_dt = _parse_ts(rep.get("target_time"))
    if isinstance(target_dt, dt.datetime):
        date_label = _target_label(target_dt)
        date_label_hm = _target_label_hm(target_dt)
    else:
        date_label = DEFAULT_DATE[5:]
        date_label_hm = "%s %02d:00" % (date_label, TARGET_HOUR)
    review = rep.get("review") or {}
    items = _review_items(review)
    summary = review.get("summary") or {}
    kpi = review.get("kpi") or {}
    preflight = rep.get("preflight")
    gng = rep.get("go_no_go")
    trend = rep.get("coverage_trend")
    cov = rep.get("coverage") or {}
    maturity = rep.get("maturity") or {}
    incidents = rep.get("incidents") or {}
    evidence = rep.get("evidence") or {}
    suggestion = rep.get("suggestion") or {}
    warnings = rep.get("warnings") or []
    steps = rep.get("steps") or []

    lines: list[str] = []
    lines.append("# 黄忠观察期复核回执（草案）· %s" % date_text)
    lines.append("")
    lines.append("> **状态：草案（非最终回执）**——本文件由 `tools/%s` 自动拼装，"
                 "数据全部来自只读子工具；需黄忠自证、赵云抽查、**老田签字**后生效。" % TOOL_NAME)
    lines.append("> 工单：`WO-HZ-RECEIPT-20261031` ｜ 生成：%s ｜ 目标复核：%s"
                 " ｜ 口径：认证标准 v2 + runbook v6 §十"
                 % (rep.get("generated_at") or "—", target or "—"))
    lines.append("> 只读声明：不触发验收、不改 `work/`、不改仓库文件；唯一写盘 = 本草案（子工具报告落 out-dir）。")
    lines.append("")

    # ---- 一、复核时间与环境
    lines.append("## 一、复核时间与环境")
    lines.append("")
    lines.append("- 生成时间：`%s`（Asia/Shanghai）" % (rep.get("generated_at") or "—"))
    lines.append("- 目标复核时刻：`%s`（--date 解析；④ 满窗按此时刻判定）" % (target or "—"))
    lines.append("- 生成工具：`tools/%s` %s（只读编排；子工具互相独立）"
                 % (TOOL_NAME, VERSION))
    lines.append("- 仓库根：`%s`" % (rep.get("repo_root") or "—"))
    lines.append("- 数据目录：`%s`" % (rep.get("work_dir") or "—"))
    lines.append("- 输出目录：`%s`" % (rep.get("out_dir") or "—"))
    lines.append("- 时区：Asia/Shanghai ｜ 六条判据：认证标准 v2（≥10 PASS / ≥1 演练 / 误拒<5% / "
                 "漏放=0 / 证据 100%+≤48h / 抽查 3/3）")
    lines.append("")
    lines.append("**子工具执行**")
    lines.append("")
    lines.extend(_commands_table(steps))
    lines.append("")
    baseline = evidence.get("baseline") if isinstance(evidence.get("baseline"), dict) else {}
    redteam = evidence.get("redteam") if isinstance(evidence.get("redteam"), dict) else {}
    lines.append("- 证据年龄（preflight 口径，门 %sh）：基线 `%s`（age≈%s，stale=%s）；"
                 "红队 `%s`（age≈%s，stale=%s）%s"
                 % (_fmt_num(evidence.get("max_age_hours") or 48),
                    baseline.get("generated_at") or "—",
                    _fmt_hours(baseline.get("age_hours")),
                    baseline.get("stale"),
                    redteam.get("generated_at") or "—",
                    _fmt_hours(redteam.get("age_hours")),
                    redteam.get("stale"),
                    "" if (evidence.get("baseline") or evidence.get("redteam"))
                    else "（明细不可得：%s）" % (evidence.get("note") or "preflight 缺失")))
    lines.append("")

    # ---- 二、六条判据
    lines.append("## 二、六条判据")
    lines.append("")
    lines.append("**overall_pass = %s（%s/%s ✓）**；仅六条全 ✓ 才 overall_pass=true。"
                 % (summary.get("overall_pass"), summary.get("passed"),
                    summary.get("total")))
    lines.append("")
    lines.append("| # | 判据 | 状态 | 实测 | 证据 |")
    lines.append("|---|---|---|---|---|")
    if items:
        for item in items:
            lines.append("| %s | %s | %s | %s | %s |"
                         % (_cell(item.get("id")), _cell(item.get("title")),
                            _cell(item.get("status_text") or item.get("status")),
                            _cell(item.get("measured")),
                            _cell(EVIDENCE_HINTS.get(str(item.get("id")), "—"))))
    else:
        lines.append("| — | 六条明细不可得 | — | — | review 报告异常 |")
    lines.append("")
    if kpi:
        lines.append("- KPI（review）：runs=%s / merge_done=%s / 覆盖率=%s / 窗口=%s 天"
                     % (_fmt_num(kpi.get("runs")), _fmt_num(kpi.get("merge_done")),
                        _fmt_pct(kpi.get("coverage_rate")),
                        _fmt_num(kpi.get("window_days"))))
    lines.append("")

    # ---- 三、覆盖率
    lines.append("## 三、覆盖率（当前 / %s 投影 / unique 补充）" % date_label)
    lines.append("")
    lines.append("| 口径 | 值 | 判读 |")
    lines.append("|---|---|---|")
    lines.append("| 主口径 runs / merge_done | %s / %s = **%s** | %s（门槛 ≥%s） |"
                 % (_fmt_num(cov.get("runs")), _fmt_num(cov.get("merge_done")),
                    _fmt_pct(cov.get("coverage")),
                    "达标" if cov.get("threshold_pass_now") else "未达标/不可判定",
                    _fmt_pct(cov.get("threshold") or COVERAGE_THRESHOLD)))
    proj = cov.get("projection") or {}
    lines.append("| %s 投影（趋势工具） | runs≈%s / merge≈%s = **%s**；unique≈**%s** | %s |"
                 % (date_label,
                    _fmt_num(proj.get("projected_runs")),
                    _fmt_num(proj.get("projected_merge_done")),
                    _fmt_pct(proj.get("projected_coverage")),
                    _fmt_pct(proj.get("projected_unique_coverage")),
                    "预计达标" if proj.get("threshold_pass") else "预计未达标/不可判定"))
    lines.append("| unique-commit 补充 | unique_commit=%s / **unique_coverage=%s** | 防「同 HEAD 重跑」注水；仅补充说明 |"
                 % (_fmt_num(cov.get("unique_commit")),
                    _fmt_pct(cov.get("unique_coverage"))))
    lines.append("")
    if isinstance(proj, dict) and proj.get("assumption"):
        lines.append("- 投影假设：%s" % proj.get("assumption"))
    lines.append("- 口径：覆盖率 = 窗口内 runs / merge_done（抽检回填单不计）；Go 门槛 ≥10%。")
    lines.append("")

    # ---- 四、④ 满窗
    lines.append("## 四、④ 满窗核对（漏放=0 前置）")
    lines.append("")
    lines.append("- now 满窗：**%s 单**；跟踪中：**%s 单**；%s 预计满窗：**%s 单**"
                 % (_fmt_num(maturity.get("mature_count")),
                    _fmt_num(maturity.get("tracking_count")),
                    date_label_hm,
                    _fmt_num(maturity.get("mature_at_1030"))))
    lines.append("- 最早满窗：`%s`；下一满窗：`%s`"
                 % (maturity.get("earliest_window_end") or "—",
                    maturity.get("next_window_end") or "—"))
    if maturity.get("note"):
        lines.append("- 说明：%s" % maturity.get("note"))
    lines.append("- ④ 当前状态：`%s`（tracking = 时间未满窗，属预期，不阻塞 ②③⑤⑥）；"
                 "判定细则见 runbook v6 §7.3。"
                 % next((i.get("status") for i in items if str(i.get("id")) == "④"), "—"))
    samples = maturity.get("expected_mature_by_target") or maturity.get("mature_samples") or []
    if samples:
        lines.append("")
        lines.append("| 预计满窗 commit | 验收单 | generated_at | window_end |")
        lines.append("|---|---|---|---|")
        for row in _cap(samples, 10):
            lines.append("| %s | %s | %s | %s |"
                         % (_cell(row.get("sha8")), _cell(row.get("file")),
                            _cell(row.get("generated_at")),
                            _cell(row.get("window_end"))))
    lines.append("")

    # ---- 五、事故台账
    lines.append("## 五、事故台账状态")
    lines.append("")
    lines.append("- 来源：%s" % (incidents.get("source") or "不可得"))
    lines.append("- 台账文件：`%s` ｜ exists=%s ｜ 行数=%s ｜ 坏行=%s ｜ issues=%s"
                 % (incidents.get("path") or "—",
                    incidents.get("exists"), _fmt_num(incidents.get("rows")),
                    _fmt_num(incidents.get("bad_lines")),
                    _fmt_num(incidents.get("issue_count"))))
    if incidents.get("issues"):
        for issue in _cap(incidents.get("issues") or [], 10):
            lines.append("  - line=%s code=%s detail=%s"
                         % (issue.get("line"), issue.get("code"),
                            issue.get("detail")))
    if incidents.get("exists") is False:
        lines.append("- **台账缺失**：按「无已知事故」记录，但须人工确认 `docs/audit/` + "
                     "`STATUS.md` 事故段，并在最终回执注明「无已知事故」。")
    confirm = incidents.get("confirm") if isinstance(incidents.get("confirm"), dict) else None
    if confirm:
        lines.append("- **人工确认（%s，%s）**：%s；记录 `%s`"
                     % (confirm.get("confirmer") or "—",
                        confirm.get("time") or "—",
                        confirm.get("conclusion") or "无已知事故（记录未写结论）",
                        confirm.get("path") or "—"))
    if incidents.get("note"):
        lines.append("- 说明：%s" % incidents.get("note"))
    lines.append("")

    # ---- 六、Go/No-Go 建议
    lines.append("## 六、Go/No-Go 建议（含 L3 提示）")
    lines.append("")
    label = suggestion.get("label") or "—"
    display = label[2:].strip() if label.startswith("建议") else label
    lines.append("**建议：%s**（code=`%s`）" % (display,
                                          suggestion.get("code") or "—"))
    lines.append("")
    if suggestion.get("suggestion"):
        lines.append("- 结论：%s" % suggestion.get("suggestion"))
    lines.append("- 决策来源：%s" % (suggestion.get("source") or "—"))
    if suggestion.get("rationale"):
        lines.append("- 依据：")
        for row in _cap(suggestion.get("rationale") or [], 12):
            lines.append("  - %s" % row)
    if suggestion.get("gaps"):
        lines.append("- 缺项：")
        for gap in _cap(suggestion.get("gaps") or [], 12):
            lines.append("  - %s %s（状态：%s，实测：%s）"
                         % (gap.get("id"), gap.get("title"),
                            gap.get("status"), gap.get("measured")))
    lines.append("- **L3 提示**：即使建议 GO，接入「merge 前置·观察不阻断」仍需**老田 L3 单独批准**；"
                 "阻断模式需另行评估误杀率后再批。本草案**不构成批准**。")
    lines.append("")

    # ---- 七、证据路径清单
    lines.append("## 七、证据路径清单")
    lines.append("")
    evidence_paths = rep.get("evidence_paths") or {}
    for key, label_text in (
            ("review", "review 六条报告"),
            ("preflight", "preflight 报告"),
            ("go_no_go", "go_no_go 决策草案"),
            ("coverage_trend_md", "coverage_trend 趋势报告"),
            ("coverage_trend_json", "coverage_trend JSON")):
        if evidence_paths.get(key):
            lines.append("- %s：`%s`" % (label_text, evidence_paths[key]))
    if evidence_paths.get("incidents"):
        lines.append("- 事故台账：`%s`" % evidence_paths["incidents"])
    if rep.get("draft", {}).get("path"):
        lines.append("- 本草稿（草案，非最终回执）：`%s`" % rep["draft"]["path"])
    lines.append("- 验收单台账：`work/huangzhong/acceptance_*.json`（PASS 按 commit 去重）")
    lines.append("- 演练台账：`work/huangzhong/drills/drill_ledger_*.json`")
    lines.append("- 赵云抽查：`work/huangzhong/audit_sample_3.json`")
    lines.append("- 误拒复核：`work/huangzhong/reject_review*.json`")
    lines.append("- 证据（基线/红队）：`agent_platform/data/test_baseline/latest.json`、"
                 "`work/p1/redteam/redteam_*.json`")
    lines.append("- 台账与口径：`docs/plans/2026-10-31_黄忠事故台账口径_v1.md`、"
                 "`docs/plans/2026-10-30_黄忠11-04复核runbook_v1.md`（v6）")
    lines.append("")

    # ---- 八、已知风险与待人工项
    lines.append("## 八、已知风险与待人工项")
    lines.append("")
    lines.append("**数据/工具告警（fail-visible）**")
    lines.append("")
    for warn in _cap(warnings, 20):
        lines.append("- %s" % warn)
    lines.append("")
    lines.append("**待人工项（草案不代替）**")
    lines.append("")
    lines.append("1. 老田 Go/No-Go 决策 + 签字（L3；本草案不构成批准）；")
    lines.append("2. 赵云抽查 3/3 一致性确认（若本轮抽查记录有更新需重跑 `audit_sample_3`）；")
    _confirm = incidents.get("confirm") if isinstance(incidents.get("confirm"), dict) else None
    if _confirm:
        lines.append("3. ④ 满窗人工核对：**已完成人工确认（%s，%s：%s）**；"
                     % (_confirm.get("confirmer") or "—",
                        _confirm.get("time") or "—",
                        _confirm.get("conclusion") or "无已知事故")
                     + "%s 满窗时仍按 runbook 再核对一次；" % date_label)
    else:
        lines.append("3. ④ 满窗人工核对：`work/huangzhong/incidents.jsonl` 缺失时须人工确认"
                     "「无已知事故」；")
    lines.append("4. %s 09:30 preflight 复核：`python -X utf8 tools/huangzhong_1104_preflight.py --json`；"
                 % date_label)
    lines.append("5. 复核数据时效：本草案数据为生成时刻快照，正式回执前如重跑验收/刷新证据需重跑本工具。")
    lines.append("")

    # ---- 九、签署与生效
    lines.append("## 九、签署与生效（草案非最终）")
    lines.append("")
    lines.append("- **本文件为草案，非最终回执**；经下列签字后方可作为 %s 观察期复核回执归档："
                 % date_text)
    lines.append("")
    lines.append("| 角色 | 职责 | 签字位 |")
    lines.append("|---|---|---|")
    lines.append("| 黄忠（验收守门官） | 自证六条与证据链 | DSH-Lead 代执行（%s） |"
                 % date_text)
    lines.append("| 赵云（平台质量门/审计） | 抽查 3/3 一致 | （待签） |")
    lines.append("| 老田 | Go/No-Go 最终决策（L3） | （待签） |")
    lines.append("")
    lines.append("- 归档落点（正式回执）：`deliverables/%s_黄忠观察期复核回执.md`；"
                 % date_text)
    lines.append("- 关联：认证标准 v2、`runbook v6 §十`、一键流程 v1.1 §六、"
                 "`deliverables/%s_黄忠复核_READ_ME_FIRST.md`。" % date_text)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("版本：%s ｜ 工具：`tools/%s` %s ｜ 工单：WO-HZ-RECEIPT-20261031 ｜ "
                 "状态：草案（自动生成，待人工签字）"
                 % (date_text, TOOL_NAME, VERSION))
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 编排主流程

def run(work_dir: Path | str | None = None,
        out_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        date_value: str | None = None,
        now: dt.datetime | None = None,
        write_draft: bool = True) -> dict:
    """编排五个只读子工具并生成回执草案（返回 JSON 摘要；不抛业务异常）。

    date_value 缺省 = DEFAULT_DATE（2026-11-04，v1 行为）；显式 2026-11-10 时
    草案标题/文件名/日期引用切到目标日期，并把 --target-date 转发给 preflight。
    now 显式传入时固定时钟并转发（--now），用于 11-10 复算/测试。
    """
    clock = now or _now()
    repo = Path(repo_root) if repo_root else ROOT
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    custom_work = work_dir is not None and str(work_dir).strip() != ""
    work = Path(work_dir) if custom_work else (repo / "work" / "huangzhong")
    target = _parse_target(date_value)
    explicit_target = bool(str(date_value or "").strip())
    explicit_now = isinstance(now, dt.datetime)
    date_text = target.date().isoformat()
    steps: list[dict] = []
    rep: dict = {
        "ok": True,
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": _iso(clock),
        "target_time": _iso(target),
        "date": date_text,
        "repo_root": str(repo),
        "work_dir": str(work),
        "out_dir": str(out),
        "read_only": True,
        "steps": steps,
        "review": None,
        "preflight": None,
        "go_no_go": None,
        "coverage_trend": None,
        "incidents": None,
        "maturity": None,
        "coverage": None,
        "evidence": None,
        "suggestion": None,
        "warnings": [],
        "draft": {"path": None, "written": False},
        "error": None,
    }

    review_step = step_review(repo, work, custom_work)
    steps.append(review_step)
    if review_step.get("status") != "ok":
        rep["ok"] = False
        rep["error"] = review_step.get("note") or "review 报告缺失/不可读"
        rep["steps"] = [_public_step(s) for s in steps]
        _log("数据错误：%s（不出草案）" % rep["error"])
        return rep
    review = review_step["data"]
    rep["review"] = {
        "report_path": review_step.get("path"),
        "generated_at": review.get("generated_at"),
        "version": review.get("version"),
        "summary": review.get("summary") or {},
        "kpi": review.get("kpi") or {},
        "items": review.get("items") or [],
        "warnings": review.get("warnings") or [],
    }

    preflight_step = step_preflight(repo, work, out, custom_work,
                                    target=target if explicit_target else None,
                                    now=clock if explicit_now else None)
    steps.append(preflight_step)
    preflight = preflight_step.get("data") if preflight_step.get("status") == "ok" else None
    rep["preflight"] = {
        "status": preflight_step.get("status"),
        "ready": (preflight or {}).get("ready"),
        "blockers": (preflight or {}).get("blockers") or [],
        "warnings": (preflight or {}).get("warnings") or [],
        "maturity": (preflight or {}).get("maturity"),
        "report_md": (preflight or {}).get("report_md"),
        "evidence": (preflight or {}).get("evidence"),
    }

    gng_step = step_go_no_go(repo, work, out, custom_work)
    steps.append(gng_step)
    gng = gng_step.get("data") if gng_step.get("status") == "ok" else None
    rep["go_no_go"] = {
        "status": gng_step.get("status"),
        "decision": (gng or {}).get("decision"),
        "coverage": (gng or {}).get("coverage"),
        "six": (gng or {}).get("six"),
        "draft": (gng or {}).get("draft"),
        "warnings": (gng or {}).get("warnings") or [],
    }

    trend_step = step_coverage_trend(repo, work, out, target, custom_work)
    steps.append(trend_step)
    trend = trend_step.get("data") if trend_step.get("status") == "ok" else None
    rep["coverage_trend"] = {
        "status": trend_step.get("status"),
        "last7": (trend or {}).get("last7"),
        "projection": (trend or {}).get("projection"),
        "paths": (trend or {}).get("paths"),
        "warnings": (trend or {}).get("warnings") or [],
    }

    incidents_step = step_incidents(repo, work, custom_work)
    steps.append(incidents_step)
    incidents = incidents_step.get("data") if incidents_step.get("status") == "ok" else None

    rep["coverage"] = _coverage_view(review, gng, trend)
    rep["maturity"] = _maturity_view(preflight, gng, review)
    rep["incidents"] = _incidents_view(incidents_step, preflight)
    rep["incidents"]["confirm"] = _latest_confirm(work)
    rep["evidence"] = _evidence_view(preflight, review)
    rep["evidence_paths"] = _evidence_paths(review_step, preflight, gng, trend,
                                            incidents)
    rep["suggestion"] = _derive_suggestion(review, gng, trend, target)
    rep["warnings"] = _collect_warnings(steps, review, preflight, gng, trend,
                                        rep["suggestion"])
    rep["steps"] = [_public_step(s) for s in steps]

    if write_draft:
        out.mkdir(parents=True, exist_ok=True)
        ts = clock.strftime("%Y%m%d_%H%M%S")
        draft_path = out / ("%s_黄忠观察期复核回执_草案_%s.md" % (date_text, ts))
        try:
            draft_path.write_text(render_md(rep), encoding="utf-8")
            rep["draft"] = {"path": str(draft_path), "written": True}
            _log("回执草案已落盘：%s" % draft_path)
        except OSError as exc:
            rep["draft"] = {"path": str(draft_path), "written": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)}
            rep["warnings"].append("草案落盘失败：%s" % exc)
            _log("草案落盘失败：%s" % exc)
    return rep


def _public_step(step: dict) -> dict:
    """对外步骤摘要（去掉大段 stdio）。"""
    keys = ("name", "tool", "cmd", "rc", "status", "note", "error",
            "path", "elapsed_s")
    public = {k: step.get(k) for k in keys if k in step}
    if isinstance(step.get("data"), dict):
        data = step["data"]
        public["data_keys"] = sorted(data.keys())
    return public


def _summary(rep: dict) -> dict:
    """stdout JSON 摘要（保持可读、不塞入全量子工具数据）。"""
    out = {
        "ok": bool(rep.get("ok")),
        "tool": rep.get("tool"),
        "version": rep.get("version"),
        "generated_at": rep.get("generated_at"),
        "target_time": rep.get("target_time"),
        "repo_root": rep.get("repo_root"),
        "work_dir": rep.get("work_dir"),
        "out_dir": rep.get("out_dir"),
        "draft": rep.get("draft"),
        "review": None,
        "preflight": None,
        "go_no_go": None,
        "coverage_trend": None,
        "incidents": None,
        "coverage": rep.get("coverage"),
        "maturity": rep.get("maturity"),
        "suggestion": rep.get("suggestion"),
        "evidence_paths": rep.get("evidence_paths"),
        "warnings": rep.get("warnings") or [],
        "steps": rep.get("steps") or [],
    }
    review = rep.get("review")
    if isinstance(review, dict):
        summary = review.get("summary") or {}
        out["review"] = {
            "report_path": review.get("report_path"),
            "generated_at": review.get("generated_at"),
            "overall_pass": summary.get("overall_pass"),
            "passed": summary.get("passed"),
            "total": summary.get("total"),
            "items": [{"id": i.get("id"), "status": i.get("status"),
                       "measured": i.get("measured")}
                      for i in (review.get("items") or [])
                      if isinstance(i, dict)],
            "kpi": review.get("kpi"),
        }
    if isinstance(rep.get("preflight"), dict):
        pre = rep["preflight"]
        out["preflight"] = {"status": pre.get("status"),
                            "ready": pre.get("ready"),
                            "blockers": pre.get("blockers"),
                            "warnings": pre.get("warnings"),
                            "report_md": pre.get("report_md")}
    if isinstance(rep.get("go_no_go"), dict):
        gng = rep["go_no_go"]
        out["go_no_go"] = {"status": gng.get("status"),
                           "decision": gng.get("decision"),
                           "draft": gng.get("draft")}
    if isinstance(rep.get("coverage_trend"), dict):
        trend = rep["coverage_trend"]
        out["coverage_trend"] = {"status": trend.get("status"),
                                 "last7": trend.get("last7"),
                                 "projection": trend.get("projection"),
                                 "paths": trend.get("paths")}
    out["incidents"] = rep.get("incidents")
    if rep.get("error"):
        out["error"] = rep["error"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠观察期复核回执自动生成器（只读编排，草案非最终回执；"
                    "默认目标 2026-11-04）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--out-dir", default="",
                    help="草案输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--date", default="",
                    help="复核日期（默认 2026-11-04；支持 ISO 日期/日期时间）")
    ap.add_argument("--target-date", default="",
                    help="显式目标日期/时刻（优先于 --date；默认 2026-11-04；"
                         "11-10 复算用，支持 2026-11-10 或 ISO 日期时间）")
    ap.add_argument("--now", default="",
                    help="固定生成时钟（ISO；默认当前时间，测试/复算用）")
    args = ap.parse_args(argv)
    date_value = args.target_date.strip() or args.date.strip()
    try:
        fixed_now = _parse_ts(args.now) if args.now else None
        if args.now and fixed_now is None:
            raise ValueError("--now 无法解析：%r" % args.now)
        rep = run(work_dir=args.work_dir or None,
                  out_dir=args.out_dir or None,
                  repo_root=args.repo_root or None,
                  date_value=date_value or None,
                  now=fixed_now)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    summary = _summary(rep)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        review = rep.get("review") or {}
        summary_r = review.get("summary") or {}
        suggestion = rep.get("suggestion") or {}
        date_label = _target_label_from_iso(rep.get("target_time"))
        print("%s 回执草案：ok=%s 六条=%s/%s 建议=%s"
              % (date_label, rep.get("ok"), summary_r.get("passed"),
                 summary_r.get("total"), suggestion.get("label")))
        print("  草案：%s" % (rep.get("draft") or {}).get("path"))
        for warn in _cap(rep.get("warnings") or [], 10):
            print("  [warn] %s" % warn)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
