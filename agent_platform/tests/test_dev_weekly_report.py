# -*- coding: utf-8 -*-
"""tools/dev_weekly_report.py 测试（WO-LEGION-P0WRAP）。

覆盖：①compute 指标（归属率/采集份额/吞吐/前置时间/心跳触发）；②render Markdown 关键段；
③--out CLI 落盘（临时目录，不写生产）；④缺 tasks.json 不崩。
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "dev_weekly_report.py"
_spec = importlib.util.spec_from_file_location("dev_weekly_report", str(_TOOL))
DWR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DWR)

NOW = time.mktime(time.strptime("2026-10-27 10:00:00", "%Y-%m-%d %H:%M:%S"))


def _tasks():
    return {
        "T-DEV1": {"owner": "DEV1", "status": "done", "created": "2026-10-27 08:00:00",
                   "merged": "2026-10-27 09:00:00", "dept": "dev", "acceptance_cmd": "pytest -q"},
        "T-A0": {"owner": "A0", "status": "doing", "created": "2026-10-26 12:00:00"},
        "T-UNK": {"owner": "unknown-agent", "status": "doing", "created": "2026-10-25 12:00:00"},
        "T-OLD": {"owner": "DEV1", "status": "done", "created": "2026-10-01 08:00:00",
                  "merged": "2026-10-01 09:00:00"},
    }


def _events():
    return [
        {"ts": "2026-10-27 09:20:17", "agent": "ARCH1", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=True model=deepseek-flash reply_len=117"},
        {"ts": "2026-10-27 09:20:17", "agent": "OPS1", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=False model=deepseek-flash reply_len=0"},
        {"ts": "2026-10-27 09:20:17", "agent": "DEV9", "kind": "task_start", "detail": "x"},
    ]


def test_compute_metrics():
    m = DWR.compute(_tasks(), _events(),
                    routing={"dev_seats": ["ARCH1", "DEV1"]},
                    gate={"phase": "warn"}, now=NOW)
    assert m["tasks_total"] == 4
    assert m["tasks_recent"] == 3            # 10-20 之后 3 条（T-DEV1/T-A0/T-UNK）
    assert m["dev_tasks_recent"] == 1
    assert m["dev_capture_share_recent"] == round(1 / 3, 4)
    assert m["dev_merged_recent"] == 1
    assert m["dev_leadtime_n"] == 2          # 含历史 DEV1 done 单（T-OLD），均为 60 分钟
    assert m["dev_leadtime_p50_min"] == 60.0
    assert m["e3_trigger"]["ARCH1"] == {"total": 1, "ok": 1}
    assert m["e3_trigger"]["OPS1"] == {"total": 1, "ok": 0}
    assert m["owner_legal_rate_recent"] is not None
    assert m["gate_phase"] == "warn"


def test_render_contains_sections():
    md = DWR.render_markdown(DWR.compute(_tasks(), _events(),
                                         routing={"dev_seats": ["DEV1"]},
                                         gate={"phase": "warn"}, now=NOW))
    for key in ("研发部周报", "八项指标", "E3 触发率", "研发采集份额", "口径与边界", "数据点"):
        assert key in md


def test_cli_writes_out(tmp_path):
    out = tmp_path / "weekly.md"
    rc = DWR.main(["--out", str(out), "--window-days", "7"])
    assert rc == 0
    assert out.exists()
    assert "研发部周报" in out.read_text(encoding="utf-8")


def test_missing_tasks_file_is_empty(tmp_path):
    assert DWR.load_tasks(tmp_path / "nope.json") == {}
