# -*- coding: utf-8 -*-
"""Agent 运行 trace + 成本观测（P0② · WO-TRACE-COST-20261027）。

设计目标：不引外部依赖、不改生产链路——每个值守/Agent 运行追加一行 JSONL，
记录 trace_id、代理/任务、耗时、状态、可选 token/成本；再提供日汇总/看板。

存储：<root>/agent_platform/data/observability/traces.jsonl（运行态，gitignored）
schema：agent_trace/v1

CLI：
  python -X utf8 agent_platform/tools/trace_cost.py record --agent ARCH1 --task arch1_weekly --status ok --elapsed-ms 1234
  python -X utf8 agent_platform/tools/trace_cost.py report --days 7
  python -X utf8 agent_platform/tools/trace_cost.py report --days 1 --write   # 落 markdown 日报
  python -X utf8 agent_platform/tools/trace_cost.py tail -n 5
退出码：0 正常；2 输入错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
OBS_REL = Path("agent_platform") / "data" / "observability"
SCHEMA = "agent_trace/v1"


def _root(root: Path | None) -> Path:
    return Path(root) if root else REPO


def trace_file(root: Path | None = None) -> Path:
    return _root(root) / OBS_REL / "traces.jsonl"


def new_trace_id() -> str:
    return dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def record(agent: str, task: str, status: str = "ok", elapsed_ms: float | None = None,
           root: Path | None = None, model: str = "", tokens_in: int = 0,
           tokens_out: int = 0, cost_usd: float = 0.0, detail: str = "",
           trace_id: str | None = None, dry_run: bool = False) -> dict:
    rec = {
        "schema": SCHEMA,
        "trace_id": trace_id or new_trace_id(),
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "agent": agent,
        "task": task,
        "status": status,
        "elapsed_ms": int(elapsed_ms) if elapsed_ms is not None else None,
        "model": model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": round(float(cost_usd or 0.0), 6),
        "detail": (detail or "")[:300],
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    path = trace_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        fd = __import__("os").open(str(path), __import__("os").O_APPEND | __import__("os").O_CREAT | __import__("os").O_WRONLY, 0o644)
        try:
            __import__("os").write(fd, line.encode("utf-8"))
        finally:
            __import__("os").close(fd)
    except OSError as exc:
        print("[trace_cost] write failed: %s" % exc)
    return rec


def load(root: Path | None = None) -> list[dict]:
    path = trace_file(root)
    if not path.is_file():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return round(s[k], 1)


def report(root: Path | None = None, days: int = 7) -> dict:
    now = dt.datetime.now().astimezone()
    cutoff = now - dt.timedelta(days=days)
    rows = []
    for r in load(root):
        try:
            ts = dt.datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=now.tzinfo)
        if ts >= cutoff:
            rows.append(r)
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (str(r.get("agent")), str(r.get("task")))
        g = groups.setdefault(key, {"agent": key[0], "task": key[1], "n": 0, "ok": 0,
                                    "fail": 0, "elapsed": [], "tokens_in": 0,
                                    "tokens_out": 0, "cost_usd": 0.0, "fails": []})
        g["n"] += 1
        if r.get("status") == "ok":
            g["ok"] += 1
        else:
            g["fail"] += 1
            if len(g["fails"]) < 3:
                g["fails"].append(str(r.get("detail") or r.get("status")))
        if isinstance(r.get("elapsed_ms"), (int, float)):
            g["elapsed"].append(float(r["elapsed_ms"]))
        g["tokens_in"] += int(r.get("tokens_in") or 0)
        g["tokens_out"] += int(r.get("tokens_out") or 0)
        g["cost_usd"] = round(g["cost_usd"] + float(r.get("cost_usd") or 0.0), 6)
    out = []
    for g in groups.values():
        g["rate"] = round(g["ok"] / g["n"], 3) if g["n"] else 0.0
        g["p50_ms"] = _pct(g["elapsed"], 0.50)
        g["p95_ms"] = _pct(g["elapsed"], 0.95)
        g.pop("elapsed")
        out.append(g)
    out.sort(key=lambda x: (-x["n"], x["agent"], x["task"]))
    return {
        "schema": "agent_trace_report/v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "window_days": days,
        "records": len(rows),
        "groups": out,
        "agents": sorted({r.get("agent") for r in rows}),
        "total_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
        "top_failures": [g for g in out if g["fail"]][:5],
    }


def write_daily_markdown(root: Path | None = None, days: int = 1) -> Path:
    rep = report(root, days=days)
    rootp = _root(root)
    out = rootp / OBS_REL / ("trace_daily_%s.md" % dt.date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Agent Trace 日报 %s（窗口 %d 天）" % (dt.date.today().isoformat(), days), "",
             "- 记录数：%d ｜ 总成本：$%s" % (rep["records"], rep["total_cost_usd"]), "",
             "| Agent | Task | n | ok | fail | 成功率 | p50 ms | p95 ms | tokens | cost $ |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for g in rep["groups"]:
        lines.append("| %s | %s | %d | %d | %d | %.0f%% | %s | %s | %d | %s |"
                     % (g["agent"], g["task"], g["n"], g["ok"], g["fail"], g["rate"] * 100,
                        g["p50_ms"], g["p95_ms"], g["tokens_in"] + g["tokens_out"], g["cost_usd"]))
    if not rep["groups"]:
        lines.append("| （无记录） | - | 0 | 0 | 0 | - | - | - | 0 | 0 |")
    if rep["top_failures"]:
        lines += ["", "## 失败 Top", ""]
        for g in rep["top_failures"]:
            lines.append("- %s/%s: %d 次（%s）" % (g["agent"], g["task"], g["fail"], "; ".join(g["fails"])))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _print_report(rep: dict) -> None:
    print("trace report ｜ 记录 %d ｜ 成本 $%s ｜ 窗口 %d 天"
          % (rep["records"], rep["total_cost_usd"], rep["window_days"]))
    print("  %-8s %-22s %4s %4s %4s %6s %8s %8s %8s"
          % ("agent", "task", "n", "ok", "fail", "rate", "p50ms", "p95ms", "cost$"))
    for g in rep["groups"]:
        print("  %-8s %-22s %4d %4d %4d %5.0f%% %8s %8s %8s"
              % (g["agent"], g["task"], g["n"], g["ok"], g["fail"], g["rate"] * 100,
                 g["p50_ms"], g["p95_ms"], g["cost_usd"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent trace/成本观测")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record")
    rec.add_argument("--agent", required=True)
    rec.add_argument("--task", required=True)
    rec.add_argument("--status", default="ok", choices=["ok", "fail", "warn", "skip"])
    rec.add_argument("--elapsed-ms", type=float, default=None)
    rec.add_argument("--model", default="")
    rec.add_argument("--tokens-in", type=int, default=0)
    rec.add_argument("--tokens-out", type=int, default=0)
    rec.add_argument("--cost-usd", type=float, default=0.0)
    rec.add_argument("--detail", default="")
    rec.add_argument("--root", default=None)

    rep = sub.add_parser("report")
    rep.add_argument("--days", type=int, default=7)
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--write", action="store_true", help="写 markdown 日报")
    rep.add_argument("--root", default=None)

    tail = sub.add_parser("tail")
    tail.add_argument("-n", type=int, default=5)
    tail.add_argument("--root", default=None)

    args = ap.parse_args(argv)
    root = Path(args.root) if getattr(args, "root", None) else None
    if args.cmd == "record":
        r = record(args.agent, args.task, status=args.status, elapsed_ms=args.elapsed_ms,
                   root=root, model=args.model, tokens_in=args.tokens_in,
                   tokens_out=args.tokens_out, cost_usd=args.cost_usd, detail=args.detail)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    if args.cmd == "report":
        rep_obj = report(root, days=args.days)
        if args.write:
            p = write_daily_markdown(root, days=args.days)
            rep_obj["written"] = str(p)
        if args.json:
            print(json.dumps(rep_obj, ensure_ascii=False, indent=1))
        else:
            _print_report(rep_obj)
            if args.write:
                print("日报: %s" % rep_obj["written"])
        return 0
    if args.cmd == "tail":
        rows = load(root)[-max(1, args.n):]
        for r in rows:
            print("%s %-8s %-20s %-4s %sms %s"
                  % (r.get("ts"), r.get("agent"), r.get("task"), r.get("status"),
                     r.get("elapsed_ms"), r.get("detail", "")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
