# -*- coding: utf-8 -*-
"""huangzhong_evidence_refresh 单测（不跑真实 pytest/红队）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools.huangzhong_evidence_refresh as R  # noqa: E402


class _FakeResult:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "agent_platform" / "data" / "test_baseline").mkdir(parents=True)
    return root


def _write_latest(root: Path, summary=None):
    p = root / "agent_platform" / "data" / "test_baseline" / "latest.json"
    p.write_text(json.dumps({"generated_at": "2026-10-31T00:00:00+08:00",
                             "summary": summary or {"passed": 2519, "failed": 0,
                                                    "error": 0}}), encoding="utf-8")
    return p


def test_refresh_ok_order_backup_and_logs(tmp_path):
    root = _fake_root(tmp_path)
    _write_latest(root)
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if any("test_baseline.py" in c for c in cmd):
            return _FakeResult(0, '{"ok": true}')
        return _FakeResult(0, '{"pass_rate": 1.0}')

    rep = R.refresh(root=root, log_dir=tmp_path / "logs", runner=runner)
    assert rep["ok"] is True
    assert [any("test_baseline.py" in x for x in c) for c in calls] == [True, False]
    assert (root / "agent_platform" / "data" / "test_baseline" / "latest_prev.json").is_file()
    assert (tmp_path / "logs" / "huangzhong_baseline_refresh.log").is_file()
    assert (tmp_path / "logs" / "huangzhong_redteam_refresh.log").is_file()
    status_line = (tmp_path / "logs" / "huangzhong_evidence_refresh_status.log").read_text(
        encoding="utf-8").strip().splitlines()[-1]
    assert json.loads(status_line)["evidence"]["summary"]["failed"] == 0


def test_refresh_fail_visible(tmp_path):
    root = _fake_root(tmp_path)
    _write_latest(root)

    def runner(cmd, **kw):
        return _FakeResult(1, "", "boom") if any(
            "test_baseline.py" in c for c in cmd) else _FakeResult(0)

    rep = R.refresh(root=root, log_dir=tmp_path / "logs", runner=runner)
    assert rep["ok"] is False and rep["baseline_rc"] == 1
    log = (tmp_path / "logs" / "huangzhong_baseline_refresh.log").read_text(encoding="utf-8")
    assert "boom" in log


def test_refresh_no_latest_still_runs(tmp_path):
    root = _fake_root(tmp_path)  # 不写 latest.json
    rep = R.refresh(root=root, log_dir=tmp_path / "logs",
                    runner=lambda cmd, **kw: _FakeResult(0, "{}"))
    assert rep["ok"] is True and rep["backup_ok"] is False
