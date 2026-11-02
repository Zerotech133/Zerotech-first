# -*- coding: utf-8 -*-
"""席位待办工人：领取可自动分析的待办，写回结论；人工事项保持 pending。

不自动复核、不自动通过/否决、不代填反馈/督导。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

OUT = Path(r"C:\Temp\agent_worker.json")
BASE = os.environ.get("ZHICE_API_BASE", "http://127.0.0.1:8105/api/v1")
TOKEN = os.environ.get("ZHICE_HANDOFF_TOKEN", "zhice-local-handoff")
HUMAN_ONLY = {"feedback_missing", "supervision_missing"}


def http(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Handoff-Token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def job_status():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8105/api/v1/health", timeout=4) as r:
            health = r.status
    except Exception:  # noqa: BLE001
        health = None
    p = Path(r"C:\Temp\zhice_job_status.json")
    job = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return health, job


def answer_text(task_key: str, ctx: dict) -> str:
    health, job = job_status()
    if task_key == "pipeline_not_success":
        failed = [s.get("name") for s in job.get("steps", []) if s.get("status") == "failed"]
        return f"每日任务状态={job.get('status')}，rc={job.get('rc')}；失败步骤={failed or '无'}；数据日={job.get('data_date')}。建议按失败步骤重跑，不改结论。"
    if task_key == "report_stale":
        return f"报告滞后：{ctx.get('detail')}；当前任务 data_date={job.get('data_date')}。需补对应市场行情后重跑，不伪造日期。"
    if task_key == "service_down":
        return f"8105 health={health}；服务检查详情={ctx.get('detail')}。需 PROD1 检查端口/进程/日志。"
    if task_key.startswith("strategy_owner_stale_"):
        return f"策略输出滞后：{ctx.get('detail')}；当前 data_date={job.get('data_date')}。需 owner 检查数据日与报告生成。"
    if task_key == "back1_pipeline":
        return f"任务失败：{ctx.get('detail')}；需 BACK1 做回测/风控复核。"
    return f"自动巡检事项：{ctx.get('title')}；详情={ctx.get('detail')}。"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", default=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    pending = http("GET", f"/agent-handoff/pending?limit={args.limit}").get("items", [])
    results = []
    for item in pending:
        ctx = item.get("context") or {}
        key = ctx.get("task_key") or ""
        if not key:
            results.append({"id": item["id"], "action": "skip", "reason": "no task_key"})
            continue
        if key in HUMAN_ONLY:
            results.append({"id": item["id"], "action": "wait_human", "task_key": key,
                            "reason": "人工事项，不自动代填/代签"})
            continue
        if args.dry_run:
            results.append({"id": item["id"], "action": "would_answer", "task_key": key})
            continue
        try:
            http("POST", f"/agent-handoff/{item['id']}/claim", {})
            ans = answer_text(key, ctx)
            http("POST", f"/agent-handoff/{item['id']}/answer", {"answer": ans, "status": "answered"})
            results.append({"id": item["id"], "action": "answered", "task_key": key, "answer": ans})
        except Exception as exc:  # noqa: BLE001
            results.append({"id": item["id"], "action": "error", "task_key": key, "error": str(exc)[:200]})
    out = {"schema": "zhice.agent_worker/v1", "run_at": now, "pending": len(pending),
           "answered": sum(1 for x in results if x["action"] == "answered"),
           "wait_human": sum(1 for x in results if x["action"] == "wait_human"),
           "results": results}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("run_at", "pending", "answered", "wait_human")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
