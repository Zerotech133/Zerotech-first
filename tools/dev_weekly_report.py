# -*- coding: utf-8 -*-
"""研发部周报生成器（C5/C6 交付物 · 2026-10-27 WO-LEGION-P0WRAP）。

只读 `.worktrees/tasks.json` + `portal/data/dev_events.jsonl` + 路由/门配置，
计算研发部 8 项 KPI，输出 Markdown 到 `agent_platform/data/dev/weekly_report.md`
（ARCH1-02 daily_work 的 evidence 文件），同时支持 `--json` 机读与 `--print`。

用法：
  python -X utf8 tools/dev_weekly_report.py                 # 写默认周报
  python -X utf8 tools/dev_weekly_report.py --json          # 只打印 JSON
  python -X utf8 tools/dev_weekly_report.py --out <path>    # 指定落盘位置
  python -X utf8 tools/dev_weekly_report.py --window-days 7
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import devtask  # noqa: E402

TASKS_PATH = ROOT / ".worktrees" / "tasks.json"
EVENTS_PATH = ROOT / "portal" / "data" / "dev_events.jsonl"
ROUTING_PATH = ROOT / "agent_platform" / "config" / "dev_routing_rules.json"
GATE_PATH = ROOT / "agent_platform" / "config" / "dev_owner_gate.json"
DEFAULT_OUT = ROOT / "agent_platform" / "data" / "dev" / "weekly_report.md"
E3_SEATS = ("ARCH1", "DEV1", "DEV5", "OPS1")


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def load_tasks(path=TASKS_PATH):
    p = Path(path)
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8")
    while True:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            i = raw.rfind(",", 0, e.pos)
            if i < 0:
                raise
            raw = raw[:i] + raw[i + 1:]


def load_events(path=EVENTS_PATH):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, int(round((len(sorted_vals) - 1) * p))))
    return round(sorted_vals[k], 1)


def _ts(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return None


def compute(tasks, events, routing=None, gate=None, window_days=7, now=None):
    """纯计算：返回周报指标 dict（不写盘）。"""
    now = now or time.time()
    cut = time.strftime("%Y-%m-%d", time.localtime(now - window_days * 86400))
    dev_seats = [s for s in (routing or {}).get("dev_seats") or []
                 if isinstance(s, str) and s.strip()]
    rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        rows.append({
            "id": tid,
            "owner": t.get("owner") or "",
            "status": t.get("status") or "",
            "created": str(t.get("created") or ""),
            "merged": str(t.get("merged") or ""),
            "dept": t.get("dept"),
            "acceptance": bool(t.get("acceptance_cmd")),
        })

    def legal(owner):
        ok, _domain, _size = devtask._owner_check(owner)
        return ok is True

    recent = [r for r in rows if r["created"][:10] >= cut]
    dev_all = [r for r in rows if r["owner"] in dev_seats] if dev_seats else []
    dev_recent = [r for r in recent if r["owner"] in dev_seats] if dev_seats else []
    dev_merged = [r for r in dev_all if (r["merged"] or "")[:10] >= cut]
    lead = []
    for r in dev_all:
        if r["status"] == "done" and r["merged"] and r["created"]:
            a, b = _ts(r["created"]), _ts(r["merged"])
            if a and b and b >= a:
                lead.append((b - a) / 60.0)
    lead.sort()

    hb_total, hb_ok = {}, {}
    for e in events or []:
        day = str(e.get("day") or e.get("ts") or "")[:10]
        if day < cut:
            continue
        if e.get("kind") != "patrol_verdict":
            continue
        detail = str(e.get("detail") or "")
        if "seat_heartbeat" not in detail:
            continue
        seat = str(e.get("agent") or "")
        hb_total[seat] = hb_total.get(seat, 0) + 1
        if "ok=True" in detail:
            hb_ok[seat] = hb_ok.get(seat, 0) + 1

    dev_dept = [r for r in rows if (r["dept"] or "") == "dev"]
    n = len(rows)
    rn = len(recent)
    return {
        "schema": "dev_weekly_report/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "window_days": window_days,
        "cut": cut,
        "tasks_total": n,
        "tasks_recent": rn,
        "owner_legal_rate_all": round(sum(1 for r in rows if legal(r["owner"])) / n, 4) if n else None,
        "owner_legal_rate_recent": (round(sum(1 for r in recent if legal(r["owner"])) / rn, 4)
                                    if rn else None),
        "dev_capture_share_all": round(len(dev_all) / n, 4) if n else None,
        "dev_capture_share_recent": round(len(dev_recent) / rn, 4) if rn else None,
        "dev_tasks_recent": len(dev_recent),
        "dev_merged_recent": len(dev_merged),
        "dev_leadtime_p50_min": _pct(lead, 0.5),
        "dev_leadtime_p90_min": _pct(lead, 0.9),
        "dev_leadtime_n": len(lead),
        "e3_trigger": {s: {"total": hb_total.get(s, 0), "ok": hb_ok.get(s, 0)}
                       for s in E3_SEATS},
        "dev_dept_tagged": len(dev_dept),
        "dev_dept_with_acceptance": sum(1 for r in dev_dept if r["acceptance"]),
        "gate_phase": str((gate or {}).get("phase") or "warn"),
        "routing_dev_seats": len(dev_seats),
        "na_metrics": [
            "review 等待 p90（devtask 无 review 时间戳，人工登记）",
            "返工率（无字段，人工登记退回次数）",
            "逃逸缺陷（无字段，QA/生产发现人工登记）",
        ],
    }


def render_markdown(m):
    lines = [
        "# 研发部周报（自动生成）",
        "",
        "- as-of：%s（窗口 %s 天，切点 %s）" % (m["generated_at"], m["window_days"], m["cut"]),
        "- 数据源：`.worktrees/tasks.json`、`portal/data/dev_events.jsonl`、dev 路由/门配置",
        "- 生成器：`tools/dev_weekly_report.py`（只读，除本文件外不写盘）",
        "",
        "## 一、八项指标",
        "",
        "| 指标 | 当前值 | D+14 目标 | 判读 |",
        "|---|---:|---:|---|",
        "| E3 触发率（ARCH1/DEV1/DEV5/OPS1） | %s | 4/4 | %s |" % (
            "/".join("%s:%s/%s" % (s, v["ok"], v["total"])
                     for s, v in m["e3_trigger"].items()),
            "达标" if all(v["ok"] >= 1 for v in m["e3_trigger"].values()) else "待补"),
        "| 归属率（全组织新单，近 %s 天） | %.1f%%（合法域） | ≥85%% | %s |" % (
            m["window_days"], (m["owner_legal_rate_recent"] or 0) * 100,
            "达标" if (m["owner_legal_rate_recent"] or 0) >= 0.85 else "未达标"),
        "| 研发采集份额（近 %s 天） | %.2f%%（%s/%s） | 观察（≥5 单/周） | %s |" % (
            m["window_days"], (m["dev_capture_share_recent"] or 0) * 100,
            m["dev_tasks_recent"], m["tasks_recent"],
            "达标" if m["dev_tasks_recent"] >= 5 else "未达标"),
        "| 研发周吞吐 | %s 单/周 | ≥8 | %s |" % (
            m["dev_merged_recent"], "达标" if m["dev_merged_recent"] >= 8 else "未达标"),
        "| dev 前置时间 p50/p90 | %s / %s 分钟（n=%s） | 出基线 | %s |" % (
            m["dev_leadtime_p50_min"], m["dev_leadtime_p90_min"], m["dev_leadtime_n"],
            "样本不足" if (m["dev_leadtime_n"] or 0) < 5 else "基线"),
        "| 验证覆盖（dev 单带验收命令） | %s/%s | ≥90%% | %s |" % (
            m["dev_dept_with_acceptance"], m["dev_dept_tagged"],
            "达标" if m["dev_dept_tagged"] and m["dev_dept_with_acceptance"] >= m["dev_dept_tagged"] * 0.9 else "未达标"),
        "| review 等待 p90 | N/A（无字段） | ≤4h（人工） | 待接线 |",
        "| 返工率 / 逃逸缺陷 | N/A（无字段） | 人工登记 | 待接线 |",
        "",
        "## 二、口径与边界",
        "",
        "- 归属率＝owner ∈ 合法域（org 成员 ∪ profile_seats：ZHAOYUN/DSH-Lead/VERIFY1）。",
        "- 采集份额＝dev 11 席承接单/全部新单，**观察指标不设硬目标**（当前偏低说明业务未流入研发部）。",
        "- 前置时间＝created→merged（仅 done 且有 merged）；样本 <5 时不作判据。",
        "- 周报初版（v1）：review/返工/逃逸无 devtask 字段，先标 N/A，人工登记后再入表。",
        "",
        "## 三、数据点",
        "",
        "- 全量工单：%s；近 %s 天新单：%s；dev 席近 %s 天：%s 单；dev 门 phase=%s；路由名单 %s 席。" % (
            m["tasks_total"], m["window_days"], m["tasks_recent"], m["window_days"],
            m["dev_tasks_recent"], m["gate_phase"], m["routing_dev_seats"]),
        "- 全量归属率：%s；近 %s 天归属率：%s。" % (
            m["owner_legal_rate_all"], m["window_days"], m["owner_legal_rate_recent"]),
        "",
        "> 本文件由生成器覆盖写；如需人工批注请另建 `weekly_report_notes.md`。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="研发部周报生成器（只读计算，除 --out 外不写盘）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="只打印 JSON，不写盘")
    ap.add_argument("--print", action="store_true", help="只打印 Markdown，不写盘")
    args = ap.parse_args(argv)

    tasks = load_tasks()
    events = load_events()
    routing = _load_json(ROUTING_PATH, {})
    gate = _load_json(GATE_PATH, {"phase": "warn"})
    metrics = compute(tasks, events, routing=routing, gate=gate,
                      window_days=max(1, args.window_days))
    md = render_markdown(metrics)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=1))
        return 0
    if args.print:
        print(md)
        return 0
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("weekly_report -> %s" % out)
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
