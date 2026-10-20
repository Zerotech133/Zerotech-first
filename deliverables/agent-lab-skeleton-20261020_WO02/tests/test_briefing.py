"""工位G S2 测试：诸葛简报汇编 compile_briefing（v0 契约）。

被测对象为纯函数（无 IO 无网络），不实例化 BaseAgent，
故无需清理 Prometheus 注册表。
"""
from __future__ import annotations

from datetime import datetime

from roles.zhuge_liang import (
    BRIEFING_ATTENTION_RULES,
    STAGNATION_MIN_SPAN_S,
    compile_briefing,
)


def _sp(platform="p1", doing="推演中", progress="50%", artifact="",
        ts=1000.0):
    """构造状态点 dict（字段同 StatusPoint）。"""
    return {"platform": platform, "doing": doing, "progress": progress,
            "artifact": artifact, "ts": ts}


# ---- 1) 正常汇总 ----

def test_normal_summary():
    goal = {"goal_id": "G1", "platforms": ["p1", "p2"]}
    points = [_sp("p1", doing="回测", progress="50%", ts=100.0),
              _sp("p2", doing="选股", progress="20%", ts=200.0)]
    issues = [{"status": "open", "subject": "端口缺失"},
              {"status": "resolved", "subject": "告警未接"}]
    tasks = [{"state": "doing"}, {"state": "created"}, {"state": "done"}]

    b = compile_briefing(goal, points, issues, tasks)

    assert b["goal"] == "G1"
    # generated_at 为可解析的 iso8601
    datetime.fromisoformat(b["generated_at"])
    assert [p["platform"] for p in b["platforms"]] == ["p1", "p2"]
    assert b["platforms"][0] == {"platform": "p1", "doing": "回测",
                                 "progress": "50%", "artifact": ""}
    assert b["issues_open"] == 1
    assert b["issues_resolved"] == 1
    assert b["tasks_by_state"] == {"doing": 1, "created": 1, "done": 1}
    # open 问题单按规则进 attention；resolved 不进
    assert b["attention"] == ["问题单未闭环：端口缺失"]
    assert b["data_gaps"] == []


# ---- 2) 空输入 ----

def test_empty_inputs():
    b = compile_briefing({}, [], [], [])
    assert b["goal"] == ""
    assert b["platforms"] == []
    assert b["issues_open"] == 0
    assert b["issues_resolved"] == 0
    assert b["tasks_by_state"] == {}
    assert b["attention"] == []
    # goal 缺失如实进 data_gaps，不造数
    assert any("goal 缺失" in g for g in b["data_gaps"])


# ---- 3) progress 停滞进 attention（阈值表驱动） ----

def test_progress_stagnation_enters_attention():
    span = STAGNATION_MIN_SPAN_S + 10
    points = [_sp("p1", progress="30%", ts=0.0),
              _sp("p1", progress="30%", ts=span)]
    b = compile_briefing({"goal_id": "G1"}, points, [], [])
    assert any("停滞" in a and "p1" in a for a in b["attention"])

    # 跨度不足阈值 → 不列入停滞
    b2 = compile_briefing(
        {"goal_id": "G1"},
        [_sp("p1", progress="30%", ts=0.0),
         _sp("p1", progress="30%", ts=10.0)],
        [], [])
    assert not any("停滞" in a for a in b2["attention"])


# ---- 4) blocked 任务计数并进 attention ----

def test_blocked_tasks_counted():
    tasks = [{"state": "blocked"}, {"state": "blocked"}, {"state": "doing"}]
    b = compile_briefing({"goal_id": "G1"}, [], [], tasks)
    assert b["tasks_by_state"]["blocked"] == 2
    assert b["tasks_by_state"]["doing"] == 1
    assert any("blocked" in a for a in b["attention"])


# ---- 5) 重复项去重 ----

def test_duplicate_status_points_dedup():
    points = [_sp("p1", doing="回测", progress="50%", ts=100.0),
              _sp("p1", doing="回测", progress="50%", ts=100.0),  # 完全重复
              _sp("p1", doing="回测", progress="60%", ts=200.0)]  # 更新
    b = compile_briefing({"goal_id": "G1"}, points, [], [])
    # 平台投影只留一条，且取 ts 最新
    assert len(b["platforms"]) == 1
    assert b["platforms"][0]["progress"] == "60%"
    # 重复点不充当停滞样本（progress 有变化，且去重后样本不足）
    assert not any("停滞" in a for a in b["attention"])


def test_duplicate_open_issue_attention_dedup():
    issues = [{"status": "open", "subject": "端口缺失"},
              {"status": "open", "subject": "端口缺失"}]
    b = compile_briefing({"goal_id": "G1"}, [], issues, [])
    assert b["issues_open"] == 2  # 计数按台账条目，如实
    # attention 要点去重：同主题只列一次
    matched = [a for a in b["attention"] if "端口缺失" in a]
    assert len(matched) == 1


# ---- 6) data_gaps 如实（缺失字段不造数） ----

def test_data_gaps_reported_honestly():
    points = [{"platform": "", "doing": "x", "progress": "10%",
               "artifact": "", "ts": 1.0},
              _sp("p1", progress="", ts=2.0)]
    issues = [{"subject": "无状态字段"}]
    tasks = [{"task_id": "t1"}]  # 缺 state

    b = compile_briefing({"goal_id": "G1"}, points, issues, tasks)

    assert any("platform 缺失" in g for g in b["data_gaps"])
    assert any("progress 缺失" in g for g in b["data_gaps"])
    assert any("status 缺失" in g for g in b["data_gaps"])
    assert any("state 缺失" in g for g in b["data_gaps"])
    # 未知/缺失状态不计数，不造数
    assert b["issues_open"] == 0
    assert b["issues_resolved"] == 0
    assert b["tasks_by_state"] == {}


# ---- 7) attention 规则表可配置 ----

def test_attention_rules_configurable():
    BRIEFING_ATTENTION_RULES["task_blocked"] = False
    try:
        b = compile_briefing({"goal_id": "G1"}, [], [],
                             [{"state": "blocked"}])
        assert not any("blocked" in a for a in b["attention"])
        # 计数不受规则开关影响
        assert b["tasks_by_state"]["blocked"] == 1
    finally:
        BRIEFING_ATTENTION_RULES["task_blocked"] = True


# ---- 8) 字符串 ts 透传比对，不做日期解析 ----

def test_string_ts_passthrough():
    points = [_sp("p1", progress="30%", ts="2026-10-20T09:00:00+00:00"),
              _sp("p1", progress="30%", ts="2026-10-20T10:00:00+00:00")]
    b = compile_briefing({"goal_id": "G1"}, points, [], [])
    # 字符串 ts 无法量化跨度 → 仍列入但如实注明
    assert any("停滞" in a and "跨度未量化" in a for a in b["attention"])
    # 字符串 ts 字典序比较：取"最新"一条投影
    assert b["platforms"][0]["progress"] == "30%"
