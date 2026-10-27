"""评估飞轮测试（WO-EVAL-HARNESS）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_WT = Path(__file__).resolve().parents[2]
_EVALS = _WT / "agent_platform" / "evals"
# 内置评估里的 selfaudit_source 依赖本机 Hermes 真实 profile（~/.hermes/profiles/zhaoyun）；
# 服务器 verify 环境没有该 profile → 相关用例 skip（避免新门误报）。
_DEV_PROFILE = Path.home() / ".hermes" / "profiles" / "zhaoyun"
_DEV_ENV_SKIP = pytest.mark.skipif(
    not _DEV_PROFILE.is_dir(),
    reason="依赖本机 Hermes 真实 profile（服务器 verify 环境无 ~/.hermes/profiles/zhaoyun）")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RE = _load("run_evals", _EVALS / "run_evals.py")
IT = _load("incident_to_test", _EVALS / "incident_to_test.py")


def test_behaviors_catalog_valid():
    data = RE._load()
    items = data["behaviors"]
    assert len(items) >= 19
    ids = [b["id"] for b in items]
    assert len(ids) == len(set(ids))
    assert all(b.get("skill") or b.get("builtin") or b.get("file") for b in items)
    assert sum(1 for b in items if b.get("smoke")) >= 10


@_DEV_ENV_SKIP
def test_builtins_all_pass():
    for name in (
        "skills_registry_total",
        "selfaudit_source",
        "trace_writable",
        "approval_kinds",
        "approval_signer_required",
    ):
        rep = RE._builtin(name)
        assert rep["ok"] is True, (name, rep)


def test_run_single_skill_behavior():
    rep = RE.run("cap.task-exit-audit", {"skill": "task-exit-audit"})
    assert rep["ok"] is True


@_DEV_ENV_SKIP
def test_smoke_run_via_subprocess(tmp_path):
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, str(_EVALS / "run_evals.py"), "--smoke", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert r.returncode == 0, r.stderr[-400:]
    rep = json.loads(r.stdout)
    assert rep["ok"] is True and rep["session"]["total"] >= 10


def test_incident_drafts_from_events(tmp_path, monkeypatch):
    events = tmp_path / "dev_events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-10-27T06:00:00+0800",
                        "kind": "guard_fail",
                        "task": "X",
                        "detail": "guard failed",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-10-27T06:01:00+0800",
                        "kind": "blocked",
                        "task": "Y",
                        "detail": "blocked",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-10-27T06:02:00+0800",
                        "kind": "commit",
                        "task": "Z",
                        "detail": "noise",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(IT, "EVENTS", events)
    monkeypatch.setattr(IT, "OUT", tmp_path / "incidents")
    rep = IT.generate(limit=10)
    assert rep["ok"] and rep["total_created"] == 2
    drafts = list((tmp_path / "incidents").glob("INC-*.json"))
    assert len(drafts) == 2
    rec = json.loads(drafts[0].read_text(encoding="utf-8"))
    assert rec["status"] == "needs_review"
