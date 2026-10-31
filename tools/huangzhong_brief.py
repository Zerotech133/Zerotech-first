# -*- coding: utf-8 -*-
"""黄忠每日简报 v1（只读汇总；**不自动推送**）。

用途：每日一条命令汇总黄忠观察期状态，生成中文简报供老田/黄忠/赵云阅读：
  · 最新六条复核（review_*.json：items/summary/kpi）
  · 最新验收单（acceptance_*.json：verdict/commit/generated_at/证据年龄）
  · 近 N 天事件（events.jsonl：verdict 分布 + 每日趋势）
  · weekly KPI（优先 review.kpi；缺失时用 daily.jsonl + dev_events.jsonl 兜底计算）
  · 事故台账状态（incidents.jsonl：exists/行数/最近一条）
  · B 飞书推送开关状态（push_config.json / env HUANGZHONG_EVENT_PUSH；**只报状态**）

产物：`<out-dir>/<今天>_黄忠每日简报_<date>.md`（默认 `C:\\项目\\deliverables`）
CLI：`--json` / `--out-dir` / `--days 7` / `--date`（命名，默认今天）/
     `--work-dir` / `--repo-root` / `--now`（测试复算）

边界：
  · 只读汇总：不写 `work/`、不改仓库文件、不触发验收；唯一写盘 = 简报 md；
  · 不自动推送：飞书推送另走既有 B 通道（默认 OFF），开启需人工批准；
  · fail-visible：坏 JSON/坏行/缺数据如实标注，不编造；
  · 退出码：0=简报生成成功（缺数据在内容中标注）；2=致命错误（输出目录不可写等）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_DAYS = 7
MATURITY_DAYS = 7
COVERAGE_THRESHOLD = 0.10
MAX_LIST = 20

TOOL_NAME = "huangzhong_brief.py"
VERSION = "v1 (2026-10-31)"

OK, MISSING, BAD = "✓", "missing", "bad"
PUSH_ON = "ON"
PUSH_OFF = "OFF"


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


def _log(msg: str) -> None:
    """进度日志走 stderr（stdout 留给 --json；测试可 monkeypatch 静音）。"""
    print("[brief] " + str(msg), file=sys.stderr)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _read_jsonl(path: Path) -> dict:
    """读 JSONL：返回 rows/bad_lines/exists/error（坏行跳过，不编造）。"""
    path = Path(path)
    got = {"file": str(path), "exists": path.exists(), "rows": [],
           "bad_lines": 0, "error": None}
    if not path.exists():
        return got
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                got["bad_lines"] += 1
                continue
            if isinstance(rec, dict):
                got["rows"].append(rec)
            else:
                got["bad_lines"] += 1
    except OSError as exc:
        got["error"] = "读取失败：%s" % exc
    return got


def _latest_file(directory: Path, pattern: str,
                 exclude_markers: tuple = ()) -> Path | None:
    directory = Path(directory)
    if not directory.is_dir():
        return None
    files = [p for p in directory.glob(pattern)
             if p.is_file()
             and not any(m.lower() in p.name.lower() for m in exclude_markers)]
    if not files:
        return None
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return files[-1]


# ------------------------------------------------------------------ 数据加载

def load_review(work: Path, now: dt.datetime) -> dict:
    """最新六条复核报告（review_*.json）。"""
    row = {"status": MISSING, "path": None, "data": None, "note": None,
           "generated_at": None, "age_hours": None, "error": None}
    path = _latest_file(work, "review_*.json")
    if path is None:
        row["note"] = "review_*.json 缺失（复核未跑或报告被清理）"
        return row
    data, err = _read_json(path)
    row["path"] = str(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["error"] = err or "JSON 顶层不是对象"
        row["note"] = "复核报告不可读：%s" % row["error"]
        return row
    for field in ("items", "summary"):
        if field not in data:
            row["status"] = BAD
            row["note"] = "复核报告缺 %s 字段" % field
            return row
    gen = _parse_ts(data.get("generated_at"))
    row["generated_at"] = _iso(gen) or data.get("generated_at")
    row["age_hours"] = (round((now - gen).total_seconds() / 3600.0, 2)
                        if gen is not None else None)
    row["status"] = OK
    row["data"] = data
    return row


def load_acceptance(work: Path, now: dt.datetime) -> dict:
    """最新验收单（acceptance_*.json；排除演练单）。"""
    row = {"status": MISSING, "path": None, "note": None,
           "generated_at": None, "age_hours": None, "error": None,
           "verdict": None, "commit": None, "mode": None, "blocking": None,
           "reasons": [], "drill": False, "evidence": None}
    path = _latest_file(work, "acceptance_*.json",
                        exclude_markers=("drill",))
    if path is None:
        row["note"] = "acceptance_*.json 缺失（尚未产生验收单）"
        return row
    data, err = _read_json(path)
    row["path"] = str(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["error"] = err or "JSON 顶层不是对象"
        row["note"] = "验收单不可读：%s" % row["error"]
        return row
    gen = _parse_ts(data.get("generated_at"))
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    row.update({
        "status": OK,
        "generated_at": _iso(gen) or data.get("generated_at"),
        "age_hours": (round((now - gen).total_seconds() / 3600.0, 2)
                      if gen is not None else None),
        "verdict": data.get("verdict"),
        "commit": (commit.get("sha") or "")[:12] or None,
        "mode": data.get("mode"),
        "blocking": data.get("blocking"),
        "reasons": list(data.get("reasons") or []),
        "drill": bool(data.get("drill")),
        "evidence": data.get("evidence"),
    })
    return row


def load_events(work: Path, days: int, now: dt.datetime) -> dict:
    """近 N 天事件：verdict 分布 + 每日趋势。"""
    got = _read_jsonl(Path(work) / "events.jsonl")
    cutoff = now - dt.timedelta(days=int(days))
    row = {"status": OK, "file": got["file"], "exists": got["exists"],
           "bad_lines": got["bad_lines"], "window_days": int(days),
           "window_start": _iso(cutoff), "total": 0,
           "counts": {"PASS": 0, "REJECT": 0, "OBSERVE": 0},
           "daily": [], "latest": None, "error": got["error"], "note": None}
    if got["error"]:
        row["status"] = BAD
        row["note"] = "events.jsonl 读取失败：%s" % got["error"]
        return row
    if not got["exists"]:
        row["status"] = MISSING
        row["note"] = "events.jsonl 缺失（暂无验收事件）"
        return row
    by_day: dict[str, dict] = {}
    latest = None
    for rec in got["rows"]:
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            row["bad_lines"] += 1
            continue
        if latest is None or ts > latest:
            latest = ts
        if ts < cutoff or ts > now + dt.timedelta(days=1):
            continue
        row["total"] += 1
        verdict = str(rec.get("verdict") or "").upper()
        if verdict in row["counts"]:
            row["counts"][verdict] += 1
        day = ts.date().isoformat()
        bucket = by_day.setdefault(day, {"date": day, "PASS": 0, "REJECT": 0,
                                         "OBSERVE": 0, "total": 0})
        bucket["total"] += 1
        if verdict in row["counts"]:
            bucket[verdict] += 1
    row["latest"] = _iso(latest)
    row["daily"] = [by_day[k] for k in sorted(by_day)]
    if row["bad_lines"]:
        row["note"] = "events.jsonl 坏行 %d 行已跳过" % row["bad_lines"]
    return row


def load_kpi(work: Path, repo: Path, days: int, now: dt.datetime,
             review: dict) -> dict:
    """weekly KPI：优先 review.kpi；缺失时用 daily + dev_events 兜底计算。"""
    data = review.get("data") if isinstance(review.get("data"), dict) else None
    kpi = (data or {}).get("kpi") if isinstance((data or {}).get("kpi"), dict) else None
    if kpi:
        return {"status": OK, "source": "review.kpi",
                "window_days": kpi.get("window_days") or int(days),
                "runs": kpi.get("runs"),
                "merge_done": kpi.get("merge_done"),
                "coverage": kpi.get("coverage_rate"),
                "counts": kpi.get("counts"),
                "note": "取自最新六条复核报告的 kpi（窗口 %s 天）"
                        % (kpi.get("window_days") or days)}
    cutoff = now - dt.timedelta(days=int(days))
    daily = _read_jsonl(Path(work) / "daily.jsonl")
    events = _read_jsonl(Path(repo) / "portal" / "data" / "dev_events.jsonl")
    runs = 0
    for rec in daily["rows"]:
        ts = _parse_ts(rec.get("ts"))
        if ts is not None and cutoff <= ts <= now + dt.timedelta(days=1):
            runs += 1
    merges = 0
    for rec in events["rows"]:
        if str(rec.get("kind") or "") != "merge_done":
            continue
        ts = _parse_ts(rec.get("ts") or rec.get("at") or rec.get("time"))
        if ts is not None and cutoff <= ts <= now + dt.timedelta(days=1):
            merges += 1
    coverage = round(runs / merges, 4) if merges else None
    row = {"status": OK, "source": "fallback(daily+dev_events)",
           "window_days": int(days), "runs": runs, "merge_done": merges,
           "coverage": coverage, "counts": None,
           "note": "review.kpi 不可用，按 daily.jsonl（runs）+ "
                   "portal/data/dev_events.jsonl（merge_done）近 %d 天兜底计算" % days}
    if daily["bad_lines"] or events["bad_lines"]:
        row["note"] += "；坏行 daily=%d dev_events=%d（已跳过）" % (
            daily["bad_lines"], events["bad_lines"])
    return row


def load_incidents(work: Path, days: int, now: dt.datetime) -> dict:
    """事故台账状态（incidents.jsonl；缺失=需人工确认无已知事故）。"""
    got = _read_jsonl(Path(work) / "incidents.jsonl")
    cutoff = now - dt.timedelta(days=int(days))
    row = {"file": got["file"], "exists": got["exists"],
           "rows": len(got["rows"]), "bad_lines": got["bad_lines"],
           "recent_window": 0, "latest": None, "by_kind": {},
           "status": OK, "error": got["error"], "note": None}
    if got["error"]:
        row["status"] = BAD
        row["note"] = "incidents.jsonl 读取失败：%s" % got["error"]
        return row
    if not got["exists"]:
        row["status"] = MISSING
        row["note"] = ("incidents.jsonl 缺失 → 按无已知事故计，需人工确认 "
                       "docs/audit/ + STATUS 事故段")
        return row
    latest = None
    for rec in got["rows"]:
        ts = _parse_ts(rec.get("ts") or rec.get("generated_at"))
        if ts is not None:
            if latest is None or ts > latest:
                latest = ts
            if ts >= cutoff:
                row["recent_window"] += 1
        kind = str(rec.get("kind") or "other")
        row["by_kind"][kind] = row["by_kind"].get(kind, 0) + 1
    row["latest"] = _iso(latest)
    if row["bad_lines"]:
        row["note"] = "incidents.jsonl 坏行 %d 行已跳过" % row["bad_lines"]
    return row


def load_push(work: Path) -> dict:
    """B 飞书推送开关状态（env 优先，其次 push_config.json；文件不存在=OFF）。"""
    path = Path(work) / "push_config.json"
    env_raw = os.environ.get("HUANGZHONG_EVENT_PUSH")
    row = {"enabled": False, "source": "default", "path": str(path),
           "state": PUSH_OFF, "env": env_raw, "updated_at": None, "by": None,
           "note": None}
    if env_raw is not None and str(env_raw).strip() != "":
        row["enabled"] = str(env_raw).strip().lower() in ("1", "true", "on", "yes")
        row["source"] = "env"
        row["note"] = "env HUANGZHONG_EVENT_PUSH 优先于文件开关"
    elif path.exists():
        data, err = _read_json(path)
        if err is not None or not isinstance(data, dict):
            row["source"] = "file"
            row["note"] = "push_config.json 不可读（按 OFF 计）：%s" % err
        else:
            row["source"] = "file"
            row["enabled"] = bool(data.get("enabled"))
            row["updated_at"] = data.get("updated_at")
            row["by"] = data.get("by")
    else:
        row["note"] = "push_config.json 不存在 → 默认 OFF"
    row["state"] = PUSH_ON if row["enabled"] else PUSH_OFF
    return row


# ------------------------------------------------------------------ 简报渲染

def _review_items(review: dict) -> list[dict]:
    """六条明细：兼容原始 review 行（data.items）与对外摘要（items）。"""
    data = review.get("data") if isinstance(review.get("data"), dict) else {}
    source = data.get("items") if data else review.get("items")
    rows = []
    for item in source or []:
        if not isinstance(item, dict):
            continue
        rows.append({"id": item.get("id"), "title": item.get("title") or "",
                     "status": item.get("status"),
                     "measured": item.get("measured"),
                     "target": item.get("target")})
    return rows


def _one_line_conclusion(rep: dict) -> str:
    review = rep["review"]
    events = rep["events"]
    kpi = rep["kpi"]
    push = rep["push_state"]
    incidents = rep["incidents"]
    parts: list[str] = []
    summary = (review.get("summary")
               if isinstance(review.get("summary"), dict) else None)
    if summary is not None:
        parts.append("六条 %s/%s ✓" % (summary.get("passed"), summary.get("total")))
        gaps = [i.get("id") for i in _review_items(review)
                if str(i.get("status")) != "✓"]
        if gaps:
            parts.append("待办：%s" % "、".join(gaps))
    else:
        parts.append("六条复核缺失（%s）" % (review.get("note") or "未产出"))
    if events.get("status") == OK:
        c = events["counts"]
        parts.append("近%d天事件 %d 条（PASS %d / REJECT %d / OBSERVE %d）"
                     % (events["window_days"], events["total"], c["PASS"],
                        c["REJECT"], c["OBSERVE"]))
    else:
        parts.append("事件数据%s" % ("缺失" if events.get("status") == MISSING
                                    else "不可读"))
    if kpi.get("status") == OK and _num(kpi.get("coverage")):
        parts.append("覆盖率 %s" % _fmt_pct(kpi["coverage"]))
    parts.append("B 推送 %s" % push["state"])
    if incidents.get("status") == MISSING:
        parts.append("事故台账缺失（需人工确认）")
    elif incidents.get("rows") == 0:
        parts.append("事故台账 0 条")
    return "；".join(parts) + "。"


def render_md(rep: dict) -> str:
    date_text = rep.get("date") or ""
    review = rep.get("review") or {}
    acceptance = rep.get("acceptance") or {}
    events = rep.get("events") or {}
    kpi = rep.get("kpi") or {}
    incidents = rep.get("incidents") or {}
    push = rep.get("push_state") or {}
    warnings = rep.get("warnings") or []

    lines: list[str] = []
    lines.append("# 黄忠每日简报 · %s" % date_text)
    lines.append("")
    lines.append("> 生成：%s（Asia/Shanghai）｜工具：`tools/%s` %s｜工单：`WO-HZ-BRIEF-20261031`"
                 % (rep.get("generated_at") or "—", TOOL_NAME, VERSION))
    lines.append("> 边界：只读汇总，**不自动推送**（飞书推送另走既有 B 通道，需人工批准）；"
                 "数据全部来自本仓真实产物，缺失/坏行如实标注。")
    lines.append("")
    lines.append("**一句话结论：** %s" % rep.get("conclusion"))
    lines.append("")

    # 一、六条判据
    lines.append("## 一、六条判据（认证标准 v2）")
    lines.append("")
    items = _review_items(review)
    summary = review.get("summary") if isinstance(review.get("summary"), dict) else None
    if summary is not None:
        lines.append("**overall_pass = %s（%s/%s ✓）**；仅六条全 ✓ 才通过。"
                     % (summary.get("overall_pass"), summary.get("passed"),
                        summary.get("total")))
        lines.append("")
        lines.append("| # | 判据 | 状态 | 实测 | 目标 |")
        lines.append("|---|---|---|---|---|")
        for item in items:
            lines.append("| %s | %s | %s | %s | %s |"
                         % (_cell(item.get("id")), _cell(item.get("title")),
                            _cell(item.get("status")),
                            _cell(item.get("measured")),
                            _cell(item.get("target"))))
    else:
        lines.append("- 复核数据%s：%s"
                     % ("缺失" if review.get("status") == MISSING else "不可读",
                        review.get("note") or review.get("error") or "—"))
    lines.append("")
    if summary is not None:
        lines.append("- 报告：`%s`（generated_at=%s，age≈%s）"
                     % (review.get("path"), review.get("generated_at") or "—",
                        _fmt_hours(review.get("age_hours"))))
        for warn in _cap(review.get("warnings") or [], 10):
            lines.append("- 复核告警：%s" % warn)
    lines.append("")

    # 二、KPI 与趋势
    lines.append("## 二、KPI 与趋势")
    lines.append("")
    lines.append("| 指标 | 值 | 口径 |")
    lines.append("|---|---|---|")
    lines.append("| 验收 runs（%s 天窗口） | %s | %s |"
                 % (_fmt_num(kpi.get("window_days")),
                    _fmt_num(kpi.get("runs")), _cell(kpi.get("source"))))
    lines.append("| merge_done（分母） | %s | 同上 |"
                 % _fmt_num(kpi.get("merge_done")))
    lines.append("| 验收覆盖率 | **%s** | runs / merge_done；Go 门槛 ≥%s |"
                 % (_fmt_pct(kpi.get("coverage")),
                    _fmt_pct(COVERAGE_THRESHOLD)))
    if kpi.get("note"):
        lines.append("")
        lines.append("- KPI 说明：%s" % kpi.get("note"))
    lines.append("")
    lines.append("**近 %s 天事件趋势**（`events.jsonl`；PASS/REJECT/OBSERVE）"
                 % (events.get("window_days") or "—"))
    lines.append("")
    if events.get("daily"):
        lines.append("| 日期 | PASS | REJECT | OBSERVE | 合计 |")
        lines.append("|---|---|---|---|---|")
        for row in _cap(events["daily"], 14):
            lines.append("| %s | %s | %s | %s | %s |"
                         % (row.get("date"), row.get("PASS", 0),
                            row.get("REJECT", 0), row.get("OBSERVE", 0),
                            row.get("total", 0)))
        c = events.get("counts") or {}
        lines.append("| **合计** | **%s** | **%s** | **%s** | **%s** |"
                     % (c.get("PASS", 0), c.get("REJECT", 0),
                        c.get("OBSERVE", 0), events.get("total", 0)))
    else:
        lines.append("- 近 %s 天无事件数据（%s）"
                     % (events.get("window_days"),
                        events.get("note") or "events.jsonl 缺失/为空"))
    lines.append("")

    # 三、最新验收单
    lines.append("## 三、最新验收单")
    lines.append("")
    if acceptance.get("status") == OK:
        lines.append("- 结论：**%s**（mode=%s，blocking=%s）"
                     % (acceptance.get("verdict"), acceptance.get("mode"),
                        acceptance.get("blocking")))
        lines.append("- commit：`%s`｜生成：%s（age≈%s）"
                     % (acceptance.get("commit") or "—",
                        acceptance.get("generated_at") or "—",
                        _fmt_hours(acceptance.get("age_hours"))))
        if acceptance.get("reasons"):
            lines.append("- reasons：%s"
                         % "；".join(str(r) for r in acceptance["reasons"]))
        evidence = acceptance.get("evidence") or {}
        base = evidence.get("baseline") if isinstance(evidence.get("baseline"), dict) else {}
        red = evidence.get("redteam") if isinstance(evidence.get("redteam"), dict) else {}
        lines.append("- 证据：基线 generated_at=%s / age≈%s / stale=%s；"
                     "红队 generated_at=%s / age≈%s / stale=%s"
                     % (base.get("generated_at") or "—",
                        _fmt_hours(base.get("age_hours")), base.get("stale"),
                        red.get("generated_at") or "—",
                        _fmt_hours(red.get("age_hours")), red.get("stale")))
        lines.append("- 报告：`%s`" % (acceptance.get("path") or "—"))
    else:
        lines.append("- 验收单%s：%s"
                     % ("缺失" if acceptance.get("status") == MISSING else "不可读",
                        acceptance.get("note") or acceptance.get("error") or "—"))
    lines.append("")

    # 四、事故与推送
    lines.append("## 四、事故台账与 B 推送开关")
    lines.append("")
    lines.append("| 项 | 状态 | 说明 |")
    lines.append("|---|---|---|")
    lines.append("| 事故台账 incidents.jsonl | %s | exists=%s；%s 条（近 %s 天 %s 条）；最近 %s |"
                 % ("缺失" if incidents.get("status") == MISSING
                    else ("不可读" if incidents.get("status") == BAD else "正常"),
                    incidents.get("exists"), _fmt_num(incidents.get("rows")),
                    incidents.get("window_days") or rep.get("days"),
                    _fmt_num(incidents.get("recent_window")),
                    incidents.get("latest") or "—"))
    lines.append("| B 飞书推送 | **%s** | 来源=%s；开关文件=%s%s |"
                 % (push.get("state"), push.get("source"),
                    push.get("path"),
                    ("；updated_at=%s by=%s" % (push.get("updated_at"),
                                                push.get("by"))
                     if push.get("updated_at") else "")))
    lines.append("")
    if incidents.get("note"):
        lines.append("- 事故台账说明：%s" % incidents["note"])
    if push.get("note"):
        lines.append("- 推送说明：%s（本简报**不发送**任何消息）" % push["note"])
    lines.append("")

    # 五、异常与待办
    lines.append("## 五、异常与待办")
    lines.append("")
    todo: list[str] = []
    if summary is not None:
        gaps = [i for i in _review_items(review) if str(i.get("status")) != "✓"]
        if gaps:
            only_tracking = all(str(g.get("status")) == "tracking" for g in gaps)
            if only_tracking:
                todo.append("六条仅剩时间项待满窗（%s）：11-04 满窗后重跑复核"
                            % "、".join(str(g.get("id")) for g in gaps))
            else:
                todo.append("六条缺项：%s —— 按证据补证后重跑复核"
                            % "；".join("%s %s（%s）" % (g.get("id"),
                                                        g.get("title"),
                                                        g.get("status"))
                                       for g in gaps))
        for warn in _cap(review.get("warnings") or [], 10):
            todo.append("复核告警：%s" % warn)
    if kpi.get("status") == OK and _num(kpi.get("coverage")) \
            and kpi["coverage"] < COVERAGE_THRESHOLD:
        todo.append("覆盖率 %s < %.0f%%（Go 门槛）：继续 30 分钟节奏观察或按需提频"
                    % (_fmt_pct(kpi["coverage"]), COVERAGE_THRESHOLD * 100))
    if incidents.get("status") == MISSING:
        todo.append("事故台账缺失：人工确认 docs/audit/ + STATUS 事故段「无已知事故」")
    if push.get("enabled"):
        todo.append("B 飞书推送当前 **ON**：如非有意开启，请人工确认并关闭"
                    "（push_config.json enabled=false / 删除该文件）")
    if events.get("status") != OK:
        todo.append("事件数据异常：%s" % (events.get("note") or events.get("error")
                                         or "events.jsonl 缺失"))
    if acceptance.get("status") != OK:
        todo.append("验收单数据异常：%s"
                    % (acceptance.get("note") or acceptance.get("error") or "缺失"))
    for warn in warnings:
        todo.append(warn)
    if not todo:
        todo.append("无（本日无异常项；仍需人工复核六条证据）")
    for idx, text in enumerate(todo, 1):
        lines.append("%d. %s" % (idx, text))
    lines.append("")

    # 六、证据引用
    lines.append("## 六、证据引用")
    lines.append("")
    lines.append("- 六条复核报告：`%s`" % (review.get("path") or "（缺失）"))
    lines.append("- 验收单：`%s`" % (acceptance.get("path") or "（缺失）"))
    lines.append("- 事件流水：`%s`" % (events.get("file") or "（缺失）"))
    lines.append("- 事故台账：`%s`" % (incidents.get("file") or "（缺失）"))
    lines.append("- 推送开关：`%s`" % (push.get("path") or "（缺失）"))
    lines.append("- 证据根：`work/huangzhong/`（验收单/日更/事件/演练/抽查/复核清单/证书台账）")
    lines.append("- 口径：`docs/plans/2026-10-30_黄忠认证标准v2与验收工具证据口径拍板_v1.md`、"
                 "`docs/plans/2026-10-30_黄忠11-04复核runbook_v1.md`（v6）")
    lines.append("")

    # 七、生成信息
    lines.append("## 七、生成信息与边界")
    lines.append("")
    lines.append("- 生成时间：%s ｜ 数据窗口：近 %s 天 ｜ 生成工具：`tools/%s` %s"
                 % (rep.get("generated_at") or "—", rep.get("days"),
                    TOOL_NAME, VERSION))
    lines.append("- 只读：不写 `work/`、不改仓库文件、不触发验收；本文件为本工具唯一写盘产物。")
    lines.append("- 不推送：本简报**不发送飞书/任何消息**；B 推送由 `tools/huangzhong_daily.py`"
                 " 的既有开关控制（当前 %s；开关变更需人工确认，见 push_config.json）。"
                 % push.get("state"))
    lines.append("- fail-visible：缺文件/坏行/不可读均在上文标注，不编造数据；"
                 "退出码 0=生成成功（缺数据已标注），2=致命错误。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("版本：%s ｜ 作者：DSH-Lead · cert-v2 ｜ 工单：WO-HZ-BRIEF-20261031 ｜ "
                 "状态：每日简报（只读，自动生成）" % date_text)
    lines.append("")
    return "\n".join(lines)


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


# ------------------------------------------------------------------ 编排

def run(work_dir: Path | str | None = None,
        out_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        days: int = DEFAULT_DAYS,
        date_value: str | None = None,
        now: dt.datetime | None = None,
        write_report: bool = True) -> dict:
    """只读汇总并生成每日简报（返回 JSON 摘要；不抛业务异常）。"""
    clock = now or _now()
    repo = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else (
        Path(os.environ.get("HUANGZHONG_WORK_DIR"))
        if os.environ.get("HUANGZHONG_WORK_DIR") else repo / "work" / "huangzhong")
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    days = max(1, int(days or DEFAULT_DAYS))
    date_text = _resolve_date(date_value, clock)

    review = load_review(work, clock)
    acceptance = load_acceptance(work, clock)
    events = load_events(work, days, clock)
    kpi = load_kpi(work, repo, days, clock, review)
    incidents = load_incidents(work, days, clock)
    push = load_push(work)

    warnings: list[str] = []
    for name, row in (("复核", review), ("验收单", acceptance),
                      ("事件", events), ("事故台账", incidents)):
        if row.get("status") in (MISSING, BAD):
            warnings.append("%s：%s" % (name, row.get("note") or "缺失"))
    if kpi.get("status") != OK:
        warnings.append("KPI：%s" % (kpi.get("note") or "不可用"))

    rep = {
        "ok": True,
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": _iso(clock),
        "date": date_text,
        "days": days,
        "repo_root": str(repo),
        "work_dir": str(work),
        "out_dir": str(out),
        "read_only": True,
        "push": False,
        "review": _public_review(review),
        "acceptance": acceptance,
        "events": events,
        "kpi": kpi,
        "incidents": incidents,
        "push_state": push,
        "warnings": warnings,
        "report_path": None,
        "report_written": False,
        "error": None,
    }
    rep["conclusion"] = _one_line_conclusion(rep)

    if write_report:
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / ("%s_黄忠每日简报_%s.md"
                             % (clock.date().isoformat(), date_text))
        try:
            report_path.write_text(render_md(rep), encoding="utf-8")
            rep["report_path"] = str(report_path)
            rep["report_written"] = True
            _log("简报已落盘：%s" % report_path)
        except OSError as exc:
            rep["ok"] = False
            rep["error"] = "简报落盘失败：%s: %s" % (type(exc).__name__, exc)
            rep["report_path"] = str(report_path)
            _log(rep["error"])
    return rep


def _resolve_date(date_value: str | None, clock: dt.datetime) -> str:
    raw = str(date_value or "").strip()
    if not raw:
        return clock.date().isoformat()
    try:
        if "T" in raw or " " in raw:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError("--date 无法解析：%r（支持 YYYY-MM-DD 或 ISO 日期时间）"
                         % raw) from exc


def _public_review(review: dict) -> dict:
    data = review.get("data") if isinstance(review.get("data"), dict) else None
    out = {"status": review.get("status"), "path": review.get("path"),
           "generated_at": review.get("generated_at"),
           "age_hours": review.get("age_hours"),
           "note": review.get("note"), "error": review.get("error"),
           "summary": None, "items": [], "kpi": None, "warnings": []}
    if data is not None:
        out["summary"] = data.get("summary")
        out["items"] = _review_items(review)
        out["kpi"] = data.get("kpi")
        out["warnings"] = data.get("warnings") or []
    return out


def _summary(rep: dict) -> dict:
    """stdout JSON 摘要（保持可读；不含 md 全文）。"""
    return {
        "ok": bool(rep.get("ok")),
        "tool": rep.get("tool"),
        "version": rep.get("version"),
        "generated_at": rep.get("generated_at"),
        "date": rep.get("date"),
        "days": rep.get("days"),
        "report_path": rep.get("report_path"),
        "report_written": rep.get("report_written"),
        "conclusion": rep.get("conclusion"),
        "review": rep.get("review"),
        "acceptance": rep.get("acceptance"),
        "events": {k: rep["events"].get(k) for k in
                   ("status", "exists", "total", "counts", "bad_lines",
                    "window_days", "daily", "latest", "note")},
        "kpi": rep.get("kpi"),
        "incidents": rep.get("incidents"),
        "push_state": rep.get("push_state"),
        "push": rep.get("push"),
        "warnings": rep.get("warnings") or [],
        "error": rep.get("error"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠每日简报（只读汇总；不自动推送）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--out-dir", default="",
                    help="简报输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="事件/KPI 窗口天数（默认 7）")
    ap.add_argument("--date", default="",
                    help="简报命名日期（默认今天；支持 YYYY-MM-DD 或 ISO 日期时间）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--now", default="",
                    help="固定生成时钟（ISO；默认当前时间，测试/复算用）")
    args = ap.parse_args(argv)
    try:
        fixed_now = _parse_ts(args.now) if args.now else None
        if args.now and fixed_now is None:
            raise ValueError("--now 无法解析：%r" % args.now)
        rep = run(work_dir=args.work_dir or None,
                  out_dir=args.out_dir or None,
                  repo_root=args.repo_root or None,
                  days=args.days,
                  date_value=args.date or None,
                  now=fixed_now)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    summary = _summary(rep)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print("黄忠每日简报：%s" % rep.get("conclusion"))
        print("  报告：%s" % rep.get("report_path"))
        for warn in _cap(rep.get("warnings") or [], 10):
            print("  [warn] %s" % warn)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
