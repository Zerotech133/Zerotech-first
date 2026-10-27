# -*- coding: utf-8 -*-
"""P2 红队定时化 v0：每日抽样 + 周报（不建计划任务，先提供可调度入口）。

- daily：跑 zhuge_redteam 的 live 抽样（默认 2 例），摘要追加 work/p1/redteam/daily.jsonl；
- weekly：聚合最近 N 天，输出 Markdown 周报；
- --dry：用安全回答 fixture 跑（零模型调用，用于自检/CI）。
建议由外部调度器每日调用：`python tools/redteam_daily.py --daily --limit 2`
（注册 Windows 计划任务属宿主机变更，需人工批准后另行执行）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
OUT = REPO / "work" / "p1" / "redteam"
DAILY = OUT / "daily.jsonl"


def _rt():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import zhuge_redteam  # noqa: E402
    return zhuge_redteam


def _safe_responder(case):
    return "资料不足，无法判断；需要先取得证据后再回答。", []


def run_daily(limit: int = 2, dry: bool = False, responder=None,
              daily_path: Path | None = None) -> dict:
    rt = _rt()
    cases = rt.load_cases()[: max(1, int(limit))]
    responder = responder or (_safe_responder if dry else rt._live_responder)
    rep = rt.run(cases, responder)
    summary = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "dry" if dry else "live",
        "total": rep["total"],
        "passed": rep["passed"],
        "failed": rep["failed"],
        "pass_rate": rep["pass_rate"],
        "failed_ids": [r["case_id"] for r in rep["results"] if not r["passed"]],
    }
    p = Path(daily_path) if daily_path else DAILY
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def weekly(days: int = 7, daily_path: Path | None = None,
           out_dir: Path | None = None) -> dict:
    p = Path(daily_path) if daily_path else DAILY
    rows = []
    if p.exists():
        cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                ts = dt.datetime.fromisoformat(r["ts"])
            except (ValueError, KeyError):
                continue
            if ts >= cutoff:
                rows.append(r)
    total = sum(r.get("total", 0) for r in rows)
    passed = sum(r.get("passed", 0) for r in rows)
    failed_counts: dict[str, int] = {}
    for r in rows:
        for cid in r.get("failed_ids") or []:
            failed_counts[cid] = failed_counts.get(cid, 0) + 1
    rep = {
        "ok": True,
        "window_days": days,
        "runs": len(rows),
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "failed_case_counts": dict(sorted(failed_counts.items(),
                                          key=lambda x: -x[1])),
        "generated_at": dt.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "read_only": True,
    }
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    md = ["# 诸葛红队周报", "",
          "- 生成：%s（窗口 %d 天）" % (rep["generated_at"], days),
          "- 运行：%d 次；用例：%d；通过 **%d/%d（%.0f%%）**"
          % (rows and len(rows), total, passed, total, rep["pass_rate"] * 100)]
    if failed_counts:
        md += ["", "## 高频失败", ""]
        for cid, n in rep["failed_case_counts"].items():
            md.append("- **%s** ×%d" % (cid, n))
    mp = out / ("weekly_%s.md" % dt.datetime.now().strftime("%Y%m%d"))
    mp.write_text("\n".join(md), encoding="utf-8")
    rep["report"] = str(mp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="红队每日抽样/周报")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=2)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.weekly:
        rep = weekly(args.days)
    else:
        rep = run_daily(args.limit, dry=args.dry)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
