# -*- coding: utf-8 -*-
"""tools/tests/test_daily_tasks_health.py — 每日任务健康监控 + 告警（task-108）。

覆盖（任务验收 ≥4，全部 mock / tmp，不联网、不真跑计划任务）：
  1. schtasks 文本解析：英文/中文标签 → Last Run Time/Last Result；
  2. 日志解析：最新 rc 行 / EXCEPTION 行 → timestamp/rc/line；
  3. 产出新鲜度：缺失/过期/新鲜判定（阈值 stale_hours）；
  4. 检查与告警：失败任务 → alert、告警文本含任务名/Last Result/日志行；报告 schema；
  5. main dry-run：写 JSON/MD、不发送；--send：有告警时调用发送（mock）并记录结果。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_daily_tasks_health.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))


def _load():
    p = ROOT / "tools" / "research" / "daily_tasks_health.py"
    spec = importlib.util.spec_from_file_location("daily_tasks_health_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ------------------------------------------------------------------ 1. schtasks 解析
def test_parse_schtasks_listing_en_and_cn():
    en = ("TaskName:                                 \\ZeroBound_DailyKB\n"
          "Status:                                   Ready\n"
          "Last Run Time:                            2026/11/1 23:41:19\n"
          "Last Result:                              0\n")
    info = mod.parse_schtasks_listing(en)
    assert info["found"] is True
    assert info["last_result"] == 0 and "23:41" in info["last_run_time"]
    cn = ("任务名:                                  \\ZeroBound_DailyKB\n"
          "状态:                                    就绪\n"
          "上次运行时间:                            2026/11/1 23:41:19\n"
          "上次结果:                                1\n")
    info_cn = mod.parse_schtasks_listing(cn)
    assert info_cn["found"] is True and info_cn["last_result"] == 1
    assert mod.parse_schtasks_listing("")["found"] is False


# ------------------------------------------------------------------ 2. 日志解析
def test_parse_last_log(tmp_path):
    log = tmp_path / "zero.log"
    log.write_text("2026-11-01 08:35:01 daily_kb rc=0\noutput ok\n"
                   "2026-11-01 17:35:02 daily_screening rc=1\nerror line\n",
                   encoding="utf-8")
    now = datetime.fromisoformat("2026-11-01T18:00:00+08:00")
    out = mod.parse_last_log(log, now)
    assert out["rc"] == 1 and out["timestamp"] == "2026-11-01T17:35:02+08:00"
    assert "rc=1" in out["line"] and out["age_hours"] == 0.42
    exc = tmp_path / "exc.log"
    exc.write_text("2026-11-01 08:00:00 daily_kb EXCEPTION: boom\n", encoding="utf-8")
    out2 = mod.parse_last_log(exc, now)
    assert out2["rc"] == 1 and out2.get("exception") == "boom"
    missing = mod.parse_last_log(tmp_path / "nope.log", now)
    assert missing["exists"] is False and missing["rc"] is None


# ------------------------------------------------------------------ 3. 新鲜度
def test_latest_output_freshness(tmp_path):
    now = datetime.now().astimezone()
    fresh = tmp_path / "fresh.json"
    fresh.write_text("{}", encoding="utf-8")
    old = tmp_path / "old.json"
    old.write_text("{}", encoding="utf-8")
    old_ts = (now - timedelta(hours=40)).timestamp()
    import os
    os.utime(old, (old_ts, old_ts))
    r1 = mod.latest_output({"path": str(fresh), "stale_hours": 30}, now)
    assert r1["exists"] and r1["fresh"] is True
    r2 = mod.latest_output({"path": str(old), "stale_hours": 30}, now)
    assert r2["exists"] and r2["fresh"] is False and r2["age_hours"] > 30
    r3 = mod.latest_output({"path": str(tmp_path / "missing.json")}, now)
    assert r3["exists"] is False and r3["fresh"] is False


# ------------------------------------------------------------------ 4. 检查/告警
def test_check_task_alert_and_format():
    cfg = {"name": "ZeroBound_DailyKB", "label": "每日知识库", "stale_hours": 30}
    info = {"found": True, "last_result": 1, "last_run_time": "2026/11/1 08:35:19", "status": "Ready"}
    log = {"exists": True, "timestamp": "2026-11-01T08:35:19+08:00", "rc": 1,
           "line": "2026-11-01 08:35:19 daily_kb rc=1", "age_hours": 2.0}
    outputs = [{"path": "portal/static/kb_learning_digest.json", "exists": True,
                "mtime": "2026-11-01T08:35:20+08:00", "age_hours": 2.0, "fresh": True}]
    task = mod.check_task(cfg, info=info, log=log, outputs=outputs)
    assert task["status"] == "alert"
    assert any("Last Result=1" in x for x in task["issues"])
    assert any("rc=1" in x for x in task["issues"])
    now = datetime.fromisoformat("2026-11-01T10:35:20+08:00")
    rep = mod.build_report([task], now, tag="20261101", dry_run=True)
    assert rep["summary"] == {"total": 1, "ok": 0, "alert": 1}
    text = rep["alert_text"]
    for needle in ("ZeroBound_DailyKB", "Last Result: 1", "daily_kb rc=1"):
        assert needle in text
    assert "每日任务健康告警" in text


# ------------------------------------------------------------------ 5. main dry-run / --send（mock 不联网）
def test_main_dry_run_and_send(tmp_path, monkeypatch):
    fake_task = {"name": "ZeroBound_DailyScreening", "label": "每日筛选",
                 "status": "alert", "issues": ["产出过期 40h"],
                 "task": {"found": True, "last_result": 0},
                 "log": {"exists": True, "timestamp": "2026-10-30T17:35:00+08:00",
                         "rc": 0, "line": "rc=0", "age_hours": 40.0},
                 "outputs": [{"path": "research/reports/daily_screening_20261030.json",
                              "exists": True, "age_hours": 40.0, "fresh": False}]}
    monkeypatch.setattr(mod, "check_task", lambda cfg, now=None: dict(fake_task))
    seen = {}

    def fake_send(text):
        seen["text"] = text
        return {"ok": True, "error": None}

    monkeypatch.setattr(mod, "send_alert", fake_send)
    out = tmp_path / "reports"
    assert mod.main(["--out-dir", str(out), "--tag", "20261101"]) == 0
    data = json.loads((out / "daily_tasks_health_20261101.json").read_text(encoding="utf-8"))
    assert data["dry_run"] is True and "send_result" not in data
    assert (out / "daily_tasks_health_20261101.md").exists()
    assert seen == {}                                    # dry-run 不发送
    assert mod.main(["--send", "--out-dir", str(out), "--tag", "20261101"]) == 0
    data2 = json.loads((out / "daily_tasks_health_20261101.json").read_text(encoding="utf-8"))
    assert data2["dry_run"] is False
    assert data2["send_result"]["ok"] is True and "ZeroBound_DailyScreening" in seen["text"]
