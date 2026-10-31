#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划台账生成器（2026-10-30，关羽 P90）。

用途：把 docs/plans/ 的现行计划扫成一张可核对台账，暴露"计划通胀/索引过期/缺生命周期标注"
等治理问题；只读扫描，不修改任何计划文件。

用法：
  python tools/plan_register.py              # 摘要 + 落 JSON/MD 台账到 agent_platform/data/reports/plan_register/
  python tools/plan_register.py --check      # 只打印摘要（供日报/复盘，不落盘）
  python tools/plan_register.py --md-out docs/plans/PLAN_REGISTER.md   # 另写一份台账到指定位置
退出码：0 正常；1 扫描失败。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLANS = REPO / "docs" / "plans"
REPORTS = REPO / "agent_platform" / "data" / "reports" / "plan_register"

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
REF_RE = re.compile(r"\b((?:K-(?:STOCK|ORG|QUANT|PUSH|DASH|INTEL|DOC)-\d+)|(?:WO-[A-Z0-9-]+-\d{8}))\b")

# 生命周期标注（文件头/正文里的显式标记）
MARKERS = {
    "superseded": ("被取代", re.compile(r"STATUS:\s*superseded|被取代|superseded by", re.I)),
    "frozen": ("冻结/只读", re.compile(r"STATUS:\s*(frozen|readonly)|只读历史|冻结", re.I)),
    "archived": ("已归档", re.compile(r"STATUS:\s*archived|已归档|归档只读", re.I)),
    "pending": ("待批/待执行", re.compile(r"待批|待签|待执行|未执行|尚未执行", re.I)),
    "done": ("完工", re.compile(r"完工|已收官|已完成|结项", re.I)),
}
KINDS = ("路线图", "规划", "计划", "方案", "设计", "施工", "复盘", "审计", "对照", "预演", "runbook", "提案", "决议")


def classify(text: str) -> str:
    """粗分生命周期：archived > superseded > frozen > pending > done > active。"""
    hits = [name for name, (_, rx) in MARKERS.items() if rx.search(text)]
    for name in ("archived", "superseded", "frozen"):
        if name in hits:
            return MARKERS[name][0]
    if "pending" in hits:
        return MARKERS["pending"][0]
    if "done" in hits:
        return MARKERS["done"][0]
    return "活跃(未标注)"


def kind_of(name: str, title: str) -> str:
    for k in KINDS:
        if k.lower() in name.lower() or k.lower() in title.lower():
            return k
    return "其他"


def scan_plan(p: Path) -> dict:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        mtime = p.stat().st_mtime
        size = p.stat().st_size
    except OSError as exc:
        return {"file": p.name, "error": repr(exc)}
    m = DATE_RE.match(p.name)
    tm = TITLE_RE.search(text)
    title = tm.group(1).strip() if tm else p.stem
    refs = sorted(set(REF_RE.findall(text)))
    plan_date = m.group(1) if m else ""
    if plan_date:  # 计划年龄按文件名日期算（worktree 检出 mtime 不可信）
        try:
            d0 = time.mktime(time.strptime(plan_date, "%Y-%m-%d"))
            age = round((time.time() - d0) / 86400.0, 1)
        except ValueError:
            age = round((time.time() - mtime) / 86400.0, 1)
    else:
        age = round((time.time() - mtime) / 86400.0, 1)
    return {
        "file": p.name,
        "date": plan_date,
        "title": title,
        "kind": kind_of(p.name, title),
        "lifecycle": classify(text),
        "size": size,
        "age_days": age,
        "refs": refs,
        "status_marked": bool(re.search(r"STATUS:", text)),
    }


def index_claim() -> dict:
    idx = PLANS / "INDEX_计划全集索引.md"
    info = {"exists": idx.exists(), "claimed_count": None, "mtime": None}
    if idx.exists():
        try:
            head = idx.read_text(encoding="utf-8", errors="replace")[:4000]
            m = re.search(r"全量\s*(\d+)\s*份", head)
            info["claimed_count"] = int(m.group(1)) if m else None
            md = re.search(r"生成[：:]\s*(\d{4}-\d{2}-\d{2})", head)
            info["mtime"] = md.group(1) if md else None
        except OSError:
            pass
    return info


STALE_DAYS = 14


