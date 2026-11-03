# -*- coding: utf-8 -*-
"""会前议程 P1 测试：收编只读/留言追加/HTTP 路由/前端标记。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from legion_command import hq_agenda as ag  # noqa: E402
from legion_command import serve as srv  # noqa: E402


def _seed(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "council_new_topics.json").write_text(json.dumps([
        {"topic": "议题A", "tags": ["govern"], "stage": "立题", "originator": "laotian"},
        {"topic": "议题B", "tags": ["daily"], "stage": "立题", "originator": "tianfeng"},
    ], ensure_ascii=False), encoding="utf-8")
    (d / "council_messages.jsonl").write_text(
        json.dumps({"at": "x", "topic": "议题A", "seat": "tianfeng", "kind": "new", "text": "留言1"},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "council_scheduled.jsonl").write_text(
        json.dumps({"at": "x", "title": "预约会", "seats": ["tianfeng"]}, ensure_ascii=False) + "\n",
        encoding="utf-8")


def test_load_agenda_readonly(tmp_path):
    d = tmp_path / "council"
    _seed(d)
    rep = ag.load_agenda(d)
    assert rep["ok"] and len(rep["topics"]) == 2 and len(rep["messages"]) == 1
    assert rep["scheduled"][0]["title"] == "预约会"
    assert "收编只读" in rep["source"]


def test_load_agenda_missing_ok(tmp_path):
    rep = ag.load_agenda(tmp_path / "none")
    assert rep["ok"] and rep["topics"] == [] and rep["messages"] == []
    assert rep["recent_events"] == []


def test_recent_events_include_acceptance_done(tmp_path):
    """日更验收联动（WO-HZ-MEET-20261030）：acceptance_done 进会前议程 recent_events，
    非白名单 kind 仍被过滤。"""
    d = tmp_path / "council"
    _seed(d)
    rows = [
        {"ts": "t1", "kind": "merge_done", "task": "WO-A"},
        {"ts": "t2", "kind": "acceptance_done", "task": "HUANGZHONG-DAILY",
         "detail": "verdict=PASS commit=abc123"},
        {"ts": "t3", "kind": "unknown_kind", "task": "NOISE"},
    ]
    (d / "dev_events.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    rep = ag.load_agenda(d)
    kinds = [e["kind"] for e in rep["recent_events"]]
    assert "acceptance_done" in kinds
    assert "merge_done" in kinds
    assert "unknown_kind" not in kinds
    assert rep["events_count"] == 2
    acc = [e for e in rep["recent_events"] if e["kind"] == "acceptance_done"][0]
    assert acc["task"] == "HUANGZHONG-DAILY"


def test_append_message(tmp_path):
    d = tmp_path / "council"
    _seed(d)
    code, env = ag.append_message(d, {"topic": "议题A", "text": "补一条留言", "seat": "zhaoyun"})
    assert code == 200 and env["ok"]
    rows = (d / "council_messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert json.loads(rows[-1])["text"] == "补一条留言"


def test_append_message_validation(tmp_path):
    d = tmp_path / "council"
    _seed(d)
    assert ag.append_message(d, {"text": "x"})[1]["error"] == "bad_topic"
    assert ag.append_message(d, {"topic": "t"})[1]["error"] == "bad_text"


def test_frontend_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "meeting.html").read_text(encoding="utf-8")
    for key in ("/api/hq/meeting/agenda", "hqPostMsg", "hqPre", "会前议题"):
        assert key in h, key


def test_http_agenda(tmp_path, monkeypatch):
    d = tmp_path / "council"
    _seed(d)
    monkeypatch.setattr(srv, "COUNCIL_DATA", d)
    srvr = srv.build_server(port=0)
    th = threading.Thread(target=srvr.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % srvr.server_address[1]
    try:
        rep = json.loads(urllib.request.urlopen(base + "/api/hq/agenda", timeout=10).read())
        assert rep["ok"] and len(rep["topics"]) == 2
        body = json.dumps({"topic": "议题A", "text": "HTTP 留言"}).encode("utf-8")
        req = urllib.request.Request(base + "/api/hq/agenda/message", data=body,
                                     headers={"Content-Type": "application/json"})
        env = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert env["ok"] and env["message"]["text"] == "HTTP 留言"
    finally:
        srvr.shutdown()
        srvr.server_close()
