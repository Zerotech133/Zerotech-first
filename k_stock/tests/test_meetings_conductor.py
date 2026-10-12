# -*- coding: utf-8 -*-
"""K-STOCK-149 · meetings_conductor 编排测试（全程 monkeypatch，零触网）。"""
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "paper"))

import meetings_conductor as cond  # noqa: E402
import meeting  # noqa: E402
import plan_review  # noqa: E402  # K-STOCK-163：conductor 经同模块做意图摘要增强
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_auto_plan_review(monkeypatch):
    """K-STOCK-168：审批单自动化在测试里缺省关闭（防存量 ok 用例真调
    review_plan 触网）；审批单相关用例在用例体内 setenv("1") 显式覆盖。"""
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "0")


def _wed_0900():
    return datetime(2026, 10, 14, 9, 0)  # 周三


def test_kind_auto_by_hour():
    assert cond.kind_auto(datetime(2026, 10, 14, 9, 5)) == "pre_open"
    assert cond.kind_auto(datetime(2026, 10, 14, 15, 15)) == "post_close"
    assert cond.kind_auto(datetime(2026, 10, 14, 12, 0)) == "post_close"


def test_weekend_skip(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list",
                        lambda: [{"manager": "laotian_pm", "source": "qd", "status": "active"}])
    code, results = cond.run("pre_open", now=datetime(2026, 10, 11, 9, 0))  # 周日
    assert code == 0 and results == []
    assert "SKIP 周末" in capsys.readouterr().out


def test_no_active_hires_skip(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [])
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results == []
    assert "无 active 雇佣" in capsys.readouterr().out


