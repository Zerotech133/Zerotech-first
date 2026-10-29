# -*- coding: utf-8 -*-
"""研究周复盘：台账状态汇总 + 本周新增报告 + 四关进展（供周五例会）。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import hypotheses as h  # noqa: E402


def review(days: int = 7) -> dict:
    rows = h.rows()
    by_status = Counter(r["status"] for r in rows)
    reports = []
    rdir = ROOT / "research" / "reports"
    cutoff = datetime.now() - timedelta(days=days)
    if rdir.exists():
        for f in sorted(rdir.glob("*.md")):
            mt = datetime.fromtimestamp(f.stat().st_mtime)
            if mt >= cutoff:
                reports.append({"file": f.name, "mtime": mt.strftime("%Y-%m-%d %H:%M")})
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "total": len(rows), "by_status": dict(by_status),
            "open": [r for r in rows if r["status"] in ("idea", "in_progress")],
            "this_week_reports": reports,
            "rejected": [r["id"] for r in rows if r["status"] == "rejected"],
            "backtested": [r["id"] for r in rows if r["status"] == "backtested"]}


def render(res: dict) -> str:
    lines = ["# 研究周复盘（%s）" % res["generated_at"], ""]
    lines.append("## 台账总览：%d 条 | %s" % (
        res["total"], " / ".join("%s=%s" % (k, v) for k, v in sorted(res["by_status"].items()))))
    lines.append("")
    lines.append("## 本周新增报告（%d）" % len(res["this_week_reports"]))
    for r in res["this_week_reports"]:
        lines.append("- %s（%s）" % (r["file"], r["mtime"]))
    lines.append("")
    lines.append("## 待推进（idea/in_progress）")
    for r in res["open"]:
        lines.append("- %s %s [%s]" % (r["id"], r["title"], r["status"]))
    lines.append("")
    lines.append("## 本周结论（rejected 也是产出）")
    for hid in res["rejected"]:
        row = h.get(hid) or {}
        lines.append("- **%s rejected**：%s" % (hid, (row.get("conclusion") or "")[:140]))
    lines.append("")
    lines.append("> 例会 30 分钟：①数据/报告变化 ②上周假设结论 ③下周 3 个假设与所需特征 ④决策（进影子/淘汰/补数据）")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="研究周复盘")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = review(args.days)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
