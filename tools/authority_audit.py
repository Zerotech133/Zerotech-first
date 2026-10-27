# -*- coding: utf-8 -*-
"""ARCH1 研发域最高权限审计与看板（2026-10-27 WO-AUTHORITY-C3）。

只读 tasks.json / dev_events.jsonl / dev_authority_charter.json / docs/adr，
计算：合并署名率、缺署名、自审自批、ARCH1 指挥动作、E3 触发、归属率、红线状态；
输出 JSON / Markdown，并生成 `docs/dev/authority_board.html`（tokens/nav 三件套）。

用法：
  python -X utf8 tools/authority_audit.py --json
  python -X utf8 tools/authority_audit.py --md
  python -X utf8 tools/authority_audit.py --html-out docs/dev/authority_board.html
  python -X utf8 tools/authority_audit.py --check     # 校验产物存在与关键段
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import devtask  # noqa: E402
import logging

TASKS = ROOT / ".worktrees" / "tasks.json"
EVENTS = ROOT / "portal" / "data" / "dev_events.jsonl"
CHARTER = ROOT / "agent_platform" / "config" / "dev_authority_charter.json"
ADR_DIR = ROOT / "docs" / "adr"
HTML_OUT = ROOT / "docs" / "dev" / "authority_board.html"
MD_OUT = ROOT / "work" / "authority" / "authority_audit.md"
E3_SEATS = ("ARCH1", "DEV1", "DEV5", "OPS1")
CMD_KINDS = ("task_start", "review_verdict", "approval_verdict", "dispatch")


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


def load_events(path=EVENTS):
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
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/authority_audit.py", exc)
    return rows


def _cut(now, days=7):
    return time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))


def compute(tasks, events, charter, adr_files=(), now=None):
    now = now or time.time()
    cut = _cut(now)
    rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        rows.append({"id": tid, "owner": (t.get("owner") or "").strip(),
                     "dept": (t.get("dept") or "").strip().lower(),
                     "status": t.get("status") or "",
                     "created": str(t.get("created") or ""),
                     "merged": str(t.get("merged") or ""),
                     "approver": (t.get("approver") or "").strip()})
    dev = [r for r in rows if r["dept"] == "dev"]
    dev_done = [r for r in dev if r["status"] == "done" and r["merged"]]
    approved = [r for r in dev_done if r["approver"]]
    missing = [r for r in dev_done if not r["approver"]]
    self_review = [r for r in dev_done
                   if r["approver"] and r["approver"].upper() == r["owner"].upper()]
    recent = [r for r in rows if r["created"][:10] >= cut]
    recent_legal = 0
    for r in recent:
        try:
            if devtask._owner_check(r["owner"])[0] is True:
                recent_legal += 1
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/authority_audit.py", exc)
    arch1_actions = sum(
        1 for e in events
        if str(e.get("day") or e.get("ts") or "")[:10] >= cut
        and str(e.get("agent") or "") == "ARCH1"
        and str(e.get("kind") or "") in CMD_KINDS)
    hb_total, hb_ok = {}, {}
    for e in events:
        day = str(e.get("day") or e.get("ts") or "")[:10]
        if day < cut or e.get("kind") != "patrol_verdict":
            continue
        detail = str(e.get("detail") or "")
        if "seat_heartbeat" not in detail:
            continue
        seat = str(e.get("agent") or "")
        hb_total[seat] = hb_total.get(seat, 0) + 1
        if "ok=True" in detail:
            hb_ok[seat] = hb_ok.get(seat, 0) + 1
    e3 = {s: {"total": hb_total.get(s, 0), "ok": hb_ok.get(s, 0)} for s in E3_SEATS}
    e3_ok = sum(1 for v in e3.values() if v["ok"] >= 1)

    approval = (charter or {}).get("approval") or {}
    red = []
    red.append({"id": "owner_legal_rate_7d", "value": (round(recent_legal / len(recent), 4)
                                                       if recent else None),
                "ok": (len(recent) == 0 or recent_legal / len(recent) >= 0.85)})
    red.append({"id": "e3_trigger", "value": "%d/4" % e3_ok, "ok": e3_ok == 4})
    red.append({"id": "arch1_command_actions_7d", "value": arch1_actions,
                "ok": arch1_actions >= 3})
    red.append({"id": "merge_approval_coverage",
                "value": (round(len(approved) / len(dev_done), 4) if dev_done else None),
                "ok": len(missing) == 0})
    red.append({"id": "self_review_self_approve", "value": len(self_review),
                "ok": len(self_review) == 0})
    red.append({"id": "adr_or_decision_records", "value": len(list(adr_files)),
                "ok": len(list(adr_files)) > 0})
    red.append({"id": "l2_violation", "value": "manual", "ok": None})
    red.append({"id": "dev_budget", "value": "manual", "ok": None})
    return {
        "schema": "authority_audit/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "charter": {"version": (charter or {}).get("version"),
                    "approved_by": (charter or {}).get("approved_by"),
                    "approved_at": (charter or {}).get("approved_at"),
                    "level": (charter or {}).get("level"),
                    "approval_mode": approval.get("mode"),
                    "approver": approval.get("approver"),
                    "alternates": approval.get("alternates")},
        "dev": {"total": len(dev), "done": len(dev_done),
                "approved": len(approved), "missing": len(missing),
                "self_review": len(self_review)},
        "recent": {"total": len(recent), "legal": recent_legal},
        "arch1_actions_7d": arch1_actions,
        "e3": e3,
        "red_lines": red,
        "adr_count": len(list(adr_files)),
    }


def render_md(m):
    lines = ["# ARCH1 研发域最高权限 · 审计", "",
             "- as-of：%s" % m["generated_at"],
             "- charter：%s（批 %s/%s）level=%s approval=%s/%s"
             % (m["charter"]["version"], m["charter"]["approved_by"],
                m["charter"]["approved_at"], m["charter"]["level"],
                m["charter"]["approval_mode"], m["charter"]["approver"]), ""]
    lines += ["| 红线 | 当前值 | 状态 |", "|---|---:|---|"]
    for r in m["red_lines"]:
        st = "✅" if r["ok"] is True else ("❌" if r["ok"] is False else "🟡 人工")
        lines.append("| %s | %s | %s |" % (r["id"], r["value"], st))
    lines += ["", "## 合并署名", "",
              "- dev 完成单：%d；有署名：%d；缺署名：%d；自审自批：%d"
              % (m["dev"]["done"], m["dev"]["approved"], m["dev"]["missing"],
                 m["dev"]["self_review"]),
              "- ADR/决策记录：%d" % m["adr_count"]]
    return "\n".join(lines) + "\n"


def render_html(m):
    rows = []
    for r in m["red_lines"]:
        st = "✅" if r["ok"] is True else ("❌" if r["ok"] is False else "🟡 人工")
        rows.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>"
                    % (html.escape(str(r["id"])), html.escape(str(r["value"])), st))
    parts = [
        "<!DOCTYPE html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>研发域最高权限看板</title>",
        "<link rel='stylesheet' href='/static/tokens.css'>",
        "<script src='/static/nav.js' defer></script>",
        "<style>",
        "body{background:var(--bg,#0d1117);color:var(--fg,#e6edf3);"
        "font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:24px}",
        "table{border-collapse:collapse;width:100%;font-size:13px}",
        "th,td{border:1px solid var(--border,#30363d);padding:8px 12px;text-align:left}",
        "th{background:var(--panel,#161b22)}",
        ".muted{color:var(--muted,#8b949e);font-size:12px}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>研发域最高权限看板 · ARCH1（L1_dev_max）</h1>",
        "<p class='muted'>as-of %s · charter %s · approval=%s/%s · 只读生成物</p>"
        % (html.escape(m["generated_at"]), html.escape(str(m["charter"]["version"])),
           html.escape(str(m["charter"]["approval_mode"])),
           html.escape(str(m["charter"]["approver"]))),
        "<table>",
        "<tr><th>红线</th><th>当前值</th><th>状态</th></tr>",
        *rows,
        "</table>",
        "<p class='muted'>决策数（ADR）：%s；dev 完成单 %s / 有署名 %s / 缺署名 %s / "
        "自审自批 %s；ARCH1 指挥动作(7d) %s</p>"
        % (m["adr_count"], m["dev"]["done"], m["dev"]["approved"],
           m["dev"]["missing"], m["dev"]["self_review"], m["arch1_actions_7d"]),
        "</body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="ARCH1 研发域最高权限审计/看板（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--out", default=str(MD_OUT))
    ap.add_argument("--html-out", default=str(HTML_OUT))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        out = Path(args.html_out)
        if not out.is_absolute():
            out = ROOT / out
        ok = out.exists()
        text = out.read_text(encoding="utf-8") if ok else ""
        ok = ok and all(k in text for k in ("研发域最高权限", "<table",
                                           "tokens.css", "nav.js"))
        print(("PASS" if ok else "FAIL") + "：" + str(out))
        return 0 if ok else 1
    adr_files = [p for p in ADR_DIR.glob("ADR-*.md")
                 if "_TEMPLATE" not in p.name] if ADR_DIR.exists() else []
    m = compute(load_tasks(), load_events(), load_json(CHARTER, {}), adr_files)
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=1))
        return 0
    md = render_md(m)
    if args.md:
        print(md)
        return 0
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    html_out = Path(args.html_out)
    if not html_out.is_absolute():
        html_out = ROOT / html_out
    html_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.write_text(render_html(m), encoding="utf-8")
    print("authority_audit -> %s / %s" % (out, html_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
