"""赵云 · 治理席前端页测试（WO-LEGION-ZHAOYUNPAGE-20261026）。

覆盖：/api/zhaoyun 聚合函数（能力卡/判例库/建议/时钟/签字链/保活）、
数据源缺失时 fail-soft、静态页存在性与关键契约（fetch /api/zhaoyun）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
for _p in (str(_PLATFORM), str(_PLATFORM / "legion_command")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from legion_command import zhaoyun_view as V  # noqa: E402


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict:
    platform = tmp_path / "repo" / "agent_platform"
    profile = tmp_path / "profile"
    exp_root = tmp_path / "experience"
    _write(profile / "skills" / "CAPABILITIES.json", {
        "total": 3, "ready": 2, "pending_key": 1,
        "capabilities": [
            {"name": "scan", "what": "扫描", "boundary": "只读", "status": "ready"},
            {"name": "case-sign-chain", "what": "签字链", "boundary": "人签", "status": "ready"},
            {"name": "harness-runner", "what": "执行框架", "boundary": "DSH", "status": "pending_key"},
        ],
    })
    exp_root.mkdir(parents=True, exist_ok=True)
    rows = [
        {"entry_id": "EXP-SUPERVISION-001", "domain": "supervision", "cue": "假成功",
         "confidence": 0.9, "signed_by": "老田", "written_at": "2026-10-26T12:46:53+0800",
         "verified": True},
        {"entry_id": "EXP-SUPERVISION-002", "domain": "supervision", "cue": "快照假绿",
         "confidence": 0.9, "signed_by": "老田", "written_at": "2026-10-26T12:47:00+0800",
         "verified": True},
    ]
    (exp_root / "_index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    advice = platform / "data" / "governance_ai" / "advice" / "2026-10-26"
    _write(advice / "anomaly_digest.json", {
        "status": "draft", "generated_at": "2026-10-26T13:00:00+08:00",
        "input_digest": "abc123def456", "model": "deepseek-flash",
        "items": [{"id": "det-1", "title": "取消码误计", "confidence": 0.8,
                   "evidence": ["exit:3221225786"]}],
        "validator": {"schema_ok": True, "grounding_ok": True, "z5_ok": True},
    })
    _write(advice / "priority_advice.json", {
        "status": "draft", "generated_at": "2026-10-26T13:10:00+08:00",
        "items": [{"id": "det-2", "title": "压降失败率", "confidence": 0.8,
                   "evidence": ["health:ai.error_rate"]}],
    })
    clock_dir = platform / "data"
    _write(clock_dir / "governance_clock_state.json",
           {"last_tick_iso": "2026-10-26T13:20:00+08:00",
            "fired": {"2026-10-26|08:46": {"action": "scan", "ok": True}}})
    _write(clock_dir / "governance_clock.lock",
           {"pid": 12345, "started_at": "2026-10-26T11:28:05+08:00"})
    return {"platform": platform, "profile": profile, "experience_root": exp_root}


def test_build_view_full(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    monkeypatch.setattr(V, "_keepalive",
                        lambda: {"available": True, "last": "2026-10-26 13:25:00 OK alive pid=30616",
                                 "lines": 9})
    view = V.build_view(fx["platform"], profile=fx["profile"],
                        experience_root=fx["experience_root"])
    assert view["ok"] is True
    assert view["identity"]["name"] == "赵云"
    assert view["capabilities"]["total"] == 3
    assert view["capabilities"]["ready"] == 2
    assert [c["name"] for c in view["capabilities"]["items"]][:1] == ["scan"]
    assert view["experience"]["entries_total"] == 2
    assert view["experience"]["verified_total"] == 2
    assert view["advice"]["anomaly_digest"]["status"] == "draft"
    assert view["advice"]["anomaly_digest"]["items"][0]["title"] == "取消码误计"
    assert view["advice"]["case_draft"]["exists"] is False
    assert view["clock"]["pid"] == 12345  # 不存在的 pid -> alive False，但字段保留
    assert view["clock"]["alive"] is False
    assert view["clock"]["last_tick"] == "2026-10-26T13:20:00+08:00"
    assert view["keepalive"]["last"].startswith("2026-10-26 13:25")
    assert view["sign_chain"]["entries_total"] == 2
    assert view["sign_chain"]["verified_total"] == 2
    assert "老田" in view["sign_chain"]["private_key_holder"]


def test_build_view_missing_sources_fail_soft(monkeypatch, tmp_path):
    monkeypatch.setattr(V, "_keepalive", lambda: {"available": False, "last": ""})
    view = V.build_view(tmp_path / "empty", profile=tmp_path / "no-profile",
                        experience_root=tmp_path / "no-exp")
    assert view["ok"] is True
    assert view["capabilities"]["total"] == 0
    assert view["experience"]["entries_total"] == 0
    assert view["advice"]["anomaly_digest"]["exists"] is False
    assert view["clock"]["pid"] is None
    assert view["clock"]["alive"] is False


def test_static_page_contract():
    page = (Path(__file__).resolve().parents[1] / "legion_command" / "static" /
            "zhaoyun.html")
    text = page.read_text(encoding="utf-8")
    assert "赵云" in text and "治理席" in text
    assert "fetch('/api/zhaoyun')" in text.replace('"', "'")
    for marker in ("能力卡", "判例库", "治理 AI", "签字链", "身份对照"):
        assert marker in text
    assert "textContent" in text  # 数据渲染走 textContent（防注入）


def test_index_has_zhaoyun_tab():
    idx = (Path(__file__).resolve().parents[1] / "legion_command" / "static" /
           "index.html").read_text(encoding="utf-8")
    assert 'data-v="zhaoyun"' in idx
    assert "/static/zhaoyun.html" in idx


def test_pid_alive_own_process_and_absent_pid():
    import os
    assert V._pid_alive(os.getpid()) is True
    assert V._pid_alive(99999999) is False
    assert V._pid_alive("not-a-pid") is False
    assert V._pid_alive(-1) is False


def test_no_os_kill_probe_in_view():
    """回归锚点：不得再用 os.kill 探活（Windows 语义会 TerminateProcess）。"""
    src = (Path(__file__).resolve().parents[1] / "legion_command" /
           "zhaoyun_view.py").read_text(encoding="utf-8")
    assert "os.kill(pid, 0)" in src          # 仅 POSIX 分支允许出现
    assert "OpenProcess" in src              # Windows 分支必须用 OpenProcess
    # 出现位置必须在 POSIX 分支：Windows 分支（os.name == "nt"）早于最后的 os.kill
    assert src.index('os.name == "nt"') < src.rindex("os.kill(pid, 0)")


def test_serve_has_clean_zhaoyun_route():
    src = (Path(__file__).resolve().parents[1] / "legion_command" /
           "serve.py").read_text(encoding="utf-8")
    assert '"/zhaoyun"' in src and '"zhaoyun.html"' in src
