# -*- coding: utf-8 -*-
"""WO-LEGION-S33 Agent 编制工具测试。

红线：全部用例在 tmp 副本上跑（复制真实 org.json 与 hr 名册），**绝不写真实真源**。
承重点：dry-run 零写入 / 重名与重复 id 阻断 / 双凭据必填 / 写后自检 / 失败自动回滚 / 回滚字节还原。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_enroll as E  # noqa: E402


@pytest.fixture()
def root(tmp_path):
    """tmp 真源副本：org.json + hr 名册（名册仅供重名检测）。"""
    r = tmp_path / "plat"
    (r / "registry").mkdir(parents=True)
    (r / "hr").mkdir()
    shutil.copy2(_PLATFORM / "registry" / "org.json", r / "registry" / "org.json")
    # registry/team_*.json 也是档案真源之一（A0A 的档案只在 team_chief.json 里）：
    # 不复制会让「主官有无档案」在测试里恒为无档，与真实盘面不符。
    for f in (_PLATFORM / "registry").glob("team_*.json"):
        shutil.copy2(f, r / "registry" / f.name)
    # 稳定化：把 intel 部还原成「名单有人、档案全空」的基准态。
    # 补档用例都以 intel 席位为样本，而真实 org.json 会随补档不断演进——
    # 不隔离就会「真数据一补档，测试全红」（2026-10-21 真实踩到）。
    org = json.loads((r / "registry" / "org.json").read_text(encoding="utf-8"))
    for d in org.get("departments") or []:
        if d.get("dept") == "intel":
            d.pop("agent_profiles", None)
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for d in (_PLATFORM / "hr").iterdir():
        f = d / "resume.yaml"
        if d.is_dir() and f.exists():
            (r / "hr" / d.name).mkdir()
            shutil.copy2(f, r / "hr" / d.name / "resume.yaml")
    return r


OK = dict(
    dept="dev",
    agent_id="DEV9",
    cn_name="蒋琬测试",
    role_profile="日志工程师",
    duty="负责全链路运行日志治理与异常归因，维护日志规范与轮转策略",
    binding="ollama/qwen3.5:9b",
    grade="E2",
    ticket="WO-ORG-210",
    signed_by="老田",
)


def org_bytes(r):
    return (r / "registry" / "org.json").read_bytes()


# ---------------------------------------------------------------- dry-run


def test_plan_writes_nothing(root):
    before = org_bytes(root)
    plan = E.build_plan(root, **OK)
    assert org_bytes(root) == before, "dry-run 不得写入任何字节"
    assert plan["agent"]["agent_id"] == "DEV9"
    assert plan["note"].startswith("dry-run")
    assert plan["cn_name"] == "蒋琬测试"
    # 「这个 Agent 到底干嘛」必须落进 charter_detail
    assert plan["agent"]["charter_detail"].startswith("负责全链路")


def test_plan_unknown_dept(root):
    kw = dict(OK, dept="no_such_dept")
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **kw)
    assert "unknown dept" in str(ei.value)


def test_plan_duplicate_agent_id(root):
    kw = dict(OK, agent_id="DEV1")  # dev 部已有 DEV1
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **kw)
    assert "已被占用" in str(ei.value)


def test_plan_duplicate_cn_name_blocks(root):
    """重名一律阻断：历史已有 4 对重名造成账面混淆，不能让第 5 对溜进来。"""
    kw = dict(OK, cn_name="田丰", agent_id="DEV91")
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **kw)
    msg = str(ei.value)
    assert "重名" in msg and "田丰" in msg


def test_plan_bad_id_format(root):
    for bad in ("dev9", "9DEV", "_DEV", "DEV-9", "A" * 20, ""):
        with pytest.raises(E.EnrollError):
            E.build_plan(root, **dict(OK, agent_id=bad))


def test_plan_bad_binding(root):
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **dict(OK, binding="gpt-9/turbo"))
    assert "词表" in str(ei.value)


def test_plan_bad_grade(root):
    with pytest.raises(E.EnrollError):
        E.build_plan(root, **dict(OK, grade="E9"))


def test_plan_short_duty_rejected(root):
    """职责写不清就不许入编——否则新局面无法被指挥。"""
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **dict(OK, duty="干活"))
    assert "duty" in str(ei.value)


def test_plan_empty_role_rejected(root):
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **dict(OK, role_profile="   "))
    assert "role_profile" in str(ei.value)


def test_plan_requires_ticket(root):
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **dict(OK, ticket=""))
    assert "工单" in str(ei.value)


def test_plan_requires_signer(root):
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **dict(OK, signed_by="  "))
    assert "放行人" in str(ei.value)


def test_plan_bad_ticket_prefix(root):
    with pytest.raises(E.EnrollError):
        E.build_plan(root, **dict(OK, ticket="ticket-210"))


# ---------------------------------------------------------------- apply


def test_apply_adds_agent_and_hr(root):
    before = json.loads(org_bytes(root).decode("utf-8"))
    n0 = sum(len(d.get("agent_profiles") or []) for d in before["departments"])
    plan = E.build_plan(root, **OK)
    res = E.apply_plan(root, plan, signed_by="老田")
    assert res["ok"] is True and res["rolled_back"] is False
    assert res["hr_written"] is True

    after = json.loads(org_bytes(root).decode("utf-8"))
    n1 = sum(len(d.get("agent_profiles") or []) for d in after["departments"])
    assert n1 == n0 + 1

    dev = next(d for d in after["departments"] if d["dept"] == "dev")
    prof = next(p for p in dev["agent_profiles"] if p["agent_id"] == "DEV9")
    assert prof["llm_binding"] == "ollama/qwen3.5:9b"
    assert prof["charter_detail"] == OK["duty"]
    assert "WO-ORG-210" in prof["binding_note"] and "老田" in prof["binding_note"]
    assert "DEV9" in dev["agents"], "部门 agents 列表须同步"

    hr = root / "hr" / "DEV9" / "resume.yaml"
    assert hr.exists()
    txt = hr.read_text(encoding="utf-8")
    assert "name: 蒋琬测试" in txt and "WO-ORG-210" in txt and "日志工程师" in txt


def test_apply_other_departments_untouched(root):
    before = json.loads(org_bytes(root).decode("utf-8"))
    E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    after = json.loads(org_bytes(root).decode("utf-8"))
    b = {d["dept"]: d for d in before["departments"]}
    a = {d["dept"]: d for d in after["departments"]}
    assert list(b.keys()) == list(a.keys()), "部门清单与顺序不得变"
    for k in b:
        if k == "dev":
            continue
        assert json.dumps(b[k], sort_keys=True, ensure_ascii=False) == json.dumps(
            a[k], sort_keys=True, ensure_ascii=False
        ), "%s 被误改" % k


def test_apply_creates_backup(root):
    """备份必须是**入编前原文的字节级副本**——只检查文件存在等于没测（M5 补强）。"""
    before = org_bytes(root)
    res = E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    bak = Path(res["backup"])
    assert bak.exists() and bak.parent.name == "backups"
    assert bak.read_bytes() == before, (
        "备份须与入编前 org.json 字节一致，否则回滚等于没救"
    )


def test_apply_signer_mismatch(root):
    plan = E.build_plan(root, **OK)
    with pytest.raises(E.EnrollError) as ei:
        E.apply_plan(root, plan, signed_by="别人")
    assert "不一致" in str(ei.value)


def test_apply_refuses_overwrite_hr(root):
    """计划生成后 hr 档案被建（TOCTOU）→ apply 层须拒，且不改 org.json。

    注意：若档案在 build_plan 之前就存在，会在"id 已被占用"更早拦下——
    本用例刻意在计划之后创建，专测 apply 这一道保险。
    """
    plan = E.build_plan(root, **OK)  # 此刻 hr/DEV9 尚不存在
    (root / "hr" / "DEV9").mkdir()
    (root / "hr" / "DEV9" / "resume.yaml").write_text(
        "agent_id: DEV9\n", encoding="utf-8"
    )
    before = org_bytes(root)
    with pytest.raises(E.EnrollError) as ei:
        E.apply_plan(root, plan, signed_by="老田")
    # hr 目录属编制本身，故拦住它的是 id 复核这一道（不是另一段死代码）
    assert "已被占用" in str(ei.value) and "hr" in str(ei.value)
    assert org_bytes(root) == before, "拒绝时不得改动 org.json"
    # 既有履历一文不动
    assert (root / "hr" / "DEV9" / "resume.yaml").read_text(
        encoding="utf-8"
    ) == "agent_id: DEV9\n"


def test_apply_rolls_back_when_selfcheck_fails(root, monkeypatch):
    """自检失败必须自动还原 org.json——绝不留半成品。"""
    before = org_bytes(root)

    def boom(*a, **k):
        raise E.EnrollError("自检失败（模拟）")

    monkeypatch.setattr(E, "_selfcheck", boom)
    with pytest.raises(E.EnrollError):
        E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    assert org_bytes(root) == before, "自检失败必须回滚到入编前字节"


def test_apply_toctou_id_taken_after_plan(root):
    """计划生成后真源被改（id 被占）→ 执行前复核须拒。"""
    plan = E.build_plan(root, **OK)
    # 模拟别处抢先入编同一个 id
    org = json.loads(org_bytes(root).decode("utf-8"))
    dev = next(d for d in org["departments"] if d["dept"] == "dev")
    dev["agent_profiles"].append({"agent_id": "DEV9", "role_profile": "抢注"})
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    taken = org_bytes(root)
    with pytest.raises(E.EnrollError) as ei:
        E.apply_plan(root, plan, signed_by="老田")
    assert "复核失败" in str(ei.value)
    assert org_bytes(root) == taken


# ---------------------------------------------------------------- rollback


def test_rollback_restores_exact_bytes(root):
    before = org_bytes(root)
    res = E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    assert org_bytes(root) != before
    E.rollback(root, res["backup"], signed_by="老田")
    assert org_bytes(root) == before, "回滚须字节级还原"
    # hr 档案不自动删（避免误删履历）
    assert (root / "hr" / "DEV9" / "resume.yaml").exists()


def test_rollback_requires_signer(root):
    res = E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    with pytest.raises(E.EnrollError):
        E.rollback(root, res["backup"], signed_by="")


def test_rollback_rejects_broken_backup(root):
    bad = root / "registry" / "backups"
    bad.mkdir(parents=True, exist_ok=True)
    f = bad / "org-broken.json"
    f.write_text("{not json", encoding="utf-8")
    before = org_bytes(root)
    with pytest.raises(E.EnrollError) as ei:
        E.rollback(root, str(f), signed_by="老田")
    assert "不可解析" in str(ei.value)
    assert org_bytes(root) == before


# ---------------------------------------------------------------- 真实真源只读


def test_real_org_untouched_by_tests():
    """本文件所有用例都不得改动真实 org.json（前后哈希一致）。"""
    import hashlib

    p = _PLATFORM / "registry" / "org.json"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    E.load_org(_PLATFORM)  # 只读加载一次
    E.build_plan(_PLATFORM, **dict(OK, agent_id="ZZTOP1", cn_name="测试不写字"))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h, "真实 org.json 被改动！"


# ---------------------------------------------------------------- 补档模式（S35）

BF = dict(
    dept="intel",
    agent_id="XQ1",
    role_profile="雪球与股吧舆情采集哨",
    duty="负责雪球与股吧社媒舆情采集：游标深翻、cookie 注入、幂等落库、停轮判据自治",
    binding="ollama/qwen3.5:9b",
    grade="E2",
    ticket="WO-ORG-211",
    signed_by="老田",
)


def test_backfill_plan_ok_and_no_write(root):
    before = org_bytes(root)
    plan = E.build_backfill_plan(root, **BF)
    assert org_bytes(root) == before, "补档 dry-run 不得写入"
    assert plan["mode"] == "backfill"
    assert plan["agent"]["agent_id"] == "XQ1"
    assert "不改 agents 名单" in plan["writes"][0]


def test_backfill_requires_listed_agent(root):
    """不在名单的 id 应提示走新入编，而不是硬塞。"""
    with pytest.raises(E.EnrollError) as ei:
        E.build_backfill_plan(root, **dict(BF, agent_id="NOPE9"))
    assert "agents 名单" in str(ei.value)


def test_backfill_rejects_existing_profile(root):
    """已有档案者不得补档（避免覆盖他人成果）。dev 部 DEV1 有档案。"""
    with pytest.raises(E.EnrollError) as ei:
        E.build_backfill_plan(root, **dict(BF, dept="dev", agent_id="DEV1"))
    assert "已有档案" in str(ei.value)


def test_backfill_validation_mirrors_enroll(root):
    for kw, kw_assert in (
        (dict(duty="干活"), "duty"),
        (dict(binding="gpt-9/x"), "词表"),
        (dict(grade="E9"), "词表"),
        (dict(ticket=""), "工单"),
        (dict(signed_by="  "), "放行人"),
        (dict(role_profile=""), "role_profile"),
    ):
        with pytest.raises(E.EnrollError) as ei:
            E.build_backfill_plan(root, **dict(BF, **kw))
        assert kw_assert in str(ei.value), kw


def test_backfill_apply_keeps_agents_list_unchanged(root):
    """**承重**：补档只加 profile，agents 名单必须一字不动。"""
    before = json.loads(org_bytes(root).decode("utf-8"))
    intel_before = next(d for d in before["departments"] if d["dept"] == "intel")
    agents_before = list(intel_before["agents"])
    res = E.apply_backfill_plan(
        root, E.build_backfill_plan(root, **BF), signed_by="老田"
    )
    assert res["ok"] is True and res["mode"] == "backfill"

    after = json.loads(org_bytes(root).decode("utf-8"))
    intel_after = next(d for d in after["departments"] if d["dept"] == "intel")
    assert intel_after["agents"] == agents_before, "补档不得改动名单"
    prof = next(p for p in intel_after["agent_profiles"] if p["agent_id"] == "XQ1")
    assert prof["role_profile"] == BF["role_profile"]
    assert "WO-ORG-211" in prof["binding_note"]


def test_backfill_other_depts_and_hr_untouched(root):
    hr_before = (root / "hr" / "XQ1" / "resume.yaml").read_bytes()
    before = json.loads(org_bytes(root).decode("utf-8"))
    E.apply_backfill_plan(root, E.build_backfill_plan(root, **BF), signed_by="老田")
    after = json.loads(org_bytes(root).decode("utf-8"))
    b = {d["dept"]: d for d in before["departments"]}
    a = {d["dept"]: d for d in after["departments"]}
    for k in b:
        if k == "intel":
            continue
        assert json.dumps(b[k], sort_keys=True, ensure_ascii=False) == json.dumps(
            a[k], sort_keys=True, ensure_ascii=False
        ), "%s 被误改" % k
    # 补档不重写履历
    assert (root / "hr" / "XQ1" / "resume.yaml").read_bytes() == hr_before


def test_backfill_rolls_back_on_selfcheck_failure(root, monkeypatch):
    before = org_bytes(root)

    def boom(*a, **k):
        raise E.EnrollError("自检失败（模拟）")

    monkeypatch.setattr(E, "_selfcheck_backfill", boom)
    with pytest.raises(E.EnrollError):
        E.apply_backfill_plan(root, E.build_backfill_plan(root, **BF), signed_by="老田")
    assert org_bytes(root) == before, "自检失败必须回滚"


def test_backfill_signer_mismatch(root):
    plan = E.build_backfill_plan(root, **BF)
    with pytest.raises(E.EnrollError):
        E.apply_backfill_plan(root, plan, signed_by="别人")


def test_backfill_consecutive_same_second_keeps_distinct_backups(root):
    """同秒连补多席不得覆盖彼此的备份——否则回滚只能回到最近一次之前。"""
    ids = ["XQ1", "TDX1", "MANCHONG1"]
    paths = []
    for i, aid in enumerate(ids):
        plan = E.build_backfill_plan(
            root, **dict(BF, agent_id=aid, ticket="WO-ORG-2%02d" % (11 + i))
        )
        res = E.apply_backfill_plan(root, plan, signed_by="老田")
        paths.append(Path(res["backup"]))
    assert len({str(p) for p in paths}) == 3, "三次补档产生了重复备份路径：%s" % paths
    for p in paths:
        assert p.exists()
    # 第一个备份必须仍是「三席都没补」的原始状态
    first = json.loads(paths[0].read_text(encoding="utf-8"))
    intel = next(d for d in first["departments"] if d["dept"] == "intel")
    # 该部门原本没有 agent_profiles 键（None），故用假值判断而非 == []
    assert not intel.get("agent_profiles"), "首次备份已被后续补档污染"


def test_cn_of_id_prefers_cn_name_over_positional_name(tmp_path):
    """履历里 name 常是岗位名（如「军情司上行受理席」）、cn_name 才是人名，且顺序不定。

    一遍扫描会在遇到 name 时提前返回，把岗位名当人名——INTEL0 授名时真实踩到过。
    """
    r = tmp_path / "plat"
    (r / "hr" / "X1").mkdir(parents=True)  # _cn_of_id 只读 hr，不需 registry
    f = r / "hr" / "X1" / "resume.yaml"
    f.write_text("agent_id: X1\nname: 某岗位名\ncn_name: 真名\n", encoding="utf-8")
    assert E._cn_of_id(r, "X1") == "真名"

    # cn_name 在前也要对
    f.write_text("agent_id: X1\ncn_name: 真名二\nname: 某岗位名\n", encoding="utf-8")
    assert E._cn_of_id(r, "X1") == "真名二"

    # 无 cn_name 时退回 name，且岗位式「烽燧·平台巡检哨」取 · 前那段
    f.write_text("agent_id: X1\nname: 烽燧·平台巡检哨\n", encoding="utf-8")
    assert E._cn_of_id(r, "X1") == "烽燧"


def test_binding_allow_covers_rule_only_seat():
    """规则执行体必须能显式声明「无 LLM」，不能被逼着填 9b（那是虚构能力）。"""
    assert "none" in E.BINDING_ALLOW
    assert "glm/glm-5.3-flash" in E.BINDING_ALLOW


def test_backfill_accepts_rule_only_binding(root):
    """BEACON1 式规则巡检席：binding=none 必须能补档成功。"""
    plan = E.build_backfill_plan(
        root,
        **dict(BF, agent_id="BEACON1", binding="none", grade="E2", ticket="WO-ORG-218"),
    )
    assert plan["agent"]["llm_binding"] == "none"
    res = E.apply_backfill_plan(root, plan, signed_by="老田")
    assert res["ok"] is True
    org = json.loads(org_bytes(root).decode("utf-8"))
    intel = next(d for d in org["departments"] if d["dept"] == "intel")
    p = next(x for x in intel["agent_profiles"] if x["agent_id"] == "BEACON1")
    assert p["llm_binding"] == "none", "不得把规则执行体硬绑成 9b"


# ---------------------------------------------------------------- 改档 rebind（S39）


def _intel_prof(root, aid):
    org = json.loads(org_bytes(root).decode("utf-8"))
    intel = next(d for d in org["departments"] if d["dept"] == "intel")
    return next(p for p in intel["agent_profiles"] if p["agent_id"] == aid), intel


def _prep_intel(root, aid="XQ1"):
    """先补一档，供改档用例使用。"""
    E.apply_backfill_plan(
        root, E.build_backfill_plan(root, **dict(BF, agent_id=aid)), signed_by="老田"
    )


def test_rebind_requires_existing_profile(root):
    with pytest.raises(E.EnrollError) as ei:
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="glm/glm-5.3",
            ticket="WO-ORG-219",
            signed_by="老田",
        )
    assert "尚无档案" in str(ei.value)


def test_rebind_rejects_bad_binding_and_missing_creds(root):
    _prep_intel(root)
    with pytest.raises(E.EnrollError):
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="gpt-9/x",
            ticket="WO-ORG-219",
            signed_by="老田",
        )
    with pytest.raises(E.EnrollError):
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="glm/glm-5.3",
            ticket="",
            signed_by="老田",
        )
    with pytest.raises(E.EnrollError):
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="glm/glm-5.3",
            ticket="WO-ORG-219",
            signed_by="  ",
        )


def test_rebind_rejects_same_binding(root):
    _prep_intel(root)
    with pytest.raises(E.EnrollError) as ei:
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="ollama/qwen3.5:9b",
            ticket="WO-ORG-219",
            signed_by="老田",
        )
    assert "无需改档" in str(ei.value)


def test_rebind_apply_changes_only_binding_fields(root):
    """**承重**：改档只动 llm_binding 与 binding_note，其余字段逐项不变。"""
    _prep_intel(root)
    before_prof, _ = _intel_prof(root, "XQ1")
    plan = E.build_rebind_plan(
        root,
        dept="intel",
        agent_id="XQ1",
        binding="glm/glm-5.3",
        ticket="WO-ORG-219",
        signed_by="老田",
        reason="flash 档实调 400",
    )
    assert plan["from_binding"] == "ollama/qwen3.5:9b"
    res = E.apply_rebind_plan(root, plan, signed_by="老田")
    assert res["ok"] is True and res["to_binding"] == "glm/glm-5.3"

    after_prof, _ = _intel_prof(root, "XQ1")
    assert after_prof["llm_binding"] == "glm/glm-5.3"
    assert "WO-ORG-219" in after_prof["binding_note"]
    assert "flash 档实调 400" in after_prof["binding_note"]
    for k, v in before_prof.items():
        if k in ("llm_binding", "binding_note"):
            continue
        assert after_prof[k] == v, "字段 %s 被改档误伤" % k


def test_rebind_leaves_other_seats_and_depts_untouched(root):
    _prep_intel(root, "XQ1")
    _prep_intel(root, "TDX1")
    before = json.loads(org_bytes(root).decode("utf-8"))
    E.apply_rebind_plan(
        root,
        E.build_rebind_plan(
            root,
            dept="intel",
            agent_id="XQ1",
            binding="glm/glm-5.3",
            ticket="WO-ORG-219",
            signed_by="老田",
        ),
        signed_by="老田",
    )
    after = json.loads(org_bytes(root).decode("utf-8"))
    b = {d["dept"]: d for d in before["departments"]}
    a = {d["dept"]: d for d in after["departments"]}
    for k in b:
        if k == "intel":
            continue
        assert json.dumps(b[k], sort_keys=True, ensure_ascii=False) == json.dumps(
            a[k], sort_keys=True, ensure_ascii=False
        ), "%s 被改档误伤" % k
    # 同部门另一席不动
    t1 = next(p for p in b["intel"]["agent_profiles"] if p["agent_id"] == "TDX1")
    t2 = next(p for p in a["intel"]["agent_profiles"] if p["agent_id"] == "TDX1")
    assert json.dumps(t1, sort_keys=True, ensure_ascii=False) == json.dumps(
        t2, sort_keys=True, ensure_ascii=False
    )


def test_rebind_rolls_back_on_selfcheck_failure(root, monkeypatch):
    _prep_intel(root)
    before = org_bytes(root)

    def boom(*a, **k):
        raise E.EnrollError("自检失败（模拟）")

    monkeypatch.setattr(E, "_selfcheck_rebind", boom)
    with pytest.raises(E.EnrollError):
        E.apply_rebind_plan(
            root,
            E.build_rebind_plan(
                root,
                dept="intel",
                agent_id="XQ1",
                binding="glm/glm-5.3",
                ticket="WO-ORG-219",
                signed_by="老田",
            ),
            signed_by="老田",
        )
    assert org_bytes(root) == before, "自检失败必须回滚"


def test_rebind_toctou_binding_changed_after_plan(root):
    _prep_intel(root)
    plan = E.build_rebind_plan(
        root,
        dept="intel",
        agent_id="XQ1",
        binding="glm/glm-5.3",
        ticket="WO-ORG-219",
        signed_by="老田",
    )
    # 模拟别处先改过绑定
    org = json.loads(org_bytes(root).decode("utf-8"))
    intel = next(d for d in org["departments"] if d["dept"] == "intel")
    next(p for p in intel["agent_profiles"] if p["agent_id"] == "XQ1")[
        "llm_binding"
    ] = "glm/glm-4.6"
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    taken = org_bytes(root)
    with pytest.raises(E.EnrollError) as ei:
        E.apply_rebind_plan(root, plan, signed_by="老田")
    assert "复核失败" in str(ei.value)
    assert org_bytes(root) == taken


# ---------------------------------------------------------------- 连通性冒烟


def test_probe_none_is_rule_only():
    r = E.probe_binding("none")
    assert r["ok"] is True and r["kind"] == "rule_only"
    assert r["latency_ms"] == 0, "规则执行体不应发起任何调用"


def test_probe_llm_ok_and_fail(monkeypatch):
    """冒烟须能分辨通与不通——这正是 S38 踩的坑（词表合法但实调 400）。"""
    from gateway import llm_gateway

    monkeypatch.setattr(
        llm_gateway,
        "chat",
        lambda **k: llm_gateway.make_envelope(
            kind="llm_chat", ok=True, data={"text": "OK"}
        ),
    )
    r = E.probe_binding("ollama/qwen3.5:9b")
    assert r["ok"] is True and r["kind"] == "llm" and r["text"] == "OK"

    monkeypatch.setattr(
        llm_gateway,
        "chat",
        lambda **k: llm_gateway.make_envelope(
            kind="llm_chat",
            ok=False,
            error={"code": "llm_error", "message": "HTTP 400 Bad Request"},
        ),
    )
    r2 = E.probe_binding("glm/glm-5.3-flash")
    assert r2["ok"] is False and "400" in r2["error"]


def test_backfill_handles_null_agent_profiles(tmp_path):
    """agent_profiles 为显式 null（非缺失）时也必须能补档。

    comm 部真实踩到：setdefault 对「键存在但值为 None」不生效，
    取回 None 后 .append 直接 AttributeError。
    """
    r = tmp_path / "plat"
    (r / "registry").mkdir(parents=True)
    (r / "hr" / "Z9").mkdir(parents=True)
    (r / "hr" / "Z9" / "resume.yaml").write_text("cn_name: 测试\n", encoding="utf-8")
    org = {"departments": [{"dept": "x", "agents": ["Z9"], "agent_profiles": None}]}
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8"
    )

    plan = E.build_backfill_plan(
        r,
        dept="x",
        agent_id="Z9",
        role_profile="测试岗",
        duty="这是一段足够长的职责描述用于测试补档健壮性",
        binding="ollama/qwen3.5:9b",
        grade="E2",
        ticket="WO-ORG-299",
        signed_by="老田",
    )
    res = E.apply_backfill_plan(r, plan, signed_by="老田")
    assert res["ok"] is True
    after = json.loads((r / "registry" / "org.json").read_text(encoding="utf-8"))
    assert [p["agent_id"] for p in after["departments"][0]["agent_profiles"]] == ["Z9"]


# ------------------------------------------------ 主官任命（--set-lead / S41）
#
# 口径：一律取 fixture 里的真实结构做动态比对，不写死「某部门主官是谁」
# ——2026-10-21 已因写死真实数据踩红三次。


def _make_leadless(r):
    """在 tmp 副本里**造**一个主官空悬的部门并可写回。

    为什么不去真实盘面找：S43 已把最后一个空悬部门（proj_backtest）任命掉，
    「找一个 lead 为空的部门」会直接 skip——**测试因盘面变好而静默失去覆盖**，
    比红更危险（2026-10-21 真实踩到：6 个任命用例整批 skip）。
    """
    org = json.loads((r / "registry" / "org.json").read_text(encoding="utf-8"))
    d = next(x for x in org["departments"] if x.get("agents"))
    d["lead"] = None
    d.pop("lead_appointed", None)
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return d


def _make_ghost_member(r):
    """在 tmp 里造一个「在名单但三处都无档案」的席位（同理，不找真实盘面）。"""
    org = json.loads((r / "registry" / "org.json").read_text(encoding="utf-8"))
    d = next(x for x in org["departments"] if x.get("agents"))
    d["agents"].append("GHOST9")
    (r / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return d, "GHOST9"


def test_set_lead_plan_writes_nothing(root):
    d = _make_leadless(root)
    before = org_bytes(root)
    plan = E.build_set_lead_plan(
        root,
        dept=d["dept"],
        lead=d["agents"][0],
        ticket="WO-LEGION-S41",
        signed_by="老田",
    )
    assert org_bytes(root) == before, "dry-run 不得写入任何字节"
    assert plan["mode"] == "set_lead" and plan["note"].startswith("dry-run")
    assert plan["from_lead"] == "" and plan["to_lead"] == d["agents"][0]
    assert plan["cross_dept"] is False


def test_set_lead_requires_profiled_lead(root):
    """无档案者不得任命：那只是把「主官空悬」换成「主官无档」。"""
    d, aid = _make_ghost_member(root)
    with pytest.raises(E.EnrollError) as ei:
        E.build_set_lead_plan(
            root,
            dept="dev",
            lead=aid,
            ticket="WO-LEGION-S41",
            signed_by="老田",
            cross_note="跨部门兼任测试说明需要足够长的一句话",
        )
    assert "尚无档案" in str(ei.value)


def test_set_lead_cross_dept_needs_note(root):
    d = _make_leadless(root)
    other = next(
        a
        for dep in json.loads(
            (root / "registry" / "org.json").read_text(encoding="utf-8")
        )["departments"]
        if dep["dept"] != d["dept"]
        for a in (dep.get("agents") or [])
        if str(a) not in (d.get("agents") or [])
    )
    with pytest.raises(E.EnrollError) as ei:
        E.build_set_lead_plan(
            root, dept=d["dept"], lead=other, ticket="WO-LEGION-S41", signed_by="老田"
        )
    assert "跨部门兼任" in str(ei.value)
    plan = E.build_set_lead_plan(
        root,
        dept=d["dept"],
        lead=other,
        ticket="WO-LEGION-S41",
        signed_by="老田",
        cross_note="该席跨部门兼任本营主官，指挥链经真源显式登记",
    )
    assert plan["cross_dept"] is True and plan["cross_note"]


def test_set_lead_rejects_noop_and_missing_creds(root):
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    dep = next(d for d in org["departments"] if d.get("lead") and d.get("agents"))
    with pytest.raises(E.EnrollError) as ei:
        E.build_set_lead_plan(
            root,
            dept=dep["dept"],
            lead=dep["lead"],
            ticket="WO-LEGION-S41",
            signed_by="老田",
        )
    assert "已是" in str(ei.value)
    d = _make_leadless(root)
    with pytest.raises(E.EnrollError):
        E.build_set_lead_plan(
            root, dept=d["dept"], lead=d["agents"][0], ticket="", signed_by="老田"
        )
    with pytest.raises(E.EnrollError):
        E.build_set_lead_plan(
            root,
            dept=d["dept"],
            lead=d["agents"][0],
            ticket="WO-LEGION-S41",
            signed_by="",
        )
    with pytest.raises(E.EnrollError):
        E.build_set_lead_plan(
            root,
            dept=d["dept"],
            lead="not-an-id",
            ticket="WO-LEGION-S41",
            signed_by="老田",
        )


def test_set_lead_apply_changes_only_lead_and_leaves_trace(root):
    d = _make_leadless(root)
    dept_name, lead = d["dept"], d["agents"][0]
    before_org = json.loads(
        (root / "registry" / "org.json").read_text(encoding="utf-8")
    )
    plan = E.build_set_lead_plan(
        root, dept=dept_name, lead=lead, ticket="WO-LEGION-S41", signed_by="老田"
    )
    res = E.apply_set_lead_plan(root, plan, signed_by="老田")
    assert res["ok"] is True and Path(res["backup"]).exists()

    after_org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    b = {x["dept"]: x for x in before_org["departments"]}
    a = {x["dept"]: x for x in after_org["departments"]}
    assert a[dept_name]["lead"] == lead
    ap = a[dept_name]["lead_appointed"]
    assert (
        ap["to"] == lead
        and ap["ticket"] == "WO-LEGION-S41"
        and ap["signed_by"] == "老田"
    )
    # 名单与档案一字未动：任命不是入编
    assert a[dept_name]["agents"] == b[dept_name]["agents"]
    assert json.dumps(
        a[dept_name].get("agent_profiles"), sort_keys=True, ensure_ascii=False
    ) == json.dumps(
        b[dept_name].get("agent_profiles"), sort_keys=True, ensure_ascii=False
    )
    # 其他部门一字未动
    for name in b:
        if name == dept_name:
            continue
        assert json.dumps(b[name], sort_keys=True, ensure_ascii=False) == json.dumps(
            a[name], sort_keys=True, ensure_ascii=False
        )


def test_set_lead_toctou_and_signer_mismatch(root):
    d = _make_leadless(root)
    plan = E.build_set_lead_plan(
        root,
        dept=d["dept"],
        lead=d["agents"][0],
        ticket="WO-LEGION-S41",
        signed_by="老田",
    )
    with pytest.raises(E.EnrollError):
        E.apply_set_lead_plan(root, plan, signed_by="他人")
    # 计划生成后真源被别人改了主官：执行前复核必须拦下
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    for x in org["departments"]:
        if x["dept"] == d["dept"]:
            x["lead"] = "RIVAL1"
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with pytest.raises(E.EnrollError) as ei:
        E.apply_set_lead_plan(root, plan, signed_by="老田")
    assert "当前主官已变" in str(ei.value)


def test_set_lead_rolls_back_on_selfcheck_failure(root, monkeypatch):
    d = _make_leadless(root)
    plan = E.build_set_lead_plan(
        root,
        dept=d["dept"],
        lead=d["agents"][0],
        ticket="WO-LEGION-S41",
        signed_by="老田",
    )
    before = org_bytes(root)

    def boom(*a, **k):
        raise E.EnrollError("自检失败：模拟")

    monkeypatch.setattr(E, "_selfcheck_set_lead", boom)
    with pytest.raises(E.EnrollError):
        E.apply_set_lead_plan(root, plan, signed_by="老田")
    monkeypatch.undo()
    assert org_bytes(root) == before, "自检失败必须用备份还原，不留半成品"


def test_set_lead_announce_cross_dept_without_changing_lead(root):
    """主官没变、但缺兼任留痕时允许补登记（不改指挥关系，只补事实凭证）。

    真实场景：A0/A0A 的 lead 早就填了，`lead_appointed` 一个字没有，
    审计只能报「指挥链跨部门」。补登记后应降级为「已显式登记」。

    ⚠️ 本用例**自己构造**前置状态，不依赖真实盘面是否已登记过兼任——
    真实 org.json 一旦补过，复制出来的 fixture 就不再有「待补登记」这个前提
    （2026-10-21 S42 真实踩到：修完 A0A 该用例立刻红）。
    """
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    dep = org["departments"][0]
    members = {str(a) for a in (dep.get("agents") or [])}
    outside = next(
        str(a)
        for d in org["departments"]
        for a in (d.get("agents") or [])
        if str(a) not in members
    )
    dep["lead"] = outside
    dep.pop("lead_appointed", None)
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    note = "该席跨部门兼任本营主官，指挥链经真源显式登记"
    plan = E.build_set_lead_plan(
        root,
        dept=dep["dept"],
        lead=outside,
        ticket="WO-LEGION-S42",
        signed_by="老田",
        cross_note=note,
    )
    assert plan["action"] == "announce_cross"
    assert plan["from_lead"] == plan["to_lead"] == outside
    res = E.apply_set_lead_plan(root, plan, signed_by="老田")
    assert res["ok"] is True
    after = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    ad = next(d for d in after["departments"] if d["dept"] == dep["dept"])
    assert ad["lead"] == outside, "补登记不得改动主官"
    assert ad["lead_appointed"]["cross_note"] == note
    # 幂等：已有留痕再跑一次必须拒绝，不许刷时间戳
    with pytest.raises(E.EnrollError) as ei:
        E.build_set_lead_plan(
            root,
            dept=dep["dept"],
            lead=outside,
            ticket="WO-LEGION-S42",
            signed_by="老田",
            cross_note=note,
        )
    assert "已登记" in str(ei.value)
