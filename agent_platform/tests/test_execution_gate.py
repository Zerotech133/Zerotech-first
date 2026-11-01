# -*- coding: utf-8 -*-
"""HQ L0/L1 执行门测试（W-C4 / K-ORG-20261101-HQEXEC）。

覆盖（任务验收口径）：
- L2 一律 deny（即使其余输入齐全）；
- 无证据 deny / 无回滚 deny / 未知动作 deny；
- L0 白名单 allow / L1 白名单 allow；
- kill switch：未配置 deny、已触发 deny（env/file 两种来源）；
- rollback schema 校验（缺步/缺 command/verify → deny；合规 → allow）；
- schema 校验：risk_level/共识/证据/confirmation/blast radius/kill switch 注入；
- 红线：本门只判定不执行（返回值 dry_run=True/executed=False，且模块不
  引入 subprocess/ctypes 等执行栈、不写文件）。

全部离线、纯函数；kill switch 用 env/kill_file 注入，不读写生产。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from command_hq import execution_gate as G  # noqa: E402


# ---------------------------------------------------------------- 合法输入
def _consensus(**kw):
    base = {"schema": "hq_consensus/v1", "status": "auto_ready",
            "p0_oppose": False, "agree": 4, "total": 5}
    base.update(kw)
    return base


def _evidence(**kw):
    item = {"kind": "test", "ref": "pytest agent_platform/tests/test_x.py",
            "digest": "sha256:" + "a" * 64, "verified": True}
    item.update(kw)
    return [item]


def _rollback(**kw):
    plan = {"schema": G.ROLLBACK_SCHEMA,
            "steps": [{"command": "git revert HEAD", "verify": "pytest -q"}],
            "max_duration_s": 300, "owner": "HQ"}
    plan.update(kw)
    return plan


def _kill(configured=True, active=False):
    return {"configured": configured, "active": active, "source": "unit-test"}


def _kw(**over):
    base = {
        "action": "docs.edit",
        "risk_level": "L0",
        "consensus": _consensus(),
        "evidence": _evidence(),
        "rollback": _rollback(),
        "kill_switch": _kill(),
        "env": {},
    }
    base.update(over)
    return base


def _codes(result):
    return {item["code"] for item in result["reasons"]}


# ---------------------------------------------------------------- 基础判定
def test_l2_always_denied_even_with_all_inputs():
    for action, risk in (("docs.edit", "L2"), ("dev.small_task", "L2")):
        result = G.evaluate(**_kw(action=action, risk_level=risk))
        assert result["decision"] == "deny" and result["allow"] is False
        assert "l2_requires_human" in _codes(result)
        assert result["dry_run"] is True and result["executed"] is False


def test_unknown_action_denied():
    result = G.evaluate(**_kw(action="host.reboot"))
    assert result["allow"] is False
    assert "action_not_whitelisted" in _codes(result)
    check = next(c for c in result["checks"]
                 if c["name"] == "action_whitelisted")
    assert "host.reboot" in check["detail"]


def test_no_evidence_denied():
    for evidence in ([], None):
        result = G.evaluate(**_kw(evidence=evidence))
        assert result["allow"] is False
        assert "no_evidence" in _codes(result)


def test_no_rollback_denied():
    for rollback in (None, {}, []):
        result = G.evaluate(**_kw(rollback=rollback))
        assert result["allow"] is False
        assert "no_rollback" in _codes(result)


# ---------------------------------------------------------------- 白名单 allow
def test_l0_whitelist_allows():
    result = G.evaluate(**_kw(action="docs.edit", risk_level="L0"))
    assert result["decision"] == "allow" and result["allow"] is True
    assert result["reasons"] == []
    assert result["action_spec"]["risk_level"] == "L0"
    assert result["kill_switch"]["configured"] is True
    assert result["dry_run"] is True and result["executed"] is False


def test_l1_whitelist_allows_with_confirmation_and_blast_radius():
    result = G.evaluate(**_kw(
        action="dev.small_task", risk_level="L1",
        confirmation={"by": "A0", "at": "2026-11-01T10:00:00+08:00"},
        blast_radius={"files": 12, "scope": "worktree/K-ORG-x"}))
    assert result["allow"] is True
    assert result["confirmation_present"] is True
    assert result["blast_radius"] == {"provided": True, "files": 12,
                                      "scope": "worktree/K-ORG-x"}
    assert result["executed"] is False


# ---------------------------------------------------------------- kill switch
def test_kill_switch_unconfigured_denied():
    result = G.evaluate(**_kw(kill_switch=None, env={},
                              kill_file=Path("__no_such_kill_file__")))
    assert result["allow"] is False
    assert "kill_switch_unconfigured" in _codes(result)
    assert result["executed"] is False


def test_kill_switch_active_denied():
    result = G.evaluate(**_kw(kill_switch=_kill(configured=True, active=True)))
    assert result["allow"] is False
    assert "kill_switch_active" in _codes(result)


def test_kill_switch_env_and_file_sources():
    assert G.kill_switch_state(env={}, kill_file=Path("nope")) == {
        "configured": False, "active": False, "sources": [],
        "env_keys": list(G.KILL_ENV_KEYS),
        "file": "nope", "file_exists": False}
    off = G.kill_switch_state(env={"HQ_EXEC_KILL_SWITCH": "0"},
                              kill_file=Path("nope"))
    assert off["configured"] is True and off["active"] is False
    on = G.kill_switch_state(env={"HQ_EXEC_KILL_SWITCH": "1"},
                             kill_file=Path("nope"))
    assert on["configured"] is True and on["active"] is True
    legacy = G.kill_switch_state(env={"PAPER_KILL": "true"},
                                 kill_file=Path("nope"))
    assert legacy["configured"] is True and legacy["active"] is True


def test_kill_switch_file_engaged_denies(tmp_path):
    kill_file = tmp_path / "KILL"
    kill_file.write_text("", encoding="utf-8")
    result = G.evaluate(**_kw(kill_switch=None, env={}, kill_file=kill_file))
    assert result["allow"] is False
    assert "kill_switch_active" in _codes(result)
    state = G.kill_switch_state(env={}, kill_file=kill_file)
    assert state["configured"] is True and state["active"] is True


# ---------------------------------------------------------------- rollback schema
def test_rollback_schema_validated():
    bad = [
        {"schema": "wrong/v1", "steps": [{"command": "x", "verify": "y"}]},
        {"schema": G.ROLLBACK_SCHEMA, "steps": []},
        {"schema": G.ROLLBACK_SCHEMA,
         "steps": [{"command": "x"}]},                    # 缺 verify
        {"schema": G.ROLLBACK_SCHEMA,
         "steps": [{"command": "", "verify": "y"}]},      # 空 command
        {"schema": G.ROLLBACK_SCHEMA,
         "steps": [{"command": "x", "verify": "y"}],
         "max_duration_s": 99999},                        # 超上限
    ]
    for plan in bad:
        result = G.evaluate(**_kw(rollback=plan))
        assert result["allow"] is False, plan
        assert "rollback_schema_invalid" in _codes(result), plan
    ok = G.validate_rollback(_rollback())
    assert ok["ok"] is True and ok["steps"] == 1


# ---------------------------------------------------------------- 其它 schema
def test_risk_level_mismatch_denied():
    # docs.edit 登记 L0，却报 L1 → 禁止自我降级/错配
    result = G.evaluate(**_kw(risk_level="L1"))
    assert result["allow"] is False
    assert "risk_level_mismatch" in _codes(result)
    bad = G.evaluate(**_kw(risk_level="L9"))
    assert bad["allow"] is False
    assert "bad_risk_level" in _codes(bad)


def test_consensus_schema_and_quorum():
    for consensus, code in (
            ({"status": "escalated"}, "consensus_not_auto_ready"),
            ({"status": "auto_ready", "p0_oppose": True},
             "consensus_p0_oppose"),
            ({"status": "auto_ready", "agree": 2},
             "consensus_quorum_unmet"),
            ({"status": "weird"}, "consensus_schema_invalid"),
            ("auto_ready", "consensus_schema_invalid")):
        result = G.evaluate(**_kw(consensus=consensus))
        assert result["allow"] is False, consensus
        assert code in _codes(result), (consensus, _codes(result))


def test_evidence_schema_and_verified_required():
    unverified = G.evaluate(**_kw(evidence=_evidence(verified=False)))
    assert unverified["allow"] is False
    assert "evidence_unverified" in _codes(unverified)
    bad_digest = G.evaluate(**_kw(evidence=_evidence(digest="md5:abc")))
    assert bad_digest["allow"] is False
    assert "evidence_schema_invalid" in _codes(bad_digest)
    bad_kind = G.evaluate(**_kw(evidence=_evidence(kind="rumor")))
    assert bad_kind["allow"] is False
    assert "evidence_schema_invalid" in _codes(bad_kind)


def test_confirmation_and_kill_switch_injection_schema():
    bad_conf = G.evaluate(**_kw(confirmation={"by": "", "at": "x"}))
    assert bad_conf["allow"] is False
    assert "confirmation_schema_invalid" in _codes(bad_conf)
    bad_kill = G.evaluate(**_kw(kill_switch={"configured": "yes"}))
    assert bad_kill["allow"] is False
    assert "kill_switch_schema_invalid" in _codes(bad_kill)


def test_blast_radius_limit():
    over = G.evaluate(**_kw(blast_radius={"files": 51, "scope": "docs"}))
    assert over["allow"] is False
    assert "blast_radius_exceeded" in _codes(over)
    within = G.evaluate(**_kw(blast_radius={"files": 3, "scope": "docs"}))
    assert within["allow"] is True
    bad = G.evaluate(**_kw(blast_radius={"files": -1}))
    assert bad["allow"] is False
    assert "blast_radius_schema_invalid" in _codes(bad)


# ---------------------------------------------------------------- 红线：不执行
def test_gate_is_decision_only_no_execution_imports():
    src = Path(G.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    forbidden = {"subprocess", "shlex", "ctypes", "socket", "urllib",
                 "requests", "httpx", "feishu", "lark_oapi", "portal",
                 "engine", "command_hq"}
    assert roots & forbidden == set()
    assert roots <= {"os", "re", "time", "pathlib", "__future__"}
    for token in ("os.system", "subprocess.", "exec(", "eval(", "Popen"):
        assert token not in src


def test_evaluate_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    result = G.evaluate(**_kw(
        kill_switch=None, env={"HQ_EXEC_KILL_SWITCH": "0"},
        kill_file=tmp_path / "KILL"))
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after == []          # 零写盘（kill 文件也未创建）
    assert result["allow"] is True
    assert result["dry_run"] is True and result["executed"] is False
    json.dumps(result, ensure_ascii=False)  # 可序列化（供编排器/审计）


def test_decision_schema_fields():
    result = G.evaluate(**_kw())
    for key in ("schema", "gate_schema", "decision", "allow", "reasons",
                "checks", "action", "action_spec", "risk_level",
                "consensus_status", "evidence_count", "rollback_steps",
                "confirmation_present", "blast_radius", "kill_switch",
                "dry_run", "executed", "evaluated_at"):
        assert key in result
    assert result["schema"] == G.DECISION_SCHEMA
    assert result["gate_schema"] == G.GATE_SCHEMA
    assert all(isinstance(c["ok"], bool) for c in result["checks"])
