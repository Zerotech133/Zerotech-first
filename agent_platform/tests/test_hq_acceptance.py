# -*- coding: utf-8 -*-
"""验收会证据 P1b 测试：gather/HTTP/纪要 acceptance 字段/前端标记 + 日更验收联动。"""
import datetime as dt
import json
import sys
import threading
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

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


def _daily_rows():
    """近 7 天 3 条（PASS/REJECT/OBSERVE）+ 1 条窗口外 PASS，latest=最近一条 OBSERVE。"""
    now = dt.datetime.now().astimezone()
    return now, [
        {"ts": (now - dt.timedelta(days=3)).isoformat(timespec="seconds"),
         "commit": "a" * 12, "verdict": "PASS", "baseline_failed": 0,
         "redteam_rate": 1.0, "report": "x.md"},
        {"ts": (now - dt.timedelta(days=2)).isoformat(timespec="seconds"),
         "commit": "b" * 12, "verdict": "REJECT", "baseline_failed": 2,
         "redteam_rate": 0.5, "report": "y.md"},
        {"ts": (now - dt.timedelta(days=1)).isoformat(timespec="seconds"),
         "commit": "c" * 12, "verdict": "OBSERVE", "baseline_failed": 0,
         "redteam_rate": 1.0, "report": "z.md"},
        {"ts": (now - dt.timedelta(days=30)).isoformat(timespec="seconds"),
         "commit": "old" * 4, "verdict": "PASS", "baseline_failed": 0,
         "redteam_rate": 1.0, "report": "old.md"},
    ]


def test_gather_ok(tmp_path):
    base, rt, ac = _seed(tmp_path)
    rep = ha.gather(base, rt, ac)
    assert rep["ok"] and rep["baseline"]["failed"] == 0
    assert rep["redteam"]["pass_rate"] == 1.0
    assert rep["acceptance"]["verdict"] == "PASS"
    # 缺日更文件：daily 块 fail-visible，但既有字段与 warnings 语义不变
    assert rep["daily"] == {"ok": False, "error": "daily_missing"}
    assert not any("日更" in w for w in rep["warnings"])


def test_gather_missing_fail_visible(tmp_path):
    rep = ha.gather(tmp_path / "n.json", tmp_path / "n_rt", tmp_path / "n_ac")
    assert rep["ok"] and rep["warnings"]
    assert rep["baseline"]["ok"] is False and rep["redteam"]["ok"] is False
    # 日更缺失只标 ok=False，绝不进 warnings 硬失败
    assert rep["daily"] == {"ok": False, "error": "daily_missing"}
    assert not any("日更" in w for w in rep["warnings"])


def test_gather_daily_present(tmp_path):
    """日更验收联动：近 7 天 runs/计数 + latest（窗口外行只进 latest 候选不影响计数）。"""
    base, rt, ac = _seed(tmp_path)
    now, rows = _daily_rows()
    (ac / "daily.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    rep = ha.gather(base, rt, ac)
    daily = rep["daily"]
    assert daily["ok"] is True
    assert daily["window_days"] == 7
    assert daily["runs"] == 3
    assert daily["counts"] == {"PASS": 1, "REJECT": 1, "OBSERVE": 1}
    assert daily["latest"]["verdict"] == "OBSERVE"
    assert daily["latest"]["commit"] == "c" * 12
    assert daily["latest"]["ts"] == rows[2]["ts"]
    assert not any("日更" in w for w in rep["warnings"])


def test_gather_daily_tolerates_bad_lines(tmp_path):
    """daily.jsonl 坏行/无 ts 行跳过，不把整块打成不可用。"""
    base, rt, ac = _seed(tmp_path)
    now = dt.datetime.now().astimezone()
    lines = [
        "not-json-at-all",
        json.dumps({"verdict": "PASS"}),  # 无 ts：不计数、不参与 latest
        json.dumps({"ts": now.isoformat(timespec="seconds"), "verdict": "PASS",
                    "commit": "d" * 12, "baseline_failed": 0, "redteam_rate": 1.0,
                    "report": "d.md"}),
    ]
    (ac / "daily.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    daily = ha.gather(base, rt, ac)["daily"]
    assert daily["ok"] is True
    assert daily["runs"] == 1 and daily["counts"]["PASS"] == 1
    assert daily["latest"]["commit"] == "d" * 12


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
        env = json.loads(safe_urlopen(
            "http://127.0.0.1:%d/api/hq/meeting/acceptance/evidence" % srvr.server_address[1],
            timeout=10).read())
        assert env["ok"] and env["acceptance"]["verdict"] == "PASS"
    finally:
        srvr.shutdown()
        srvr.server_close()
