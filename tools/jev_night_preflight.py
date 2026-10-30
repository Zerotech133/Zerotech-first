# -*- coding: utf-8 -*-
"""JEV 夜跑前置检查（2026-10-30 PREFLIGHT，只读；--warm 可选预热 9B）。

每个夜跑前（建议 21:36）跑一次，避免手工漏项：
待判积压 / JevNight 任务状态 / M3 / 暂停旗 / ollama 锁 / 9B 模型 / 磁盘 / wrapper env / keep-awake。

用法：
    python -B tools/jev_night_preflight.py                 # 只读检查
    python -B tools/jev_night_preflight.py --warm          # 额外预热 9B（keep_alive=10h）
    python -B tools/jev_night_preflight.py --json out.json
退出码：0=全 PASS（允许 WARN）；1=有 FAIL。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEWS_DB = Path(r"C:\aios\news_collector\news.db")
PY = r"C:\Program Files\Python311\python.exe"
OLLAMA = "http://127.0.0.1:11434"
NIGHT_TASK = r"\ZeroBound\JevNight"
PAUSE_FLAG = Path(r"C:\Temp\k266_m3.pause")
KEEP_AWAKE = Path(r"C:\Temp\keep_awake.py")
NIGHT_LOG = Path(r"C:\Temp\jev_night.log")


def pending_count() -> int:
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=15)
    try:
        return int(conn.execute(
            "SELECT count(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0])
    finally:
        conn.close()


def night_window(now: datetime) -> tuple[bool, str]:
    """返回 (是否在夜跑窗口, 说明)。窗口 21:50-07:00；07:00-21:50 为等待期。"""
    t = now.hour * 60 + now.minute
    if t >= 21 * 60 + 50 or t <= 7 * 60:
        return True, "窗口内"
    target = now.replace(hour=21, minute=50, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    mins = int((target - now).total_seconds() // 60)
    return False, "距 21:50 开跑 %d 分钟" % mins


def task_state(name: str) -> dict:
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/fo", "csv", "/v"],
            capture_output=True, text=True, timeout=25,
            encoding="utf-8", errors="replace").stdout
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": "schtasks 失败: %s" % str(exc)[:80]}
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {"ok": False, "detail": "任务不存在: %s" % name}
    header = [h.strip('"') for h in lines[0].split('","')]
    vals = [v.strip('"') for v in lines[1].split('","')]
    row = dict(zip(header, vals))
    status = row.get("Status") or row.get("状态") or "?"
    nxt = row.get("Next Run Time") or row.get("下次运行时间") or "?"
    last = row.get("Last Result") or row.get("上次结果") or "?"
    return {"ok": status in ("Ready", "就绪", "Running", "正在运行"),
            "detail": "状态=%s 上次结果=%s 下次=%s" % (status, last, nxt)}


def m3_state() -> tuple[bool, str]:
    """暂停旗存在 = 视作 M3 已被夜跑暂停。"""
    if PAUSE_FLAG.exists():
        return True, "暂停旗存在（M3 暂停中）"
    return False, "无暂停旗"


def lock_state() -> tuple[bool, str]:
    p = REPO / "locks" / "ollama_batch.lock"
    if not p.exists():
        return True, "无锁（空闲）"
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        return True, "锁持有者=%s pid=%s" % (meta.get("purpose"), meta.get("pid"))
    except Exception:  # noqa: BLE001
        return False, "锁存在但不可解析"


def model_loaded() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=8) as r:
            tags = json.loads(r.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        hit = [n for n in names if "qwen3.5:9b" in n]
        return bool(hit), ("9B=%s" % hit[0]) if hit else ("未找到 9B（现有: %s）" % ",".join(names[:4]))
    except Exception as exc:  # noqa: BLE001
        return False, "Ollama 不可达: %s" % str(exc)[:80]


def warm_model() -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/generate",
            data=json.dumps({"model": "qwen3.5:9b", "prompt": "ok",
                             "stream": False, "keep_alive": "10h",
                             "options": {"num_predict": 1}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
        return True, "预热完成（keep_alive=10h）"
    except Exception as exc:  # noqa: BLE001
        return False, "预热失败: %s" % str(exc)[:100]


def disk_free_gb() -> float:
    return round(shutil.disk_usage(str(REPO)).free / (1024 ** 3), 1)


def wrappers_ok() -> tuple[bool, str]:
    files = [REPO / "jev-eval" / "start_jev_night.ps1",
             REPO / "jev-eval" / "jev_backfill_task.ps1"]
    for f in files:
        if not f.exists():
            return False, "缺 wrapper: %s" % f.name
        if "JEV_PREFILTER" not in f.read_text(encoding="utf-8", errors="replace"):
            return False, "%s 缺 JEV_PREFILTER" % f.name
    return True, "night/hourly wrapper env OK"


def collect(now: datetime | None = None) -> list[dict]:
    """收集所有检查项 → [{name, ok, detail}]。纯只读（--warm 除外）。"""
    now = now or datetime.now()
    checks: list[dict] = []

    in_win, win_detail = night_window(now)
    checks.append({"name": "夜跑窗口", "ok": True, "detail": win_detail})

    try:
        n = pending_count()
        checks.append({"name": "JEV 待判", "ok": True, "detail": "%d 条" % n})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "JEV 待判", "ok": False, "detail": "查询失败: %s" % str(exc)[:100]})

    checks.append({"name": "JevNight 任务", **task_state(NIGHT_TASK)})
    checks.append({"name": "M3/暂停旗", "ok": True, "detail": m3_state()[1]})
    checks.append({"name": "ollama 锁", "ok": lock_state()[0], "detail": lock_state()[1]})
    checks.append({"name": "9B 模型", "ok": model_loaded()[0], "detail": model_loaded()[1]})
    try:
        free = disk_free_gb()
        checks.append({"name": "C 盘空间", "ok": free > 5.0, "detail": "%.1fGB 可用" % free})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "C 盘空间", "ok": False, "detail": str(exc)[:80]})

    checks.append({"name": "wrapper env", "ok": wrappers_ok()[0], "detail": wrappers_ok()[1]})
    checks.append({"name": "keep-awake", "ok": KEEP_AWAKE.exists(),
                   "detail": "存在" if KEEP_AWAKE.exists() else "缺失 C:\\Temp\\keep_awake.py"})
    last = "-"
    if NIGHT_LOG.exists():
        last = datetime.fromtimestamp(NIGHT_LOG.stat().st_mtime).strftime("%m-%d %H:%M")
    checks.append({"name": "夜跑日志", "ok": True, "detail": "最近写入 %s" % last})
    return checks


def summarize(checks: list[dict]) -> dict:
    fails = [c["name"] for c in checks if not c["ok"]]
    return {"total": len(checks), "fail": len(fails), "fails": fails,
            "overall": "FAIL" if fails else "PASS"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warm", action="store_true", help="预热 9B（keep_alive=10h）")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    checks = collect()
    if a.warm:
        ok, detail = warm_model()
        checks.append({"name": "9B 预热", "ok": ok, "detail": detail})
    summary = summarize(checks)
    out = {"generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "summary": summary, "checks": checks}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    for c in checks:
        print("[%s] %-12s %s" % ("PASS" if c["ok"] else "FAIL", c["name"], c["detail"]),
              file=sys.stderr)
    return 0 if not summary["fails"] else 1