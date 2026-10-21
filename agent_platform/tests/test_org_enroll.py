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
    for d in (_PLATFORM / "hr").iterdir():
        f = d / "resume.yaml"
        if d.is_dir() and f.exists():
            (r / "hr" / d.name).mkdir()
            shutil.copy2(f, r / "hr" / d.name / "resume.yaml")
    return r


OK = dict(dept="dev", agent_id="DEV9", cn_name="蒋琬", role_profile="日志工程师",
          duty="负责全链路运行日志治理与异常归因，维护日志规范与轮转策略",
          binding="ollama/qwen3.5:9b", grade="E2",
          ticket="WO-ORG-210", signed_by="老田")


def org_bytes(r):
    return (r / "registry" / "org.json").read_bytes()


# ---------------------------------------------------------------- dry-run

def test_plan_writes_nothing(root):
    before = org_bytes(root)
    plan = E.build_plan(root, **OK)
    assert org_bytes(root) == before, "dry-run 不得写入任何字节"
    assert plan["agent"]["agent_id"] == "DEV9"
    assert plan["note"].startswith("dry-run")
    assert plan["cn_name"] == "蒋琬"
    # 「这个 Agent 到底干嘛」必须落进 charter_detail
    assert plan["agent"]["charter_detail"].startswith("负责全链路")


def test_plan_unknown_dept(root):
    kw = dict(OK, dept="no_such_dept")
    with pytest.raises(E.EnrollError) as ei:
        E.build_plan(root, **kw)
    assert "unknown dept" in str(ei.value)


def test_plan_duplicate_agent_id(root):
    kw = dict(OK, agent_id="DEV1")                     # dev 部已有 DEV1
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
    assert "name: 蒋琬" in txt and "WO-ORG-210" in txt and "日志工程师" in txt


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
        assert json.dumps(b[k], sort_keys=True, ensure_ascii=False) == \
               json.dumps(a[k], sort_keys=True, ensure_ascii=False), "%s 被误改" % k


def test_apply_creates_backup(root):
    """备份必须是**入编前原文的字节级副本**——只检查文件存在等于没测（M5 补强）。"""
    before = org_bytes(root)
    res = E.apply_plan(root, E.build_plan(root, **OK), signed_by="老田")
    bak = Path(res["backup"])
    assert bak.exists() and bak.parent.name == "backups"
    assert bak.read_bytes() == before, "备份须与入编前 org.json 字节一致，否则回滚等于没救"


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
    plan = E.build_plan(root, **OK)          # 此刻 hr/DEV9 尚不存在
    (root / "hr" / "DEV9").mkdir()
    (root / "hr" / "DEV9" / "resume.yaml").write_text("agent_id: DEV9\n", encoding="utf-8")
    before = org_bytes(root)
    with pytest.raises(E.EnrollError) as ei:
        E.apply_plan(root, plan, signed_by="老田")
    # hr 目录属编制本身，故拦住它的是 id 复核这一道（不是另一段死代码）
    assert "已被占用" in str(ei.value) and "hr" in str(ei.value)
    assert org_bytes(root) == before, "拒绝时不得改动 org.json"
    # 既有履历一文不动
    assert (root / "hr" / "DEV9" / "resume.yaml").read_text(encoding="utf-8") == "agent_id: DEV9\n"


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
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
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
    E.load_org(_PLATFORM)                      # 只读加载一次
    E.build_plan(_PLATFORM, **dict(OK, agent_id="ZZTOP1", cn_name="测试不写字"))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h, "真实 org.json 被改动！"
