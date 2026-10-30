# -*- coding: utf-8 -*-
r"""赵云治理日报 + deliverables 索引（只读采集，报告落盘）。

用法：
  python tools/zhaoyun_daily.py                 # 生成今日日报 + INDEX.md
  python tools/zhaoyun_daily.py --no-index      # 只生成日报
  python tools/zhaoyun_daily.py --out C:\Temp\zy_daily.md --index C:\Temp\INDEX.md
  python tools/zhaoyun_daily.py --json          # stdout 输出采集 JSON（不写文件）

原则：只读采集；不修改任务板/registry；失败项写“未知”，不猜测。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# safe_path=true 时脚本目录不入 sys.path：本树路径自举（便于 import 同级 tools 模块）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPO = _HERE.parent
DELIVERABLES = REPO / "deliverables"
TASKS = REPO / ".worktrees" / "tasks.json"
DEADLINES = _HERE / "zhaoyun_deadlines.json"
DOSSIER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_.+_v\d+\.(md|json|html|png)$")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _run(cmd, timeout=30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _http_ok(url: str, timeout=3) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def scan_deliverables(root: Path) -> list[dict]:
    rows = []
    for p in sorted(root.glob("*")):
        if not p.is_file() or p.name == "INDEX.md":
            continue
        title = ""
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except OSError:
            continue
        m = DOSSIER_RE.match(p.name)
        rows.append({
            "name": p.name,
            "title": title[:80],
            "date": m.group(1) if m else "",
            "size": p.stat().st_size,
            "naming_ok": bool(m),
            "mtime": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return rows


def build_index(rows: list[dict], generated_at: str) -> str:
    dated = [r for r in rows if r["date"]]
    bad = [r for r in rows if not r["naming_ok"]]
    lines = [
        "# deliverables 索引（自动生成，勿手工编辑）",
        "",
        "> 命名规范：`YYYY-MM-DD_主题_vN.md`（扩展名可为 md/json/html/png）；"
        "由 `tools/zhaoyun_daily.py` 生成。生成时间：%s" % generated_at,
        "",
        "## 按日期（倒序）",
        "",
        "| 日期 | 文件 | 标题 | 修改时间 |",
        "|---|---|---|---|",
    ]
    for r in sorted(dated, key=lambda x: (x["date"], x["name"]), reverse=True):
        lines.append("| %s | %s | %s | %s |" % (r["date"], r["name"], r["title"] or "-", r["mtime"]))
    if bad:
        lines += ["", "## 命名待治理（%d）" % len(bad), ""]
        for r in sorted(bad, key=lambda x: x["name"]):
            lines.append("- `%s`" % r["name"])
    lines.append("")
    return "\n".join(lines)


def deadline_status(items: list[dict], now: dt.datetime) -> list[dict]:
    out = []
    for it in items:
        due = it.get("due")
        rec = dict(it)
        if it.get("recurring"):
            rec.update({"state": "recurring", "days_left": None})
        elif due:
            try:
                d = dt.datetime.fromisoformat(due)
                now_cmp = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
                if d.tzinfo is not None:
                    d = d.astimezone().replace(tzinfo=None)
                rec["days_left"] = (d - now_cmp).days
                if d < now_cmp:
                    rec["state"] = "overdue"
                elif (d - now).days <= 1:
                    rec["state"] = "due_soon"
                else:
                    rec["state"] = "upcoming"
            except ValueError:
                rec.update({"state": "bad_due", "days_left": None})
        else:
            rec.update({"state": "unknown", "days_left": None})
        out.append(rec)
    return out


def collect() -> dict:
    now = dt.datetime.now()
    data = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "date": now.strftime("%Y-%m-%d")}

    board = None
    try:
        import task_board_audit as tba
        board = tba.audit()
    except Exception:
        board = {"ok": False, "error": "task_board_audit 不可用"}
    data["board"] = board

    tasks = _read_json(TASKS) or {}
    status_counts = {}
    for v in tasks.values():
        s = v.get("status") or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1
    data["tasks"] = {"total": len(tasks), "by_status": status_counts}

    wt = _run(["git", "-C", str(REPO), "worktree", "list", "--porcelain"])
    data["worktrees"] = sum(1 for ln in wt.splitlines() if ln.startswith("worktree "))

    data["deliverables"] = scan_deliverables(DELIVERABLES)
    data["today_deliverables"] = sum(1 for r in data["deliverables"] if r["date"] == data["date"])

    data["health"] = {
        "8796": _http_ok("http://127.0.0.1:8796/api/health"),
        "8091": _http_ok("http://127.0.0.1:8091/health"),
        "6006": _http_ok("http://127.0.0.1:6006/health"),
    }

    status = _read_json(Path(r"C:\Temp\security_audit_status.json")) or {}
    bandit = None
    if status:
        bandit = {"HIGH": int(status.get("bandit_high") or 0),
                  "MEDIUM": int(status.get("bandit_medium") or 0),
                  "LOW": int(status.get("bandit_low") or 0)}
    else:
        bpath = Path(r"C:\Temp\bandit_latest.json")
        if bpath.exists():
            b = _read_json(bpath) or {}
            sev = {}
            for x in b.get("results") or []:
                k = x.get("issue_severity") or "?"
                sev[k] = sev.get(k, 0) + 1
            bandit = sev
    data["bandit"] = bandit
    osv_paths = sorted(glob.glob(r"C:\Temp\pip_osv*.json"), key=lambda p: Path(p).stat().st_mtime, reverse=True)
    osv = _read_json(Path(osv_paths[0])) if osv_paths else None
    data["osv"] = {
        "packages": osv.get("vulnerable_packages"),
        "vulns": osv.get("vulnerabilities"),
        "scanned": osv.get("scanned"),
    } if osv else None

    gate_logs = sorted(glob.glob(r"C:\Temp\test_gate_*_*.log") + glob.glob(r"C:\Temp\audit_gate_*.log") + glob.glob(r"C:\Temp\gate_after_*.log"),
                       key=lambda p: Path(p).stat().st_mtime, reverse=True)
    gate = "未知"
    for lp in gate_logs:
        try:
            txt = Path(lp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "GATE PASS" in txt:
            gate = "PASS"
            break
        if "GATE FAIL" in txt:
            gate = "FAIL"
            break
    data["gate"] = gate

    data["deadlines"] = deadline_status((_read_json(DEADLINES) or {}).get("items") or [], now)
    return data


def render_report(d: dict) -> str:
    b = d.get("board") or {}
    counts = {k: (len(v) if isinstance(v, list) else v) for k, v in b.items() if k != "ok"}
    health = d.get("health") or {}
    sev = d.get("bandit") or {}
    osv = d.get("osv") or {}
    lines = [
        "# 赵云治理日报 · %s" % d.get("date"),
        "",
        "> 自动生成（`tools/zhaoyun_daily.py`）；只读采集；采集时间 %s" % d.get("generated_at"),
        "",
        "## 一、任务板",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 开单总数 | %s |" % (d.get("tasks") or {}).get("total"),
        "| 状态分布 | %s |" % json.dumps((d.get("tasks") or {}).get("by_status") or {}, ensure_ascii=False),
    ]
    for k in ("merged_but_open", "branch_missing", "ahead_unmerged", "worktree_missing", "worktree_dirty"):
        if k in counts:
            lines.append("| %s | %s |" % (k, counts[k]))
    lines += [
        "",
        "## 二、运行健康",
        "",
        "| 服务 | 状态 |",
        "|---|---|",
    ]
    for k, v in health.items():
        lines.append("| %s | %s |" % (k, "200" if v else "异常/离线"))
    lines += ["| 质量门（最近日志） | %s |" % d.get("gate"), ""]

    lines += ["## 三、安全扫描", ""]
    if sev:
        lines.append("- bandit：%s" % json.dumps(sev, ensure_ascii=False))
    else:
        lines.append("- bandit：无最新报告")
    if osv:
        lines.append("- OSV：扫描 %s 包，漏洞包 %s，漏洞 %s 条"
                     % (osv.get("scanned"), osv.get("packages"), osv.get("vulns")))
    else:
        lines.append("- OSV：无最新报告")

    lines += ["", "## 四、交付物", "",
              "- 今日交付物：%s 份；deliverables 总数：%s"
              % (d.get("today_deliverables"), len(d.get("deliverables") or [])),
              "- 命名待治理：%s 份" % sum(1 for r in d.get("deliverables") or [] if not r["naming_ok"]),
              ""]

    lines += ["## 五、截止事项", "", "| 事项 | 截止 | 状态 | 剩余天数 | 责任 |", "|---|---|---|---|---|"]
    for it in d.get("deadlines") or []:
        lines.append("| %s | %s | %s | %s | %s |" % (
            it.get("title") or it.get("id"), it.get("due") or it.get("recurring") or "-",
            it.get("state"), it.get("days_left") if it.get("days_left") is not None else "-",
            it.get("owner") or "-"))
    lines += ["", "## 六、仓库卫生", "",
              "- worktree 数：%s" % d.get("worktrees"), ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云治理日报 + deliverables 索引")
    ap.add_argument("--out", default="", help="日报输出路径（默认 deliverables/<date>_赵云治理日报.md）")
    ap.add_argument("--index", default=str(DELIVERABLES / "INDEX.md"), help="INDEX 输出路径")
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--json", action="store_true", help="只输出采集 JSON")
    args = ap.parse_args(argv)

    data = collect()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    out = Path(args.out) if args.out else (DELIVERABLES / ("%s_赵云治理日报.md" % data["date"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(data), encoding="utf-8")
    print("report:", out)

    if not args.no_index:
        idx = Path(args.index)
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(build_index(data["deliverables"], data["generated_at"]), encoding="utf-8")
        print("index:", idx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
