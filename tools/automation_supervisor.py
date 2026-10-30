# -*- coding: utf-8 -*-
"""自动化监督器 dry-run（D2-D3，2026-10-30 WO-SUPERVISOR）。

只读检查 L1 回执 / L3 审计红线 / L4 环境健康，输出告警与建议动作；
**不执行重启/发布/删除/权限**，默认 dry-run。

用法：
  python -X utf8 tools/automation_supervisor.py --json
  python -X utf8 tools/automation_supervisor.py --md --out docs/dev/supervisor_dryrun.md
  python -X utf8 tools/automation_supervisor.py --check   # 有红/告警 rc1
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".worktrees" / "tasks.json"
DISPATCH = ROOT / "work" / "tianfeng_dispatch.jsonl"
CUTOFF = "2026-10-27T16:00"
HIGH_KW = ("发布", "上线", "权限", "预算", "回滚", "重启", "L2", "生产")


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _http(url, timeout=3):
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception as e:  # noqa: BLE001
        return "ERR:" + type(e).__name__


def _port_open(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _task_event_times(root):
    """从 dev_events.jsonl 取每单最近事件时间戳。"""
    out = {}
    path = Path(root) / "portal" / "data" / "dev_events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        task = str(e.get("task") or "")
        stamp = str(e.get("ts") or e.get("day") or "")
        try:
            ts = time.mktime(time.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:  # noqa: BLE001
            continue
        if task:
            out[task] = max(out.get(task, 0), ts)
    return out


def run_checks(root=None, now=None):
    root = Path(root or ROOT)
    now = now or time.time()
    tasks = _load(root / ".worktrees/tasks.json", {})
    last_ev = _task_event_times(root)
    checks = {"L1_回执": [], "L3_审计": [], "L4_环境": []}
    # L1：dev 在制超 24h 未更新（用 created 判断旧单）
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        if (t.get("dept") or "").lower() != "dev":
            continue
        if t.get("status") not in ("doing", "review"):
            continue
        created = str(t.get("created") or "")
        try:
            ts = time.mktime(time.strptime(created[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:  # noqa: BLE001
            continue
        ts = max(ts, last_ev.get(tid, 0))
        if now - ts > 86400:
            checks["L1_回执"].append({
                "id": tid, "owner": t.get("owner"), "status": t.get("status"),
                "age_h": round((now - ts) / 3600, 1),
                "suggest": "催办 owner/组长（不代做）"})
    # L3：block 后 dev done 缺署名 + 高风险关键词
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict) or (t.get("dept") or "").lower() != "dev":
            continue
        created = str(t.get("created") or "").replace(" ", "T")[:16]
        if t.get("status") == "done" and t.get("merged") and created >= CUTOFF:
            if not (t.get("approver") or "").strip():
                checks["L3_审计"].append({
                    "id": tid, "level": "red", "type": "missing_approver",
                    "suggest": "补署名/拒合"})
        note = str(t.get("note") or "")
        if t.get("status") in ("doing", "review") and any(k in note for k in HIGH_KW):
            checks["L3_审计"].append({
                "id": tid, "level": "amber", "type": "high_risk_keyword",
                "suggest": "S2 人工确认，禁止自动发布"})
    # L4：环境
    checks["L4_环境"] = [
        {"name": "portal_api_8091", "value": _http("http://127.0.0.1:8091/api/dev/legion"),
         "ok": _port_open(8091)},
        {"name": "nginx_8734", "value": _http("http://127.0.0.1:8734/api/dev/legion"),
         "ok": _port_open(8734)},
        {"name": "push_hub_8097", "value": _http("http://127.0.0.1:8097/api/public/ops"),
         "ok": _port_open(8097)},
    ]
    red = (len(checks["L3_审计"]) > 0
           or any((not x["ok"]) or x.get("value") != 200
                  for x in checks["L4_环境"]))
    return {"schema": "supervisor_dryrun/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dry_run": True,
            "checks": checks,
            "red": red,
            "actions_taken": []}


def render_md(r):
    lines = ["# 自动化监督 dry-run", "",
             "- as-of：%s｜**只读**｜未执行重启/发布/删除" % r["generated_at"], ""]
    for name, rows in r["checks"].items():
        lines.append("## %s" % name)
        if not rows:
            lines.append("- 无告警 ✅")
        for x in rows:
            lines.append("- `%s` %s" % (x.get("id") or x.get("name"),
                                         json.dumps({k: v for k, v in x.items()
                                                     if k not in ("id", "name")},
                                                    ensure_ascii=False)))
        lines.append("")
    lines.append("**判定**：%s" % ("RED（需人工/催办）" if r["red"] else "GREEN"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    r = run_checks()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    if args.md or args.out:
        txt = render_md(r)
        if args.out:
            p = Path(args.out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt, encoding="utf-8")
            print("written", args.out)
        else:
            print(txt)
    if args.check:
        print("SUPERVISOR", "RED" if r["red"] else "GREEN")
        return 1 if r["red"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
