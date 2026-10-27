# -*- coding: utf-8 -*-
"""durable-workflow 最小版：持久步骤状态机（断点续跑/超时/重试）。

用法：
  python -X utf8 tools/durable_workflow.py start <id> --steps steps.json
  python -X utf8 tools/durable_workflow.py run <id> [--continue-on-error] [--json]
  python -X utf8 tools/durable_workflow.py status <id> [--json]
  python -X utf8 tools/durable_workflow.py list [--json]
状态：work/workflows/<id>.json；每步执行前后落盘，进程中断可重跑 run 续跑。
steps.json: [{"id":"s1","cmd":["python","-c","print(1)"],"timeout":60,"retries":1}, ...]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STORE = REPO / "work" / "workflows"


def _dir(store=None) -> Path:
    p = Path(store) if store else STORE
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file(wid: str, store=None) -> Path:
    return _dir(store) / ("%s.json" % wid)


def _load(wid: str, store=None) -> dict:
    p = _file(wid, store)
    if not p.exists():
        raise ValueError("workflow not found: %s" % wid)
    return json.loads(p.read_text(encoding="utf-8"))


def _save(wf: dict, store=None) -> None:
    p = _file(wf["id"], store)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def start(wid: str, steps: list, store=None) -> dict:
    if not wid or not steps:
        raise ValueError("id/steps 必填")
    wf = {"schema": "durable_workflow/v1", "id": str(wid),
          "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "steps": []}
    for i, s in enumerate(steps):
        cmd = s.get("cmd")
        if not isinstance(cmd, list) or not cmd:
            raise ValueError("step %d 缺少 cmd 数组" % i)
        wf["steps"].append({"id": str(s.get("id") or ("step-%d" % (i + 1))),
                            "cmd": [str(x) for x in cmd],
                            "timeout": int(s.get("timeout") or 600),
                            "retries": max(0, int(s.get("retries") or 0)),
                            "status": "pending", "attempts": 0,
                            "rc": None, "error": None,
                            "started_at": None, "finished_at": None,
                            "stdout_tail": "", "stderr_tail": ""})
    _save(wf, store)
    return wf


def _run_step(wf: dict, idx: int, store=None) -> bool:
    st = wf["steps"][idx]
    while st["attempts"] <= st["retries"]:
        st["attempts"] += 1
        st["status"] = "running"
        st["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        wf["updated_at"] = st["started_at"]
        _save(wf, store)
        try:
            cp = subprocess.run(st["cmd"], cwd=str(REPO), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=st["timeout"])
            st["rc"] = cp.returncode
            st["stdout_tail"] = cp.stdout[-2000:]
            st["stderr_tail"] = cp.stderr[-2000:]
            if cp.returncode == 0:
                st["status"] = "done"
                st["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                _save(wf, store)
                return True
            st["error"] = "rc=%s" % cp.returncode
        except subprocess.TimeoutExpired:
            st["rc"] = -9
            st["error"] = "timeout %ss" % st["timeout"]
        except Exception as e:  # noqa: BLE001
            st["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        st["status"] = "failed"
        st["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _save(wf, store)
    return False


def run(wid: str, continue_on_error: bool = False, max_steps: int = 0,
        store=None) -> dict:
    wf = _load(wid, store)
    n = 0
    for i, st in enumerate(wf["steps"]):
        if st.get("status") in ("done", "skipped"):
            continue
        if max_steps and n >= max_steps:
            break
        ok = _run_step(wf, i, store)
        n += 1
        if not ok and not continue_on_error:
            break
    wf["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save(wf, store)
    done = sum(1 for s in wf["steps"] if s.get("status") == "done")
    return {"ok": done == len(wf["steps"]), "id": wid, "done": done,
            "total": len(wf["steps"]), "steps": wf["steps"]}


def status(wid: str, store=None) -> dict:
    return _load(wid, store)


def list_wf(store=None) -> list:
    out = []
    for p in sorted(_dir(store).glob("*.json")):
        try:
            wf = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": wf.get("id"), "done": sum(1 for s in wf.get("steps", [])
                                                        if s.get("status") == "done"),
                        "total": len(wf.get("steps", [])),
                        "updated_at": wf.get("updated_at")})
        except Exception:
            continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 durable-workflow（持久步骤状态机）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("id")
    s.add_argument("--steps", required=True)
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("id")
    r.add_argument("--continue-on-error", action="store_true")
    r.add_argument("--max-steps", type=int, default=0)
    r.add_argument("--json", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("id")
    st.add_argument("--json", action="store_true")
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "start":
            steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
            wf = start(args.id, steps)
            print(json.dumps(wf, ensure_ascii=False, indent=1) if args.json else
                  "started %s steps=%d" % (wf["id"], len(wf["steps"])))
        elif args.cmd == "run":
            res = run(args.id, args.continue_on_error, args.max_steps)
            print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
                  "done %d/%d ok=%s" % (res["done"], res["total"], res["ok"]))
            return 0 if res["ok"] else 1
        elif args.cmd == "status":
            wf = status(args.id)
            if args.json:
                print(json.dumps(wf, ensure_ascii=False, indent=1))
            else:
                print("%s done=%d/%d" % (wf["id"],
                                         sum(1 for s in wf["steps"] if s["status"] == "done"),
                                         len(wf["steps"])))
                for s in wf["steps"]:
                    print("  %-12s %-8s attempts=%s %s" % (s["id"], s["status"],
                                                           s["attempts"], s.get("error") or ""))
        else:
            rows = list_wf()
            print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else
                  "\n".join("%s %d/%d" % (r["id"], r["done"], r["total"]) for r in rows))
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
