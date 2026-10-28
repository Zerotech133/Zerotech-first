# -*- coding: utf-8 -*-
"""authority_audit 测试（WO-AUTHORITY-C3）。"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "authority_audit.py"
_spec = importlib.util.spec_from_file_location("authority_audit", str(_TOOL))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

NOW = time.mktime(time.strptime("2026-10-27 16:00:00", "%Y-%m-%d %H:%M:%S"))
CHARTER = {"version": "dev-authority-v1", "approved_by": "老田",
           "approved_at": "2026-10-27", "level": "L1_dev_max",
           "approval": {"mode": "warn", "approver": "ARCH1",
                        "alternates": ["DEV1"]}}


def _tasks():
    return {
        "T-DEV-OK": {"dept": "dev", "owner": "DEV1", "status": "done",
                     "created": "2026-10-27 10:00:00", "merged": "2026-10-27 11:00:00",
                     "approver": "ARCH1"},
        "T-DEV-MISS": {"dept": "dev", "owner": "DEV5", "status": "done",
                       "created": "2026-10-26 10:00:00", "merged": "2026-10-26 11:00:00"},
        "T-DEV-SELF": {"dept": "dev", "owner": "ARCH1", "status": "done",
                       "created": "2026-10-25 10:00:00", "merged": "2026-10-25 11:00:00",
                       "approver": "ARCH1"},
        "T-DEV-NEW-OK": {"dept": "dev", "owner": "DEV6", "status": "done",
                         "created": "2026-10-28 10:00:00", "merged": "2026-10-28 11:00:00",
                         "approver": "ARCH1"},
        "T-DEV-NEW-MISS": {"dept": "dev", "owner": "DEV8", "status": "done",
                           "created": "2026-10-28 12:00:00", "merged": "2026-10-28 13:00:00"},
        "T-OTHER": {"dept": "quant", "owner": "D1", "status": "doing",
                    "created": "2026-10-27 09:00:00"},
    }


def _events():
    return [
        {"ts": "2026-10-27 09:20:17", "agent": "ARCH1", "kind": "task_start", "detail": "x"},
        {"ts": "2026-10-27 09:21:00", "agent": "ARCH1", "kind": "review_verdict", "detail": "y"},
        {"ts": "2026-10-27 09:20:17", "agent": "ARCH1", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=True"},
        {"ts": "2026-10-27 09:20:17", "agent": "DEV1", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=True"},
        {"ts": "2026-10-27 09:20:17", "agent": "DEV5", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=True"},
        {"ts": "2026-10-27 09:20:17", "agent": "OPS1", "kind": "patrol_verdict",
         "detail": "seat_heartbeat ok=False"},
    ]


def test_compute_metrics():
    m = A.compute(_tasks(), _events(), CHARTER, adr_files=["ADR-001.md"], now=NOW)
    assert m["dev"]["done"] == 5
    assert m["dev"]["approved"] == 3
    assert m["dev"]["missing"] == 1          # 新单缺署名（历史不追改）
    assert m["dev"]["historical_missing"] == 1
    assert m["dev"]["new_done"] == 2
    assert m["dev"]["self_review"] == 1
    assert m["dev"]["missing"] == 1
    assert m["arch1_actions_7d"] == 2
    assert m["e3"]["OPS1"]["ok"] == 0
    rl = {r["id"]: r for r in m["red_lines"]}
    assert rl["merge_approval_coverage"]["value"] == 0.5
    assert rl["merge_approval_coverage"]["ok"] is False
    assert isinstance(m["red_lines"], list)
    assert m["adr_count"] == 1


def test_render_contains_sections():
    m = A.compute(_tasks(), _events(), CHARTER, adr_files=["ADR-001.md"], now=NOW)
    md = A.render_md(m)
    for k in ("研发域最高权限", "红线", "合并署名", "dev 完成单"):
        assert k in md
    h = A.render_html(m)
    for k in ("研发域最高权限", "<table", "tokens.css", "nav.js"):
        assert k in h


def test_check_mode(tmp_path):
    out = tmp_path / "board.html"
    m = A.compute(_tasks(), _events(), CHARTER, adr_files=[], now=NOW)
    out.write_text(A.render_html(m), encoding="utf-8")
    assert A.main(["--check", "--html-out", str(out)]) == 0
    assert A.main(["--check", "--html-out", str(tmp_path / "nope.html")]) == 1