def test_dedup_and_ok_flow(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"},
        {"manager": "bull_desk", "source": "qd", "status": "active"},   # 待实开
        {"manager": "laotian_pm", "source": "qd", "status": "active"}, # 重复对滤掉
    ])
    # laotian_pm 今日已开 → 去重跳过；bull_desk 无今日纪要 → 实际召开
    monkeypatch.setattr(cond, "minutes_list", lambda mid: (
        [{"kind": "pre_open", "created_at": "2026-10-14 09:00:00"}] if mid == "laotian_pm" else []))
    calls = []

    def fake_hold(mid, source, kind, context="", channel=None):
        calls.append((mid, source, kind))
        return ({"title": f"{mid} 盘前会", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0
    assert calls == [("bull_desk", "qd", "pre_open")]          # 只有去重外的实开
    st = {r["mid"]: r["status"] for r in results}
    assert st["laotian_pm"] == "skipped" and st["bull_desk"] == "ok"
    out = capsys.readouterr().out
    assert "账本去重" in out and "SUMMARY" in out


def test_failure_isolated_and_exit2(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "a_pm", "source": "qd", "status": "active"},
        {"manager": "b_pm", "source": "qd", "status": "active"},
    ])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    ok_holder = {"n": 0}

    def fake_hold(mid, source, kind, context="", channel=None):
        if mid == "a_pm":
            raise meeting.MeetingError("LLM 生成纪要失败（模拟）")
        ok_holder["n"] += 1
        return ({"title": "t", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 2                                  # 有失败 → exit 2
    assert ok_holder["n"] == 1                        # b_pm 不被 a_pm 阻断
    st = {r["mid"]: r["status"] for r in results}
    assert st["a_pm"] == "failed" and st["b_pm"] == "ok"
    assert "1 失败" in capsys.readouterr().out


def test_context_compose_tolerates_missing(monkeypatch):
    monkeypatch.setattr(cond, "STRATEGIST_LATEST", cond.HERE.parent / "data" / "__no_such__.json")
    ctx = cond.build_context(_wed_0900())
    assert "日期：" in ctx                             # 缺文件不炸，仅日期行


# ---- K-STOCK-163 · build_context 组合意图清单摘要增强（无产物=原文案，向后兼容） ----

def test_build_context_pf_source_appends_digest(monkeypatch):
    """source 为组合且有当日意图 → context 追加摘要块。"""
    monkeypatch.setattr(cond, "STRATEGIST_LATEST", cond.HERE.parent / "data" / "__no_such__.json")
    monkeypatch.setattr(plan_review, "collect_context",
                        lambda pf, now=None: "【今日买入意图清单摘要】组合 pf_001")
    ctx = cond.build_context(_wed_0900(), source="pf_001")
    assert "日期：" in ctx
    assert "【今日买入意图清单摘要】" in ctx and "pf_001" in ctx


def test_build_context_no_artifact_degrades_to_original(monkeypatch):
    """无产物（collect_context 空串）→ 输出与旧版完全一致（只有日期行）。"""
    monkeypatch.setattr(cond, "STRATEGIST_LATEST", cond.HERE.parent / "data" / "__no_such__.json")
    monkeypatch.setattr(plan_review, "collect_context", lambda pf, now=None: "")
    ctx = cond.build_context(_wed_0900(), source="pf_001")
    assert ctx.strip() == f"日期：{_wed_0900().strftime('%Y-%m-%d %A')}"
    assert cond.build_context(_wed_0900()) == ctx            # 不传 source 同款


def test_build_context_collect_failure_silent(monkeypatch):
    """collect_context 抛错（如模块级故障）→ 静默为空，会议线不受阻。"""
    monkeypatch.setattr(cond, "STRATEGIST_LATEST", cond.HERE.parent / "data" / "__no_such__.json")

    def boom(pf, now=None):
        raise RuntimeError("模拟故障")
    monkeypatch.setattr(plan_review, "collect_context", boom)
    ctx = cond.build_context(_wed_0900(), source="pf_001")
    assert "日期：" in ctx and "意图清单" not in ctx


def test_run_passes_source_context_to_meeting(monkeypatch, tmp_path, capsys):
    """run() 召开会议时把 source 传给 build_context（context 含意图摘要）。"""
    _write_subs(tmp_path, monkeypatch, [{"id": "pf_001", "name": "我的组合"}])
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_001", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    monkeypatch.setattr(plan_review, "collect_context",
                        lambda pf, now=None: "【今日买入意图清单摘要】组合 pf_001")
    got = {}

    def fake_hold(mid, source, kind, context="", channel=None):
        got["context"] = context
        return ({"title": "t", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results[0]["status"] == "ok"
    assert "【今日买入意图清单摘要】" in got["context"]


# ---- K-STOCK-158 · S-g 订阅服务开关（缺省全开，显式 false 才关） ----

import json  # noqa: E402


def _write_subs(tmp_path, monkeypatch, portfolios):
    p = tmp_path / "strategy_subscriptions.json"
    p.write_text(json.dumps({"portfolios": portfolios}), encoding="utf-8")
    monkeypatch.setattr(cond, "STRATEGY_SUBSCRIPTIONS", p)


def test_services_missing_field_defaults_on(monkeypatch, tmp_path, capsys):
    # 缺 services 字段（存量形态）→ 照常开会，行为零变化
    _write_subs(tmp_path, monkeypatch, [{"id": "qd", "name": "老订阅"}])
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    calls = []
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        calls.append((mid, source, kind)) or
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and calls == [("laotian_pm", "qd", "pre_open")]
    assert results[0]["status"] == "ok"
    assert "客户未订阅" not in capsys.readouterr().out


def test_pre_open_report_false_skips_pre_open_but_post_close_on(monkeypatch, tmp_path, capsys):
    # 显式 pre_open_report=false → 盘前跳过、盘后照开
    _write_subs(tmp_path, monkeypatch, [
        {"id": "qd", "services": {"pre_open_report": False, "post_close_report": True}}])
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    calls = []

    def fake_hold(mid, source, kind, context="", channel=None):
        calls.append((mid, source, kind))
        return ({"title": "t", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and calls == []
    assert results[0]["status"] == "skipped"
    assert "SKIP laotian_pm×qd pre_open 客户未订阅该报告服务" in capsys.readouterr().out
    code, results = cond.run("post_close", now=datetime(2026, 10, 14, 15, 15))
    assert code == 0 and calls == [("laotian_pm", "qd", "post_close")]
    assert results[0]["status"] == "ok"


def test_services_explicit_true_holds(monkeypatch, tmp_path, capsys):
    # 显式双 true（pf_001/pf_002 锚定形态）→ 照常开会
    _write_subs(tmp_path, monkeypatch, [
        {"id": "qd", "services": {"pre_open_report": True, "post_close_report": True}}])
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    calls = []
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        calls.append((mid, source, kind)) or
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and calls == [("laotian_pm", "qd", "pre_open")]
    assert results[0]["status"] == "ok"


def test_subscriptions_file_missing_all_on_with_warning(monkeypatch, tmp_path, capsys):
    # 订阅清单缺失 → 全开兜底 + stderr 警告（不阻断会议线）
    monkeypatch.setattr(cond, "STRATEGY_SUBSCRIPTIONS", tmp_path / "__no_such__.json")
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    calls = []
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        calls.append((mid, source, kind)) or
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and calls == [("laotian_pm", "qd", "pre_open")]
    captured = capsys.readouterr()
    assert "WARN" in captured.err and "订阅清单不可读" in captured.err


def test_hired_source_not_in_subscriptions_not_killed(monkeypatch, tmp_path, capsys):
    # 雇佣的 source 不在订阅清单（如 qd 直订信号源）→ 视为开启，不误杀
    _write_subs(tmp_path, monkeypatch, [{"id": "pf_001", "name": "我的组合"}])
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    calls = []
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        calls.append((mid, source, kind)) or
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and calls == [("laotian_pm", "qd", "pre_open")]
    assert results[0]["status"] == "ok"


# ---- K-STOCK-168 · 盘前会后自动生成经理审批单（KSTOCK_PLAN_REVIEW=0 保险丝） ----

def _fake_review_calls(monkeypatch, raise_exc=None):
    """挂 plan_review.review_plan 桩，记录调用；raise_exc 非空则抛。"""
    calls = []

    def fake_review(pf_id, manager_id, source=None):
        calls.append((pf_id, manager_id, source))
        if raise_exc is not None:
            raise raise_exc
        return {"date": "20261014", "pf_id": pf_id,
                "provider": "glm-5.3-flash", "approved": [], "rejected": []}

    monkeypatch.setattr(plan_review, "review_plan", fake_review)
    return calls


def test_pre_open_ok_triggers_review_plan(monkeypatch, capsys):
    """pre_open ok → review_plan 被调且参数正确（pf_id=source, manager_id=mid）。"""
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_002", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    calls = _fake_review_calls(monkeypatch)
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "1")
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results[0]["status"] == "ok"
    assert calls == [("pf_002", "laotian_pm", None)]
    out = capsys.readouterr().out
    assert "OK 审批单 pf_002 → 20261014_pf_002.json (provider=glm-5.3-flash)" in out


def test_review_failure_warns_but_still_exit0(monkeypatch, capsys):
    """审批单失败 → WARN 留痕，但会议成功主语义不变（仍 exit 0）。"""
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_002", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    _fake_review_calls(monkeypatch,
                       raise_exc=plan_review.PlanReviewError("LLM 审查失败（模拟）"))
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "1")
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results[0]["status"] == "ok"
    out = capsys.readouterr().out
    assert "WARN 审批单生成失败 pf_002" in out and "SUMMARY" in out


def test_env_switch_off_disables_review(monkeypatch, capsys):
    """KSTOCK_PLAN_REVIEW=0 → 不调 review_plan（保险丝）。"""
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_002", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    calls = _fake_review_calls(monkeypatch)
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "0")
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results[0]["status"] == "ok"   # 会议照开
    assert calls == []
    assert "审批单" not in capsys.readouterr().out


def test_post_close_does_not_trigger_review(monkeypatch, capsys):
    """审批单属盘前语义 → post_close ok 也不调 review_plan。"""
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_002", "status": "active"}])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    monkeypatch.setattr(meeting, "hold_meeting",
                        lambda mid, source, kind, context="", channel=None:
                        ({"title": "t", "channel": "site"}, "glm-5.3-flash"))
    calls = _fake_review_calls(monkeypatch)
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "1")
    code, results = cond.run("post_close", now=datetime(2026, 10, 14, 15, 15))
    assert code == 0 and results[0]["status"] == "ok"
    assert calls == []
    assert "审批单" not in capsys.readouterr().out


def test_skipped_and_dry_do_not_trigger_review(monkeypatch, tmp_path, capsys):
    """去重跳过（skipped）与 dry-run（dry）都不调 review_plan。"""
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "pf_002", "status": "active"},
        {"manager": "bull_desk", "source": "pf_002", "status": "active"}])
    # laotian_pm 今日已开 → skipped；bull_desk 走 dry-run
    monkeypatch.setattr(cond, "minutes_list", lambda mid: (
        [{"kind": "pre_open", "created_at": "2026-10-14 09:00:00"}]
        if mid == "laotian_pm" else []))
    calls = _fake_review_calls(monkeypatch)
    monkeypatch.setenv("KSTOCK_PLAN_REVIEW", "1")
    code, results = cond.run("pre_open", dry_run=True, now=_wed_0900())
    st = {r["mid"]: r["status"] for r in results}
    assert code == 0
    assert st["laotian_pm"] == "skipped" and st["bull_desk"] == "dry"
    assert calls == []
    assert "审批单" not in capsys.readouterr().out
