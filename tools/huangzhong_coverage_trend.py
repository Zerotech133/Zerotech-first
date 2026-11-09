# -*- coding: utf-8 -*-
"""黄忠覆盖率趋势与 11-04 投影（只读，纯 stdlib）。

补足 go_no_go 只有单点覆盖率的问题：给出逐日趋势 + 近 7 日均值 + 目标时刻投影。
工单：WO-HZ-COVTREND-20261031。

数据源（全部只读）：
  · ``work/huangzhong/daily.jsonl``       —— 日更验收 runs（ts/commit/verdict），按日统计
                                            runs/PASS/REJECT/OBSERVE；
  · ``portal/data/dev_events.jsonl``      —— ``kind=merge_done`` 事件，按日计数；
  · ``work/huangzhong/acceptance_*.json`` —— 验收单：PASS 单按 ``commit.sha`` 去重
                                            （排除演练台账）并按 ``generated_at`` 日落日；
  · ``work/huangzhong/drills/drill_ledger_*.json`` —— 演练台账（排除：演练单不计入
                                            PASS 样本/覆盖率 KPI）。

口径（与 runbook / 一键流程 / go_no_go 保持一致）：
  · 主口径 coverage = 当日 runs / 当日 merge_done（merge_done=0 → None，不编造）；
  · unique-commit coverage = 当日去重 PASS commit 数 / 当日 merge_done；
  · 近 7 日（可配 --days，最少 7）：runs/merge 求和与日均、unique 周去重；
  · target 投影：
      projected_runs        = 近 7 日 runs + 每日计划运行数 × 剩余天数
      projected_merge_done  = 近 7 日 merge 日均 × 7（窗口滚动近似）
      projected_coverage    = projected_runs / projected_merge_done
      projected_unique_coverage = (近 7 日去重 unique + 每日 unique 均值 × 剩余天数)
                                  / projected_merge_done

fail-visible：坏行/坏 JSON 计数、缺文件标注、merge_done=0 显示 None，不编造数值。
只读边界：不写任何业务数据；只把 md/json 报告写到 ``--out-dir``（默认平台 deliverables）。

用法：
  python -X utf8 tools/huangzhong_coverage_trend.py --days 14 --target 2026-11-04T10:00 --json
  python -X utf8 tools/huangzhong_coverage_trend.py --repo-root C:\\项目\\老田的AI助手 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

VERSION = "v1"
DEFAULT_DAYS = 14
DEFAULT_TARGET = "2026-11-04T10:00"
DEFAULT_RUNS_PER_DAY = 48          # 30 分钟节奏（2026-10-31 提频，L2 已批）
DEFAULT_LOOKBACK_DAYS = 7          # 近 7 日均值 / 投影窗口
COVERAGE_THRESHOLD = 0.10          # Go 门槛（runs / merge_done ≥ 10%）

REPO = Path(__file__).resolve().parents[1]

# 事件层 ts 格式（tools/events.py 写入 "%Y-%m-%d %H:%M:%S"）
_EV_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------- 基础工具

def _parse_ts(value: Any) -> dt.datetime | None:
    """宽松解析 ISO/事件层时间戳；无时区按本机时区解释（与 daily.jsonl 的 +08:00 不混算）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError:
        out = None
    if out is None:
        for fmt in _EV_TS_FORMATS:
            try:
                out = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if out is None:
        return None
    if out.tzinfo is None:
        out = out.astimezone()  # 本机时区（Windows = Asia/Shanghai）
    return out


