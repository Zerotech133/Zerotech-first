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

# WO-OS-20 第七源 pushflow：当日通知投递成败统计（真实账本口径）
PUSHFLOW_HIGH = {
    "ok": True, "today": 276, "attempted": 276, "ok_count": 124, "fail_count": 152,
    "fail_rate": 0.55, "policy_suppressed": 0,
    "channels": {"openapi": {"ok": 100, "fail": 150},
                 "lark-cli": {"ok": 24, "fail": 2}},
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
    assert gaps == {"os", "home", "alerts", "notify", "tasks", "capability",
                    "pushflow"}, "七源全缺应逐一点名（WO-OS-20 扩容）"
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


# ── WO-OS-20 第七源 pushflow：当日实际失败率（不是历史失败簇）──────────
def _pushflow_case(rate, fail_count=11, today=20):
    return dict(GOOD_SIX, pushflow={
        "ok": True, "today": today, "ok_count": today - fail_count,
        "fail_count": fail_count, "fail_rate": rate,
        "channels": {"openapi": {"ok": today - fail_count, "fail": fail_count}},
    })


def _pick(out, sid="pushflow:daily_rate"):
    return next((s for s in out["suggestions"] if s["id"] == sid), None)


def test_pushflow_high_rate_is_priority_1_with_percent():
    m = _load()
    out = m.build_brief(dict(GOOD_SIX, pushflow=PUSHFLOW_HIGH), CLOCK)
    s = _pick(out)
    assert s is not None, "fail_rate=0.55 必须产出 pushflow 建议"
    assert s["priority"] == 1, "≥50% 应为 P1"
    assert s["domain"] == "通知"
    assert s["confidence"] == "rule-based"
    assert "今日真实投递失败率 55%" in s["reason"]
    assert "失败 152/尝试 276" in s["reason"], "分母必须是真实投递尝试数"
    assert "不是历史失败簇" in s["reason"], "必须区分当日实际失败率与历史失败簇"
    assert s["source"] == "/api/zhuge/brief.pushflow"
    assert "pushflow.jsonl" in s["reason"], "建议需说明原始来源"


def test_pushflow_policy_suppressed_excluded_and_noted():
    """WO-OS-20-FIX：静默窗口策略抑制既不算成功也不算失败，只在文案附注。

    真实口径（2026-10-21 实测账本）：总行 282、策略抑制 103、真实尝试 179、
    失败 49 → 真实失败率 27.4%（若把抑制算进去会虚报 53.9%）。
    """
    m = _load()
    stats = {"ok": True, "today": 282, "attempted": 179, "ok_count": 130,
             "fail_count": 49, "fail_rate": 0.274, "policy_suppressed": 103,
             "channels": {"openapi": {"ok": 64, "fail": 46},
                          "lark-cli": {"ok": 65, "fail": 3}}}
    out = m.build_brief(dict(GOOD_SIX, pushflow=stats), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 3, "27.4% 属 P3 区间"
    assert "失败 49/尝试 179" in s["reason"], "分母不得包含 103 条抑制行"
    assert "103 条静默窗口策略抑制" in s["reason"]
    assert "不计入失败" in s["reason"]


def test_pushflow_mid_rate_is_priority_2():
    m = _load()
    out = m.build_brief(_pushflow_case(0.3), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 2, "≥30% 应为 P2"


def test_pushflow_low_rate_emits_no_suggestion():
    m = _load()
    out = m.build_brief(_pushflow_case(0.05), CLOCK)
    assert _pick(out) is None, "<10% 视为正常，不产建议"
    assert all(g["source"] != "pushflow" for g in out["gaps"]), "源可用时不得进 gaps"


def test_pushflow_missing_goes_to_gaps_pending():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)  # 无 pushflow 键
    gap = next((g for g in out["gaps"] if g["source"] == "pushflow"), None)
    assert gap is not None, "缺 pushflow 必须进 gaps"
    assert gap["note"].startswith("待核")
    assert _pick(out) is None, "缺源不得产建议"

