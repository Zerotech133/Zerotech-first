# -*- coding: utf-8 -*-
"""P0-03 rollback preflight tests: no production entry is touched."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import d1_rollback_drill as m  # noqa: E402


def test_parse_targets_dedupes_and_ignores_words():
    text = "目标提交存在（4788b1527/e15cc824b/HEAD b11cda7c2）且 sha 4788b1527 重复"
    assert m._parse_targets(text) == ["4788b1527", "e15cc824b", "b11cda7c2"]


def test_endpoint_url_keeps_absolute_and_prefixes_loopback():
    assert m._endpoint_url("http://127.0.0.1:8091/api/health") == "http://127.0.0.1:8091/api/health"
    assert m._endpoint_url("/api/d1/decisions") == "http://127.0.0.1:8091/api/d1/decisions"


def test_render_markdown_marks_preflight_not_real_drill():
    result = {
        "checked_at": "2026-11-07T16:00:00+08:00",
        "mode": "preflight",
        "real_drill_performed": False,
        "ok": True,
        "checks": [{"name": "x", "ok": True, "detail": "ok"}],
        "runbook": ["步骤一"],
    }
    text = m.render_markdown(result)
    assert "PASS" in text
    assert "preflight" in text
    assert "last_drilled 仍 pending" in text


def test_execute_is_blocked_without_approval(capsys):
    code = m.main(["--execute"])
    assert code == 3
    out = json.loads(capsys.readouterr().out)
    assert out["blocked"] is True
    assert out["ok"] is False


def test_preflight_skips_network_and_container():
    result = m.preflight(probe_http=False, check_container=False)
    assert result["mode"] == "preflight"
    assert result["real_drill_performed"] is False
    assert result["baseline"]["http"][0]["error"] == "skipped"
    assert any(c["name"] == "page.d1.index" for c in result["checks"])