def build(stale_days: float = STALE_DAYS) -> dict:
    if not PLANS.is_dir():
        raise SystemExit("docs/plans 不存在：%s" % PLANS)
    # 2026-10-31 PLANREG：生成本工具/全量台账（PLAN_REGISTER*）不计入计划统计
    files = sorted(p for p in PLANS.glob("*.md")
                   if p.name.lower() not in ("readme.md", "plan_register.md")
                   and not p.name.startswith("INDEX_")
                   and not p.name.upper().startswith("PLAN_REGISTER"))
    rows = [scan_plan(p) for p in files]
    by_life: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    unmarked_old = []
    for r in rows:
        if r.get("error"):
            continue
        by_life[r["lifecycle"]] = by_life.get(r["lifecycle"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        if r["lifecycle"] == "活跃(未标注)" and r["age_days"] > stale_days:
            unmarked_old.append(r)
    unmarked_old.sort(key=lambda x: -x["age_days"])
    stale_ratio = round(len(unmarked_old) * 100.0 / len(rows), 1) if rows else 0.0
    idx = index_claim()
    return {
        "tool": "plan_register",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plans_dir": str(PLANS),
        "total": len(rows),
        "by_lifecycle": dict(sorted(by_life.items(), key=lambda kv: -kv[1])),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "status_marked": sum(1 for r in rows if r.get("status_marked")),
        "stale_days": stale_days,
        "stale_unmarked": len(unmarked_old),
        "stale_ratio_pct": stale_ratio,
        "stale_sample": [{"file": r["file"], "age_days": r["age_days"]}
                         for r in unmarked_old[:15]],
        "index": idx,
        "rows": rows,
    }


def to_md(rep: dict) -> str:
    lines = ["# 计划台账（PLAN_REGISTER）", "",
             "> 自动生成：`python tools/plan_register.py`（只读扫描 docs/plans/）",
             "> 生成时间：%s ｜ 计划数：**%d**" % (rep["generated_at"], rep["total"]), "",
             "## 一、摘要", "",
             "| 指标 | 值 |", "|---|---|"]
    lines.append("| 计划总数 | %d |" % rep["total"])
    lines.append("| 有 STATUS 显式标注 | %d（%.0f%%） |"
                 % (rep["status_marked"],
                    rep["status_marked"] * 100.0 / rep["total"] if rep["total"] else 0))
    lines.append("| 生命周期分布 | %s |" % "，".join(
        "%s %d" % kv for kv in rep["by_lifecycle"].items()))
    lines.append("| >30 天且未标注（疑似过期） | **%d（%.1f%%）** |"
                 % (rep["stale_unmarked"], rep["stale_ratio_pct"]))
    idx = rep["index"]
    lines.append("| 旧索引 INDEX | %s 声称 %s 份（索引日期 %s），实际 %d 份 → %s |"
                 % ("存在" if idx["exists"] else "缺失",
                    idx["claimed_count"], idx["mtime"], rep["total"],
                    "**过期**" if (idx["claimed_count"] or 0) != rep["total"] else "一致"))
    lines += ["", "## 二、疑似过期计划（>30 天未动且未标冻结/取代）", "",
              "| 文件 | 年龄(天) |", "|---|---:|"]
    for r in rep["stale_sample"]:
        lines.append("| %s | %.1f |" % (r["file"], r["age_days"]))
    lines += ["", "## 三、全量台账", "",
              "| 日期 | 文件 | 类型 | 生命周期 | 年龄(天) | 工单引用 |",
              "|---|---|---|---|---:|---|"]
    for r in rep["rows"]:
        if r.get("error"):
            lines.append("| ? | %s | - | 扫描失败 | - | - |" % r["file"])
            continue
        lines.append("| %s | %s | %s | %s | %.1f | %s |" % (
            r["date"] or "?", r["file"], r["kind"], r["lifecycle"], r["age_days"],
            ",".join(r["refs"][:3])))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="docs/plans 计划台账生成器")
    ap.add_argument("--check", action="store_true", help="只打印摘要，不落盘")
    ap.add_argument("--md-out", default="", help="另写台账到指定 md 路径")
    ap.add_argument("--stale-days", type=float, default=STALE_DAYS,
                    help="活跃未标注且超龄多少天算疑似过期（默认 14）")
    args = ap.parse_args(argv)
    try:
        rep = build(args.stale_days)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("PLAN_REGISTER ERROR:", repr(exc), file=sys.stderr)
        return 1
    print("plan_register: total=%d status_marked=%d stale>%dd_unmarked=%d(%.1f%%) index_claim=%s"
          % (rep["total"], rep["status_marked"], int(rep["stale_days"]),
             rep["stale_unmarked"], rep["stale_ratio_pct"],
             (rep["index"] or {}).get("claimed_count")))
    if not args.check:
        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        (REPORTS / ("register_%s.json" % stamp)).write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        (REPORTS / ("register_%s.md" % stamp)).write_text(to_md(rep), encoding="utf-8")
        print("report -> %s" % REPORTS)
    if args.md_out:
        out = Path(args.md_out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_md(rep), encoding="utf-8", newline="\n")
        print("md -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
