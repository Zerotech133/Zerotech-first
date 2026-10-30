# -*- coding: utf-8 -*-
"""军团排程调度器（K-ORG-122）：事先调度的机器化

schedules.json 结构（dispatch API 写入）：
  [{"id": "sch-001", "agent": "T1", "desc": "盘前 weak 池三线扫描", "cron": "daily 09:30",
    "weekdays_only": true, "last_run": "2026-10-17", "enabled": true}]

本脚本由 LegionScheduler 任务每分钟调起：扫表→到点→dispatch（复用 api_legion_ops_v2.dispatch 逻辑）
"""
import json
import time
import urllib.request
from pathlib import Path

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

ROOT = Path(__file__).resolve().parent.parent
SCHED = ROOT / "agent_platform" / "data" / "schedules.json"


def load() -> list:
    try:
        return json.loads(SCHED.read_text(encoding="utf-8"))
    except FileNotFoundError:
        SCHED.parent.mkdir(parents=True, exist_ok=True)
        SCHED.write_text("[]", encoding="utf-8")
        return []


def save(rows: list) -> None:
    SCHED.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def due_now(item: dict) -> bool:
    """cron 格式 v1：'daily HH:MM' / 'weekly MON HH:MM' / 'once YYYY-MM-DD HH:MM'"""
    if not item.get("enabled", True):
        return False
    now = time.localtime()
    today = time.strftime("%Y-%m-%d")
    if item.get("last_run") == today and "once" not in item.get("cron", ""):
        return False  # 当日已跑（daily 类）
    cron = item.get("cron", "")
    if cron.startswith("daily"):
        hm = cron.split()[-1]
        ok_time = time.strftime("%H:%M") >= hm
        wd_ok = (not item.get("weekdays_only")) or now.tm_wday < 5
        return ok_time and wd_ok
    if cron.startswith("weekly"):
        parts = cron.split()
        wd_map = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
        if len(parts) >= 3 and wd_map.get(parts[1]) == now.tm_wday:
            return time.strftime("%H:%M") >= parts[2]
        return False
    if cron.startswith("once"):
        if item.get("last_run"):
            return False
        return f"{today} {time.strftime('%H:%M')}" >= cron[5:]
    return False


def dispatch_one(item: dict) -> dict:
    body = json.dumps({"agent": item["agent"], "desc": item["desc"],
                       "dept": item.get("dept", ""), "stage": item.get("stage", ""),
                       "worker": bool(item.get("worker", False)),
                       "acceptance": item.get("acceptance", [])}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8096/api/legion/dispatch", data=body,
                                 headers={"Content-Type": "application/json"})
    with safe_urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run() -> list:
    rows = load()
    fired = []
    for it in rows:
        if due_now(it):
            try:
                res = dispatch_one(it)
                it["last_run"] = time.strftime("%Y-%m-%d")
                it["last_result"] = "ok" if res.get("ok") else str(res.get("err", ""))[:60]
                fired.append({"id": it.get("id"), "agent": it["agent"], "ok": res.get("ok")})
            except Exception as e:
                it["last_result"] = f"fail:{type(e).__name__}"
    if fired:
        save(rows)
    return fired


if __name__ == "__main__":
    out = run()
    if out:
        print(json.dumps(out, ensure_ascii=False))
