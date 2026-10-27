# -*- coding: utf-8 -*-
"""A 阶段组级看板生成器（2026-10-27 WO-A-STAGE-BOARD）。

只读 `agent_platform/registry/org.json` 的 `dev.groups` 与 `.worktrees/tasks.json`，
按 G1/G2 汇总组内工单：总数/近 N 天/在制/完成/前置时间/验收覆盖/成员负载，
输出 `docs/dev/group_board.html`（引用 /static/tokens.css 与 /static/nav.js）
以及 `--json` 机读结果。`--check` 校验产物存在与关键段。

边界：只读；不写 org、不改工单；生成物除 --out 外不写盘。
"""
from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
TASKS = ROOT / ".worktrees" / "tasks.json"
OUT = ROOT / "docs" / "dev" / "group_board.html"
DEV_SEATS = ["ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5",
             "DEV6", "DEV7", "DEV8", "OPS1", "DOC1"]


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def load_tasks(path=TASKS):
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
                return {}
            raw = raw[:i] + raw[i + 1:]


def _ts(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return None


def dev_groups(org):
    for dept in (org or {}).get("departments") or []:
        if str(dept.get("dept") or "") == "dev":
            groups = dept.get("groups") or []
            return [g for g in groups if isinstance(g, dict)]
    return []


def compute(groups, tasks, window_days=7, now=None):
    now = now or time.time()
    cut = time.strftime("%Y-%m-%d", time.localtime(now - window_days * 86400))
    rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        rows.append({
            "id": tid, "owner": (t.get("owner") or "").strip(),
            "status": t.get("status") or "",
            "created": str(t.get("created") or ""),
            "merged": str(t.get("merged") or ""),
            "dept": t.get("dept"),
            "acceptance": bool(t.get("acceptance_cmd")),
        })

    def stat(members):
        mine = [r for r in rows if r["owner"] in members]
        recent = [r for r in mine if r["created"][:10] >= cut]
        done = [r for r in recent if r["status"] == "done"]
        doing = [r for r in mine if r["status"] in ("doing", "review")]
        lead = []
        for r in done:
            a, b = _ts(r["created"]), _ts(r["merged"])
            if a and b and b >= a:
                lead.append((b - a) / 60.0)
        lead.sort()
        dev_mine = [r for r in mine if (r["dept"] or "") == "dev"]
        acc = [r for r in dev_mine if r["acceptance"]]
        load = {}
        for m in sorted(members):
            load[m] = {
                "total": sum(1 for r in mine if r["owner"] == m),
                "recent": sum(1 for r in recent if r["owner"] == m),
                "doing": sum(1 for r in doing if r["owner"] == m),
            }
        return {
            "total": len(mine), "recent": len(recent), "doing": len(doing),
            "done_recent": len(done),
            "lead_p50_min": (round(lead[len(lead) // 2], 1) if lead else None),
            "lead_n": len(lead),
            "acceptance": {"covered": len(acc), "total": len(dev_mine)},
            "members": load,
        }

    out = []
    for g in groups:
        lead = str(g.get("lead") or "")
        members = set(g.get("members") or [])
        if lead:
            members.add(lead)
        out.append({
            "id": str(g.get("id") or ""), "name": str(g.get("name") or ""),
            "lead": lead, "members": sorted(members),
            "stats": stat(members),
        })
    dept = {"groups": out, "total": len(rows),
            "dev_seats": DEV_SEATS}
    return {
        "schema": "a_phase_group_board/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_days": window_days, "cut": cut, "dept": dept,
    }


def _fmt(v, suffix=""):
    return "—" if v is None else "%s%s" % (v, suffix)


def render_html(report):
    d = report["dept"]
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>研发部组级看板（A 阶段）</title>",
        "<link rel='stylesheet' href='/static/tokens.css'>",
        "<script src='/static/nav.js' defer></script>",
        "<style>body{background:var(--bg,#0d1117);color:var(--fg,#e6edf3);"
        "font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:24px}"
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}"
        "th,td{border:1px solid var(--border,#30363d);padding:8px 12px;text-align:left}"
        "th{background:var(--panel,#161b22)}h1{font-size:20px}h2{font-size:16px}"
        ".muted{color:var(--muted,#8b949e);font-size:12px}</style></head><body>",
        "<h1>研发部组级看板 · A 阶段准备</h1>",
        "<p class='muted'>数据源 org.dev.groups + .worktrees/tasks.json · 生成 %s · "
        "窗口 %s 天（切点 %s）· 只读生成物</p>"
        % (html.escape(report["generated_at"]), report["window_days"],
           html.escape(report["cut"])),
        "<table><tr><th>组</th><th>组长</th><th>成员</th><th>总单</th>"
        "<th>近 %s 天</th><th>在制</th><th>近 %s 天完成</th><th>前置 p50（分钟）</th>"
        "<th>验收覆盖</th></tr>"
        % (report["window_days"], report["window_days"]),
    ]
    for g in d["groups"]:
        s = g["stats"]
        parts.append(
            "<tr><td><b>%s</b> %s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s/%s</td></tr>"
            % (html.escape(g["id"]), html.escape(g["name"]),
               html.escape(g["lead"]), html.escape("、".join(g["members"])),
               s["total"], s["recent"], s["doing"], s["done_recent"],
               _fmt(s["lead_p50_min"]), s["acceptance"]["covered"],
               s["acceptance"]["total"]))
    parts.append("</table>")
    parts.append("<h2>成员负载（近 %s 天/在制）</h2>" % report["window_days"])
    parts.append("<table><tr><th>席位</th><th>组</th><th>总单</th><th>近 %s 天</th>"
                 "<th>在制</th></tr>" % report["window_days"])
    for g in d["groups"]:
        for m, v in g["stats"]["members"].items():
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (html.escape(m), html.escape(g["id"]), v["total"],
                            v["recent"], v["doing"]))
    parts.append("</table>")
    parts.append("<p class='muted'>说明：只读看板；验收覆盖=dept=dev 单中带验收命令占比；"
                 "前置时间仅统计 created→merged 的完成单；无数据记 —。</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="A 阶段组级看板生成器（只读）")
    ap.add_argument("--org", default=str(ORG))
    ap.add_argument("--tasks", default=str(TASKS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        ok = out.exists()
        text = out.read_text(encoding="utf-8") if ok else ""
        ok = ok and all(k in text for k in ("组级看板", "<table", "tokens.css", "nav.js"))
        print(("PASS" if ok else "FAIL") + "：" + str(out))
        return 0 if ok else 1
    org = load_json(args.org, {})
    tasks = load_tasks(args.tasks)
    report = compute(dev_groups(org), tasks, window_days=max(1, args.window_days))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding="utf-8")
    print("group_board -> %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
