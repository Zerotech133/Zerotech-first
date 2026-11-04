# -*- coding: utf-8 -*-
"""Close the 24h watch observation window and write a review (read-only).

由 WatchEngineLoop 调用 watch_24h_probe.py 每 10 分钟记录心跳；本脚本在
24h 窗口到期后（计划 2026-11-05 15:20）汇总日志、输出复盘、并写入关闭标记
`observe_24h.closed`。probe 检测到标记后变为 no-op，不再追加记录。

用法:
  python -X utf8 tools/watch_24h_closeout.py --dry-run
  python -X utf8 tools/watch_24h_closeout.py
  python -X utf8 tools/watch_24h_closeout.py --force
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LOG = REPO / "agent_platform" / "data" / "watch_engine" / "observe_24h.jsonl"
DEFAULT_MARKER = REPO / "agent_platform" / "data" / "watch_engine" / "observe_24h.closed"
DEFAULT_OUT_JSON = REPO / "deliverables" / "watch_24h_review_2026-11-05.json"
DEFAULT_OUT_MD = REPO / "deliverables" / "2026-11-05_watch_24h复盘_v1.md"


def _now():
    return datetime.now().astimezone().replace(microsecond=0)


def _parse_at(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _read_records(path: Path):
    records = []
    bad = 0
    if not path.exists():
        return records, bad
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            bad += 1
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            bad += 1
    return records, bad


def analyze(records, bad_lines=0, log_path=None):
    parsed = [(r, _parse_at(r.get("at"))) for r in records]
    stamps = [t for _, t in parsed if t is not None]
    problems = [r for r in records if r.get("problems")]
    alerts = [r for r in records if r.get("alert_sent")]
    active_problems = list((records[-1].get("problems") or [])) if records else []
    problem_counts = {}
    for r in problems:
        for p in (r.get("problems") or []):
            problem_counts[p] = problem_counts.get(p, 0) + 1
    gaps = []
    for (prev, a), (_, b) in zip(parsed, parsed[1:]):
        if a is None or b is None:
            continue
        minutes = (b - a).total_seconds() / 60.0
        if minutes > 15:
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "minutes": round(minutes, 1)})
    max_gap = max([g["minutes"] for g in gaps], default=0)
    span_hours = 0.0
    if stamps:
        span_hours = round((max(stamps) - min(stamps)).total_seconds() / 3600.0, 2)
    data = {
        "generated_at": _now().isoformat(),
        "log": str(log_path or DEFAULT_LOG),
        "records": len(records),
        "bad_lines": bad_lines,
        "first_at": min(stamps).isoformat() if stamps else None,
        "last_at": max(stamps).isoformat() if stamps else None,
        "span_hours": span_hours,
        "expected_records_24h": 144,
        "problems_records": len(problems),
        "active_problems": active_problems,
        "historical_problem_records": len(problems) - (1 if active_problems else 0),
        "problem_counts": problem_counts,
        "alert_sent_records": len(alerts),
        "gaps_over_15m": len(gaps),
        "max_gap_minutes": max_gap,
        "gaps": gaps,
        "verdict": "healthy" if records and not active_problems else "degraded",
    }
    return data


def _render_md(data, marker: Path):
    lines = [
        "# Watch 24h 心跳/告警观察复盘",
        "",
        "- 生成时间：%s" % data["generated_at"],
        "- 日志：`%s`" % data["log"],
        "- 关闭标记：`%s`" % marker,
        "",
        "## 结果",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        "| 记录数 / 24h 理论值 | %s / %s |" % (data["records"], data["expected_records_24h"]),
        "| 首条 | %s |" % (data["first_at"] or "-"),
        "| 末条 | %s |" % (data["last_at"] or "-"),
        "| 覆盖时长 | %s h |" % data["span_hours"],
        "| problems 记录 | %s（历史 %s / 末条 active %s） |" % (
            data["problems_records"], data["historical_problem_records"], len(data["active_problems"])),
        "| 已发送告警记录 | %s |" % data["alert_sent_records"],
        "| >15min 断点 | %s（最大 %s 分钟） |" % (data["gaps_over_15m"], data["max_gap_minutes"]),
        "| 坏行 | %s |" % data["bad_lines"],
        "| 结论 | **%s** |" % data["verdict"],
        "",
        "## 告警分布",
        "",
    ]
    if data["problem_counts"]:
        lines += ["- `%s`: %s" % (k, v) for k, v in sorted(data["problem_counts"].items())]
    else:
        lines.append("- 无 problems 记录。")
    lines += ["", "## 断点", ""]
    if data["gaps"]:
        lines += ["- %s → %s：%s 分钟" % (g["from"], g["to"], g["minutes"]) for g in data["gaps"]]
    else:
        lines.append("- 无超过 15 分钟的断点。")
    lines += [
        "",
        "## 处置",
        "",
        "- 写入 `observe_24h.closed`，`tools/watch_24h_probe.py` 后续调用为 no-op。",
        "- WatchAdmin / WatchEngineLoop 继续运行，不影响盯盘链路。",
        "- 本复盘为只读观察，不涉及交易、不下单。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--marker", default=str(DEFAULT_MARKER))
    p.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    p.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    log = Path(a.log)
    marker = Path(a.marker)
    out_json = Path(a.out_json)
    out_md = Path(a.out_md)

    if marker.exists() and not a.force:
        print("[watch_24h_closeout] already closed: %s (use --force to redo)" % marker)
        return 0

    records, bad = _read_records(log)
    if not records:
        print("[watch_24h_closeout] no records: %s" % log, file=sys.stderr)
        return 1
    data = analyze(records, bad, log_path=log)
    data["closed_at"] = _now().isoformat()
    data["marker"] = str(marker)
    data["report_json"] = str(out_json)
    data["report_md"] = str(out_md)
    md = _render_md(data, marker)

    if a.dry_run:
        if a.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print("[watch_24h_closeout] dry-run records=%s verdict=%s problems=%s gaps=%s"
                  % (data["records"], data["verdict"], data["problems_records"], data["gaps_over_15m"]))
        return 0

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(md, encoding="utf-8")
    marker.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("[watch_24h_closeout] closed records=%s verdict=%s report=%s"
              % (data["records"], data["verdict"], out_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
