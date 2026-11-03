# -*- coding: utf-8 -*-
"""研发试点门（[dev-gate]）告警汇总（WO-DRILL4-GATEWARN · 2026-10-27）。

只读扫描平台事件层 `portal/data/dev_events.jsonl`，把 detail 含 `[dev-gate]`
的研发试点门事件（devtask 建单时由 `_dev_gate_check` 打标）按近 N 天窗口
汇总：三类原因计数（`owner 不在合法域` / `缺验收命令` / `owner 不在研发部`）、
按 agent 计数、窗口内总事件数、最近 5 条样例。输出 JSON（默认）或 Markdown，
**只读不写盘**，供 T3 把试点门从 warn 切 block 前评估告警存量。

口径：
  * 只看 detail 含 `[dev-gate]` 的事件；`[dev-gate] · desc`（无告警）是
    「门已启用且通过」的留痕，计入总数但不计入告警；
  * 同一事件可含多条原因（如「owner 不在合法域；缺验收命令」），按包含
    关系各计一次；匹配只做在 `[dev-gate]` 后的**门告警摘要段**（到首个
    ` · ` 分隔符为止）——devtask 的 detail 是「摘要 · 工单描述」两段式，
    而工单描述可能恰好引用这些文案（如实测 WO-DRILL4 自身描述含
    「owner 不在合法域/缺验收命令」字样），全串匹配会把它误计成告警；
  * 窗口按事件日期（ts 前 10 位，退化到 day 字段）与切点日期比较，含切点
    当天；ts 不可解析的事件不参与窗口计数，单独记 `undated_gate_events`；
  * 文件缺失/空/坏行一律容错（不抛异常），报告模式恒返回 0。

用法：
  python -X utf8 tools/dev_gate_warn_report.py                    # 默认 JSON
  python -X utf8 tools/dev_gate_warn_report.py --md               # Markdown
  python -X utf8 tools/dev_gate_warn_report.py --json --window-days 14
  python -X utf8 tools/dev_gate_warn_report.py --events <jsonl> --md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "portal" / "data" / "dev_events.jsonl"
GATE_MARK = "[dev-gate]"
GATE_SEP = " · "
REASONS = ("owner 不在合法域", "缺验收命令", "owner 不在研发部")
SAMPLE_LIMIT = 5
DETAIL_CHARS = 120
SCHEMA = "dev_gate_warn_report/v1"


def _ensure_utf8_stdout():
    """控制台非 UTF-8（Windows 默认 GBK）时切 UTF-8，避免中文报告 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/dev_gate_warn_report.py", exc)


def load_events(path=EVENTS_PATH):
    """读 JSONL 事件（只读 + 容错）：缺文件/坏行/非 dict 行一律跳过，绝不抛异常。"""
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            rows.append(event)
    return rows


def event_day(event):
    """事件日期（YYYY-MM-DD）：优先 ts，退化到 day 字段；无法判定返回 None。"""
    for key in ("ts", "day"):
        text = str(event.get(key) or "").strip()
        head = text[:10]
        if len(head) == 10 and head[4] == "-" and head[7] == "-" and (
                head[:4].isdigit() and head[5:7].isdigit() and head[8:10].isdigit()):
            return head
    return None


def cut_day(window_days=7, now=None):
    """窗口切点日期（含当天）：now 往前 window_days 天的日期串。"""
    now = time.time() if now is None else now
    days = max(1, int(window_days))
    return time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))


def gate_note(detail):
    """取 `[dev-gate]` 后的门告警摘要段（到首个 ` · ` 分隔符为止）。

    devtask 的 detail 形如 ``[dev-gate] <告警1>；<告警2> · <工单描述>``：告警摘要
    在前、描述在后。只回摘要段，避免工单描述引用同名文案时被误计成告警。
    """
    text = str(detail or "")
    start = text.find(GATE_MARK)
    if start < 0:
        return ""
    note = text[start + len(GATE_MARK):]
    end = note.find(GATE_SEP)
    if end >= 0:
        note = note[:end]
    return note


