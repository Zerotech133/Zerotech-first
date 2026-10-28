# -*- coding: utf-8 -*-
"""验收会证据 P1b 测试：gather/HTTP/纪要 acceptance 字段/前端标记。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from legion_command import hq_acceptance as ha  # noqa: E402
from legion_command import hq_meeting as hm  # noqa: E402
from legion_command import serve as srv  # noqa: E402


def _seed(tmp_path):
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps({"generated_at": "t", "summary": {"passed": 2209, "failed": 0, "error": 0}}),
                    encoding="utf-8")
    rt = tmp_path / "redteam"
    rt.mkdir()
    (rt / "redteam_1.json").write_text(json.dumps({"total": 3, "passed": 3, "pass_rate": 1.0,
                                                  "generated_at": "t"}), encoding="utf-8")
    ac = tmp_path / "accept"
    ac.mkdir()
    (ac / "acceptance_abc.json").write_text(json.dumps({
        "verdict": "PASS", "commit": {"sha": "a" * 40}, "reasons": [], "blocking": False,
        "generated_at": "t"}), encoding="utf-8")
    return base, rt, ac


def test_gather_ok(tmp_path):
    base, rt, ac = _seed(tmp_path)
    rep = ha.gather(base, rt, ac)
    assert rep["ok"] and rep["baseline"]["failed"] == 0
    assert rep["redteam"]["pass_rate"] == 1.0
    assert rep["acceptance"]["verdict"] == "PASS"


def test_gather_missing_fail_visible(tmp_path):
    rep = ha.gather(tmp_path / "n.json", tmp_path / "n_rt", tmp_path / "n_ac")
    assert rep["ok"] and rep["warnings"]
    assert rep["baseline"]["ok"] is False and rep["redteam"]["ok"] is False


def test_meeting_saves_acceptance(tmp_path):
    d = tmp_path / "hq"
    code, env = hm.save_meeting(d, {
        "type": "acceptance", "attendees": ["A0", "HUANGZHONG"],
        "decisions": [{"decision": "验收结论[PASS]：满足放行", "owner": "HUANGZHONG"}],
        "acceptance": [{"verdict": "PASS", "commit": "abc123", "report": "acceptance_x.md"}],
    })
    assert code == 200 and env["ok"]
    doc = json.loads((d / (env["meeting_id"] + ".json")).read_text(encoding="utf-8"))
    assert doc["acceptance"][0]["verdict"] == "PASS"
    assert doc["decisions"][0]["owner"] == "HUANGZHONG"


def test_frontend_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "meeting.html").read_text(encoding="utf-8")
    for key in ("/api/hq/meeting/acceptance/evidence", "hqVerdict", "hqEvidence", "loadHqEvidence"):
        assert key in h, key


def test_http_evidence(tmp_path, monkeypatch):
    base, rt, ac = _seed(tmp_path)
    monkeypatch.setattr(srv, "_BASELINE_FILE", base)
    monkeypatch.setattr(srv, "_REDTEAM_DIR", rt)
    monkeypatch.setattr(srv, "_ACCEPTANCE_DIR", ac)
    srvr = srv.build_server(port=0)
    th = threading.Thread(target=srvr.serve_forever, daemon=True)
    th.start()
    try:
        env = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/api/hq/meeting/acceptance/evidence" % srvr.server_address[1],
            timeout=10).read())
        assert env["ok"] and env["acceptance"]["verdict"] == "PASS"
    finally:
        srvr.shutdown()
        srvr.server_close()
