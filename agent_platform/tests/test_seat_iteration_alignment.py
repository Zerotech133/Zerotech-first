# -*- coding: utf-8 -*-
"""11 席能力迭代配置口径对齐守卫（task-19 / WO-SEAT-ITER-ALIGN-20261031）。

背景：2026-10-31 task-15 审计发现 11 席迭代配置存在"口径不一致"风险：
  - `seat_iterations/*.json` 的 `cn_name` 全空（`capability_iterations.md` 只剩 ARCH1 有名字）；
  - 6 席实践载体为裸占位"待定（下一任务）"，与 `tools/seat_iteration.py` 的策划载体脱节；
  - DEV5/DEV6 的载体指向已 aborted 的 P0 单（T19 降级关闭）却仍显示为有效载体。
本测试把"配置与真源对齐"变成可回归断言（只读 org/hr/config，不改任何文件）：

1. cn_name == `agent_platform/hr/<SEAT>/resume.yaml` 真源（不得凭空编造名字）；
2. mentor == org.json dev 部门 command_chain/groups 真源（ARCH1→A0；DEV1/DEV5→ARCH1；
   其余→所在组组长），并与 `tools/seat_iteration.py` 的 MENTORS 表一致；
3. 载体占位显式化：不留裸"待定（下一任务）"；失效载体必须注明 aborted + v1.1 补证；
4. 不伪造进展：status/evidence 语义一致（planned 不得带 evidence，passed 必须有 evidence）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import seat_iteration as SI  # noqa: E402

ITER_DIR = ROOT / "agent_platform" / "config" / "seat_iterations"
ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
SEATS = ["ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5",
         "DEV6", "DEV7", "DEV8", "DOC1", "OPS1"]

# 已在配置中作为"历史/在用载体"保留的真实工单（done）；其余 TID 必须是显式占位
REAL_CARRIER_TIDS = {
    "DEV1": "WO-P0-DEV1-MONDAY-20261027",
    "DEV7": "WO-P0-DEV7-WIKI-20261027",
    "DEV8": "WO-P0-DEV8-METRICS-20261027",
}
# 已 aborted、不得继续作为有效载体的单
ABORTED_CARRIER_TIDS = ("WO-P0-DEV5-EVALCASES-20261027",
                        "WO-P0-DEV6-TRACE-20261027")


def _load(seat: str) -> dict:
    p = ITER_DIR / (seat + ".json")
    assert p.is_file(), "缺迭代配置：%s" % p
    return json.loads(p.read_text(encoding="utf-8"))


def _hr_cn(seat: str) -> str:
    p = HR / seat / "resume.yaml"
    if not p.is_file():
        return ""
    t = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^\s*cn_name:\s*[\"']?([^\n\"']+)", t, re.M)
    return m.group(1).strip() if m else ""


def _org_dev() -> dict:
    org = json.loads(ORG.read_text(encoding="utf-8"))
    return next(d for d in org["departments"] if d.get("dept") == "dev")


def test_all_seats_present_and_schema_valid():
    seats = {p.stem for p in ITER_DIR.glob("*.json")}
    assert seats == set(SEATS), "迭代配置席位集合漂移：%r" % (seats ^ set(SEATS))
    assert SI.check(ROOT) == [], "seat_iteration --check 不通过：%r" % SI.check(ROOT)


def test_cn_name_matches_hr_source():
    """cn_name 必须与 hr 真源一致：不允许空（表里无名），也不允许编造。"""
    for seat in SEATS:
        d = _load(seat)
        cn = str(d.get("cn_name") or "").strip()
        assert cn, "%s cn_name 为空（capability_iterations.md 会显示无名）" % seat
        truth = _hr_cn(seat)
        if truth:
            assert cn == truth, "%s cn_name=%r 与 hr/resume.yaml 真源 %r 不一致" % (
                seat, cn, truth)


def test_mentor_matches_org_command_chain():
    """mentor 必须与 org.json 的指挥链/分组真源一致，且工具 MENTORS 表同源。"""
    dev = _org_dev()
    groups = {g.get("id"): g for g in (dev.get("groups") or [])}
    lead = dev.get("lead")
    team_lead = {p.get("agent_id"): p.get("team_lead_of")
                 for p in (dev.get("agent_profiles") or [])}
    for seat in SEATS:
        d = _load(seat)
        mentor = str(d.get("mentor") or "").strip()
        if seat == "ARCH1":
            expected = "A0"                       # 部门主官向 A0 汇报
        elif team_lead.get(seat):                 # 组长（DEV1=G1 / DEV5=G2）向主官
            expected = lead
        else:                                     # 组员向所在组组长
            gid = d.get("group")
            expected = (groups.get(gid) or {}).get("lead")
            assert expected, "%s 无有效 group/组长（%r）" % (seat, gid)
        assert mentor == expected, "%s mentor=%r 与 org 真源 %r 不一致" % (
            seat, mentor, expected)
        assert SI.MENTORS.get(seat) == expected, (
            "%s 工具 MENTORS=%r 与 org 真源 %r 不一致" % (
                seat, SI.MENTORS.get(seat), expected))


def test_carrier_placeholder_explicit():
    """载体不得留裸"待定"；失效载体必须标注 aborted/补证；真实载体须在用单。"""
    for seat in SEATS:
        d = _load(seat)
        tid = str(d["iteration"]["practice"]["tid"]).strip()
        assert tid, "%s 实践载体为空" % seat
        assert tid != "待定（下一任务）", "%s 载体仍是裸占位（未显式化）" % seat
        for aborted in ABORTED_CARRIER_TIDS:
            assert tid != aborted, "%s 载体仍指向已 aborted 单 %s" % (seat, aborted)
        if tid.startswith("待定"):
            assert ("计划载体：" in tid) or ("已 aborted" in tid), (
                "%s 占位未说明后续载体：%r" % (seat, tid))
        if seat in REAL_CARRIER_TIDS:
            assert tid == REAL_CARRIER_TIDS[seat], "%s 真实载体被改动：%r" % (seat, tid)
    # DEV5/DEV6 必须明确标注 aborted 来源与 v1.1 补证
    for seat in ("DEV5", "DEV6"):
        tid = str(_load(seat)["iteration"]["practice"]["tid"])
        assert "已 aborted" in tid and "v1.1 补证" in tid, (
            "%s 失效载体未标注：%r" % (seat, tid))


def test_no_fake_progress_semantics():
    """不伪造进展：planned 不得带 evidence；passed 必须有 evidence；无 done 状态值。"""
    for seat in SEATS:
        d = _load(seat)
        it = d["iteration"]
        for part in ("learning", "practice", "assessment"):
            x = it.get(part) or {}
            status = str(x.get("status") or "")
            evidence = str(x.get("evidence") or "")
            assert status != "done", "%s.%s status=done 非法（迭代 schema 无 done）" % (
                seat, part)
            if status == "planned":
                assert evidence == "", "%s.%s 仍 planned 却带 evidence（伪造进展）" % (
                    seat, part)
            if status == "passed":
                assert evidence.strip(), "%s.%s passed 无 evidence（validate 应已拒）" % (
                    seat, part)
        # 评估 passed 必须 version 升级且有证据（当前全部 planned，防提前标 passed）


def test_capability_md_regenerated_with_names_and_kickoff():
    """md 由 --out 生成且每个席位带名字；手工维护的「迭代 1 启动」段不得丢失。"""
    md = (ROOT / "docs" / "dev" / "capability_iterations.md").read_text(
        encoding="utf-8")
    for seat in SEATS:
        cn = str(_load(seat).get("cn_name") or "").strip()
        assert "| %s %s |" % (seat, cn) in md, "%s 在 md 表中缺名字" % seat
    assert "## 迭代 1 启动" in md, "迭代 1 启动段丢失（--out 不生成该段，须保留）"
    assert "口径对齐（2026-10-31 · task-19）" in md, "md 缺口径对齐注记"
