"""Agent IDE 组织架构富化测试（WO-IDE-ENRICH-20261026）。

覆盖：HR 名字真源读取、org profile 合并、中文岗位解析、bundle 产物契约
（名字/岗位/专长/部门称谓）、ide.html 渲染契约。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

import build_agent_ide_bundle as B  # noqa: E402


def test_hr_meta_reads_cn_name_and_skills():
    meta = B._load_hr_meta(B.ROOT)
    assert meta["DEV1"]["cn_name"] == "马钧"
    assert meta["ARCH1"]["cn_name"] == "曹操"
    assert meta["D1"]["cn_name"] == "鲁肃"
    assert meta["DEV1"]["skills"], "DEV1 技能应从 resume.yaml 解析出来"


def test_org_profiles_merge_cn_name():
    org = json.load(open(Path(B.REG) / "org.json", encoding="utf-8"))
    prof = B._load_org_profiles(org)
    assert prof["ST1"]["cn_name"] == "程昱"
    assert "attributes" in prof["ST1"]
    assert prof["ST1"]["attributes"]["specialties"]


def test_role_cn_from_prompt_forms():
    assert B._role_cn_from_prompt("# ARCH1 · 架构师（solution_architect）\n", "x") == "架构师"
    assert B._role_cn_from_prompt("你是 DEV1，研发部的代码工程师（…）", "x") == "代码工程师"
    assert B._role_cn_from_prompt("你是 TR1，交易部执行岗（v2 人设）", "x") == "交易部执行岗"
    assert B._role_cn_from_prompt("", "fallback") == "fallback"


def test_bundle_names_and_capabilities_contract():
    bundle = json.load(open(Path(B.OUT), encoding="utf-8"))
    agents = bundle["agents"]
    assert len(agents) == 41
    named = [c for c in agents.values() if c.get("cn_name")]
    assert len(named) >= 40, "中文名覆盖应 >= 40/41"
    assert all(c.get("role_cn") for c in agents.values()), "每席应有中文岗位名"
    with_caps = [c for c in agents.values() if c.get("capabilities")]
    assert len(with_caps) >= 39, "专长覆盖应 >= 39/41"
    assert agents["DEV1"]["cn_name"] == "马钧"
    assert agents["ARCH1"]["role_cn"] == "架构师"
    assert agents["ST1"]["dept_cn_title"] == "谋略府"
    assert agents["A0"]["cn_name"] == "田丰"
    depts = {d["id"]: d for d in bundle["departments"]}
    assert depts["dev"]["cn_title"] == "将作监"
    assert depts["dev"]["lead_name"] == "曹操"


def test_ide_html_renders_identity_block():
    html = (Path(_ROOT) / "portal" / "ide.html").read_text(encoding="utf-8")
    for marker in ("身份 · 组织架构", "专长能力", "capabilities", "cn_name", "role_cn"):
        assert marker in html, marker
