# -*- coding: utf-8 -*-
"""归因缺口报表（WO-DRILL3-ATTRGAP）——只读统计开发事件层的归因缺失。

背景：`portal/data/dev_events.jsonl` 是零界开发侧全量回流的唯一事件文件，
`task_start` 事件若由无法识别 owner 的入口写入，detail 会带
`attribution_missing=1`。本工具按窗口统计缺口，供治理席/研发部巡检。

口径（全部只读，不写任何文件）：
  - 窗口：含今天共 N 个自然日，即 [今天-(N-1), 今天]；按 event.day 判定，
    缺 day 时退回 ts 前 10 位。
  - total：窗口内 kind=task_start 的事件数。
  - missing：上述事件中 detail 含 `attribution_missing=1` 的条数。
  - coverage：(total-missing)/total，total=0 记 None（不臆造 0% 或 100%）。
  - by_agent：task_start 按 agent 分布（空 agent 记 `(未署名)`），含 total/missing。
  - by_kind：窗口内**全部**事件按 kind 分布（含 missing），用于判断缺口是否只
    出现在 task_start；非 task_start 的 missing 不计入上面的 missing。
  - samples：窗口内最近 5 条 missing 样例（ts/agent/task/detail，detail 截断 160 字）。

用法：
  python -X utf8 tools/attribution_gap_report.py                  # 默认 JSON
  python -X utf8 tools/attribution_gap_report.py --events <path>  # 指定事件文件
  python -X utf8 tools/attribution_gap_report.py --window-days 14 --md
  python -X utf8 tools/attribution_gap_report.py --strict         # 缺口>0 时 rc=1

退出码：默认报告模式恒 0（文件缺失/空文件也出空报表）；`--strict` 且 missing>0
时 1；参数错误由 argparse 返回 2。
"""
from __future__ import annotations

import sys
import logging

try:  # 2026-10-26 K-ORG-20261026-ENCPROT: 非 UTF-8 控制台打印中文不再崩溃
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/attribution_gap_report.py", exc)

import argparse  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS_PATH = ROOT / "portal" / "data" / "dev_events.jsonl"
TASK_START_KIND = "task_start"
MISSING_MARKER = "attribution_missing=1"
SAMPLE_LIMIT = 5
DETAIL_LIMIT = 160
UNSIGNED_AGENT = "(未署名)"


def default_events_path() -> Path:
    """默认事件文件路径：本仓优先；worktree 内不存在时回主仓（事件层统一落主仓）。

    与 `tools/events.py::_master_events_file` 同口径：worktree 隔离开发时事件
    仍写主仓 `portal/data/dev_events.jsonl`，报表必须读同一份，否则误报全绿。
    """
    if DEFAULT_EVENTS_PATH.exists():
        return DEFAULT_EVENTS_PATH
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
        ).stdout.strip()
        if out:
            common = Path(out)
            if not common.is_absolute():
                common = ROOT / common
            cand = common.resolve().parent / "portal" / "data" / "dev_events.jsonl"
            if cand.exists():
                return cand
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/attribution_gap_report.py", exc)
    return DEFAULT_EVENTS_PATH


def load_events(path) -> list[dict]:
    """读 JSONL；文件缺失/不可读返回 []，坏行跳过（报表不因脏行中断）。"""
    rows: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _event_day(event: dict) -> str:
    """事件日期：day 优先，缺省取 ts 前 10 位；都没有返回空串（窗口外）。"""
    day = str(event.get("day") or "").strip()
    if day:
        return day[:10]
    return str(event.get("ts") or "").strip()[:10]


def _has_missing_detail(event: dict) -> bool:
    """detail 含 `attribution_missing=1` 即记缺口（与事件层写入口径一致）。"""
    return MISSING_MARKER in str(event.get("detail") or "")


def _truncate(text, limit: int = DETAIL_LIMIT) -> str:
    """压空白 + 截断；超长以省略号收尾，保证单行可入表。"""
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _sample(event: dict) -> dict:
    """一条 missing 样例：ts/agent/task/detail（detail 截断）。"""
    return {
        "ts": str(event.get("ts") or ""),
        "agent": str(event.get("agent") or UNSIGNED_AGENT),
        "task": str(event.get("task") or ""),
        "detail": _truncate(event.get("detail")),
    }


def compute(events, window_days: int = 7, now: float | None = None,
            source: str = "", source_exists: bool = True) -> dict:
    """纯计算归因缺口报表（不读盘、不写盘）。

    events：事件 dict 列表；window_days：含今天共 N 天；now：时间戳（测试注入）。
    source/source_exists 仅回填报表元信息，便于判读数据源是否缺失。
    """
    days = max(1, int(window_days or 1))
    now = time.time() if now is None else now
    cut = time.strftime("%Y-%m-%d", time.localtime(now - (days - 1) * 86400))
    in_window = [e for e in (events or []) if _event_day(e) >= cut]

    task_starts = [e for e in in_window if e.get("kind") == TASK_START_KIND]
    missing_events = [e for e in task_starts if _has_missing_detail(e)]
    total = len(task_starts)
    missing = len(missing_events)
    coverage = round((total - missing) / total, 4) if total else None

    by_agent: dict[str, dict] = {}
    for e in task_starts:
        agent = str(e.get("agent") or "").strip() or UNSIGNED_AGENT
        item = by_agent.setdefault(agent, {"total": 0, "missing": 0})
        item["total"] += 1
        if _has_missing_detail(e):
            item["missing"] += 1
    by_agent = dict(sorted(by_agent.items(), key=lambda kv: (-kv[1]["total"], kv[0])))

    by_kind: dict[str, dict] = {}
    for e in in_window:
        kind = str(e.get("kind") or "").strip() or "(未知)"
        item = by_kind.setdefault(kind, {"total": 0, "missing": 0})
        item["total"] += 1
        if _has_missing_detail(e):
            item["missing"] += 1
    by_kind = dict(sorted(by_kind.items(), key=lambda kv: (-kv[1]["total"], kv[0])))

    # 最近 N 条按 ts 倒序；同 ts 以文件后写入者更新（append-only 语义）。
    indexed = list(enumerate(missing_events))
    indexed.sort(key=lambda pair: (str(pair[1].get("ts") or ""), pair[0]), reverse=True)
    samples = [_sample(e) for _, e in indexed[:SAMPLE_LIMIT]]
    return {
        "schema": "attribution_gap_report/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "source": str(source or ""),
        "source_exists": bool(source_exists),
        "window_days": days,
        "cut": cut,
        "total": total,
        "missing": missing,
        "coverage": coverage,
        "by_agent": by_agent,
        "by_kind": by_kind,
        "samples": samples,
    }


