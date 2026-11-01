# -*- coding: utf-8 -*-
"""LS-03 只读 API 契约测试（研发军团作战屏 v1）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import api_dev_legion as A  # noqa: E402


def test_payload_contract():
    p = A.build_payload(A.ROOT)
    assert p["schema"] == "legion_screen_contract/v1"
    assert p["readonly"] is True
    assert p["department"]["id"] == "dev"
    assert p["department"]["lead"] == "ARCH1"
    assert p["department"]["lead_name"] == "曹操"
    assert len(p["department"]["groups"]) == 2
    assert len(p["seats"]) == 11
    seat = p["seats"][0]
    for k in ("id", "name", "role_title", "discipline", "group", "grade",
              "model", "org_role", "report_to"):
        assert k in seat
    assert isinstance(p["work_in_progress"], list)
    for k in ("window_days", "dev_tasks_recent", "dev_merged_recent",
              "owner_legal_rate_recent", "verification_rate", "e3_trigger"):
        assert k in p["metrics"]
    assert isinstance(p["errors"], list)
    assert p["sources"] == A.SOURCES
    # 必须可 JSON 序列化（HTTP 出口）
    json.dumps(p, ensure_ascii=False)


def test_fallback_empty_root(tmp_path):
    p = A.build_payload(tmp_path)
    assert p["schema"] == "legion_screen_contract/v1"
    assert p["seats"] == []
    assert p["errors"], "缺数据源必须记 errors，不静默"


def test_handle_get_capture():
    class Fake:
        def __init__(self):
            self.calls = []

        def _json(self, obj, status=200):
            self.calls.append((status, obj))

    f = Fake()
    A.handle_get(f, "/api/dev/legion")
    assert f.calls and f.calls[0][0] == 200
    body = f.calls[0][1]
    assert body["schema"] == "legion_screen_contract/v1"
    assert "seats" in body and "metrics" in body

    f2 = Fake()
    A.handle_get(f2, "/api/dev/other")
    assert f2.calls and f2.calls[0][0] == 404

def test_v2_extras_additive():
    p = A.build_payload(A.ROOT)
    assert isinstance(p["workorder_bus"], list)
    assert isinstance(p["decisions"], list)
    assert isinstance(p["events"], list)
    assert p["v2"]["readonly"] is True
    for k in ("workorder_bus", "decisions", "events"):
        assert k in p["v2"]["extras"]