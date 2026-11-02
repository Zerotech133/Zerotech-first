# -*- coding: utf-8 -*-
"""LS-03 只读 API 契约测试（研发军团作战屏 v1）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "dev" / "legion_screen_contract.json"
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


# ---------- 2026-11-03 WP1：v2 extras 契约真源（ADR-020 修订） ----------


def test_v2_extras_readonly_and_no_write_methods():
    """live extras：只读 + 无写方法/写接口（契约复核）。"""
    p = A.build_payload(A.ROOT)
    assert p["readonly"] is True
    assert isinstance(p["v2"], dict)
    assert p["v2"]["readonly"] is True
    assert set(p["v2"]["extras"]) == {"workorder_bus", "decisions", "events"}
    # 模块不得暴露写方法（HTTP 出口只允许 GET）
    for bad_attr in ("handle_post", "handle_put", "handle_delete", "handle_patch"):
        assert not hasattr(A, bad_attr), bad_attr
    src = Path(A.__file__).read_text(encoding="utf-8")
    for bad_def in ("def handle_post(", "def handle_put(",
                    "def handle_delete(", "def handle_patch("):
        assert bad_def not in src, bad_def
    json.dumps(p, ensure_ascii=False)


def test_v2_extras_registered_in_contract():
    """契约 JSON 已只增登记 v2 extras；v1 schema 不变。"""
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c["schema"] == "legion_screen_contract/v1"
    assert c["readonly"] is True
    v2 = c["optional_extensions"]["v2"]
    assert v2["readonly"] is True
    for k in ("workorder_bus", "decisions", "events", "v2"):
        assert k in v2["fields"], k
    assert ".worktrees/tasks.json" in v2["fields"]["workorder_bus"]["source"]
    assert "dev_events.jsonl" in v2["fields"]["events"]["source"]
    assert v2["fields"]["workorder_bus"]["max_items"] == 30
    assert any(r.get("adr") == "ADR-020" for r in c.get("revisions") or [])


def test_v2_extras_error_state_keeps_errors(tmp_path):
    """错误态：缺源仍返回 v2 结构 + 非空 errors（不静默、不 500）。"""
    p = A.build_payload(tmp_path)
    assert p["schema"] == "legion_screen_contract/v1"
    assert p["errors"], "缺数据源必须记 errors，不静默"
    for k in ("workorder_bus", "decisions", "events"):
        assert isinstance(p[k], list)
    assert p["v2"]["readonly"] is True


def test_v2_extras_shapes_and_limits():
    """字段形状/上限与契约一致：bus≤30 且 status 受限；events≤10。"""
    p = A.build_payload(A.ROOT)
    bus = p["workorder_bus"]
    assert isinstance(bus, list) and len(bus) <= 30
    for row in bus:
        assert set(("id", "owner", "status", "desc", "created")) <= set(row)
        assert row["status"] in ("doing", "review")
    ev = p["events"]
    assert isinstance(ev, list) and len(ev) <= 10
    for row in ev:
        assert set(("ts", "kind", "agent", "task")) <= set(row)
    for row in p["decisions"]:
        assert isinstance(row, dict)