def classify(detail):
    """按包含关系判原因：返回命中的原因列表（同一事件可多条，每条只出现一次）。

    只看门告警摘要段（见 :func:`gate_note`）——工单描述里引用的文案不算告警。
    """
    note = gate_note(detail)
    return [reason for reason in REASONS if reason in note]


def clip_detail(detail, limit=DETAIL_CHARS):
    """样例 detail 截断：压平空白后超长截断，尾部补省略号。"""
    text = " ".join(str(detail or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def summarize(events, window_days=7, now=None, source=None):
    """纯计算（无 IO）：把事件列表汇总成报告 dict，供 CLI 与测试共用。"""
    days = max(1, int(window_days))
    cut = cut_day(days, now)
    source = str(source if source is not None else EVENTS_PATH)
    events_total = 0
    gate_events = 0
    warn_events = 0
    undated = 0
    counts = {reason: 0 for reason in REASONS}
    agents = {}
    samples = []
    for index, event in enumerate(events or []):
        if not isinstance(event, dict):
            continue
        events_total += 1
        detail = str(event.get("detail") or "")
        if GATE_MARK not in detail:
            continue
        day = event_day(event)
        if day is None:
            undated += 1
            continue
        if day < cut:
            continue
        gate_events += 1
        reasons = classify(detail)
        if reasons:
            warn_events += 1
        for reason in reasons:
            counts[reason] += 1
        agent = str(event.get("agent") or "").strip() or "(未知)"
        agents[agent] = agents.get(agent, 0) + 1
        samples.append({
            "ts": str(event.get("ts") or "").strip(),
            "agent": agent,
            "task": str(event.get("task") or "").strip(),
            "detail": clip_detail(detail),
            "_sort": (day, index),
        })
    samples.sort(key=lambda item: item["_sort"], reverse=True)
    recent = []
    for item in samples[:SAMPLE_LIMIT]:
        recent.append({key: item[key] for key in ("ts", "agent", "task", "detail")})
    by_agent = sorted(agents.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(time.time() if now is None else now)),
        "source": source,
        "source_exists": Path(source).exists(),
        "window_days": days,
        "cut": cut,
        "events_total": events_total,
        "gate_events": gate_events,
        "warn_events": warn_events,
        "clean_events": gate_events - warn_events,
        "undated_gate_events": undated,
        "categories": counts,
        "by_agent": dict(by_agent),
        "recent": recent,
    }


def _verdict(report):
    """切 block 参考判读：无数据时不许说「已达成」（缺文件不等于告警清零）。"""
    gate_events = report.get("gate_events") or 0
    warn_events = report.get("warn_events") or 0
    if not report.get("source_exists"):
        return "无法评估（事件文件不存在）"
    if not report.get("events_total"):
        return "无法评估（事件文件为空）"
    if not gate_events:
        return "无样本（窗口内无 `[dev-gate]` 事件）"
    if warn_events:
        return "未达成（仍有告警）"
    return "已达成（窗口内告警清零）"


def render_markdown(report):
    """把报告 dict 渲染成 Markdown（供人读；数字与 JSON 完全同源）。"""
    categories = report.get("categories") or {}
    by_agent = report.get("by_agent") or {}
    recent = report.get("recent") or []
    lines = [
        "# 研发试点门告警汇总（[dev-gate]）",
        "",
        "- as-of：%s（窗口 %s 天，切点 %s，含切点当天）" % (
            report.get("generated_at"), report.get("window_days"), report.get("cut")),
        "- 数据源：`%s`（只读；%s）" % (
            report.get("source"),
            "文件存在" if report.get("source_exists") else "文件不存在，按空集处理"),
        "- 生成器：`tools/dev_gate_warn_report.py`（只读汇总，不写盘、不改门配置）",
        "",
        "## 一、原因分类",
        "",
        "| 原因 | 事件数 |",
        "|---|---:|",
    ]
    for reason in REASONS:
        lines.append("| %s | %s |" % (reason, categories.get(reason, 0)))
    lines += [
        "",
        "> 同一事件可含多条原因（如「owner 不在合法域；缺验收命令」），按包含关系各计一次。",
        "",
        "## 二、窗口内总量",
        "",
        "- `[dev-gate]` 事件：%s 条；其中带告警 %s 条，无告警 %s 条（门已启用且通过，仅留痕）。" % (
            report.get("gate_events"), report.get("warn_events"), report.get("clean_events")),
        "- 可解析事件记录数（全部 kind）：%s；日期不可解析、未参与窗口计数的 `[dev-gate]` 事件：%s。" % (
            report.get("events_total"), report.get("undated_gate_events")),
        "- 切 block 参考判据：窗口内告警事件数 = 0；当前 %s 条，判读：%s。" % (
            report.get("warn_events"), _verdict(report)),
        "",
        "## 三、按 Agent",
        "",
    ]
    if by_agent:
        lines += ["| Agent | 事件数 |", "|---|---:|"]
        for agent, count in by_agent.items():
            lines.append("| %s | %s |" % (agent, count))
    else:
        lines.append("（窗口内无 `[dev-gate]` 事件）")
    lines += ["", "## 四、最近 %s 条样例" % SAMPLE_LIMIT, ""]
    if recent:
        lines += ["| ts | agent | task | detail |", "|---|---|---|---|"]
        for row in recent:
            lines.append("| %s | %s | %s | %s |" % (
                row.get("ts") or "-", row.get("agent") or "-",
                row.get("task") or "-",
                str(row.get("detail") or "").replace("|", "\\|")))
    else:
        lines.append("（窗口内无 `[dev-gate]` 事件）")
    lines += [
        "",
        "## 五、口径与边界",
        "",
        "- 仅统计 detail 含 `[dev-gate]` 的事件；非研发单（未声明 `--dept dev`）不打该标，天然不入表。",
        "- 原因匹配只在 `[dev-gate]` 后的告警摘要段（到首个 ` · ` 分隔符）内做：工单描述里引用同名文案不算告警。",
        "- 三类原因串与 `tools/devtask.py::_dev_gate_check` 的告警文案一致（含「owner 不在研发部（路由规则：…）」全串）。",
        "- 窗口＝事件日期 ≥ 切点日期；ts 缺省/不可解析的事件不参与窗口计数（单独计数，不静默丢弃）。",
        "- 本工具只读：不写盘、不改 `dev_owner_gate.json`、不切换 phase；是否切 block 由人工/工单决定。",
        "",
    ]
    return "\n".join(lines)


def build_report(events_path=EVENTS_PATH, window_days=7, now=None):
    """读文件 + 汇总（CLI 与测试共用的薄封装）。"""
    return summarize(load_events(events_path), window_days=window_days,
                     now=now, source=events_path)


def main(argv=None):
    """CLI 入口：默认 JSON，`--md` 出 Markdown；报告模式恒返回 0。"""
    parser = argparse.ArgumentParser(
        description="研发试点门 [dev-gate] 告警汇总（只读，不写盘）")
    parser.add_argument("--events", default=str(EVENTS_PATH),
                        help="事件 JSONL 路径（默认 portal/data/dev_events.jsonl）")
    parser.add_argument("--now", default="",
                        help="复算/测试用 ISO 时间")
    parser.add_argument("--window-days", type=int, default=7,
                        help="统计窗口天数（默认 7，最小 1）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    group.add_argument("--md", action="store_true", help="输出 Markdown")
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()
    now_ts = None
    if args.now:
        try:
            now_ts = time.mktime(time.strptime(args.now, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            parser.error("--now 需要 ISO 时间，如 2026-10-27T10:00:00")
    report = build_report(args.events, window_days=max(1, args.window_days), now=now_ts)
    if args.md:
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
