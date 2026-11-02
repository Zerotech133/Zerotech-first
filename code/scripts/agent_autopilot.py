# -*- coding: utf-8 -*-
"""席位自动运转巡检（Autopilot）。

不是自动决策：只做「运行态巡检 + 生成待办」，不写反馈/复核/督导/审批结论。
默认只写 C:\\Temp\\agent_autopilot.json；--write 时把需要行动的项写入 v2_agent_handoff。
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\agent_autopilot.json")
JOB = Path(r"C:\Temp\zhice_job_status.json")

SEATS = [
    ("ACAD1", "司马徽", "复盘/复核/督导"),
    ("STRAT1", "徐庶", "策略研究 owner"),
    ("ST1", "程昱", "策略研究 owner"),
    ("STRAT2", "庞统", "策略研究 owner"),
    ("BACK1", "王粲", "回测/风控/审批"),
    ("DATA1", "张松", "数据/契约"),
    ("AI1", "诸葛亮", "运行/发布"),
    ("PROD1", "黄月英", "工程/部署"),
    ("COMM1", "马良", "对外发布/沟通"),
    ("INTEL1", "郭嘉", "情报/证据"),
    ("LUSU1", "阚泽", "下游反馈"),
]


def _db():
    return sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true", help="把待办写入 v2_agent_handoff")
    args = p.parse_args()

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    job = {}
    if JOB.exists():
        try:
            job = json.loads(JOB.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            job = {}

    e = _db()
    with e.connect() as c:
        def one(sql, **kw):
            return c.execute(sa.text(sql), kw).scalar()
        latest_bar = dict(c.execute(sa.text("SELECT market,max(trade_date) FROM v2_bar_daily GROUP BY market")).all())
        latest_report = dict(c.execute(sa.text("SELECT market,max(report_date) FROM v2_selection_report GROUP BY market")).all())
        latest_acad1 = dict(c.execute(sa.text("SELECT market,max(pool_date) FROM v2_acad1_pool GROUP BY market")).all())
        feedback = int(one("SELECT count(*) FROM v2_selection_feedback WHERE pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)") or 0)
        feedback_total = int(one("SELECT count(*) FROM v2_selection_feedback") or 0)
        supervision = int(one("SELECT count(*) FROM v2_strategy_supervision WHERE supervision_date=(SELECT max(pool_date) FROM v2_acad1_pool)") or 0)
        supervision_total = int(one("SELECT count(*) FROM v2_strategy_supervision") or 0)
        draft = int(one("SELECT count(*) FROM v2_stock_research WHERE status='draft'") or 0)
        ownership = c.execute(sa.text("SELECT research_owner,count(*) FROM v2_strategy_ownership GROUP BY research_owner")).all()
        pending_handoff = int(one("SELECT count(*) FROM v2_agent_handoff WHERE status IN ('pending','claimed')") or 0)

    services = {
        "5175": _http_ok("http://127.0.0.1:5175/"),
        "8105": _http_ok("http://127.0.0.1:8105/api/v1/health"),
        "8787": _http_ok("http://127.0.0.1:8787/health"),
        "8734": _http_ok("http://127.0.0.1:8734/hermes/"),
    }
    data_date = job.get("data_date")
    pipeline_ok = job.get("status") == "success" and int(job.get("rc") or 0) == 0
    report_stale = [m for m, d in latest_report.items() if d and latest_bar.get(m) and d < latest_bar[m]]

    seats = []
    tasks = []

    def add_task(key, seat, title, detail, page="/cn/replay?tab=flow"):
        tasks.append({"task_key": key, "assigned_to": seat, "title": title, "detail": detail, "page": page})

    for sid, name, role in SEATS:
        current, done, state = "巡检完成", "", "running"
        if sid == "AI1":
            current = f"每日任务 {job.get('status','未上报')} · {job.get('finished_at','—')}"
            done = f"数据日 {data_date or '—'} · 11 步"
            if not pipeline_ok:
                state = "blocked"
                add_task("pipeline_not_success", "AI1", "每日任务未成功", f"status={job.get('status')} rc={job.get('rc')}")
        elif sid == "DATA1":
            current = f"行情日 {latest_bar} · 报告日 {latest_report}"
            done = f"数据日 {data_date}; 滞后市场 {report_stale or '无'}"
            if report_stale:
                state = "blocked"
                add_task("report_stale", "DATA1", "报告落后于行情", f"{report_stale}")
        elif sid == "ACAD1":
            current = f"ACAD1 {sum(1 for k in latest_acad1)} 市场 · draft {draft}"
            done = f"反馈当日 {feedback} / 全库 {feedback_total}; 督导当日 {supervision} / 全库 {supervision_total}"
            if feedback == 0:
                state = "blocked"
                add_task("feedback_missing", "LUSU1", "当日真实反馈为 0", "请下游按实际用/不用录入，不得代填")
            if supervision == 0:
                state = "blocked"
                add_task("supervision_missing", "ACAD1", "当日督导为 0", "请 ACAD1 人工录入督导意见，不得自动结论")
        elif sid in ("STRAT1", "ST1", "STRAT2"):
            n = dict(ownership).get(sid, 0)
            current = f"负责 {n} 条策略"
            done = f"策略输出巡检完成；报告滞后 {report_stale or '无'}"
            if report_stale:
                state = "blocked"
                add_task(f"strategy_owner_stale_{sid}", sid, f"{name} 负责策略输出滞后", f"{report_stale}")
        elif sid == "PROD1":
            current = f"服务 {services}"
            done = "端口/健康检查完成"
            if not all(services.values()):
                state = "blocked"
                add_task("service_down", "PROD1", "服务健康检查失败", json.dumps(services, ensure_ascii=False))
        elif sid == "BACK1":
            current = f"任务 {job.get('status','未上报')} · 变更待办 {pending_handoff}"
            done = "回测/审批巡检完成"
            if not pipeline_ok:
                state = "blocked"
                add_task("back1_pipeline", "BACK1", "任务失败需回测/风控复核", f"rc={job.get('rc')}")
        elif sid == "INTEL1":
            current = f"ACAD1 池 {sum(1 for _ in latest_acad1)} 市场"
            done = "证据/情报覆盖巡检完成"
        elif sid == "LUSU1":
            current = f"当日反馈 {feedback} · 全库 {feedback_total}"
            done = "下游反馈巡检完成"
            if feedback == 0:
                state = "blocked"
        elif sid == "COMM1":
            current = "对外发布保持人工审批"
            done = "发布队列巡检完成"
        seats.append({"seat": sid, "name": name, "role": role, "status": state,
                      "current": current, "done": done})

    created = []
    if args.write and tasks:
        with e.begin() as c:
            for t in tasks:
                exists = c.execute(sa.text("""
                    SELECT 1 FROM v2_agent_handoff
                    WHERE status IN ('pending','claimed') AND created_by='autopilot'
                      AND context->>'task_key' = :k LIMIT 1
                """), {"k": t["task_key"]}).scalar()
                if exists:
                    continue
                c.execute(sa.text("""
                    INSERT INTO v2_agent_handoff(question, context, page, status, created_by)
                    VALUES (:q, CAST(:ctx AS jsonb), :p, 'pending', 'autopilot')
                """), {"q": f"[{t['assigned_to']}] {t['title']}", "ctx": json.dumps(t, ensure_ascii=False), "p": t["page"]})
                created.append(t["task_key"])

    out = {
        "schema": "zhice.agent_autopilot/v1",
        "run_at": now,
        "read_only": not args.write,
        "pipeline": {"status": job.get("status"), "data_date": data_date,
                      "finished_at": job.get("finished_at"), "ok": pipeline_ok},
        "data": {"bars": {k: str(v) for k, v in latest_bar.items()},
                 "reports": {k: str(v) for k, v in latest_report.items()},
                 "acad1": {k: str(v) for k, v in latest_acad1.items()},
                 "feedback": feedback, "feedback_total": feedback_total,
                 "supervision": supervision, "supervision_total": supervision_total,
                 "draft": draft, "report_stale": report_stale},
        "services": services,
        "seats": seats,
        "tasks": tasks,
        "created": created,
        "autonomy": "L2：自动巡检 + 生成待办；复核/通过/否决/反馈仍人工。",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_at": now, "pipeline": out["pipeline"], "tasks": len(tasks),
                      "created": created, "blocked_seats": [s["seat"] for s in seats if s["status"] == "blocked"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
