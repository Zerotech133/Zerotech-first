# -*- coding: utf-8 -*-
"""O8 P3d：依赖 triage 周报段（只读、warn-only、不阻断）。

每周一部门交付周报前运行：调用 ``tools/dep_age_check.py`` 生成依赖年龄摘要，
输出 Markdown 段（可直接贴进周报），并落盘 ``agent_platform/data/dev/dep_triage/<week>.json``。

- 只读：不安装/不升级/不改 lock；
- **不阻断**：任何错误/超时都写进报告，退出码始终 0；
- 默认周 = 当前 ISO 周；周一报上周时显式 ``--week`` 上一周。

用法::

    python -X utf8 tools/weekly_dep_triage.py --week 2026-W44
    python -X utf8 tools/weekly_dep_triage.py --week 2026-W44 --limit 60
    python -X utf8 tools/weekly_dep_triage.py --week 2026-W44 --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
DEFAULT_OUT = os.path.join(REPO, "agent_platform", "data", "dev", "dep_triage")


def build_cmd(limit=0):
    cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "dep_age_check.py"), "--json"]
    if limit and limit > 0:
        cmd += ["--limit", str(limit)]
    return cmd


def summarize(payload, week, now=None):
    now = now or datetime.now(timezone.utc)
    fresh = list(payload.get("fresh") or [])
    errors = list(payload.get("errors") or [])
    return {
        "week": week,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "checked": payload.get("checked", 0),
        "fresh_count": payload.get("fresh_count", len(fresh)),
        "error_count": payload.get("error_count", len(errors)),
        "cutoff": payload.get("cutoff", ""),
        "fresh": fresh[:50],
        "errors_top": [{"name": x.get("name"), "version": x.get("version"),
                        "error": str(x.get("error") or "")[:100]} for x in errors[:10]],
        "warn_only": True,
    }


def render_markdown(triage):
    lines = ["## 依赖 triage（P3d · 只读 warn-only，不阻断）", ""]
    lines.append("> 周：**%s** ｜ checked=**%d** ｜ 7 天内新发布=**%d** ｜ 查询失败=**%d** ｜ 截止=%s" % (
        triage.get("week", ""), triage.get("checked", 0), triage.get("fresh_count", 0),
        triage.get("error_count", 0), triage.get("cutoff", "")))
    lines.append("")
    fresh = triage.get("fresh") or []
    if fresh:
        lines.append("| 包 | 版本 | 发布日期 | 年龄(天) |")
        lines.append("|---|---|---|---:|")
        for x in fresh[:20]:
            lines.append("| %s | %s | %s | %s |" % (
                x.get("name"), x.get("version"), x.get("released"), x.get("age_days")))
        if len(fresh) > 20:
            lines.append("| …共 %d 个 | | | |" % len(fresh))
    else:
        lines.append("- 本周**无** 7 天内新发布依赖 ✅")
    if triage.get("errors_top"):
        lines.append("")
        lines.append("查询失败（网络/超时，fail-open，不影响结论）：%d 个（示例：%s）" % (
            triage.get("error_count", 0),
            "、".join("%s %s" % (x.get("name"), x.get("version")) for x in triage["errors_top"][:3])))
    lines.append("")
    lines.append("**处理纪律**：只提示，不自动升级/不改 lock；安全修复例外走人工批准。")
    return "\n".join(lines)


def run_triage(week, limit=0, timeout=600, runner=None):
    try:
        run = runner or subprocess.run
        proc = run(build_cmd(limit), cwd=REPO, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=timeout)
        payload = json.loads((proc.stdout or "").strip())
        triage = summarize(payload, week)
        triage["ok"] = True
        return triage
    except Exception as exc:  # noqa: BLE001
        return {"week": week, "ok": False, "warn_only": True,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "checked": 0, "fresh_count": 0, "error_count": 0,
                "fresh": [], "errors_top": [], "cutoff": ""}


def save(triage, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.json" % triage.get("week", "unknown"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(triage, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="依赖 triage 周报段（只读、不阻断）")
    ap.add_argument("--week", default=None, help="ISO 周，如 2026-W44（默认当前周）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=0, help="只查前 N 个包（0=全部）")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)
    week = args.week or datetime.now().strftime("%G-W%V")
    triage = run_triage(week, args.limit, args.timeout)
    if not args.no_save:
        path = save(triage, args.out_dir)
        if not args.json:
            print("[dep_triage] saved %s（warn-only，不阻断）" % path)
    if args.json:
        print(json.dumps(triage, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(triage))
        if not triage.get("ok"):
            print("\n> dep_age 执行失败（%s）；triage 仍不阻断周报。" % triage.get("error"))
    return 0  # P3d 永远不阻断


if __name__ == "__main__":
    raise SystemExit(main())
