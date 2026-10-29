# -*- coding: utf-8 -*-
"""黄忠每日验收观察（观察模式）：跑验收单 → 记 daily.jsonl → 周报指标。

不阻断 merge；不改代码/配置/审批。仅写 work/huangzhong/。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
OUT = ROOT / "work" / "huangzhong"
DAILY = OUT / "daily.jsonl"


def _accept_fn():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import huangzhong_accept as hz
    return hz.accept


def _emit_event(summary: dict, emit_fn=None) -> None:
    """日更验收单落事件层（WO-HZ-MEET-20261030 黄忠日更验收联动）。

    emit_fn 可注入（测试用 fake，不写真实事件文件）；缺省惰性 import
    tools/events.py 的 emit——不在模块导入期拉起事件层。事件层**不是**主流程
    依赖：任何异常一律吞掉（fail-open），日更验收单必须先落盘、结果必须先返回，
    不得因事件层故障而中断或失败。
    """
    try:
        if emit_fn is None:
            if str(TOOLS) not in sys.path:
                sys.path.insert(0, str(TOOLS))
            import events as _events
            emit_fn = _events.emit
        emit_fn(
            kind="acceptance_done",
            task="HUANGZHONG-DAILY",
            detail="verdict=%s commit=%s baseline_failed=%s redteam=%s report=%s"
            % (summary.get("verdict"), summary.get("commit"),
               summary.get("baseline_failed"), summary.get("redteam_rate"),
               summary.get("report")),
        )
    except Exception:  # noqa: BLE001  fail-open：事件层永不影响日更主流程
        pass


def run_daily(commit: str = "HEAD", accept_fn=None, out_dir: Path | None = None,
              emit_fn=None) -> dict:
    accept = accept_fn or _accept_fn()
    rep = accept(commit)
    summary = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": (rep.get("commit") or {}).get("sha", "")[:12],
        "verdict": rep.get("verdict"),
        "blocking": rep.get("blocking"),
        "reasons": rep.get("reasons") or [],
        "baseline_failed": ((rep.get("evidence") or {}).get("baseline") or {}).get("failed"),
        "redteam_rate": ((rep.get("evidence") or {}).get("redteam") or {}).get("pass_rate"),
        "report": rep.get("report_md", ""),
    }
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    with (out / "daily.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    # 先落盘再发事件：事件层失败不回滚、不影响返回值（fail-open）
    _emit_event(summary, emit_fn)
    return summary


def _merge_done_count(days: int = 7) -> int:
    """窗口内 dev_events 中 merge_done 数（覆盖率分母）；读不到返回 0。"""
    p = ROOT / "portal" / "data" / "dev_events.jsonl"
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
    n = 0
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "merge_done":
                continue
            try:
                ts = dt.datetime.strptime(r.get("ts", ""), "%Y-%m-%d %H:%M:%S")
                ts = ts.astimezone()
            except (ValueError, TypeError):
                continue
            if ts >= cutoff:
                n += 1
    except OSError:
        return 0
    return n


def weekly(days: int = 7, out_dir: Path | None = None) -> dict:
    out = Path(out_dir) if out_dir else OUT
    p = out / "daily.jsonl"
    rows = []
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                ts = dt.datetime.fromisoformat(r["ts"])
            except (ValueError, KeyError):
                continue
            if ts >= cutoff:
                rows.append(r)
    merge_done = _merge_done_count(days)
    counts = {"PASS": 0, "REJECT": 0, "OBSERVE": 0}
    for r in rows:
        if r.get("verdict") in counts:
            counts[r["verdict"]] += 1
    total = len(rows)
    coverage = round(total / merge_done, 3) if merge_done else 0.0
    rep = {
        "ok": True, "window_days": days, "runs": total,
        "merge_done": merge_done, "coverage_rate": coverage,
        "counts": counts,
        "pass_rate": round(counts["PASS"] / total, 3) if total else 0.0,
        "reject_rate": round(counts["REJECT"] / total, 3) if total else 0.0,
        "observe_rate": round(counts["OBSERVE"] / total, 3) if total else 0.0,
        "needs_manual_review": [r for r in rows if r.get("verdict") != "PASS"],
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kpi_note": "误拒绝率需人工复核 REJECT 列表；漏放率=PASS 后 7 天事故数/PASS 数（人工核对）",
    }
    md = ["# 黄忠验收观察周报（观察模式）", "",
          "- 窗口：%d 天；运行 %d 次" % (days, total),
          "- 验收覆盖率：%d/%d merge_done = %.0f%%" % (total, merge_done,
                                                        coverage * 100),
          "- PASS=%d REJECT=%d OBSERVE=%d（pass_rate=%.0f%%）"
          % (counts["PASS"], counts["REJECT"], counts["OBSERVE"],
             rep["pass_rate"] * 100),
          "- 待人工复核（REJECT/OBSERVE）：%d 条" % len(rep["needs_manual_review"]),
          "", "## 指标口径", "- " + rep["kpi_note"], ""]
    if rep["needs_manual_review"]:
        md.append("## 待复核明细")
        for r in rep["needs_manual_review"][:20]:
            md.append("- %s commit=%s verdict=%s %s"
                      % (r.get("ts", ""), r.get("commit", ""),
                         r.get("verdict", ""), "；".join(r.get("reasons") or [])[:120]))
    mp = out / ("weekly_%s.md" % dt.datetime.now().strftime("%Y%m%d"))
    mp.write_text("\n".join(md), encoding="utf-8")
    rep["report_md"] = str(mp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠每日验收观察")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = weekly(args.days) if args.weekly else run_daily(args.commit)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else json.dumps(rep, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
