# WO-OS-04 契约测试：诸葛亮建议引擎（规则版，纯函数离线）
# 先红后绿：本文件先于 api_zhuge_brief.py 落地时必须红。
# 运行：python -m pytest test_api_zhuge_brief.py -q
import sys
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "api_zhuge_brief", Path(__file__).with_name("api_zhuge_brief.py"))
mod = importlib.util.module_from_spec(spec)

BEIJING = timezone(timedelta(hours=8))
CLOCK = datetime(2026, 10, 21, 9, 0, tzinfo=BEIJING)

GOOD_SIX = {
    "os": {"ok": True, "biz": {"near": [], "notes": []}},
    "home": {"ok": True, "calendar": {"items": []}},
    "alerts": {"ok": True, "source_health": {"sources": {
        "premarket": {"status": "ok", "as_of": "2026-10-21T08:00:00+08:00"}}}},
    "notify": {"ok": True, "errors": [], "items": [
        {"id": "n1", "title": "阻断通知", "state": "blocked", "created_at": "2026-10-21T07:00:00+08:00", "reason": "通道异常"}]},
    "tasks": {"ok": True, "tasks": [
        {"task_id": "t1", "title": "今日到期工单", "status": "open", "due": "2026-10-21", "created_at": "2026-10-20T10:00:00+08:00"},
        {"task_id": "t2", "title": "逾期工单", "status": "open", "due": "2026-10-19", "created_at": "2026-10-18T10:00:00+08:00"}]},
    "capability": {"ok": True, "production_lines": []},
}


def _load():
    spec.loader.exec_module(mod)
    return mod


def test_module_loadable_and_pure():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    # 纯函数：不修改输入
    import copy
    snapshot = copy.deepcopy(GOOD_SIX)
    m.build_brief(GOOD_SIX, CLOCK)
    assert GOOD_SIX == snapshot


def test_output_contract():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    assert out["engine"] == "rule-based"
    assert out["model_invoked"] is False
    assert out["dispatched"] is False
    assert out["generated_at"] == CLOCK.isoformat()
    assert isinstance(out["suggestions"], list) and out["suggestions"], "应至少有一条建议"
    for s in out["suggestions"]:
        # 每条建议必须带来源与生产者时点（可为 None 但键必须在）
        for key in ("id", "domain", "priority", "reason", "source", "as_of", "confidence"):
            assert key in s, f"建议缺 {key}"
        assert s["confidence"] == "rule-based"
    ids = [s["id"] for s in out["suggestions"]]
    assert len(ids) == len(set(ids)), "建议 id 不得重复"


def test_blocked_and_overdue_rank_first():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    top_ids = [s["id"] for s in out["suggestions"][:2]]
    assert "notify:n1" in top_ids and "task:t2" in top_ids, "阻断通知与逾期工单应排最前"


def test_due_today_called_out():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    s = next(x for x in out["suggestions"] if x["id"] == "task:t1")
    assert "今日" in s["reason"]


def test_missing_sources_degrade_explicitly():
    m = _load()
    out = m.build_brief({}, CLOCK)
    assert out["suggestions"] == []
    gaps = {g["source"] for g in out["gaps"]}
    assert gaps == {"os", "home", "alerts", "notify", "tasks", "capability"}, "六源全缺应逐一点名"
    for g in out["gaps"]:
        assert g["note"].startswith("待核"), "缺源说明必须以待核开头"


def test_ok_false_source_degrades():
    m = _load()
    raw = dict(GOOD_SIX, notify={"ok": False})
    out = m.build_brief(raw, CLOCK)
    assert all(s["id"] != "notify:n1" for s in out["suggestions"]), "ok:false 不得产出建议"
    assert any(g["source"] == "notify" for g in out["gaps"])


def test_stale_as_of_flags_low_freshness():
    m = _load()
    raw = dict(GOOD_SIX, alerts={"ok": True, "source_health": {"sources": {
        "premarket": {"status": "ok", "as_of": "2026-10-01T08:00:00+08:00"}}}})
    out = m.build_brief(raw, CLOCK)
    s = next(x for x in out["suggestions"] if x["id"] == "alert:premarket")
    assert s["as_of"] is not None
    assert any("新鲜度" in k or "新鲜度" in s["reason"] for k in [s["reason"]])


def test_bad_clock_raises():
    m = _load()
    try:
        m.build_brief(GOOD_SIX, "not-a-date")
    except (ValueError, TypeError):
        return
    raise AssertionError("坏时钟应抛 ValueError/TypeError")


def test_no_write_side_effects_by_design():
    m = _load()
    # 模块级不得出现写库/HTTP派单调用痕迹
    src = Path(__file__).with_name("api_zhuge_brief.py").read_text(encoding="utf-8")
    for banned in ("requests.post", "urllib.request", "subprocess", "sqlite3.connect", "open("):
        assert banned not in src, f"建议引擎源码不得出现 {banned}"
