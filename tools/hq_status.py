#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零界指挥部 · 只读状态视图 v1。

聚合（全部只读、不写文件、不启停服务）：
- 指挥层：org.json chief/departments/agents + hr 档案数
- 军团账本：compositions/registry.json + 各 manifest 的 seats/skills/pages/jobs 计数
- 任务板：.worktrees/tasks.json 按状态计数 + 活跃单
- 指挥通道：legion/devops 页面是否存在 + 8096/8796 端口探活 + hq 模块数

用法：
  python -X utf8 tools/hq_status.py            # Markdown
  python -X utf8 tools/hq_status.py --json     # JSON（stdout 只输出 JSON）
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
TASKS = ROOT / ".worktrees" / "tasks.json"
REGISTRY = ROOT / "compositions" / "registry.json"
HQ_DIR = ROOT / "agent_platform" / "legion_command"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _manifest_counts(manifest_path: Path) -> dict:
    if yaml is None or not manifest_path.is_file():
        return {}
    try:
        d = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    spec = d.get("spec", {}) or {}
    return {
        "seats": len(spec.get("seats") or []),
        "skills": len(spec.get("skills") or []),
        "pages": len(spec.get("pages") or []),
        "jobs": len(spec.get("jobs") or []),
        "strategies": len(spec.get("strategies") or []),
        "interfaces": len(spec.get("interfaces") or []),
    }


def build_status() -> dict:
    org = _load_json(ORG, {})
    chief = org.get("chief", {}) or {}
    departments = org.get("departments", []) or []
    agents = []
    for d in departments:
        agents.extend(d.get("agents") or [])
    hr_count = len([p for p in HR.iterdir() if p.is_dir()]) if HR.is_dir() else 0

    tasks = _load_json(TASKS, {})
    task_counts: dict[str, int] = {}
    if isinstance(tasks, dict):
        for v in tasks.values():
            st = str((v or {}).get("status") or "?")
            task_counts[st] = task_counts.get(st, 0) + 1
    active_tasks = [
        {"id": k, "owner": (v or {}).get("owner"), "status": (v or {}).get("status"),
         "desc": str((v or {}).get("desc") or "")[:60]}
        for k, v in (tasks or {}).items()
        if isinstance(v, dict) and v.get("status") in ("doing", "review")
    ]

    reg = _load_json(REGISTRY, {})
    comps = []
    for c in (reg.get("compositions") or []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        row = {
            "id": c.get("id"),
            "name": c.get("name"),
            "tier": c.get("tier"),
            "owner": c.get("owner"),
            "version": c.get("version"),
            "lifecycle": c.get("lifecycle"),
            "manifest": c.get("manifest"),
        }
        mpath = ROOT / str(c.get("manifest") or "")
        row.update(_manifest_counts(mpath))
        comps.append(row)

    pages = {name: (ROOT / "portal" / f"{name}.html").is_file()
             for name in ("legion", "devops", "notify_command", "dev_legion_v2")}
    hq_modules = len(list(HQ_DIR.glob("*.py"))) if HQ_DIR.is_dir() else 0
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": "lingjie",
        "entry": "http://127.0.0.1:8734",
        "chief": {"agent_id": chief.get("agent_id", "A0"), "name": chief.get("name", "田丰")},
        "org": {"departments": len(departments), "agents": len(agents), "hr_profiles": hr_count},
        "task_board": {"total": len(tasks) if isinstance(tasks, dict) else 0,
                       "by_status": task_counts, "active": active_tasks[:20]},
        "compositions": comps,
        "channels": {"pages": pages, "ports": {"8096": _port_open(8096), "8796": _port_open(8796)},
                     "hq_modules": hq_modules},
    }


def render_markdown(st: dict) -> str:
    lines = [
        "# 零界指挥部 · 状态视图 v1",
        "",
        f"- 生成：{st.get('generated_at')}｜宿主：{st.get('host')}｜唯一入口：{st.get('entry')}",
        f"- 参谋长：{st['chief']['name']}（{st['chief']['agent_id']}）",
        f"- 组织：{st['org']['departments']} 部门 / {st['org']['agents']} 席位 / {st['org']['hr_profiles']} HR 档案",
        "",
        "## 军团账本",
        "",
        "| 军团 | tier | owner | version | lifecycle | seats | pages | jobs |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for c in st.get("compositions", []):
        lines.append("| {name} ({id}) | {tier} | {owner} | {version} | {lifecycle} | {seats} | {pages} | {jobs} |".format(
            name=c.get("name") or "?", id=c.get("id"), tier=c.get("tier") or "-", owner=c.get("owner") or "-",
            version=c.get("version") or "-", lifecycle=c.get("lifecycle") or "-",
            seats=c.get("seats", 0), pages=c.get("pages", 0), jobs=c.get("jobs", 0)))
    lines += ["", "## 任务板", ""]
    tb = st.get("task_board", {})
    lines.append(f"- 总 {tb.get('total', 0)}；按状态：" + ", ".join(f"{k}={v}" for k, v in sorted(tb.get("by_status", {}).items())))
    for t in tb.get("active", []):
        lines.append(f"- [{t.get('status')}] {t.get('id')} · {t.get('owner') or '-'} · {t.get('desc')}")
    lines += ["", "## 指挥通道", ""]
    ch = st.get("channels", {})
    for name, ok in (ch.get("pages") or {}).items():
        lines.append(f"- 页面 `/{name}`：{'存在' if ok else '缺失'}")
    for port, ok in (ch.get("ports") or {}).items():
        lines.append(f"- 端口 {port}：{'LISTEN' if ok else 'off'}")
    lines.append(f"- `agent_platform/legion_command` 模块：{ch.get('hq_modules')} 个")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="零界指挥部只读状态视图 v1")
    ap.add_argument("--json", action="store_true", help="输出 JSON（stdout 只输出 JSON）")
    args = ap.parse_args(argv)
    st = build_status()
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(st), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