def _fmt_coverage(coverage) -> str:
    return "N/A" if coverage is None else "%.2f%%" % (coverage * 100)


def _md_cell(value, limit: int | None = None) -> str:
    """Markdown 单元格转义：换行压平、竖线转义，可选截断。"""
    s = " ".join(str(value or "").split())
    if limit is not None:
        s = _truncate(s, limit)
    return s.replace("|", "\\|")


def render_markdown(report: dict) -> str:
    """把 compute() 的报表渲染为 Markdown（表格 + 样例）。"""
    lines = [
        "# 归因缺口报表（attribution gap）",
        "",
        "- 生成时间：%s" % report.get("generated_at", ""),
        "- 数据源：`%s`（存在：%s）" % (
            report.get("source") or "(未指定)",
            "是" if report.get("source_exists") else "否，按空报表处理"),
        "- 窗口：含今天共 %s 天（切点 %s）" % (
            report.get("window_days", ""), report.get("cut", "")),
        "- 口径：kind=task_start；missing=detail 含 `%s`；coverage=(total-missing)/total"
        % MISSING_MARKER,
        "",
        "## 一、总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        "| task_start 总数（total） | %s |" % report.get("total", 0),
        "| 归因缺失（missing） | %s |" % report.get("missing", 0),
        "| 覆盖率（coverage） | %s |" % _fmt_coverage(report.get("coverage")),
        "",
        "## 二、按 Agent 分布（task_start）",
        "",
        "| Agent | total | missing |",
        "|---|---:|---:|",
    ]
    by_agent = report.get("by_agent") or {}
    if by_agent:
        for agent, item in by_agent.items():
            lines.append("| %s | %s | %s |" % (
                _md_cell(agent), item.get("total", 0), item.get("missing", 0)))
    else:
        lines.append("| （窗口内无 task_start） | 0 | 0 |")

    lines += [
        "",
        "## 三、按事件类型分布（窗口内全部事件）",
        "",
        "| kind | total | missing |",
        "|---|---:|---:|",
    ]
    by_kind = report.get("by_kind") or {}
    if by_kind:
        for kind, item in by_kind.items():
            lines.append("| %s | %s | %s |" % (
                _md_cell(kind), item.get("total", 0), item.get("missing", 0)))
    else:
        lines.append("| （窗口内无事件） | 0 | 0 |")

    lines += [
        "",
        "## 四、最近 %s 条缺失样例" % SAMPLE_LIMIT,
        "",
        "| ts | agent | task | detail（截断 %s 字） |" % DETAIL_LIMIT,
        "|---|---|---|---|",
    ]
    samples = report.get("samples") or []
    if samples:
        for s in samples:
            lines.append("| %s | %s | %s | %s |" % (
                _md_cell(s.get("ts")), _md_cell(s.get("agent")),
                _md_cell(s.get("task")), _md_cell(s.get("detail"), DETAIL_LIMIT)))
    else:
        lines.append("| （窗口内无归因缺失样例） | - | - | - |")

    lines += [
        "",
        "> 本报表只读，不写任何文件；`--strict` 且 missing>0 时退出码 1。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="归因缺口报表（只读统计 dev_events.jsonl 的 attribution_missing）",
        epilog="退出码：默认 0；--strict 且 missing>0 → 1；参数错误 → 2。",
    )
    parser.add_argument("--events", default=None,
                        help="事件 JSONL 路径（默认主仓 portal/data/dev_events.jsonl）")
    parser.add_argument("--window-days", type=int, default=7,
                        help="窗口天数，含今天，默认 7")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON 输出（默认）")
    fmt.add_argument("--md", action="store_true", help="Markdown 输出")
    parser.add_argument("--strict", action="store_true",
                        help="存在归因缺口时退出码 1（巡检/CI 用）")
    parser.add_argument("--now", default="",
                        help="复算/测试用 ISO 时间（如 2026-10-27T10:00:00）")
    args = parser.parse_args(argv)

    events_path = Path(args.events) if args.events else default_events_path()
    events_path = events_path.expanduser().resolve()
    exists = events_path.exists()
    if not exists:
        print("警告：事件文件不存在：%s（按空报表处理）" % events_path,
              file=sys.stderr)
    events = load_events(events_path)
    now_ts = None
    if args.now:
        try:
            now_ts = datetime.datetime.fromisoformat(args.now).timestamp()
        except ValueError:
            parser.error("--now 需要 ISO 时间，如 2026-10-27T10:00:00")
    report = compute(events, window_days=max(1, args.window_days), now=now_ts,
                     source=str(events_path), source_exists=exists)

    if args.md:
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))

    if args.strict and report["missing"] > 0:
        print("strict：归因缺口 %s 条（窗口 %s 天，切点 %s），rc=1"
              % (report["missing"], report["window_days"], report["cut"]),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
