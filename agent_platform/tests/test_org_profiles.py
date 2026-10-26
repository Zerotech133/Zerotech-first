# -*- coding: utf-8 -*-
"""WO-LEGION-S41 档案索引测试。

承重点：三处档案登记的**优先级**、以及会议层取档真的能读到 team 文件里的绑定。
背景：A0（org.chief）/A0A（team_chief.json）曾因取档方只扫部门档案表而被判「无档案」，
会议层回落到默认 9b——档案与能力不符且现场看不出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_profiles as P  # noqa: E402


def _mk(root: Path, dept_profs, chief=None, team=None):
    (root / "registry").mkdir(parents=True, exist_ok=True)
    org = {"departments": [
        {"dept": "d1", "agents": ["X1", "X2"],
         "agent_profiles": [{"agent_id": a, "llm_binding": b} for a, b in dept_profs]}]}
    if chief is not None:
        org["chief"] = chief
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    if team is not None:
        (root / "registry" / "team_x.json").write_text(
            json.dumps({"agents": [{"agent_id": a, "llm_binding": b} for a, b in team]},
                       ensure_ascii=False, indent=2), encoding="utf-8")


def test_index_covers_three_sources(tmp_path):
    _mk(tmp_path, [("X1", "ollama/qwen3.5:9b")],
        chief={"agent_id": "C1", "llm_binding": "glm/glm-5.3"},
        team=[("T1", "glm/glm-5.3-flash")])
    idx = P.profile_index(tmp_path)
    assert set(idx) == {"X1", "C1", "T1"}
    assert idx["X1"]["source"] == P.SRC_DEPT
    assert idx["C1"]["source"] == P.SRC_CHIEF
    assert idx["T1"]["source"] == P.SRC_TEAM
    assert idx["T1"]["source_detail"].endswith("team_x.json")


def test_dept_profile_wins_over_team_file(tmp_path):
    """同一 id 多处登记：以部门档案表为准（不是后写覆盖）。"""
    _mk(tmp_path, [("X1", "ollama/qwen3.5:9b")], team=[("X1", "glm/glm-5.3")])
    idx = P.profile_index(tmp_path)
    assert idx["X1"]["llm_binding"] == "ollama/qwen3.5:9b"
    assert idx["X1"]["source"] == P.SRC_DEPT


def test_index_tolerates_broken_sources(tmp_path):
    """坏 JSON / 缺失目录不得把整个索引搞崩（只读工具应降级为「少一条」）。"""
    (tmp_path / "registry").mkdir(parents=True)
    (tmp_path / "registry" / "org.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "registry" / "team_bad.json").write_text("[", encoding="utf-8")
    assert P.profile_index(tmp_path) == {}


def test_stamp_detects_team_file_rewrite(tmp_path):
    """缓存指纹必须覆盖 team_*.json：只认 org.json 的 mtime 会漏掉 A0A 改档。"""
    _mk(tmp_path, [("X1", "ollama/qwen3.5:9b")], team=[("T1", "glm/glm-5.3")])
    s1 = P.stamp(tmp_path)
    # 同秒改写：mtime 可能不变，靠 size 变化也要能察觉
    (tmp_path / "registry" / "team_x.json").write_text(
        json.dumps({"agents": [{"agent_id": "T1", "llm_binding": "ollama/qwen3.6:35b-a3b"}]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    assert P.stamp(tmp_path) != s1
    assert P.profile_index(tmp_path)["T1"]["llm_binding"] == "ollama/qwen3.6:35b-a3b"


def test_resolve_binding_reads_team_file(tmp_path):
    """会议层取档：team 文件里的绑定必须生效，否则静默回落默认档。"""
    from legion_command.serve import _DEFAULT_BINDING, resolve_binding
    _mk(tmp_path, [("X1", "ollama/qwen3.5:9b")],
        chief={"agent_id": "C1", "llm_binding": "glm/glm-5.3"},
        team=[("T1", "glm/glm-5.3-flash")])
    assert resolve_binding("C1", platform_dir=tmp_path) == "glm/glm-5.3"
    assert resolve_binding("T1", platform_dir=tmp_path) == "glm/glm-5.3-flash"
    assert resolve_binding("X1", platform_dir=tmp_path) == "ollama/qwen3.5:9b"
    # 查不到的仍回落默认档（不因加了口径就编造绑定）
    assert resolve_binding("NOPE", platform_dir=tmp_path) == _DEFAULT_BINDING


def test_index_is_readonly(tmp_path):
    """索引只读：跑一遍不得改动任何真源字节。"""
    _mk(tmp_path, [("X1", "ollama/qwen3.5:9b")], team=[("T1", "glm/glm-5.3")])
    before = {p: p.read_bytes() for p in (tmp_path / "registry").iterdir()}
    P.profile_index(tmp_path)
    P.stamp(tmp_path)
    after = {p: p.read_bytes() for p in (tmp_path / "registry").iterdir()}
    assert before == after


def test_real_org_has_chief_and_assistant():
    """真实盘面回归锁：A0 与 A0A 必须查得到档案（否则全组织汇报对象显示为无档）。"""
    idx = P.profile_index(_PLATFORM)
    for aid in ("A0", "A0A"):
        assert aid in idx, "%s 的档案必须可查" % aid
        assert idx[aid]["llm_binding"], "%s 的绑定不得为空" % aid


def test_audit_ghost_requires_all_three_sources_missing():
    """幽灵席位 = 三处都查不到；档案在 team 文件里不算幽灵。"""
    from engine import org_audit as A
    rep = A.audit(_PLATFORM)
    idx = P.profile_index(_PLATFORM)
    ghosts = {a for f in rep["findings"] if f["code"] == "GHOST_SEAT" for a in f["agents"]}
    for aid in ghosts:
        assert aid not in idx, "%s 有档案（%s）却被报成幽灵席位" % (
            aid, (idx.get(aid) or {}).get("source_detail"))


def test_audit_ghost_requires_all_three_sources_missing__dup2():
    """幽灵席位 = 三处都查不到；档案在 team 文件里不算幽灵。"""
    from engine import org_audit as A
    rep = A.audit(_PLATFORM)
    idx = P.profile_index(_PLATFORM)
    ghosts = {a for f in rep["findings"] if f["code"] == "GHOST_SEAT" for a in f["agents"]}
    for aid in ghosts:
        assert aid not in idx, "%s 有档案（%s）却被报成幽灵席位" % (
            aid, (idx.get(aid) or {}).get("source_detail"))


def test_snapshot_does_not_degrade_name_to_id(tmp_path):
    """档案自带 name、但该席没有 hr 目录时，指挥台不得把中文名显示成 id。

    用 tmp 构造（S43 把邓芝迁进部门档案表后，真实盘面已无「档案在别处」样本——
    若仍去真实数据里找，这条会 skip 而不是失败，等于悄悄丢掉覆盖）。
    """
    from legion_command.snapshot import build_snapshot
    (tmp_path / "registry").mkdir(parents=True)
    (tmp_path / "hr").mkdir()
    # 部门名单里有 X1，但没有 hr 目录；档案在 team 文件里且自带 name
    org = {"departments": [{"dept": "d1", "name": "测试营", "lead": "X1",
                            "agents": ["X1"], "agent_profiles": []}]}
    (tmp_path / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "registry" / "team_d.json").write_text(
        json.dumps({"agents": [{"agent_id": "X1", "name": "测试名",
                                "llm_binding": "none"}]}, ensure_ascii=False),
        encoding="utf-8")
    d = build_snapshot(tmp_path)
    a = d["departments"][0]["agents"][0]
    assert a["cn"] == "测试名", "档案自带的名字没被认，退化成 %r" % a["cn"]
    assert a["has_profile"] is True
    assert a["profile_source"]
