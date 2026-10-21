# -*- coding: utf-8 -*-
"""WO-LEGION-S34 编制完整性审计测试。

承重点：缺口必须被如实检出（没有档案的部门/主官不能放过）、口径不许把主官误判为编外、
审计只读（真源字节不变）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_audit as A  # noqa: E402


def _raw_org():
    return json.loads((_PLATFORM / "registry" / "org.json").read_text(encoding="utf-8"))


def _expect_depts_without_profile():
    """独立口径：自己从 org.json 数「名单有人但零档案」的部门，不借用审计代码。"""
    return {d["dept"] for d in _raw_org()["departments"]
            if d.get("agents") and not d.get("agent_profiles")}


def _expect_leads_without_profile():
    out = set()
    for d in _raw_org()["departments"]:
        lead = d.get("lead")
        prof = [p.get("agent_id") for p in (d.get("agent_profiles") or [])]
        if lead and lead not in prof:
            out.add(d["dept"])
    return out


def _expect_ghost_total():
    n = 0
    for d in _raw_org()["departments"]:
        ids = [str(x) for x in (d.get("agents") or [])]
        prof = [str(p.get("agent_id")) for p in (d.get("agent_profiles") or [])]
        n += sum(1 for i in ids if i not in prof)
    return n


def _codes(rep):
    return [f["code"] for f in rep["findings"]]


def _by_code(rep, code):
    return [f for f in rep["findings"] if f["code"] == code]


# ---------------------------------------------------------------- 真实盘面

def test_audit_real_summary():
    rep = A.audit(_PLATFORM)
    assert rep["ok"] is True
    s = rep["summary"]
    assert s["depts"] == 17
    assert s["seats_listed"] >= 60          # 名单口径
    assert s["seats_profiled"] >= 25
    # 核心事实：席位名单远多于档案
    assert s["seats_ghost"] == s["seats_listed"] - s["seats_profiled"]
    assert 0 < s["ghost_ratio"] < 1
    assert s["findings_p1"] > 0, "当前盘面确实存在 P1 缺口，审计不该报零"


def test_audit_detects_depts_without_profile():
    """零档案部门须与独立口径完全一致。

    不硬编码部门名：S35–S37 已把 intel 补满 8/8，写死「intel 必须零档案」会随补档过时
    （2026-10-21 真实踩到——补档后这条断言直接红）。
    """
    rep = A.audit(_PLATFORM)
    got = {f["dept"] for f in _by_code(rep, "DEPT_NO_PROFILE")}
    assert got == _expect_depts_without_profile(), "审计结果与实际零档案部门不一致"


def test_audit_detects_null_lead():
    rep = A.audit(_PLATFORM)
    depts = {f["dept"] for f in _by_code(rep, "LEAD_NULL")}
    assert "proj_backtest" in depts and "proj_data" in depts


def test_audit_detects_lead_without_profile():
    """主官无档须与独立口径完全一致（同样不硬编码部门名）。"""
    rep = A.audit(_PLATFORM)
    got = {f["dept"] for f in _by_code(rep, "LEAD_NO_PROFILE")}
    assert got == _expect_leads_without_profile(), "主官无档检出与实际不一致"


def test_audit_detects_lead_out_of_dept():
    """A0A 当 ops_runtime 主官却不在该部门名单——跨部门指挥链必须报出来。"""
    rep = A.audit(_PLATFORM)
    out = {f["dept"] for f in _by_code(rep, "LEAD_OUT_OF_DEPT")}
    assert "ops_runtime" in out and "chief_line" in out


def test_audit_detects_duplicate_names():
    rep = A.audit(_PLATFORM)
    dups = _by_code(rep, "DUPLICATE_CN_NAME")
    if dups:                                  # 存量 4 对；若已清理则不必红
        names = {f["name"] for f in dups}
        assert rep["summary"]["duplicate_names"] == len(dups)
        for f in dups:
            assert len(f["holders"]) >= 2
        assert names


def test_audit_lead_registered_not_called_orphan():
    """口径承重：主官有 hr 档案但真源未登记 → LEAD_UNREGISTERED，不得混作编外。"""
    rep = A.audit(_PLATFORM)
    unreg = _by_code(rep, "LEAD_UNREGISTERED")
    orphan = _by_code(rep, "ORPHAN_STAFF")
    unreg_ids = {a for f in unreg for a in f["agents"]}
    orphan_ids = {a for f in orphan for a in f["agents"]}
    assert not (unreg_ids & orphan_ids), "同一人不能既判主官未登记又判编外"


def test_audit_is_readonly():
    p = _PLATFORM / "registry" / "org.json"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    A.audit(_PLATFORM)
    A.audit(_PLATFORM)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h, "审计必须只读"


# ---------------------------------------------------------------- tmp 构造场景

def _minimal_org(tmp_path, *, profiled=True, lead="X1"):
    r = tmp_path / "plat"
    (r / "registry").mkdir(parents=True)
    (r / "hr").mkdir()
    prof = [{"agent_id": "X1", "role_profile": "测试岗", "llm_binding": "ollama/qwen3.5:9b"}] if profiled else []
    org = {"departments": [{"dept": "only", "name": "唯一部", "lead": lead,
                            "agents": ["X1"], "agent_profiles": prof}]}
    (r / "registry" / "org.json").write_text(json.dumps(org, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    (r / "hr" / "X1").mkdir()
    (r / "hr" / "X1" / "resume.yaml").write_text("cn_name: 测试员\n", encoding="utf-8")
    return r


def test_audit_clean_case_has_no_findings(tmp_path):
    r = _minimal_org(tmp_path, profiled=True, lead="X1")
    rep = A.audit(r)
    assert rep["findings"] == [], rep["findings"]
    s = rep["summary"]
    assert s["seats_ghost"] == 0 and s["leads_ok"] == 1
    assert s["findings_p1"] == 0


def test_audit_flags_ghost_and_null_lead(tmp_path):
    r = _minimal_org(tmp_path, profiled=False, lead=None)
    rep = A.audit(r)
    codes = _codes(rep)
    assert "DEPT_NO_PROFILE" in codes
    assert "LEAD_NULL" in codes
    assert rep["summary"]["seats_ghost"] == 1


def test_audit_profile_not_listed(tmp_path):
    r = _minimal_org(tmp_path, profiled=True, lead="X1")
    org = json.loads((r / "registry" / "org.json").read_text(encoding="utf-8"))
    org["departments"][0]["agents"] = []          # 有档案但名单里没有
    (r / "registry" / "org.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    rep = A.audit(r)
    assert "PROFILE_NOT_LISTED" in _codes(rep)


def test_audit_missing_org_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        A.audit(tmp_path)


# ---------------------------------------------------------------- 快照口径

def test_snapshot_shows_all_seats_not_just_profiles():
    """快照必须按『名单 ∪ 档案』出人，否则有名无档的席位会被整个藏起来。"""
    from legion_command.snapshot import build_snapshot
    d = build_snapshot()
    total = sum(len(x["agents"]) for x in d["departments"])
    ghost = sum(1 for x in d["departments"] for a in x["agents"] if a.get("has_profile") is False)
    assert total == d["summary"]["agents"]
    assert total >= 60, "名单口径应 60+ 席"
    # 幽灵席位总数须与独立口径一致（不写死数字，随补档演进）
    assert ghost == _expect_ghost_total(), "快照的 has_profile 判定与实际档案缺口不一致"
    # 回归锁：情报部已补满 8/8（S37 成果），不得退化
    intel = next(x for x in d["departments"] if "情报" in x["name"])
    assert intel["seats_profiled"] == intel["seats_listed"], "情报部满档成果退化"
    assert all(a.get("has_profile") is True for a in intel["agents"])
    assert intel["lead_profiled"] is True
