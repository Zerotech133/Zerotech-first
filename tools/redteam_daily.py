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
ROTATION = OUT / "rotation.json"


def _rt():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import zhuge_redteam  # noqa: E402
    return zhuge_redteam


def _safe_responder(case):
    return "资料不足，无法判断；需要先取得证据后再回答。", []


def _rotation_path(path: Path | None = None) -> Path:
    return Path(path) if path else ROTATION


def _rotation_offset(state_path: Path | None = None) -> int:
    try:
        return max(0, int(json.loads(_rotation_path(state_path).read_text(encoding="utf-8")).get("offset") or 0))
    except (OSError, ValueError, TypeError):
        return 0


def _write_rotation(offset: int, state_path: Path | None = None) -> None:
    p = _rotation_path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"offset": int(offset), "updated": dt.datetime.now().astimezone().isoformat(timespec="seconds")},
                            ensure_ascii=False), encoding="utf-8")


def run_daily(limit: int = 2, dry: bool = False, responder=None,
              daily_path: Path | None = None,
              state_path: Path | None = None, rotate: bool = True) -> dict:
    rt = _rt()
    all_cases = rt.load_cases()
    n = max(1, int(limit))
    offset = _rotation_offset(state_path) % len(all_cases) if (rotate and all_cases) else 0
    selected = all_cases[offset:offset + n] if all_cases else []
    if all_cases and len(selected) < n:
        selected += all_cases[: n - len(selected)]
    responder = responder or (_safe_responder if dry else rt._live_responder)
    rep = rt.run(selected, responder)
    summary = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "dry" if dry else "live",
        "total": rep["total"],
        "passed": rep["passed"],
        "failed": rep["failed"],
        "pass_rate": rep["pass_rate"],
        "failed_ids": [r["case_id"] for r in rep["results"] if not r["passed"]],
        "case_ids": [c["id"] for c in selected],
        "offset": offset,
    }
    p = Path(daily_path) if daily_path else DAILY
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    if rotate and not dry and all_cases:
        _write_rotation((offset + n) % len(all_cases), state_path)
    return summary


def weekly(days: int = 7, daily_path: Path | None = None,
           out_dir: Path | None = None, dry: bool = False) -> dict:
    p = Path(daily_path) if daily_path else DAILY
    rows = []
    skipped = 0
    if p.exists():
        cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
                if not isinstance(r, dict):
                    raise TypeError("not a dict")
                ts = dt.datetime.fromisoformat(str(r["ts"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                skipped += 1
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
            if ts >= cutoff:
                rows.append(r)
    live = [r for r in rows if r.get("mode", "live") == "live"]
    dry_rows = [r for r in rows if r.get("mode") == "dry"]
    total = sum(int(r.get("total") or 0) for r in live)
    passed = sum(int(r.get("passed") or 0) for r in live)
    failed_counts: dict[str, int] = {}
    for r in live:
        for cid in r.get("failed_ids") or []:
            failed_counts[cid] = failed_counts.get(cid, 0) + 1
    ok = bool(live) and total > 0
    rep = {
        "ok": ok,
        "insufficient_data": not ok,
        "window_days": days,
        "runs": len(live),
        "runs_live": len(live),
        "runs_dry": len(dry_rows),
        "skipped_lines": skipped,
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
    if not dry:
        out = Path(out_dir) if out_dir else OUT
        out.mkdir(parents=True, exist_ok=True)
        md = ["# 诸葛红队周报", "",
              "- 生成：%s（窗口 %d 天）" % (rep["generated_at"], days),
              "- live 运行：%d 次；dry 运行：%d 次；跳过脏行：%d"
              % (len(live), len(dry_rows), skipped),
              "- live 用例：%d；通过 **%d/%d（%.0f%%）**"
              % (total, passed, total, rep["pass_rate"] * 100)]
        if not ok:
            md.append("- **数据不足：没有 live 样本，不能判定健康**")
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
        rep = weekly(args.days, dry=args.dry)
    else:
        rep = run_daily(args.limit, dry=args.dry)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else json.dumps(rep, ensure_ascii=False))
    if args.weekly and not rep.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
