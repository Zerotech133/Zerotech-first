# -*- coding: utf-8 -*-
"""WO-LEGION-S34 编制完整性审计测试。

承重点：缺口必须被如实检出（没有档案的部门/主官不能放过）、口径不许把主官误判为编外、
审计只读（真源字节不变）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_audit as A  # noqa: E402


def _raw_org():
    return json.loads((_PLATFORM / "registry" / "org.json").read_text(encoding="utf-8"))


def _matrix_ids_by_dept():
    """P2 独立口径：项目部门档案在主档 matrix_assignments（不在部门表）。"""
    out = {}
    for d in _raw_org()["departments"]:
        for p in d.get("agent_profiles") or []:
            for m in p.get("matrix_assignments") or []:
                dep = m.get("dept")
                if dep and p.get("agent_id"):
                    out.setdefault(dep, set()).add(str(p["agent_id"]))
    return out


def _expect_depts_without_profile():
    """独立口径：自己从 org.json 数「名单有人但零档案（含矩阵档）」的部门。"""
    mx = _matrix_ids_by_dept()
    return {
        d["dept"]
        for d in _raw_org()["departments"]
        if d.get("agents") and not d.get("agent_profiles") and not mx.get(d["dept"])
    }


def _known_profile_ids():
    """独立口径：自己扫「档案可能登记的三处」——部门档案表、顶层 chief、team_*.json。

    2026-10-21 修正：A0/A0A 的档案本来就不在部门表（chief 块 / team_chief.json），
    旧口径把「部门表里没有」直接当成「无档案」，等于把在位参谋长报成缺失。
    """
    org = _raw_org()
    ids = {
        str(p.get("agent_id"))
        for d in org["departments"]
        for p in (d.get("agent_profiles") or [])
    }
    for _dep, _mx in _matrix_ids_by_dept().items():
        ids.update(_mx)
    chief = org.get("chief") or {}
    if chief.get("agent_id"):
        ids.add(str(chief["agent_id"]))
    for f in (_PLATFORM / "registry").glob("team_*.json"):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for a in t.get("agents") or []:
            if a.get("agent_id"):
                ids.add(str(a["agent_id"]))
    return ids


def _expect_leads_without_profile():
    known = _known_profile_ids()
    out = set()
    for d in _raw_org()["departments"]:
        lead = d.get("lead")
        prof = [p.get("agent_id") for p in (d.get("agent_profiles") or [])]
        if lead and lead not in prof and str(lead) not in known:
            out.add(d["dept"])
    return out


def _expect_leads_profile_elsewhere():
    """主官档案在部门表之外、但确实登记过的部门。"""
    known = _known_profile_ids()
    out = set()
    for d in _raw_org()["departments"]:
        lead = d.get("lead")
        prof = [p.get("agent_id") for p in (d.get("agent_profiles") or [])]
        if lead and lead not in prof and str(lead) in known:
            out.add(d["dept"])
    return out


def _expect_ghost_total():
    """真无档 = 名单里有、但部门表/chief/team_* 三处都查不到。

    邓芝 DENGZHI1 的档案在 registry/team_comm.json，只算部门表会把它误报成幽灵席位。
    """
    known = _known_profile_ids()
    n = 0
    for d in _raw_org()["departments"]:
        ids = [str(x) for x in (d.get("agents") or [])]
        n += sum(1 for i in ids if i not in known)
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
    assert s["seats_listed"] >= 60  # 名单口径
    assert s["seats_profiled"] >= 25
    # 三态必须自平：名单 = 部门档案表 + 档案在别处 + 真无档。
    # 旧断言写的是二态（名单 = 有档 + 无档），档案在 team 文件里的席会被算成无档（S42 修）。
    assert s["seats_listed"] == (
        s["seats_profiled"]
        + s.get("seats_matrix", 0)
        + s.get("seats_profiled_elsewhere", 0)
        + s["seats_ghost"]
    )
    assert 0 <= s["ghost_ratio"] <= 1
    # 成果锁（S42）：S35→S42 已把「有名无档」清零。回升只可能是有人绕过编制工具
    # 往名单里塞 id 不补档——这正是要抓的，不是该放宽的。
    assert s["seats_ghost"] == 0, "真无档席位应已清零（当前 %d）" % s["seats_ghost"]


def test_audit_detects_depts_without_profile():
    """零档案部门须与独立口径完全一致。

    不硬编码部门名：S35–S37 已把 intel 补满 8/8，写死「intel 必须零档案」会随补档过时
    （2026-10-21 真实踩到——补档后这条断言直接红）。
    """
    rep = A.audit(_PLATFORM)
    got = {f["dept"] for f in _by_code(rep, "DEPT_NO_PROFILE")}
    assert got == _expect_depts_without_profile(), "审计结果与实际零档案部门不一致"


def test_audit_detects_null_lead():
    """空悬主官须与独立口径一致——不写死部门名（S42 已给 proj_data 任命主官）。"""
    rep = A.audit(_PLATFORM)
    depts = {f["dept"] for f in _by_code(rep, "LEAD_NULL")}
    expect = {d["dept"] for d in _raw_org()["departments"] if not d.get("lead")}
    assert depts == expect, "空悬主官检出与实际不一致"


def test_audit_detects_lead_without_profile():
    """主官无档须与独立口径完全一致（同样不硬编码部门名）。"""
    rep = A.audit(_PLATFORM)
    got = {f["dept"] for f in _by_code(rep, "LEAD_NO_PROFILE")}
    assert got == _expect_leads_without_profile(), "主官无档检出与实际不一致"


def test_audit_detects_lead_out_of_dept():
    """主官不在本部门名单＝跨部门指挥链，须与独立口径一致。

    不写死部门名：S43 把 A0/A0A 正式并入 chief_line 后，chief_line 的 lead 已在本部门名单，
    原先写死的 "chief_line" 立刻过期（这正是「断言真实盘面」的代价）。
    """
    rep = A.audit(_PLATFORM)
    out = {f["dept"] for f in _by_code(rep, "LEAD_OUT_OF_DEPT")}
    expect = {
        d["dept"]
        for d in _raw_org()["departments"]
        if d.get("lead") and str(d["lead"]) not in (d.get("agents") or [])
    }
    assert out == expect, "跨部门主官检出与实际不一致"


def test_audit_detects_duplicate_names():
    rep = A.audit(_PLATFORM)
    dups = _by_code(rep, "DUPLICATE_CN_NAME")
    if dups:  # 存量 4 对；若已清理则不必红
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
    prof = (
        [
            {
                "agent_id": "X1",
                "role_profile": "测试岗",
                "llm_binding": "ollama/qwen3.5:9b",
            }
        ]
        if profiled
        else []
    )
    org = {
        "departments": [
            {
                "dept": "only",
                "name": "唯一部",
                "lead": lead,
                "agents": ["X1"],
                "agent_profiles": prof,
            }
        ]
    }
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
    org["departments"][0]["agents"] = []  # 有档案但名单里没有
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8"
    )
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
    ghost = sum(
        1
        for x in d["departments"]
        for a in x["agents"]
        if a.get("has_profile") is False
    )
    assert total == d["summary"]["agents"]
    assert total >= 60, "名单口径应 60+ 席"
    # 幽灵席位总数须与独立口径一致（不写死数字，随补档演进）
    assert ghost == _expect_ghost_total(), "快照的 has_profile 判定与实际档案缺口不一致"
    # 回归锁：情报部已补满 8/8（S37 成果），不得退化
    intel = next(x for x in d["departments"] if "情报" in x["name"])
    assert intel["seats_profiled"] == intel["seats_listed"], "情报部满档成果退化"
    assert all(a.get("has_profile") is True for a in intel["agents"])
    assert intel["lead_profiled"] is True


# ------------------------------------------- 档案登记位置（rules 1.1 / S41）


def test_audit_detects_lead_profile_elsewhere():
    """档案在部门表之外（chief / team_*）须单列，且不得再报成「无档案」。

    2026-10-21 前 A0（田丰）与 A0A 一直被报 P1「主官无档案」——事实上两人档案
    分别躺在 org.json 顶层 chief 与 registry/team_chief.json 里。
    """
    rep = A.audit(_PLATFORM)
    got = {f["dept"] for f in _by_code(rep, "LEAD_PROFILE_ELSEWHERE")}
    assert got == _expect_leads_profile_elsewhere(), "档案在别处的检出与实际不一致"
    # 两类缺口必须互斥：同一部门不能既「无档案」又「档案在别处」
    assert not (got & {f["dept"] for f in _by_code(rep, "LEAD_NO_PROFILE")})


def test_audit_cross_dept_note_downgrades(tmp_path):
    """跨部门主官未登记兼任说明 P2，登记后降 P3（不是消失——兼任仍是事实）。"""
    plat = tmp_path / "plat"
    (plat / "registry").mkdir(parents=True)
    (plat / "hr").mkdir()
    cases = {
        "no_note": None,
        "with_note": {
            "cross_dept": True,
            "cross_note": "参谋长兼任直属线主官，指挥链经真源登记",
        },
    }
    findings = {}
    for tag, appointed in cases.items():
        dept = {
            "dept": "x",
            "name": "测试营",
            "lead": "LEADX",
            "agents": ["MEM1"],
            "agent_profiles": [
                {"agent_id": "MEM1", "charter_detail": "测试席"},
                {"agent_id": "LEADX", "charter_detail": "跨部门主官"},
            ],
        }
        if appointed:
            dept["lead_appointed"] = appointed
        org = {"departments": [dept]}
        (plat / "registry" / "org.json").write_text(
            json.dumps(org, ensure_ascii=False), encoding="utf-8"
        )
        findings[tag] = [
            f for f in A.audit(plat)["findings"] if f["code"] == "LEAD_OUT_OF_DEPT"
        ]
    assert findings["no_note"] and findings["no_note"][0]["severity"] == "P2"
    assert findings["with_note"] and findings["with_note"][0]["severity"] == "P3"
    assert "跨部门兼任" in findings["with_note"][0]["detail"]


# ------------------------------------------- 绑定声明（rules 1.2 / S43）


def test_audit_detects_binding_missing(tmp_path):
    """有档案但没写 llm_binding 必须报出来：取档方会回落默认档。

    为什么算缺口而不是"默认就行"：规则执行体（桥接/回调/门面）本该 none，
    回落 9b 等于替它虚构 LLM 能力；真需要 LLM 的席位则缺了能力声明。两种都不该由工具猜。
    """
    plat = tmp_path / "plat"
    (plat / "registry").mkdir(parents=True)
    (plat / "hr").mkdir()
    org = {
        "departments": [
            {
                "dept": "x",
                "name": "测试营",
                "lead": "P1",
                "agents": ["P1", "Q1"],
                "agent_profiles": [
                    {"agent_id": "P1", "llm_binding": "ollama/qwen3.5:9b"},
                    {"agent_id": "Q1"},  # 有档案、没写绑定
                ],
            }
        ]
    }
    (plat / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8"
    )
    rep = A.audit(plat)
    got = {a for f in _by_code(rep, "BINDING_MISSING") for a in f["agents"]}
    assert got == {"Q1"}, "未声明绑定的席位必须被检出（实际 %r）" % got


def test_audit_binding_missing_matches_independent_count():
    """真实盘面：检出的未声明绑定席位须与独立口径一致（不写死人名）。"""
    from engine.org_profiles import profile_index

    rep = A.audit(_PLATFORM)
    got = {a for f in _by_code(rep, "BINDING_MISSING") for a in f["agents"]}
    idx = profile_index(_PLATFORM)
    listed = {
        str(a) for d in _raw_org()["departments"] for a in (d.get("agents") or [])
    }
    expect = {a for a in listed if (idx.get(a) or {}).get("llm_binding", "") == ""}
    assert got == expect


# ------------------------------------------- 绑定声明（rules 1.2 / S43）


def test_audit_detects_binding_missing__dup2(tmp_path):
    """有档案但没写 llm_binding 必须报出来：取档方会回落默认档。

    为什么算缺口而不是"默认就行"：规则执行体（桥接/回调/门面）本该 none，
    回落 9b 等于替它虚构 LLM 能力；真需要 LLM 的席位则缺了能力声明。两种都不该由工具猜。
    """
    plat = tmp_path / "plat"
    (plat / "registry").mkdir(parents=True)
    (plat / "hr").mkdir()
    org = {
        "departments": [
            {
                "dept": "x",
                "name": "测试营",
                "lead": "P1",
                "agents": ["P1", "Q1"],
                "agent_profiles": [
                    {"agent_id": "P1", "llm_binding": "ollama/qwen3.5:9b"},
                    {"agent_id": "Q1"},  # 有档案、没写绑定
                ],
            }
        ]
    }
    (plat / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8"
    )
    rep = A.audit(plat)
    got = {a for f in _by_code(rep, "BINDING_MISSING") for a in f["agents"]}
    assert got == {"Q1"}, "未声明绑定的席位必须被检出（实际 %r）" % got


def test_audit_binding_missing_matches_independent_count__dup2():
    """真实盘面：检出的未声明绑定席位须与独立口径一致（不写死人名）。"""
    from engine.org_profiles import profile_index

    rep = A.audit(_PLATFORM)
    got = {a for f in _by_code(rep, "BINDING_MISSING") for a in f["agents"]}
    idx = profile_index(_PLATFORM)
    listed = {
        str(a) for d in _raw_org()["departments"] for a in (d.get("agents") or [])
    }
    expect = {a for a in listed if (idx.get(a) or {}).get("llm_binding", "") == ""}
    assert got == expect


def test_audit_other_dept_profile_downgraded_to_p3():
    """档案在**别的部门表**（跨部门兼任的常态）应是 P3，不能当成取档故障报 P2。

    统一取档口径能查到这类档案，不存在"静默回落默认档"；把它算 P2 会让真实的
    取档故障被噪音淹没。口径不写死部门：凡 source_detail 以 dept: 开头的都按 P3。
    """
    from engine.org_profiles import SRC_DEPT, profile_index

    idx = profile_index(_PLATFORM)
    rep = A.audit(_PLATFORM)
    checked = 0
    for f in _by_code(rep, "LEAD_PROFILE_ELSEWHERE"):
        src = idx.get(str(f.get("lead"))) or {}
        if src.get("source") == SRC_DEPT:
            checked += 1
            assert f["severity"] == "P3", "档案在主属部门的跨部门兼任不该报 P2：%r" % (
                f,
            )
    assert checked > 0, "样本为空：没有「主官档案在别的部门表」的情形，用例失去承重"


def test_audit_other_dept_profile_downgraded_to_p3__dup2():
    """档案在**别的部门表**（跨部门兼任的常态）应是 P3，不能当成取档故障报 P2。

    统一取档口径能查到这类档案，不存在"静默回落默认档"；把它算 P2 会让真实的
    取档故障被噪音淹没。口径不写死部门：凡 source_detail 以 dept: 开头的都按 P3。
    """
    from engine.org_profiles import SRC_DEPT, profile_index

    idx = profile_index(_PLATFORM)
    rep = A.audit(_PLATFORM)
    checked = 0
    for f in _by_code(rep, "LEAD_PROFILE_ELSEWHERE"):
        src = idx.get(str(f.get("lead"))) or {}
        if src.get("source") == SRC_DEPT:
            checked += 1
            assert f["severity"] == "P3", "档案在主属部门的跨部门兼任不该报 P2：%r" % (
                f,
            )
    assert checked > 0, "样本为空：没有「主官档案在别的部门表」的情形，用例失去承重"
