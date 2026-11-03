# -*- coding: utf-8 -*-
"""指挥部会议 v2 P0 测试：schema/校验/列表/派单/HTTP 路由/前端标记。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from legion_command import hq_meeting as hq  # noqa: E402
from legion_command import serve as srv  # noqa: E402

SEATS = ["A0", "AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG"]


def _valid(**kw):
    body = {
        "type": "morning",
        "attendees": SEATS,
        "agenda": [{"item": "昨日决议跟进", "owner": "A0", "minutes": 3}],
        "transcript": [{"by": "A0", "text": "结论先行：先修基线。", "src": "live"}],
        "decisions": [{"decision": "本周先清 flaky 根因", "owner": "A0",
                       "due": "2026-11-01", "dissent": ["AI1"]}],
    }
    body.update(kw)
    return body


def test_save_get_list(tmp_path):
    d = tmp_path / "hq"
    code, env = hq.save_meeting(d, _valid())
    assert code == 200 and env["ok"] and env["status"] == "decided"
    mid = env["meeting_id"]
    assert (d / (mid + ".json")).is_file()
    doc = json.loads((d / (mid + ".json")).read_text(encoding="utf-8"))
    assert doc["schema_version"] == "2.0"
    assert doc["attendees"] == SEATS
    assert doc["source_verification"] == "server_recorded_client_text"
    assert doc["decisions"][0]["dissent"] == ["AI1"]
    got = hq.get_meeting(d, mid)
    assert got["ok"] and got["meeting"]["meeting_id"] == mid
    rows = hq.list_meetings(d)["meetings"]
    assert rows and rows[0]["decisions"] == 1


def test_save_rejects_bad_inputs(tmp_path):
    d = tmp_path / "hq"
    assert hq.save_meeting(d, _valid(type="x"))[1]["error"] == "bad_type"
    assert hq.save_meeting(d, _valid(attendees=["R2"]))[1]["error"] == "bad_attendee"
    assert hq.save_meeting(d, _valid(decisions=[{"decision": ""}]))[1]["error"] == "bad_decision"
    assert hq.save_meeting(d, _valid(type="operation", decisions=[]))[1]["ok"]


def test_dispatch_creates_task_request(tmp_path):
    d = tmp_path / "hq"
    _, env = hq.save_meeting(d, _valid())
    calls = []

    def submit(kind, payload, source):
        calls.append((kind, payload, source))
        return {"ok": True, "id": "req-1"}

    code, out = hq.dispatch_meeting(d, {"meeting_id": env["meeting_id"],
                                        "title": "修 flaky 根因",
                                        "order_type": "work", "owner": "GUANYU"},
                                    submit)
    assert code == 200 and out["ok"] and out["dispatch"]["id"] == "req-1"
    assert calls and calls[0][0] == "task_request" and calls[0][2] == "hq-meeting"
    doc = json.loads((d / (env["meeting_id"] + ".json")).read_text(encoding="utf-8"))
    assert doc["status"] == "dispatched"
    assert doc["orders"][-1]["title"] == "修 flaky 根因"
    assert doc["orders"][-1]["state"] == "已生成任务申请"


def test_dispatch_errors(tmp_path):
    d = tmp_path / "hq"
    assert hq.dispatch_meeting(d, {"meeting_id": "hq-20260101-000000"}, lambda *a: {})[0] == 404
    _, env = hq.save_meeting(d, _valid())
    assert hq.dispatch_meeting(d, {"meeting_id": env["meeting_id"], "title": ""},
                               lambda *a: {})[1]["error"] == "bad_title"


def test_frontend_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "meeting.html").read_text(encoding="utf-8")
    for key in ("指挥部会议", "hqPreset", "/api/hq/meeting/save",
                "/api/hq/meeting/dispatch", "HUANGZHONG", "hqDecision"):
        assert key in h, key


def test_http_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "HQ_MEETINGS_DIR", tmp_path / "hq")
    srvr = srv.build_server(port=0)
    th = threading.Thread(target=srvr.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % srvr.server_address[1]
    try:
        body = json.dumps(_valid()).encode("utf-8")
        req = urllib.request.Request(base + "/api/hq/meeting/save", data=body,
                                     headers={"Content-Type": "application/json"})
        env = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert env["ok"] and env["meeting_id"].startswith("hq-")
        assert json.loads(urllib.request.urlopen(
            base + "/api/hq/meeting/list", timeout=10).read())["ok"] is True
        got = json.loads(urllib.request.urlopen(
            base + "/api/hq/meeting/" + env["meeting_id"], timeout=10).read())
        assert got["ok"] and got["meeting"]["type"] == "morning"
    finally:
        srvr.shutdown()
        srvr.server_close()


def test_new_org_department_leads_accepted(tmp_path):
    d = tmp_path / "hq"
    leads = ["D1", "ARCH1", "DATA1", "QA1", "INTEL0", "PM1"]
    code, env = hq.save_meeting(d, _valid(attendees=leads))
    assert code == 200 and env["ok"]
    doc = json.loads((d / (env["meeting_id"] + ".json")).read_text(encoding="utf-8"))
    assert doc["attendees"] == leads
    # 未知席位仍拒绝
    assert hq.save_meeting(d, _valid(attendees=["UNKNOWN"]))[1]["error"] == "bad_attendee"
