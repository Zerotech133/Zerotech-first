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
    assert (
        B._role_cn_from_prompt("# ARCH1 · 架构师（solution_architect）\n", "x")
        == "架构师"
    )
    assert (
        B._role_cn_from_prompt("你是 DEV1，研发部的代码工程师（…）", "x")
        == "代码工程师"
    )
    assert (
        B._role_cn_from_prompt("你是 TR1，交易部执行岗（v2 人设）", "x")
        == "交易部执行岗"
    )
    assert B._role_cn_from_prompt("", "fallback") == "fallback"


def test_bundle_names_and_capabilities_contract():
    bundle = json.load(open(Path(B.OUT), encoding="utf-8"))
    agents = bundle["agents"]
    # 2026-10-30：席位数会随组织扩编变化，契约改为“与 stats 自洽”，
    # 不再硬编码 63（当前 66 在编 + 1 治理席虚拟卡 = 67）。
    stats = bundle["stats"]
    assert len(agents) == stats["total_cards"]
    assert stats["agents"] == len(agents) - stats["governance_seats"]
    assert stats["governance_seats"] >= 1
    assert stats["prompt_ok"] == stats["agents"]
    assert stats["prompt_miss"] == []
    named = [c for c in agents.values() if c.get("cn_name")]
    assert len(named) == len(agents), "中文名覆盖应与卡片总数一致"
    assert all(c.get("role_cn") for c in agents.values()), "每席应有中文岗位名"
    with_caps = [c for c in agents.values() if c.get("capabilities")]
    assert len(with_caps) >= 39, "专长覆盖应 >= 39/61"
    assert agents["DEV1"]["cn_name"] == "马钧"
    assert agents["ARCH1"]["role_cn"] == "研发主官 · 架构师"
    assert agents["ST1"]["dept_cn_title"] == "谋略府"
    assert agents["A0"]["cn_name"] == "田丰"
    assert agents["DENGZHI1"]["cn_name"] == "邓芝"
    assert agents["DENGZHI1"]["role_cn"] == "信驿使"
    assert agents["DENGZHI1"]["prompt_file"].endswith("comm_envoy.md")
    assert agents["COWBRIDGE"]["cn_name"] == "桥玄"
    assert agents["COWBRIDGE"]["prompt_file"].endswith("cowbridge_memory_bridge.md")
    assert agents["R2"]["role_cn"] == "保守风控师·右侧立场官"
    assert agents["LUSU1"]["prompt_file"].endswith("comm_lead.md")
    assert agents["INTEL0"]["prompt_file"].endswith("intel_sector_receiver.md")

    # P1：三个缺失部门补入（risk/ops_runtime/chief_line），R2=赵云·保守风控哨
    depts = {d["id"]: d for d in bundle["departments"]}
    assert {"risk", "ops_runtime", "chief_line"} <= set(depts)
    assert depts["risk"]["cn_title"] == "大都督府"
    assert set(depts["risk"]["agents"]) == {"R1", "R2", "R3"}
    assert agents["R2"]["cn_name"] == "赵云"
    assert agents["R2"]["role_profile"] == "risk_conservative_sentinel"
    assert agents["R2"]["entry_source"] == "org_profiles"

    # P2：治理席虚拟卡（不占编，与 R2 不同 id）
    zy = agents["ZHAOYUN"]
    assert zy["cn_name"] == "赵云·治理席（不占编）"
    assert "不占编" in zy["seat_note"]
    assert "R2" in zy["seat_note"]
    assert len(zy["capabilities"]) >= 5
    assert "8796/zhaoyun" in zy["home_page"]
    assert depts["governance"]["agents"] == ["ZHAOYUN"]
    # 名称去重（WO-SEAT-DEDUP）：编制内 R2 仍叫赵云，治理席显示名已消歧；全卡无同名冲突
    assert agents["R2"]["cn_name"] == "赵云"
    assert bundle["stats"]["name_collisions"] == {}

    # P3：R2 编制口径对齐（会议厅人格=保守风控哨）
    meeting = (
        Path(_ROOT) / "agent_platform" / "legion_command" / "static" / "meeting.html"
    ).read_text(encoding="utf-8")
    assert "保守风控哨" in meeting and "进化官" not in meeting

    depts = {d["id"]: d for d in bundle["departments"]}
    assert depts["dev"]["cn_title"] == "将作监"
    assert depts["dev"]["lead_name"] == "曹操"


def test_ide_html_renders_identity_block():
    html = (Path(_ROOT) / "portal" / "ide.html").read_text(encoding="utf-8")
    for marker in ("身份 · 组织架构", "专长能力", "capabilities", "cn_name", "role_cn"):
        assert marker in html, marker


def test_ide_capability_search_contract():
    """能力搜索（WO-IDE-CAPSEARCH）：Ctrl+P 与 @ 菜单都匹配 cn_name/role_cn/capabilities。"""
    html = (Path(_ROOT) / "portal" / "ide.html").read_text(encoding="utf-8")
    assert "a.capabilities" in html
    assert "(a.capabilities||[]).join" in html
    assert "能力专长" in html
    assert "a.cn_name" in html
