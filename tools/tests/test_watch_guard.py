# -*- coding: utf-8 -*-
"""盯盘 W4+W7 守卫测试（WO-WATCH-GUARD-20261104）。

覆盖：healthy/stale 边界/engine_state=error/status 缺失/坏 JSON/无心跳/心跳未来/
安全标记反例/8789 down/8091 down/HTTPError 状态/expire dry-run 不写/expire 真实回收/
显式 ledger 强制/alert-file 显式且异常才写/JSON/Markdown/无 token/参数错误。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import watch_guard as G  # noqa: E402

UTC = timezone.utc
SECRET_RE = r"(?i)(bearer\s+[a-z0-9._-]{8,}|sk-[a-z0-9]{10,}|zb_session|token=[a-z0-9]{6,})"


def _status_file(tmp_path, hb_offset_s=0, **overrides):
    now = datetime.now(UTC)
    heartbeat = now - timedelta(seconds=hb_offset_s)
    data = {
        "engine_state": "running",
        "paper_only": True,
        "executes": False,
        "last_tick_at": heartbeat.isoformat(),
        "heartbeat_at": heartbeat.isoformat(),
        "tick_count": 3,
        "armed_count": 0,
    }
    data.update(overrides)
    if "heartbeat_at" in overrides and "last_tick_at" not in overrides:
        data["last_tick_at"] = overrides["heartbeat_at"]
    path = tmp_path / "status.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _fake_get(admin_status=200, plans_status=200, admin_error=None, plans_error=None):
    def fake(url, timeout=3.0):
        if "8091" in url or "watch-plans" in url:
            status, error = plans_status, plans_error
        else:
            status, error = admin_status, admin_error
        ok = status == 200
        return {"url": url, "status": status, "ok": ok, "latency_ms": 4,
                "content_type": "application/json" if ok else None,
                "error": error or (None if ok else "expected_200_got_%s" % status)}
    return fake


def _plan_ledger(tmp_path, now):
    plans = tmp_path / "watch_plans.jsonl"
    rows = [
        {"plan_id": "wp-due", "status": "armed",
         "expires_at": (now - timedelta(hours=1)).isoformat()},
        {"plan_id": "wp-future", "status": "armed",
         "expires_at": (now + timedelta(hours=1)).isoformat()},
        {"plan_id": "wp-triggered", "status": "triggered",
         "expires_at": (now - timedelta(hours=2)).isoformat()},
    ]
    plans.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                     encoding="utf-8")
    return plans


# ── healthy / stale / 边界 ────────────────────────────────────────────
def test_healthy_ok_cli_json(tmp_path, monkeypatch, capsys):
    path = _status_file(tmp_path, hb_offset_s=10)
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "watch_guard_report/v1"
    assert report["overall"] == "ok" and report["ok"] is True
    assert report["status"]["engine_state"] == "running"
    assert report["status"]["stale"] is False
    assert report["checks"]["watch_admin"]["ok"] is True
    assert report["checks"]["watch_plans"]["ok"] is True
    assert report["expire"] is None
    assert report["paper_only"] is True and report["not_a_trade"] is True


def test_stale_heartbeat_is_anomaly(tmp_path, monkeypatch, capsys):
    path = _status_file(tmp_path, hb_offset_s=240)
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["overall"] == "stale" and report["ok"] is False
    assert report["status"]["stale"] is True
    assert report["status"]["age_s"] >= 180
    assert any("heartbeat_stale" in w for w in report["warnings"])


def test_stale_threshold_boundary_180(tmp_path, monkeypatch):
    heartbeat = datetime(2026, 11, 4, 10, 0, 0, tzinfo=UTC)
    path = _status_file(tmp_path, hb_offset_s=0, heartbeat_at=heartbeat.isoformat())
    monkeypatch.setattr(G, "_get", _fake_get())
    at_180 = G.load_status(path, stale_after=180,
                           now=heartbeat + timedelta(seconds=180))
    assert at_180["age_s"] == 180 and at_180["stale"] is False  # 边界：>180 才 stale
    at_181 = G.load_status(path, stale_after=180,
                           now=heartbeat + timedelta(seconds=181))
    assert at_181["age_s"] == 181 and at_181["stale"] is True


# ── status 异常（fail-visible）────────────────────────────────────────
def test_engine_state_error(tmp_path, monkeypatch):
    path = _status_file(tmp_path, engine_state="error")
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(path))
    assert report["overall"] == "error" and report["ok"] is False
    assert report["status"]["error_code"] == "engine_state_error"
    assert any("status:engine_state_error" in e for e in report["errors"])


def test_status_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(tmp_path / "missing.json"))
    assert report["overall"] == "error"
    assert report["status"]["error_code"] == "status_missing"


def test_status_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["status"]["error_code"] == "status_invalid_json"


def test_heartbeat_unparsable(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"engine_state": "running"}), encoding="utf-8")
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["status"]["error_code"] == "heartbeat_unparsable"


def test_heartbeat_future_clock_skew(tmp_path, monkeypatch):
    future = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()
    path = _status_file(tmp_path, heartbeat_at=future)
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["status"]["error_code"] == "heartbeat_future"


@pytest.mark.parametrize("override,code", [
    ({"paper_only": False}, "unsafe_status_paper_only_false"),
    ({"executes": True}, "unsafe_status_executes_true"),
])
def test_unsafe_status_flags(tmp_path, monkeypatch, override, code):
    path = _status_file(tmp_path, **override)
    monkeypatch.setattr(G, "_get", _fake_get())
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["status"]["error_code"] == code


# ── 8789/8091 探测 ───────────────────────────────────────────────────
def test_watch_admin_8789_down(tmp_path, monkeypatch):
    path = _status_file(tmp_path, hb_offset_s=5)
    monkeypatch.setattr(G, "_get", _fake_get(admin_status=0,
                                             admin_error="URLError: refused"))
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["checks"]["watch_admin"]["ok"] is False
    assert any("watch-admin-8789_down" in e for e in report["errors"])


def test_watch_plans_8091_down(tmp_path, monkeypatch):
    path = _status_file(tmp_path, hb_offset_s=5)
    monkeypatch.setattr(G, "_get", _fake_get(plans_status=500))
    report = G.check(status_path=str(path))
    assert report["overall"] == "error"
    assert report["checks"]["watch_plans"]["status"] == 500
    assert any("watch-plans-8091_down" in e for e in report["errors"])


def test_probe_http_error_status_kept_and_no_credentials(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=3.0):
        captured["headers"] = {key.lower(): value for key, value in req.headers.items()}
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    row = G._get("http://127.0.0.1:8091/api/d1/watch-plans")
    assert row["ok"] is False and row["status"] == 401
    assert "expected_200_got_401" in row["error"]
    assert "authorization" not in captured["headers"]
    assert "cookie" not in captured["headers"]


# ── 过期回收（W4）────────────────────────────────────────────────────
def test_expire_dry_run_lists_due_without_write(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_get", _fake_get())
    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
    plans = _plan_ledger(tmp_path, now)
    before = plans.read_bytes()
    path = _status_file(tmp_path, hb_offset_s=5)
    report = G.check(status_path=str(path), expire_plan_ledger=str(plans),
                     expire_dry_run=True, now=now)
    expire = report["expire"]
    assert expire["status"] == "dry_run" and expire["due"] == 1
    assert expire["changed"] == 0
    assert expire["plan_ids"] == ["wp-due"]
    assert plans.read_bytes() == before  # dry-run 零写


def test_expire_due_executes_with_explicit_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_get", _fake_get())
    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
    plans = _plan_ledger(tmp_path, now)
    path = _status_file(tmp_path, hb_offset_s=5)
    report = G.check(status_path=str(path), expire_plan_ledger=str(plans),
                     expire_dry_run=False, now=now)
    expire = report["expire"]
    assert expire["status"] == "expired" and expire["changed"] == 1
    assert expire["plan_ids"] == ["wp-due"]
    rows = [json.loads(line) for line in plans.read_text(encoding="utf-8").splitlines()]
    by_id = {row["plan_id"]: row["status"] for row in rows}
    assert by_id["wp-due"] == "expired"
    assert by_id["wp-future"] == "armed"
    assert by_id["wp-triggered"] == "triggered"


def test_expire_due_requires_explicit_ledger(monkeypatch, capsys):
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--expire-due", "--json"]) == 2
    err = capsys.readouterr().err
    assert "显式 --plan-ledger" in err


def test_expire_env_ledger_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "_get", _fake_get())
    now = datetime.now(UTC)
    plans = _plan_ledger(tmp_path, now)
    monkeypatch.setenv(G.PLAN_LEDGER_ENV, str(plans))
    path = _status_file(tmp_path, hb_offset_s=5)
    assert G.main(["--status", str(path), "--expire-due", "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["expire"]["status"] == "dry_run"
    assert report["expire"]["due"] == 1
    assert report["expire"]["plan_ledger"] == str(plans)


# ── alert-file（显式才写；不接 Feishu）───────────────────────────────
def test_alert_file_written_only_on_anomaly(tmp_path, monkeypatch):
    path = _status_file(tmp_path, hb_offset_s=240)
    alerts = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(path), "--alert-file", str(alerts), "--json"]) == 1
    lines = [line for line in alerts.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["schema"] == "watch_guard_alert/v1"
    assert event["overall"] == "stale"
    assert event["paper_only"] is True and event["not_a_trade"] is True

    # healthy 时即便给了 alert-file 也不写（append 0 行）
    healthy = _status_file(tmp_path, hb_offset_s=5)
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(healthy), "--alert-file", str(alerts),
                   "--json"]) == 0
    assert len(alerts.read_text(encoding="utf-8").splitlines()) == 1


def test_no_alert_file_zero_write(tmp_path, monkeypatch):
    path = _status_file(tmp_path, hb_offset_s=240)
    monkeypatch.setattr(G, "_get", _fake_get())
    before = sorted(p.name for p in tmp_path.iterdir())
    assert G.main(["--status", str(path), "--json"]) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 零新增文件


# ── 输出 / 参数 / 无 token ───────────────────────────────────────────
def test_markdown_output(tmp_path, monkeypatch, capsys):
    path = _status_file(tmp_path, hb_offset_s=5)
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(path), "--markdown"]) == 0
    text = capsys.readouterr().out
    assert "# 盯盘 W4/W7 守卫报告" in text
    assert "180" in text and "| 检查 | 结果 |" in text
    assert "watch-admin-8789" in text and "watch-plans-8091" in text
    assert "只读守卫" in text


def test_json_markdown_mutually_exclusive():
    assert G.main(["--json", "--markdown"]) == 2


def test_bad_stale_after_rejected():
    assert G.main(["--stale-after", "0"]) == 2
    assert G.main(["--timeout", "-1"]) == 2


def test_output_has_no_token_or_credentials(tmp_path, monkeypatch, capsys):
    path = _status_file(tmp_path, hb_offset_s=5,
                        secret_token="super-secret-value",
                        authorization="Bearer abcdefghijklmnop")
    monkeypatch.setattr(G, "_get", _fake_get())
    assert G.main(["--status", str(path), "--json"]) == 0
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert "Bearer abcdefghijklmnop" not in out
    assert not __import__("re").search(SECRET_RE, out)