def _read_json(p: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except OSError:
        return None, "read_error"
    except ValueError as exc:
        return None, "json_error: %s" % str(exc)[:80]
    if not isinstance(data, dict):
        return None, "top_level_not_object"
    return data, None


def _read_jsonl(p: Path) -> tuple[list[dict], int, str | None]:
    """读 jsonl → (合法 dict 行, 坏行数, 文件错误)。文件缺失返回 ( [], 0, 'missing')。"""
    try:
        text = Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0, "missing"
    rows: list[dict] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return rows, bad, None


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _pct(value: float | None) -> str:
    return "—" if value is None else "%.2f%%" % (value * 100.0)


def _day_range(now: dt.datetime, days: int) -> list[str]:
    """近 days 个自然日（含今天），升序 ISO 日期。"""
    days = max(1, int(days))
    today = now.date()
    return [(today - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


# --------------------------------------------------------------------- 数据加载

def load_daily(work_dir: Path) -> dict:
    """daily.jsonl → 按日 runs/PASS/REJECT/OBSERVE。"""
    p = Path(work_dir) / "daily.jsonl"
    rows, bad, err = _read_jsonl(p)
    by_day: dict[str, dict] = {}
    missing_ts = 0
    for row in rows:
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            missing_ts += 1
            continue
        day = ts.date().isoformat()
        st = by_day.setdefault(day, {"runs": 0, "PASS": 0, "REJECT": 0, "OBSERVE": 0})
        st["runs"] += 1
        verdict = str(row.get("verdict") or "").strip().upper()
        if verdict in ("PASS", "REJECT", "OBSERVE"):
            st[verdict] += 1
    return {"file": str(p), "ok": err is None, "error": err, "rows": len(rows),
            "bad_lines": bad, "missing_ts": missing_ts, "by_day": by_day}


def load_merge_done(repo_root: Path) -> dict:
    """dev_events.jsonl → 按日 merge_done 计数。"""
    p = Path(repo_root) / "portal" / "data" / "dev_events.jsonl"
    rows, bad, err = _read_jsonl(p)
    by_day: dict[str, int] = {}
    events = 0
    missing_ts = 0
    for row in rows:
        if str(row.get("kind") or "").strip() != "merge_done":
            continue
        events += 1
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            day = str(row.get("day") or "").strip()
            if len(day) == 10:
                by_day[day] = by_day.get(day, 0) + 1
            else:
                missing_ts += 1
            continue
        day = ts.date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    return {"file": str(p), "ok": err is None, "error": err, "rows": len(rows),
            "bad_lines": bad, "events": events, "missing_ts": missing_ts,
            "by_day": by_day}


def load_drill_ids(work_dir: Path) -> dict:
    """读 drills/drill_ledger_*.json（兼容 work 根）→ 演练单文件名/commit sha8 集合。"""
    work_dir = Path(work_dir)
    paths: list[Path] = []
    for d in (work_dir / "drills", work_dir):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("drill_ledger_*.json")):
            if all(p.resolve() != q.resolve() for q in paths):
                paths.append(p)
    files_lower: set[str] = set()
    sha8: set[str] = set()
    entries = 0
    bad: list[dict] = []
    for p in paths:
        data, err = _read_json(p)
        if err is not None:
            bad.append({"file": p.name, "error": err})
            continue
        rows = data if isinstance(data, list) else None
        if rows is None and isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if rows is None:
            bad.append({"file": p.name, "error": "no_entries_array"})
            continue
        for e in rows:
            if not isinstance(e, dict):
                bad.append({"file": p.name, "error": "entry_not_object"})
                continue
            entries += 1
            f = (e.get("acceptance_report") or e.get("file") or e.get("path")
                 or e.get("acceptance_file") or e.get("report"))
            if isinstance(f, str) and f.strip():
                files_lower.add(Path(f).name.lower())
            commit = e.get("commit") if isinstance(e.get("commit"), dict) else {}
            sha = (e.get("target_commit") or e.get("sha") or e.get("commit_sha")
                   or commit.get("sha")
                   or (e.get("commit") if isinstance(e.get("commit"), str) else ""))
            if isinstance(sha, str) and len(sha.strip()) >= 8:
                sha8.add(sha.strip().lower()[:8])
    return {"files": [str(p) for p in paths], "entries": entries,
            "files_lower": files_lower, "sha8": sha8, "bad": bad}


def _md_marked_drill(json_path: Path) -> bool:
    """兜底：同名 .md 首行含 ``drill=true`` 视为演练单（台账缺失时防漏排）。"""
    md = Path(json_path).with_suffix(".md")
    try:
        first = md.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    except OSError:
        return False
    return bool(first) and "drill=true" in first[0]


def load_unique_commits(work_dir: Path, drills: dict) -> dict:
    """acceptance_*.json → 按 generated_at 日的去重 PASS commit 集合（排除演练单）。"""
    work_dir = Path(work_dir)
    by_day: dict[str, set[str]] = {}
    pass_sheets = 0
    excluded_drills: list[str] = []
    missing_sha: list[str] = []
    missing_generated_at: list[str] = []
    invalid: list[dict] = []
    for p in sorted(work_dir.glob("acceptance_*.json")):
        data, err = _read_json(p)
        if err is not None:
            invalid.append({"file": p.name, "error": err})
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        sha = sha.strip().lower()
        # 演练单识别先于 verdict 过滤：演练台账里的 REJECT/OBSERVE 单同样要显式排除留痕
        drill_flag = bool(data.get("drill")) or str(data.get("mode") or "").lower() == "drill"
        name = p.name.lower()
        sha_hit = len(sha) >= 8 and sha[:8] in drills["sha8"]
        if drill_flag or name in drills["files_lower"] or sha_hit or _md_marked_drill(p):
            excluded_drills.append(p.name)
            continue
        verdict = data.get("verdict")
        verdict = verdict.strip().upper() if isinstance(verdict, str) else ""
        if verdict != "PASS":
            continue
        if len(sha) < 8:
            missing_sha.append(p.name)
            continue
        stamp = _parse_ts(data.get("generated_at"))
        if stamp is None:
            missing_generated_at.append(p.name)
            continue
        pass_sheets += 1
        by_day.setdefault(stamp.date().isoformat(), set()).add(sha)
    return {"file": str(work_dir), "pass_sheets": pass_sheets, "by_day": by_day,
            "excluded_drills": sorted(excluded_drills), "missing_sha": missing_sha,
            "missing_generated_at": missing_generated_at, "invalid": invalid}


# --------------------------------------------------------------------- 汇总/投影

def build_days(daily: dict, merges: dict, uniques: dict, days: int,
               now: dt.datetime) -> list[dict]:
    """逐日趋势行（覆盖窗口内每一天，缺数据日补 0/None，不跳过）。"""
    out: list[dict] = []
    for date_s in _day_range(now, days):
        d = daily["by_day"].get(date_s) or {}
        runs = int(d.get("runs", 0) or 0)
        md = int(merges["by_day"].get(date_s, 0) or 0)
        unique_commit = len(uniques["by_day"].get(date_s) or ())
        out.append({
            "date": date_s,
            "runs": runs,
            "pass": int(d.get("PASS", 0) or 0),
            "reject": int(d.get("REJECT", 0) or 0),
            "observe": int(d.get("OBSERVE", 0) or 0),
            "merge_done": md,
            "coverage": _round(runs / md, 4) if md else None,
            "unique_commit": unique_commit,
            "unique_coverage": _round(unique_commit / md, 4) if md else None,
        })
    return out


def summarize_last7(days: list[dict], lookback: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """近 N 日均值（默认 7）：求和 + 日均 + 聚合覆盖率 + unique 周去重/日均。"""
    n = max(1, min(int(lookback), len(days)))
    tail = days[-n:]
    runs = sum(r["runs"] for r in tail)
    merge = sum(r["merge_done"] for r in tail)
    unique = sum(r["unique_commit"] for r in tail)
    daily_cov = [r["coverage"] for r in tail if r["coverage"] is not None]
    daily_uniq_cov = [r["unique_coverage"] for r in tail if r["unique_coverage"] is not None]
    return {
        "window_days": n,
        "start": tail[0]["date"],
        "end": tail[-1]["date"],
        "runs": runs,
        "merge_done": merge,
        "coverage": _round(runs / merge, 4) if merge else None,
        # 注意：这里 unique 是「每日 unique 之和」，周级去重另由 compute_unique_7d 计算
        "unique_commit_daily_sum": unique,
        "avg_runs_daily": _round(runs / n, 2),
        "avg_merge_daily": _round(merge / n, 2),
        "avg_daily_coverage": _round(sum(daily_cov) / len(daily_cov), 4) if daily_cov else None,
        "avg_daily_unique": _round(unique / n, 2),
        "avg_daily_unique_coverage": (
            _round(sum(daily_uniq_cov) / len(daily_uniq_cov), 4) if daily_uniq_cov else None),
        "days_with_merge": len(daily_cov),
    }


def compute_unique_7d(unique_by_day: dict, days: list[dict],
                      lookback: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """近 N 日 unique commit 周级去重数（同一 commit 跨日只计一次）。"""
    n = max(1, min(int(lookback), len(days)))
    tail_dates = [r["date"] for r in days[-n:]]
    shas: set[str] = set()
    for date_s in tail_dates:
        shas.update(unique_by_day.get(date_s) or ())
    return {"window_days": n, "start": tail_dates[0], "end": tail_dates[-1],
            "unique_commit": len(shas)}


def project(summary: dict, unique_7d: dict, target: dt.datetime,
            now: dt.datetime, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """11-04 投影（公式见模块 docstring；target 已过去 → 不外推并提示）。"""
    remaining = (target - now).total_seconds() / 86400.0
    past_target = remaining <= 0
    remaining = max(0.0, remaining)
    window_runs = summary["runs"]
    avg_merge_daily = summary["avg_merge_daily"]
    projected_merge = (avg_merge_daily or 0.0) * DEFAULT_LOOKBACK_DAYS if avg_merge_daily else None
    projected_runs = window_runs + runs_per_day * remaining
    projected_unique = unique_7d["unique_commit"] + (summary["avg_daily_unique"] or 0.0) * remaining
    out = {
        "target": target.isoformat(),
        "now": now.isoformat(timespec="seconds"),
        "remaining_days": _round(remaining, 4),
        "runs_per_day": int(runs_per_day),
        "past_target": past_target,
        "window_runs_basis": window_runs,
        "avg_merge_daily_basis": avg_merge_daily,
        "unique_7d_basis": unique_7d["unique_commit"],
        "unique_daily_avg_basis": summary["avg_daily_unique"],
        "projected_runs": _round(projected_runs, 1),
        "projected_merge_done": _round(projected_merge, 1),
        "projected_coverage": (
            _round(projected_runs / projected_merge, 4)
            if projected_merge else None),
        "projected_unique_commits": _round(projected_unique, 1),
        "projected_unique_coverage": (
            _round(projected_unique / projected_merge, 4)
            if projected_merge else None),
        "threshold": COVERAGE_THRESHOLD,
        "threshold_pass": None,
        "formula": (
            "projected_runs = 近7日 runs + runs_per_day × 剩余天数；"
            "projected_merge_done = 近7日 merge 日均 × 7；"
            "projected_coverage = projected_runs / projected_merge_done；"
            "projected_unique_coverage = (近7日去重 unique + 每日 unique 均值 × 剩余天数)"
            " / projected_merge_done"),
    }
    if out["projected_coverage"] is not None:
        out["threshold_pass"] = bool(out["projected_coverage"] >= COVERAGE_THRESHOLD)
    if past_target:
        out["note"] = "target 已过去（now > target）：剩余天数按 0 处理，不做外推；仅回看趋势"
    return out


# --------------------------------------------------------------------- 报告

def compute(repo_root: Path | str | None = None,
            work_dir: Path | str | None = None,
            days: int = DEFAULT_DAYS,
            target: str | dt.datetime = DEFAULT_TARGET,
            runs_per_day: int = DEFAULT_RUNS_PER_DAY,
            now: dt.datetime | None = None,
            out_dir: Path | str | None = None,
            write: bool = True) -> dict:
    """主计算入口（路径/时钟/参数全部可注入，便于测试与固定参数复算）。"""
    repo = Path(repo_root) if repo_root else REPO
    work = Path(work_dir) if work_dir else repo / "work" / "huangzhong"
    if isinstance(now, str):
        now = _parse_ts(now)
    clock = now or dt.datetime.now().astimezone()
    if clock.tzinfo is None:
        clock = clock.astimezone()
    if isinstance(target, dt.datetime):
        target_dt = target if target.tzinfo else target.replace(tzinfo=clock.tzinfo)
    else:
        target_dt = _parse_ts(target)
        if target_dt is None:
            raise ValueError("target 无法解析：%r（示例 2026-11-04T10:00）" % (target,))
        if target_dt.tzinfo is None:  # 防御：_parse_ts 已补时区
            target_dt = target_dt.replace(tzinfo=clock.tzinfo)

    daily = load_daily(work)
    merges = load_merge_done(repo)
    drills = load_drill_ids(work)
    uniques = load_unique_commits(work, drills)

    days_rows = build_days(daily, merges, uniques, days, clock)
    summary = summarize_last7(days_rows)
    unique_7d = compute_unique_7d(uniques["by_day"], days_rows)
    # 周级去重覆盖率：去重 unique / 近 7 日 merge_done（与 go_no_go 的窗口口径一致）
    summary["unique_commit"] = unique_7d["unique_commit"]
    summary["unique_coverage"] = (
        _round(unique_7d["unique_commit"] / summary["merge_done"], 4)
        if summary["merge_done"] else None)
    proj = project(summary, unique_7d, target_dt, clock, runs_per_day)

    warnings: list[str] = []
    missing: list[str] = []
    if not daily["ok"]:
        missing.append("daily.jsonl")
        warnings.append("daily.jsonl 缺失/不可读（%s）→ runs 全部按 0 呈现，不编造"
                        % (daily["error"] or "?"))
    if not merges["ok"]:
        missing.append("dev_events.jsonl")
        warnings.append("portal/data/dev_events.jsonl 缺失/不可读（%s）→ merge_done 不可判定，"
                        "coverage 记 None" % (merges["error"] or "?"))
    if daily["bad_lines"]:
        warnings.append("daily.jsonl 坏行 %d 行已跳过" % daily["bad_lines"])
    if daily["missing_ts"]:
        warnings.append("daily.jsonl 有 %d 行缺/坏 ts，未计入逐日统计" % daily["missing_ts"])
    if merges["bad_lines"]:
        warnings.append("dev_events.jsonl 坏行 %d 行已跳过（不影响 merge_done 计数）"
                        % merges["bad_lines"])
    if merges["missing_ts"]:
        warnings.append("有 %d 条 merge_done 事件缺/坏 ts，未计入逐日统计"
                        % merges["missing_ts"])
    if drills["bad"]:
        for b in drills["bad"]:
            warnings.append("演练台账异常：%s（%s）" % (b["file"], b["error"]))
    if uniques["invalid"]:
        warnings.append("验收单坏 JSON %d 份已跳过：%s"
                        % (len(uniques["invalid"]),
                           "、".join(x["file"] for x in uniques["invalid"][:5])))
    if uniques["missing_sha"]:
        warnings.append("有 %d 份 PASS 单缺 commit.sha，未计入 unique-commit 覆盖率：%s"
                        % (len(uniques["missing_sha"]),
                           "、".join(uniques["missing_sha"][:5])))
    if uniques["missing_generated_at"]:
        warnings.append("有 %d 份 PASS 单缺/坏 generated_at，未计入按日统计：%s"
                        % (len(uniques["missing_generated_at"]),
                           "、".join(uniques["missing_generated_at"][:5])))
    if not uniques["pass_sheets"]:
        warnings.append("窗口内无可计 PASS 单（acceptance_*.json 为空/全被排除）→ "
                        "unique-commit 覆盖率为 0/None")
    if proj.get("past_target"):
        warnings.append(proj["note"])
    threshold_days = [r for r in days_rows if r["merge_done"] > 0]
    if not threshold_days:
        warnings.append("窗口内无 merge_done 事件 → 所有覆盖率均为 None")
    if days < DEFAULT_LOOKBACK_DAYS:
        warnings.append("--days=%d < 7：近 7 日均值按窗口内 %d 天计算" % (days, days))
    if summary["runs"] and summary["merge_done"] and summary["coverage"] is not None:
        if summary["coverage"] < COVERAGE_THRESHOLD:
            warnings.append("近 %d 日主口径覆盖率 %s < %.0f%% Go 门槛（11-04 前需提频观察）"
                            % (summary["window_days"], _pct(summary["coverage"]),
                               COVERAGE_THRESHOLD * 100))

    rep: dict = {
        "ok": bool(daily["ok"] and merges["ok"]),
        "tool": "huangzhong_coverage_trend",
        "version": VERSION,
        "generated_at": clock.isoformat(timespec="seconds"),
        "mode": "read_only",
        "window": {"days": int(days), "start": days_rows[0]["date"],
                   "end": days_rows[-1]["date"]},
        "inputs": {
            "repo_root": str(repo),
            "work_dir": str(work),
            "daily_file": daily["file"],
            "daily_ok": daily["ok"],
            "daily_rows": daily["rows"],
            "daily_missing_ts": daily["missing_ts"],
            "events_file": merges["file"],
            "events_ok": merges["ok"],
            "events_merge_done": merges["events"],
            "events_missing_ts": merges["missing_ts"],
            "acceptance_dir": uniques["file"],
            "acceptance_pass_sheets": uniques["pass_sheets"],
            "drill_ledger_files": drills["files"],
            "drill_entries": drills["entries"],
            "excluded_drills": uniques["excluded_drills"],
            "bad_lines": {"daily": daily["bad_lines"], "events": merges["bad_lines"],
                          "acceptance_invalid": len(uniques["invalid"])},
            "missing": missing,
        },
        "days": days_rows,
        "last7": summary,
        "unique7d": unique_7d,
        "projection": proj,
        "threshold": COVERAGE_THRESHOLD,
        "threshold_pass": proj.get("threshold_pass"),
        "warnings": warnings,
        "assumptions": [
            "runs 取 daily.jsonl 记录数（真实日更执行，抽检回填单不计）；merge_done 取 "
            "dev_events.jsonl 的 kind=merge_done 事件数（7 天窗口）",
            "主口径 coverage=runs/merge_done；unique-commit coverage=去重 PASS commit 数"
            "/merge_done（演练单排除，见 drills 台账）",
            "投影按「近 7 日」作滚动近似：merge 取近 7 日日均×7；runs 取近 7 日实测 + "
            "每日计划运行数×剩余天数（默认 48 次/天=30 分钟节奏）",
            "unique 投影取「近 7 日去重 unique + 每日 unique 均值×剩余天数」，与 runs 同结构",
            "时区 Asia/Shanghai；每日归属按 ts/generated_at 的本地日期",
        ],
        "paths": {},
    }
    if write:
        out = Path(out_dir) if out_dir else _default_out_dir(repo)
        ts = clock.strftime("%Y%m%d_%H%M%S")
        base = "%s_黄忠覆盖率趋势_%s" % (target_dt.date().isoformat(), ts)
        md_path = out / (base + ".md")
        json_path = out / (base + ".json")
        try:
            out.mkdir(parents=True, exist_ok=True)
            md_path.write_text(render_md(rep), encoding="utf-8")
            json_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
            rep["paths"] = {"md": str(md_path), "json": str(json_path), "out_dir": str(out)}
        except OSError as exc:
            rep["ok"] = False
            rep["warnings"].append("报告落盘失败：%s（%s）" % (out, exc))
            rep["paths"] = {"md": None, "json": None, "out_dir": str(out),
                            "write_error": str(exc)}
    return rep


def _default_out_dir(repo_root: Path) -> Path:
    """默认平台 deliverables（C:\\项目\\deliverables）；worktree 下回退到 <repo>/deliverables。"""
    platform = Path(repo_root).parent / "deliverables"
    local = Path(repo_root) / "deliverables"
    if platform.is_dir():
        return platform
    if local.is_dir():
        return local
    return platform


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def render_md(rep: dict) -> str:
    """渲染 markdown 报告（趋势表/近 7 日均值/投影/假设与风险/fail-visible/复现命令）。"""
    proj = rep["projection"]
    last = rep["last7"]
    win = rep["window"]
    lines: list[str] = []
    lines.append("# 黄忠覆盖率趋势与 11-04 投影（%s）" % rep["generated_at"][:16])
    lines.append("")
    lines.append("> 工单：WO-HZ-COVTREND-20261031 ｜ 工具：`tools/huangzhong_coverage_trend.py`"
                 "（只读，纯 stdlib）")
    lines.append("> 窗口：近 %d 天（%s ~ %s）｜ 目标时刻：%s ｜ 生成：%s"
                 % (win["days"], win["start"], win["end"], proj["target"], rep["generated_at"]))
    lines.append("> 数据源：`work/huangzhong/daily.jsonl`（runs）+ "
                 "`portal/data/dev_events.jsonl`（merge_done）+ `work/huangzhong/acceptance_*.json`"
                 "（unique-commit，演练单排除）")
    lines.append("")
    lines.append("## 一、结论摘要")
    lines.append("")
    lines.append("- 近 %d 日主口径：runs **%d** / merge_done **%d** = **%s**"
                 % (last["window_days"], last["runs"], last["merge_done"],
                    _pct(last["coverage"])))
    lines.append("- unique-commit 覆盖率（补充）：近 %d 日去重 PASS commit **%d** 个 / "
                 "merge_done %d = **%s**（日均 unique %s 个）"
                 % (rep["unique7d"]["window_days"], last["unique_commit"],
                    last["merge_done"], _pct(last["unique_coverage"]),
                    _fmt_num(last["avg_daily_unique"])))
    lines.append("- **%s 投影**：runs %s / merge_done %s → 主口径覆盖率 **%s**；"
                 "unique 投影 %s 个 → **%s**"
                 % (proj["target"][:16], _fmt_num(proj["projected_runs"]),
                    _fmt_num(proj["projected_merge_done"]),
                    _pct(proj["projected_coverage"]),
                    _fmt_num(proj["projected_unique_commits"]),
                    _pct(proj["projected_unique_coverage"])))
    verdict = ("达标（≥%.0f%%）" % (rep["threshold"] * 100)
               if rep["threshold_pass"] else
               ("未达标（<%.0f%%）" % (rep["threshold"] * 100)
                if rep["threshold_pass"] is False else "不可判定"))
    lines.append("- Go 门槛（覆盖率 ≥%.0f%%）：投影口径 **%s**；剩余 %.2f 天，"
                 "按 %d 次/天节奏外推"
                 % (rep["threshold"] * 100, verdict, proj["remaining_days"],
                    proj["runs_per_day"]))
    lines.append("")
    lines.append("## 二、逐日趋势（近 %d 天）" % win["days"])
    lines.append("")
    lines.append("| 日期 | runs | PASS | REJECT | OBSERVE | merge_done | 覆盖率 | "
                 "unique_commit | unique_coverage |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rep["days"]:
        lines.append("| %s | %d | %d | %d | %d | %d | %s | %d | %s |"
                     % (r["date"], r["runs"], r["pass"], r["reject"], r["observe"],
                        r["merge_done"], _pct(r["coverage"]), r["unique_commit"],
                        _pct(r["unique_coverage"])))
    lines.append("")
    lines.append("> 覆盖率 = 当日 runs / 当日 merge_done；merge_done=0 的日期显示「—」"
                 "（不可判定，不按 0 编造）。")
    lines.append("")
    lines.append("## 三、近 %d 天均值" % last["window_days"])
    lines.append("")
    lines.append("| 指标 | 值 | 说明 |")
    lines.append("|---|---:|---|")
    lines.append("| 窗口 | %s ~ %s（%d 天） | 滚动窗口 |"
                 % (last["start"], last["end"], last["window_days"]))
    lines.append("| runs 合计 / 日均 | %d / %s | 日更验收执行次数 |"
                 % (last["runs"], _fmt_num(last["avg_runs_daily"])))
    lines.append("| merge_done 合计 / 日均 | %d / %s | dev_events merge_done |"
                 % (last["merge_done"], _fmt_num(last["avg_merge_daily"])))
    lines.append("| 主口径覆盖率（聚合） | %s | runs 合计/merge 合计 |"
                 % _pct(last["coverage"]))
    lines.append("| 逐日覆盖率算术均值 | %s | 仅含有 merge_done 的天 |"
                 % _pct(last["avg_daily_coverage"]))
    lines.append("| unique commit（7 日去重） | %d | PASS 单按 sha 去重（排除演练） |"
                 % last["unique_commit"])
    lines.append("| unique-commit 覆盖率（去重） | %s | 去重 unique/merge 合计 |"
                 % _pct(last["unique_coverage"]))
    lines.append("| 逐日 unique-commit 覆盖率均值 | %s | 仅含有 merge_done 的天 |"
                 % _pct(last["avg_daily_unique_coverage"]))
    lines.append("")
    lines.append("## 四、%s 投影（公式与结果）" % proj["target"][:16])
    lines.append("")
    lines.append("| 项 | 值 | 公式/依据 |")
    lines.append("|---|---:|---|")
    lines.append("| 剩余天数 | %s | target − now（天） |"
                 % _fmt_num(proj["remaining_days"]))
    lines.append("| 现窗口 runs（近 %d 日） | %s | daily.jsonl |"
                 % (last["window_days"], _fmt_num(proj["window_runs_basis"])))
    lines.append("| 每日计划运行数 | %d | --runs-per-day（默认 48=30 分钟节奏） |"
                 % proj["runs_per_day"])
    lines.append("| **projected_runs** | **%s** | 现窗口 runs + 每日计划运行数 × 剩余天数 |"
                 % _fmt_num(proj["projected_runs"]))
    lines.append("| merge 日均（近 %d 日） | %s | dev_events merge_done |"
                 % (last["window_days"], _fmt_num(proj["avg_merge_daily_basis"])))
    lines.append("| **projected_merge_done** | **%s** | 近 7 日 merge 日均 × 7 |"
                 % _fmt_num(proj["projected_merge_done"]))
    lines.append("| **projected_coverage** | **%s** | projected_runs / projected_merge_done |"
                 % _pct(proj["projected_coverage"]))
    lines.append("| **projected_unique_coverage** | **%s** | (近 7 日去重 unique %s + 每日 "
                 "unique 均值 %s × 剩余天数) / projected_merge_done |"
                 % (_pct(proj["projected_unique_coverage"]),
                    _fmt_num(proj["unique_7d_basis"]), _fmt_num(proj["unique_daily_avg_basis"])))
    lines.append("")
    lines.append("## 五、假设与风险")
    lines.append("")
    if proj["projected_coverage"] is None:
        lines.append("1. **merge 速率波动**：近 7 日 merge 日均 %s/天；"
                     "merge_done 不可判定，投影覆盖率与敏感度不可计算（不编造）。"
                     % _fmt_num(last["avg_merge_daily"]))
    else:
        lines.append("1. **merge 速率波动**：投影用近 7 日 merge 日均（%s/天）作滚动近似；"
                     "若 11-04 前 merge 速率上升 20%%，覆盖率约降至 %.2f%%；下降 20%% 则约升至 "
                     "%.2f%%（线性敏感度，供复核时判读）。"
                     % (_fmt_num(last["avg_merge_daily"]),
                        proj["projected_coverage"] / 1.2 * 100,
                        proj["projected_coverage"] / 0.8 * 100))
    lines.append("2. **运行节奏风险**：runs 投影按计划任务 30 分钟节奏（%d 次/天）假设；"
                 "任务偶发 miss/延迟/成功率下降会等比拉低投影。" % proj["runs_per_day"])
    lines.append("3. **unique-commit 近似**：每份 PASS 单只取一个 commit.sha；"
                 "同一 commit 跨日只计一次；演练单已排除（见第六节），抽检回填单不计。")
    lines.append("4. **窗口滚动近似**：11-04 的「近 7 日」窗口与当前窗口不是同一段日期；"
                 "投影假设新窗口 merge 速率与近期一致，不做日间节律（工作日/夜间）建模。")
    lines.append("5. **口径边界**：主口径判 Go 门槛（≥%.0f%%），unique-commit 为补充"
                 "口径（防同 HEAD 重跑注水），两者并列展示、不互相替代。" % (rep["threshold"] * 100))
    lines.append("")
    lines.append("## 六、数据源与 fail-visible")
    lines.append("")
    inp = rep["inputs"]
    lines.append("- daily.jsonl：`%s`（ok=%s，rows=%d，坏行 %d，缺/坏 ts %d）"
                 % (inp["daily_file"], inp["daily_ok"], inp["daily_rows"],
                    inp["bad_lines"]["daily"], inp["daily_missing_ts"]))
    lines.append("- dev_events.jsonl：`%s`（ok=%s，merge_done 事件 %d，坏行 %d，缺/坏 ts %d）"
                 % (inp["events_file"], inp["events_ok"], inp["events_merge_done"],
                    inp["bad_lines"]["events"], inp["events_missing_ts"]))
    lines.append("- acceptance_*.json：`%s`（PASS 单 %d 份；排除演练单 %d 份：%s；坏 JSON %d 份）"
                 % (inp["acceptance_dir"], inp["acceptance_pass_sheets"],
                    len(inp["excluded_drills"]),
                    "、".join(inp["excluded_drills"]) or "无",
                    inp["bad_lines"]["acceptance_invalid"]))
    lines.append("- 演练台账：%s（条目 %d）"
                 % ("、".join(inp["drill_ledger_files"]) or "无", inp["drill_entries"]))
    lines.append("- 缺失数据源：%s" % ("、".join(inp["missing"]) or "无"))
    lines.append("")
    lines.append("**fail-visible 警告（%d 条）**" % len(rep["warnings"]))
    lines.append("")
    if rep["warnings"]:
        for w in rep["warnings"]:
            lines.append("- %s" % w)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 七、复现命令")
    lines.append("")
    lines.append("```powershell")
    lines.append("cd C:\\项目\\老田的AI助手")
    lines.append("C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_coverage_trend.py "
                 "--days %d --target %s --json" % (win["days"], proj["target"][:16]))
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("版本：%s｜工具：huangzhong_coverage_trend.py（只读）｜口径：runbook / "
                 "11-04 一键流程 / go_no_go 一致" % rep["version"])
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="黄忠覆盖率趋势与 11-04 投影（只读）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--runs-per-day", type=int, default=DEFAULT_RUNS_PER_DAY)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo-root", default="")
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--now", default="",
                    help="固定时钟（ISO，如 2026-10-31T12:00；默认当前时间，测试/复算用）")
    ap.add_argument("--no-write", action="store_true", help="只计算并输出，不落 md/json 文件")
    args = ap.parse_args(argv)

    now = _parse_ts(args.now) if args.now else None
    if args.now and now is None:
        print("--now 无法解析：%r" % args.now, file=sys.stderr)
        return 2
    try:
        rep = compute(repo_root=args.repo_root or None,
                      work_dir=args.work_dir or None,
                      days=args.days,
                      target=args.target,
                      runs_per_day=args.runs_per_day,
                      now=now,
                      out_dir=args.out_dir or None,
                      write=not args.no_write)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        last, proj = rep["last7"], rep["projection"]
        print("黄忠覆盖率趋势：近 %d 日 runs=%d merge_done=%d coverage=%s"
              % (last["window_days"], last["runs"], last["merge_done"], _pct(last["coverage"])))
        print("  unique-commit：%d 个 / %s" % (last["unique_commit"],
                                              _pct(last["unique_coverage"])))
        print("  %s 投影：runs=%s merge_done=%s coverage=%s unique=%s"
              % (proj["target"][:16], _fmt_num(proj["projected_runs"]),
                 _fmt_num(proj["projected_merge_done"]), _pct(proj["projected_coverage"]),
                 _pct(proj["projected_unique_coverage"])))
        if rep.get("paths", {}).get("md"):
            print("  报告：%s" % rep["paths"]["md"])
        for w in rep["warnings"]:
            print("  [warn] %s" % w)
    if not rep["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
