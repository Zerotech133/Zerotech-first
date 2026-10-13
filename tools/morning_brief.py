#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/morning_brief.py —— 晨会三件套生成器 v0（K-ORG-056）
方案：docs/plans/2026-10-14_0210_晨会三件套生成器v0_K-ORG-056.md
产出：agent_platform/data/morning_brief/YYYYMMDD.md（--out 可覆盖）
  ## 昨日事件汇总   —— portal/data/dev_events.jsonl 按 --date 过滤，五类计数+明细（P-1）
  ## 今日分发清单   —— agent_platform/registry/org_baseline.json 批状态表（P-2）
  ## 风险与走形跟踪 —— agent_platform/data/sec2/ 最近一份走查报告判定统计（P-3）
K-ORG-056 返工轮（SEC2 走查 20261014_0226 判走形 5，主控兜底修复）：
  1) P-1 改全账本按 day 字段过滤 + 五类计数（原 load_events_tail 行数窗口判走形）
  2) P-2 路径改 agent_platform/registry/ + 批状态表（原治理要点摘录判走形）
  3) P-3 路径改 agent_platform/data/sec2/ + 判定统计正则（原全文摘录判走形）
  4) P-4 输出目录改 agent_platform/data/morning_brief/（原 portal/briefs 违边界纪律）
  5) P-5 移除 events emit 回流（只读工具不得写账本——SEC2 抓到的边界违反）
只读零网络；任何单点异常降级为占位文本不中断整体（P-5）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_FILE_CHARS = 20000


def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parent, *here.parents):
        if (cand / "portal").is_dir() or (cand / ".git").is_dir():
            return cand
    return here.parent.parent


REPO_ROOT: Path = find_repo_root()
EVENTS_PATH: Path = REPO_ROOT / "portal" / "data" / "dev_events.jsonl"
BASELINE_PATH: Path = REPO_ROOT / "agent_platform" / "registry" / "org_baseline.json"
SEC2_DIR: Path = REPO_ROOT / "agent_platform" / "data" / "sec2"
BRIEF_OUT_DIR: Path = REPO_ROOT / "agent_platform" / "data" / "morning_brief"

EVENT_KINDS = ("approval_request", "approval_verdict", "deviation_report",
               "task_start", "commit")


def _warn(msg: str) -> None:
    print(f"[morning_brief][degrade] {msg}", file=sys.stderr)


def load_events(day: str) -> tuple[dict[str, int], list[dict[str, Any]]] | str:
    """P-1：全账本遍历按 day 过滤，五类计数+明细。降级返回占位字符串。"""
    try:
        if not EVENTS_PATH.is_file():
            raise FileNotFoundError(str(EVENTS_PATH))
        counts = {k: 0 for k in EVENT_KINDS}
        hits: list[dict[str, Any]] = []
        with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("day") != day:
                    continue
                kind = rec.get("kind", "")
                if kind in counts:
                    counts[kind] += 1
                    hits.append(rec)
        return counts, hits
    except Exception as exc:  # noqa: BLE001
        _warn(f"load_events failed: {exc!r}")
        return "[占位] 事件账本缺失或不可读（已降级）"


def load_batches() -> str:
    """P-2：org_baseline → A~G 批状态表。"""
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        rows = []
        for b in data.get("batches", []):
            pend = [i["id"] for i in b.get("items", []) if not i.get("done")]
            rows.append("| %s | %s | %s | %s |" % (
                b.get("id", "?"), (b.get("name", "") or "")[:24],
                b.get("status", "?"), "、".join(pend) if pend else "—"))
        if not rows:
            return "[占位] org_baseline 无批次数据（已降级）"
        return "\n".join(["| 批 | 名称 | 状态 | pending |", "|---|---|---|---|"] + rows)
    except Exception as exc:  # noqa: BLE001
        _warn(f"load_batches failed: {exc!r}")
        return "[占位] org_baseline 缺失或不可读（已降级）"


_STAT_RE = re.compile(r"完善\s*(\d+)\s*[·.·]?\s*走形\s*(\d+)\s*[·.·]?\s*缺失\s*(\d+)")


def load_sec2_stat() -> str:
    """P-3：最近一份走查报告判定统计行；读不到输出「无近期走查」不报错。"""
    try:
        if not SEC2_DIR.is_dir():
            raise NotADirectoryError(str(SEC2_DIR))
        files = sorted(p for p in SEC2_DIR.iterdir()
                       if p.is_file() and p.suffix == ".md")
        if not files:
            return "无近期走查"
        for p in reversed(files):  # 最新优先
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
            except Exception:  # noqa: BLE001
                continue
            m = _STAT_RE.search(text)
            if m:
                return ("%s：走形 %s · 缺失 %s（完善 %s）"
                        % (p.name[:40], m.group(2), m.group(3), m.group(1)))
        return "无近期走查（有报告但未解析到判定统计行）"
    except Exception as exc:  # noqa: BLE001
        _warn(f"load_sec2_stat failed: {exc!r}")
        return "无近期走查（sec2 目录缺失或不可读，已降级）"


def render(day: str, brief_out: str | None) -> str:
    lines = ["# 晨会三件套（%s）" % day, ""]
    lines.append("- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    lines.append("## 昨日事件汇总")
    ev = load_events(day)
    if isinstance(ev, str):
        lines.append(ev)
    else:
        counts, hits = ev
        total = sum(counts.values())
        lines.append("五类事件共 %d 笔：%s" % (
            total, "、".join("%s %d" % (k, v) for k, v in counts.items())))
        lines.append("")
        if hits:
            lines.append("| 时间 | 类别 | 工单 | 岗 | 摘要 |")
            lines.append("|---|---|---|---|---|")
            for r in hits:
                lines.append("| %s | %s | %s | %s | %s |" % (
                    r.get("ts", "-"), r.get("kind", "-"), r.get("task", "-"),
                    r.get("agent", "-"), str(r.get("detail", ""))[:120]))
        else:
            lines.append("当日无五类事件记录。")
    lines.append("")
    lines.append("## 今日分发清单")
    lines.append("org_baseline 批状态（pending 项即今日待分发）：")
    lines.append("")
    lines.append(load_batches())
    lines.append("")
    lines.append("## 风险与走形跟踪")
    lines.append("- 最近 SEC2 走查：%s" % load_sec2_stat())
    lines.append("- 直批灰度：approval_verdict 全量抄送老田至 10-28（K-ORG-055 L2）。")
    lines.append("")
    del brief_out
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="晨会三件套生成器 v0（K-ORG-056·只读）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="事件统计日期 YYYY-MM-DD（缺省今天）")
    ap.add_argument("--out", default=None, help="输出路径覆盖（缺省 agent_platform/data/morning_brief/）")
    args = ap.parse_args()

    md = render(args.date, args.out)
    out_path = Path(args.out) if args.out else (
        BRIEF_OUT_DIR / ("%s.md" % args.date.replace("-", "")))
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print("[morning_brief] written: %s" % out_path)
    except Exception as exc:  # noqa: BLE001
        _warn(f"write brief failed: {out_path} -> {exc!r}")
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
