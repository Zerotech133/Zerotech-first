# -*- coding: utf-8 -*-
"""会议 P2 测试：看板统计/跑偏/回放、赵云会议绑定覆盖、HTTP、前端标记。"""
import json
import sys
import threading
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

from legion_command import hq_stats  # noqa: E402
from legion_command import serve as srv  # noqa: E402


def _doc(mid, typ="morning", status="draft", decisions=(), orders=(), created="2026-10-20T09:00:00"):
    return {"schema_version": "2.0", "meeting_id": mid, "created_at": created,
            "type": typ, "status": status, "attendees": ["A0", "HUANGZHONG"],
            "agenda": [], "transcript": [], "decisions": list(decisions),
            "orders": list(orders), "acceptance": []}


def _seed(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    a = _doc("hq-20261020-090000", status="dispatched",
             decisions=[{"id": "D1", "decision": "x", "owner": "GUANYU", "due": "2026-11-01"}],
             orders=[{"title": "x", "type": "work", "dispatch": {"ok": True}}])
    b = _doc("hq-20261021-090000", status="draft",
             decisions=[{"id": "D1", "decision": "y", "owner": "", "due": ""}])
    (d / "hq-20261020-090000.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    (d / "hq-20261021-090000.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")


def test_board_stats(tmp_path):
    d = tmp_path / "hq"
    _seed(d)
    rep = hq_stats.board(d)
    assert rep["ok"] and rep["total"] == 2
    assert rep["decisions"] == 2 and rep["orders"] == 1 and rep["dispatched"] == 1
    assert rep["closure_rate"] == 0.5
    assert rep["drift"]["missing_owner"] == 1
    assert rep["drift"]["missing_due"] == 1
    # 审计修复：决议无订单按会议计一次，不得因缺 due 再计一次（双计数）
    assert rep["drift"]["decisions_without_orders"] == 1
    assert rep["drift"]["draft_stale"] >= 1
    assert len(rep["replay"]) == 2 and rep["replay"][0]["meeting_id"].startswith("hq-")


def test_board_empty(tmp_path):
    rep = hq_stats.board(tmp_path / "none")
    assert rep["ok"] and rep["total"] == 0 and rep["closure_rate"] == 0.0


def test_meeting_binding_override(monkeypatch):
    monkeypatch.setattr(srv, "resolve_binding", lambda a, *x: {
        "ZHAOYUN": "deepseek/deepseek-v4-pro", "A0": "glm/glm-5.3",
        "NONE1": "none"}.get(a, "ollama/qwen3.5:9b"))
    assert srv.resolve_meeting_binding("ZHAOYUN") == "ollama/qwen3.5:9b"
    assert srv.resolve_meeting_binding("A0") == "glm/glm-5.3"
    assert srv.resolve_meeting_binding("NONE1") == "none"


def test_frontend_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "meeting.html").read_text(encoding="utf-8")
    for key in ("purpose: 'meeting'", "/api/hq/meeting/stats", "hqBoardStats", "loadHqStats",
                "function esc(", "isHtml ? text : esc(text)"):
        assert key in h, key


def test_http_stats(tmp_path, monkeypatch):
    d = tmp_path / "hq"
    _seed(d)
    monkeypatch.setattr(srv, "HQ_MEETINGS_DIR", d)
    srvr = srv.build_server(port=0)
    th = threading.Thread(target=srvr.serve_forever, daemon=True)
    th.start()
    try:
        rep = json.loads(safe_urlopen(
            "http://127.0.0.1:%d/api/hq/meeting/stats" % srvr.server_address[1], timeout=10).read())
        assert rep["ok"] and rep["total"] == 2
    finally:
        srvr.shutdown()
        srvr.server_close()
