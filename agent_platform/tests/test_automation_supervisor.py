# -*- coding: utf-8 -*-
"""automation_supervisor dry-run 测试（不触网）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import automation_supervisor as S  # noqa: E402


def test_run_checks_red(tmp_path, monkeypatch):
    (tmp_path / ".worktrees").mkdir()
    tasks = {
        "T-OLD": {"dept": "dev", "owner": "DEV1", "status": "doing",
                  "created": "2026-10-25 10:00:00"},
        "T-NOAPPROVER": {"dept": "dev", "owner": "DEV5", "status": "done",
                         "created": "2026-10-28 10:00:00",
                         "merged": "2026-10-28 11:00:00"},
    }
    (tmp_path / ".worktrees/tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    monkeypatch.setattr(S, "_http", lambda url, timeout=3: 200)
    monkeypatch.setattr(S, "_port_open", lambda port, host="127.0.0.1": True)
    r = S.run_checks(tmp_path)
    assert r["schema"] == "supervisor_dryrun/v1"
    assert r["dry_run"] is True
    assert any(x["id"] == "T-OLD" for x in r["checks"]["L1_回执"])
    assert any(x["id"] == "T-NOAPPROVER" for x in r["checks"]["L3_审计"])
    assert r["red"] is True
    md = S.render_md(r)
    assert "自动化监督 dry-run" in md and "RED" in md


def test_healthy_green(tmp_path, monkeypatch):
    (tmp_path / ".worktrees").mkdir()
    (tmp_path / ".worktrees/tasks.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(S, "_http", lambda url, timeout=3: 200)
    monkeypatch.setattr(S, "_port_open", lambda port, host="127.0.0.1": True)
    r = S.run_checks(tmp_path)
    assert r["red"] is False
