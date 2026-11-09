# -*- coding: utf-8 -*-
"""backup_alert target/app guard（JEV 2026-11-06 cross-app 修复）。

只用 mock，不发送任何飞书消息。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "news_collector"))
sys.path.insert(0, str(ROOT / "jev-eval"))

import backup_alert  # noqa: E402
import feishu_client  # noqa: E402


def _targets(*rows):
    return list(rows)


def test_registered_target_matching_sender_app(monkeypatch):
    monkeypatch.setattr(
        backup_alert.push_targets,
        "targets",
        lambda: _targets({"name": "个人", "id": "ou_ok", "app": "app_news"}),
    )
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: ("app_news", "secret"))
    ok, why = backup_alert._target_app_guard("ou_ok")
    assert ok is True
    assert why == "app_news"


def test_unregistered_target_is_rejected(monkeypatch):
    monkeypatch.setattr(
        backup_alert.push_targets,
        "targets",
        lambda: _targets({"name": "个人", "id": "ou_new", "app": "app_news"}),
    )
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: ("app_news", "secret"))
    ok, why = backup_alert._target_app_guard("ou_old_cross_app")
    assert ok is False
    assert "not registered" in why


def test_target_app_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(
        backup_alert.push_targets,
        "targets",
        lambda: _targets({"name": "个人", "id": "ou_old", "app": "app_other"}),
    )
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: ("app_news", "secret"))
    ok, why = backup_alert._target_app_guard("ou_old")
    assert ok is False
    assert "target app app_other" in why
    assert "sender app app_news" in why


def test_missing_sender_credentials_is_rejected(monkeypatch):
    monkeypatch.setattr(
        backup_alert.push_targets,
        "targets",
        lambda: _targets({"name": "个人", "id": "ou_ok", "app": "app_news"}),
    )
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: (None, None))
    ok, why = backup_alert._target_app_guard("ou_ok")
    assert ok is False
    assert "no static app credentials" in why


def test_empty_target_is_rejected(monkeypatch):
    monkeypatch.setattr(backup_alert.push_targets, "targets", lambda: [])
    ok, why = backup_alert._target_app_guard("")
    assert ok is False
    assert why == "empty target"


def test_main_blocks_old_target_and_notifies_safe_target(monkeypatch):
    calls = []

    monkeypatch.setattr(
        backup_alert.push_targets,
        "targets",
        lambda: _targets({"name": "个人", "id": "ou_new", "app": "app_news"}),
    )
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: ("app_news", "secret"))
    monkeypatch.setattr(backup_alert, "DEFAULT_USER_ID", "ou_new", raising=False)
    monkeypatch.setattr(backup_alert, "DEFAULT_GROUP_ID", "", raising=False)
    monkeypatch.setattr(
        backup_alert,
        "send_openapi",
        lambda msg_type, content, user_id, receive_id_type="open_id": calls.append(
            (msg_type, user_id, content)
        )
        or True,
    )
    monkeypatch.setenv("LARK_ALERT_OPEN_ID", "ou_old_cross_app")
    monkeypatch.setattr(sys, "argv", ["backup_alert.py", "hello"])

    try:
        backup_alert.main()
        assert False, "main should exit 2"
    except SystemExit as exc:
        assert exc.code == 2
    assert len(calls) == 1
    assert calls[0][0] == "text"
    assert calls[0][1] == "ou_new"
    assert "JEV 告警未发送" in calls[0][2]

