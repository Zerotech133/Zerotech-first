# -*- coding: utf-8 -*-
"""席位「真成员化」审计器（五维：授权/工具/触发/审计/回滚）。

标准：docs/standards/2026-10-26_席位真成员化标准_v1.md
判例：HERMES_UI（糜竺）· WO-HERMES-UI-REAL-20261026

用法：
  python agent_platform/tools/seat_realness.py --seat HERMES_UI --task HermesUI-Probe
  python agent_platform/tools/seat_realness.py --all --limit 80
  python agent_platform/tools/seat_realness.py --seat HERMES_UI --json
退出码：0 = 五维全过；1 = 有缺口；2 = 自身错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORG_REL = Path("agent_platform") / "registry" / "org.json"
BUNDLE_REL = Path("portal") / "agent_ide_bundle.json"
EVENTS_REL = Path("portal") / "data" / "dev_events.jsonl"
PLATFORM_TOOLS = {"events", "devtask"}  # 平台内置工具（无独立文件实现）
ROLLBACK_RE = re.compile(r"回滚|撤销|kill\s*switch|Uninstall|_PROBE|_PROBE=|安全停止", re.I)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_org_profiles(root: Path) -> dict:
    org = _read_json(root / ORG_REL) or {}
    out = {}
    for d in org.get("departments") or []:
        ap = d.get("agent_profiles") or {}
        items = ap.values() if isinstance(ap, dict) else ap or []
        for p in items:
            if isinstance(p, dict) and p.get("agent_id"):
                out[p["agent_id"]] = (d, p)
    return out


def load_bundle_agents(root: Path) -> dict:
    b = _read_json(root / BUNDLE_REL) or {}
    return b.get("agents") or {}


def load_recent_events(root: Path, days: int = 7) -> list:
    path = root / EVENTS_REL
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        day = str(rec.get("day") or rec.get("ts") or "")[:10]
        if day and day >= cutoff:
            out.append(rec)
    return out


def task_exists(task: str) -> bool:
    try:
        proc = subprocess.run(["schtasks", "/Query", "/TN", task],
                              capture_output=True, text=True,
                              encoding="gbk", errors="replace", timeout=20)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _tool_impl(root: Path, name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return ""
    if name in PLATFORM_TOOLS:
        return "platform-builtin"
    for cand in (root / "agent_platform" / "tools" / (name + ".py"),
                 root / "tools" / (name + ".py"),
                 root / "agent_platform" / "tools" / (name + ".ps1")):
        if cand.is_file():
            return str(cand.relative_to(root)).replace("\\", "/")
    # 平台数据工具层：agent_platform/engine/tools.py 内以 def <name>( 实现（如 kline_daily）
    engine = root / "agent_platform" / "engine" / "tools.py"
    try:
        if engine.is_file() and re.search(
                r"^def %s\(" % re.escape(name),
                engine.read_text(encoding="utf-8"), re.M):
            return "agent_platform/engine/tools.py:%s" % name
    except OSError:
        pass
    # 投研/研究只读工具层（2026-10-27 ACAD1 P0）：quant_backtest/agent_tools.py
    # 内以 def <name>( 实现（datamart_read/market_snapshot/signal_precedents…）。
    quant_tools = root / "quant_backtest" / "agent_tools.py"
    try:
        if quant_tools.is_file() and re.search(
                r"^def %s\(" % re.escape(name),
                quant_tools.read_text(encoding="utf-8", errors="replace"), re.M):
            return "quant_backtest/agent_tools.py:%s" % name
    except OSError:
        pass
    return ""


def audit(root: Path, seat: str, task: str = "", days: int = 7) -> dict:
    root = Path(root)
    profiles = load_org_profiles(root)
    bundle_agents = load_bundle_agents(root)
    events = load_recent_events(root, days)
    row = {}

    def add(dim: str, ok: bool, evidence: str):
        row[dim] = {"ok": bool(ok), "evidence": evidence}

    # 1) 授权（org + HR + prompt）
    dept_prof = profiles.get(seat)
    card = bundle_agents.get(seat) or {}
    hr_dir = root / "agent_platform" / "hr" / seat
    hr = any(hr_dir.rglob("*.yaml")) if hr_dir.is_dir() else False
    prompt_file = card.get("prompt_file") or ""
    if not prompt_file and dept_prof:
        prompt_file = "agent_platform/prompts/%s.md" % dept_prof[1].get("role_profile", "")
    prompt_ok = bool(prompt_file) and (root / prompt_file).is_file()
    identity_ok = bool(dept_prof) and hr and prompt_ok
    add("authorization", identity_ok,
        "org=%s hr=%s prompt=%s" % (bool(dept_prof), hr, prompt_file or "-"))

    # 2) 工具（org tools 非空 + 每个有实现）
    tools = (dept_prof[1].get("tools") if dept_prof else None) or card.get("tools") or []
    impls = {t: _tool_impl(root, t) for t in tools}
    tool_ok = bool(tools) and all(impls.values())
    add("tools", tool_ok,
        json.dumps(impls, ensure_ascii=False) if tools else "tools=[]（空卡）")

    # 3) 触发（计划任务存在；未指定任务名则显式失败，提醒未验证）
    t_ok = bool(task) and task_exists(task)
    add("trigger", t_ok,
        "task=%s exists=%s" % (task or "-", task_exists(task) if task else False))

    # 4) 审计（近 N 天有该席事件）
    hits = [e for e in events
            if str(e.get("agent") or "") == seat or str(e.get("task") or "") == seat]
    add("audit", bool(hits),
        "events_last_%dd=%d" % (days, len(hits)))

    # 5) 回滚（prompt 写明回滚/kill switch；无 prompt 则失败）
    prompt_text = ""
    if prompt_ok:
        try:
            prompt_text = (root / prompt_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            prompt_text = ""
    rb = ROLLBACK_RE.search(prompt_text)
    add("rollback", bool(rb), "prompt_rollback_marker=%s" % (rb.group(0) if rb else "none"))

    score = sum(1 for v in row.values() if v["ok"])
    return {"seat": seat, "score": score, "total": 5, "rows": row,
            "missing": [k for k, v in row.items() if not v["ok"]]}


def render(report: dict) -> str:
    icons = {"ok": "✓", "fail": "✗"}
    lines = ["%s  真成员度 %d/%d" % (report["seat"], report["score"], report["total"])]
    for dim, v in report["rows"].items():
        lines.append("  %s %-14s %s" % (icons["ok" if v["ok"] else "fail"], dim, v["evidence"]))
    if report["missing"]:
        lines.append("  缺口: " + ", ".join(report["missing"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="席位真成员化审计（五维）")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--seat", default="")
    ap.add_argument("--task", default="", help="该席计划任务名（有则检查存在性）")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not (root / ORG_REL).is_file():
        print(json.dumps({"ok": False, "error": "org.json missing"}, ensure_ascii=False))
        return 2
    if args.all:
        agents = load_bundle_agents(root)
        seats = [s for s, c in agents.items()
                 if (c or {}).get("entry_source") != "virtual_governance"][: args.limit]
        reports = [audit(root, s, days=args.days) for s in seats]
        if args.json:
            print(json.dumps({"reports": reports}, ensure_ascii=False, indent=1))
        else:
            for r in reports:
                print(render(r))
                print("-" * 60)
        return 0 if all(r["score"] == 5 for r in reports) else 1
    seat = args.seat.strip()
    if not seat:
        print("用法: --seat <ID> [--task <任务名>] 或 --all", file=sys.stderr)
        return 2
    report = audit(root, seat, task=args.task, days=args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render(report))
    return 0 if report["score"] == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
