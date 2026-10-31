# -*- coding: utf-8 -*-
"""黄忠 W1 单测：样例文档在位 + 每日观察/周报工具 + 日更验收事件联动。"""
import importlib.util
import json
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _noop_emit(**_kw):
    """测试默认注入：不写真实事件文件（portal/data/dev_events.jsonl）。"""
    return {"ok": True}


def _fake_verdict(v="PASS", failed=0, rate=1.0):
    return {
        "commit": {"sha": "a" * 40}, "verdict": v, "blocking": False,
        "reasons": [] if v == "PASS" else ["基线失败"],
        "evidence": {"baseline": {"failed": failed},
                     "redteam": {"pass_rate": rate}},
        "report_md": "x.md",
    }


def test_w1_doc_exists():
    root = _TOOLS.parent
    p = root / "docs" / "plans" / "2026-10-28_黄忠W1验收样例与批改标准_v1.md"
    assert p.is_file()
    t = p.read_text(encoding="utf-8")
    for key in ("验收判据样例", "七检解读", "回滚点样例", "Rubric", "批改标准"):
        assert key in t


def test_daily_appends(tmp_path):
    d = _load("huangzhong_daily")
    rep = d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path,
                      emit_fn=_noop_emit)
    assert rep["verdict"] == "PASS"
    lines = (tmp_path / "daily.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["commit"] == "a" * 12


def test_daily_multiple(tmp_path):
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path, emit_fn=_noop_emit)
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=2), out_dir=tmp_path,
                emit_fn=_noop_emit)
    lines = (tmp_path / "daily.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_weekly_aggregates(tmp_path):
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path, emit_fn=_noop_emit)
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=1), out_dir=tmp_path,
                emit_fn=_noop_emit)
    d.run_daily(accept_fn=lambda c: _fake_verdict("OBSERVE"), out_dir=tmp_path,
                emit_fn=_noop_emit)
    rep = d.weekly(days=7, out_dir=tmp_path)
    assert rep["runs"] == 3
    assert rep["counts"] == {"PASS": 1, "REJECT": 1, "OBSERVE": 1}
    assert Path(rep["report_md"]).exists()
    assert "观察周报" in Path(rep["report_md"]).read_text(encoding="utf-8")


def test_weekly_empty(tmp_path):
    d = _load("huangzhong_daily")
    rep = d.weekly(days=7, out_dir=tmp_path)
    assert rep["runs"] == 0 and rep["pass_rate"] == 0.0


def test_daily_emits_acceptance_done(tmp_path):
    """日更落盘后必须发 acceptance_done 事件（注入 fake emit，不写真实事件文件）。"""
    d = _load("huangzhong_daily")
    events = []

    def fake_emit(**kw):
        events.append(kw)
        return {"ok": True}

    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path, emit_fn=fake_emit)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "acceptance_done"
    assert ev["task"] == "HUANGZHONG-DAILY"
    assert ev["agent"] == "HUANGZHONG", ev
    assert ev["ide"] == "HUANGZHONG-DAILY", ev
    detail = ev["detail"]
    for key in ("verdict=PASS", "commit=" + "a" * 12, "baseline_failed=0",
                "redteam=1.0", "report=x.md"):
        assert key in detail, (key, detail)


def test_daily_emit_fail_open(tmp_path):
    """事件层异常不得影响日更主流程：daily.jsonl 照写、结果照返回。"""
    d = _load("huangzhong_daily")

    def boom(**_kw):
        raise RuntimeError("events down")

    rep = d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=2),
                      out_dir=tmp_path, emit_fn=boom)
    assert rep["verdict"] == "REJECT"
    lines = (tmp_path / "daily.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["verdict"] == "REJECT"


def test_daily_appends_events_jsonl(tmp_path):
    """Phase2 事件面：验收后 events.jsonl 追加一行 acceptance 事件（append-only）。"""
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=2),
                out_dir=tmp_path, emit_fn=_noop_emit)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["kind"] == "acceptance"
    assert ev["verdict"] == "REJECT"
    assert ev["commit"] == "a" * 12
    assert ev["baseline_age_h"] is None
    # 再跑一次 → 追加第二行（不覆盖）
    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path,
                emit_fn=_noop_emit)
    assert len((tmp_path / "events.jsonl").read_text(
        encoding="utf-8").splitlines()) == 2


# ── B（飞书 REJECT 推送，默认关闭）────────────────────────────────────────

def _push_env(tmp_path, monkeypatch, enabled="1", drill=None):
    alerts = tmp_path / "alerts"
    monkeypatch.setenv("HUANGZHONG_ALERTS_DIR", str(alerts))
    monkeypatch.setenv("HUANGZHONG_EVENT_PUSH", enabled)
    if drill is not None:
        monkeypatch.setenv("HUANGZHONG_DRILL", drill)
    else:
        monkeypatch.delenv("HUANGZHONG_DRILL", raising=False)
    return alerts


def _run_reject(d, tmp_path):
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=2),
                out_dir=tmp_path, emit_fn=_noop_emit)


def test_push_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HUANGZHONG_EVENT_PUSH", raising=False)
    alerts = tmp_path / "alerts"
    monkeypatch.setenv("HUANGZHONG_ALERTS_DIR", str(alerts))
    d = _load("huangzhong_daily")
    _run_reject(d, tmp_path)
    assert not alerts.exists() or not list(alerts.glob("*.flag"))


def test_reject_push_writes_flag_once(tmp_path, monkeypatch):
    alerts = _push_env(tmp_path, monkeypatch, "1")
    d = _load("huangzhong_daily")
    _run_reject(d, tmp_path)
    flags = list(alerts.glob("huangzhong_reject_*.flag"))
    assert len(flags) == 1
    txt = flags[0].read_text(encoding="utf-8")
    for key in ("黄忠验收 REJECT", "commit=" + "a" * 12, "reasons=基线失败",
                "baseline_failed=2"):
        assert key in txt, (key, txt)
    # 同 sha 再跑 → 去重（不新增 flag）
    _run_reject(d, tmp_path)
    assert len(list(alerts.glob("huangzhong_reject_*.flag"))) == 1


def test_pass_does_not_push(tmp_path, monkeypatch):
    alerts = _push_env(tmp_path, monkeypatch, "1")
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict("PASS"), out_dir=tmp_path,
                emit_fn=_noop_emit)
    assert not list(alerts.glob("*.flag"))


def test_drill_marker_in_flag(tmp_path, monkeypatch):
    alerts = _push_env(tmp_path, monkeypatch, "1", drill="1")
    d = _load("huangzhong_daily")
    _run_reject(d, tmp_path)
    flags = list(alerts.glob("huangzhong_reject_*.flag"))
    assert len(flags) == 1 and "【演练】" in flags[0].read_text(encoding="utf-8")
