# -*- coding: utf-8 -*-
"""夜跑轮次摘要（2026-10-30 NIGHTSUM，只读日志）。

用途：夜跑后快速核对每轮 judged/耗时/速率，验证 reason 合并（-25%）与
safe 预筛是否达到预期；不依赖 CIM/进程，纯日志解析。

用法：
    python -B tools/jev_night_summary.py                 # 最近一个夜跑（到 END/DONE 为止）
    python -B tools/jev_night_summary.py --tail-nights 2 # 最近两晚
    python -B tools/jev_night_summary.py --json out.json --md out.md
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

LOG = Path(r"C:\Temp\jev_night.log")
ROUND_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+JEV rc=(?P<rc>-?\d+) (?P<elapsed>\d+)s BACKFILL "
    r"judged=(?P<judged>\d+)/(?P<target>\d+) left=(?P<left>\d+)"
    r".*?pref_skip=(?P<pref_skip>\d+).*?pref_keep=(?P<pref_keep>\d+)")
END_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+END 轮数=(?P<rounds>\d+)")
START_RE = re.compile(r"^\[(?P<ts>[^\]]+)\].*?(?:NIGHT|夜跑).*(?:start|开始|启动)", re.I)


def parse_rounds(text: str) -> list[dict]:
    """解析 JEV 判读轮次行 → [{ts, rc, elapsed, judged, target, left, rate_per_h, pref_skip}]。"""
    out = []
    for line in text.splitlines():
        m = ROUND_RE.match(line.strip())
        if not m:
            continue
        d = m.groupdict()
        elapsed = max(1, int(d["elapsed"]))
        judged = int(d["judged"])
        out.append({
            "ts": d["ts"],
            "rc": int(d["rc"]),
            "elapsed_s": elapsed,
            "judged": judged,
            "target": int(d["target"]),
            "left": int(d["left"]),
            "rate_per_h": round(judged / elapsed * 3600, 1),
            "pref_skip": int(d["pref_skip"]),
            "pref_keep": int(d["pref_keep"]),
        })
    return out


def summarize(rounds: list[dict], end_line: str = "") -> dict:
    if not rounds:
        return {"rounds": 0, "judged_total": 0, "avg_rate_per_h": None}
    total = sum(r["judged"] for r in rounds)
    span = sum(r["elapsed_s"] for r in rounds)
    rates = [r["rate_per_h"] for r in rounds if r["judged"] > 0]
    return {
        "rounds": len(rounds),
        "judged_total": total,
        "elapsed_total_s": span,
        "avg_rate_per_h": round(total / span * 3600, 1) if span else None,
        "median_round_rate": round(statistics.median(rates), 1) if rates else None,
        "best_round": max(rounds, key=lambda r: r["judged"])["judged"],
        "worst_round": min(r["judged"] for r in rounds),
        "rounds_under_300": sum(1 for r in rounds if r["judged"] < 300),
        "pref_skip_total": sum(r["pref_skip"] for r in rounds),
        "end_line": end_line,
    }


def render_md(rows: list[dict], summary: dict) -> str:
    lines = [
        "# 夜跑轮次摘要",
        "",
        "| # | 时间 | judged/目标 | 耗时 | 速率/h | pref_skip |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append("| %d | %s | %d/%d | %ds | %.0f | %d |"
                     % (i, r["ts"], r["judged"], r["target"],
                        r["elapsed_s"], r["rate_per_h"], r["pref_skip"]))
    lines += ["", "**汇总**：轮数=%d ｜ 总判读=%d ｜ 平均速率=%.0f/h ｜ 中位轮速率=%.0f/h ｜ 预筛 skip=%d"
              % (summary["rounds"], summary["judged_total"],
                 summary.get("avg_rate_per_h") or 0, summary.get("median_round_rate") or 0,
                 summary.get("pref_skip_total") or 0)]
    if summary.get("end_line"):
        lines.append("")
        lines.append(summary["end_line"])
    return "\n".join(lines) + "\n"


def last_night(rounds: list[dict], nights: int = 1) -> list[dict]:
    """按时间戳日期分段（跨午夜按连续 >6h 间隔断夜），保留最后 nights 段。"""
    if not rounds:
        return rounds
    from datetime import datetime
    parsed = []
    for r in rounds:
        try:
            parsed.append((datetime.strptime(r["ts"][:19], "%Y-%m-%d %H:%M:%S"), r))
        except ValueError:
            parsed.append((None, r))
    segments: list[list[dict]] = []
    cur: list[dict] = []
    prev = None
    for dt, r in parsed:
        if prev is not None and dt is not None and prev is not None:
            if (dt - prev).total_seconds() > 6 * 3600:
                segments.append(cur)
                cur = []
        cur.append(r)
        prev = dt
    segments.append(cur)
    return [r for seg in segments[-nights:] for r in seg]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--tail-nights", type=int, default=1)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)
    text = Path(a.log).read_text(encoding="utf-8", errors="replace")
    rounds = parse_rounds(text)
    rounds = last_night(rounds, a.tail_nights)
    end_line = ""
    for line in text.splitlines():
        if END_RE.match(line.strip()):
            end_line = line.strip()
    summary = summarize(rounds, end_line)
    out = {"summary": summary, "rounds": rounds}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(render_md(rounds, summary), encoding="utf-8")
    return 0 if rounds else 1