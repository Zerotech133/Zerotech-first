# -*- coding: utf-8 -*-
"""M4 强制开关单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。

覆盖：默认灰度放行 / 强制失败拒绝 / 强制成功放行 / 配置缺失回退灰度 /
env 覆盖策略 / enforce_after 日期 / reject 不受强制影响 / 坏配置回退。
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import approval_control as AC  # noqa: E402

ROLE_MAP = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU", "ou_verify1": "VERIFY1",
            "ou_a0": "A0"}
NO_ENV = {}


def _payload(tag="approval:approve", task="M4", op="ou_qa1"):
    return {"operator": {"open_id": op},
            "action": {"value": {"tag": tag, "task": task, "level": "S2"}}}


def _sign(ledger, op, **kwargs):
    kwargs.setdefault("signoff_roles", ROLE_MAP)
    kwargs.setdefault("m4_env", NO_ENV)
    return AC.handle_action(_payload(op=op), ledger=ledger, **kwargs)


# ----------------------------------------------------------------------
# 纯函数：开关解析
# ----------------------------------------------------------------------
def test_state_default_gray_without_policy_and_env():
    st = AC.m4_enforce_state(policy={}, env=NO_ENV)
    assert st["enabled"] is False
    assert st["source"] == "default_gray"


def test_state_env_overrides_policy(monkeypatch):
    st = AC.m4_enforce_state(policy={"m4_enforce": False},
                             env={"M4_SIGNOFF_ENFORCE": "1"})
    assert st["enabled"] is True and st["source"] == "env"
    st2 = AC.m4_enforce_state(policy={"m4_enforce": True},
                              env={"M4_SIGNOFF_ENFORCE": "0"})
    assert st2["enabled"] is False and st2["source"] == "env"


def test_state_policy_bool_beats_enforce_after():
    st = AC.m4_enforce_state(policy={"m4_enforce": False,
                                     "enforce_after": "2026-01-01"}, env=NO_ENV)
    assert st["enabled"] is False and st["source"] == "policy"


def test_state_enforce_after_only_when_policy_flag_absent():
    st = AC.m4_enforce_state(policy={"enforce_after": "2026-10-01"}, env=NO_ENV)
    assert st["enabled"] is True and st["source"] == "enforce_after"
    st2 = AC.m4_enforce_state(policy={"enforce_after": "2099-01-01"}, env=NO_ENV)
    assert st2["enabled"] is False


def test_state_corrupt_or_missing_policy_falls_back_to_gray(monkeypatch, tmp_path):
    bad = tmp_path / "bad_policy.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(AC, "POLICY_PATH", bad)
    monkeypatch.setattr(AC, "_POLICY_CACHE", {"mtime": None, "data": None})
    st = AC.m4_enforce_state(env=NO_ENV)
    assert st["enabled"] is False and st["source"] == "default_gray"
    monkeypatch.setattr(AC, "POLICY_PATH", tmp_path / "no_such.json")
    st2 = AC.m4_enforce_state(env=NO_ENV)
    assert st2["enabled"] is False and st2["source"] == "default_gray"


def test_state_reads_real_policy_file_is_gray(monkeypatch):
    """仓内默认策略文件必须是灰度（不阻断存量）。"""
    monkeypatch.setattr(AC, "_POLICY_CACHE", {"mtime": None, "data": None})
    st = AC.m4_enforce_state(env=NO_ENV)
    assert st["enabled"] is False
    raw = json.loads(AC.POLICY_PATH.read_text(encoding="utf-8"))
    assert raw["m4_enforce"] is False


# ----------------------------------------------------------------------
# handle_action 集成：默认灰度放行
# ----------------------------------------------------------------------
def test_default_gray_single_sign_passes_with_warning(tmp_path):
    ledger = tmp_path / "a.jsonl"
    r = _sign(ledger, "ou_qa1", m4_policy={})
    assert r["ok"] is True                      # 动作已记录（灰度不阻断）
    assert r["signoff_state"] == "incomplete"
    assert r["approved"] is False
    assert "error" not in r
    assert any("m4_signoff_incomplete" in w for w in r["warnings"])
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


# ----------------------------------------------------------------------
# handle_action 集成：强制开启
# ----------------------------------------------------------------------
def test_enforce_blocks_incomplete_approve(tmp_path):
    ledger = tmp_path / "a.jsonl"
    enforce = {"m4_enforce": True}
    r = _sign(ledger, "ou_qa1", m4_policy=enforce)
    assert r["ok"] is False
    assert r["error"] == "m4_signoff_enforce_incomplete"
    assert r["approved"] is False
    assert r["signoff_state"] == "incomplete"
    assert r["enforce"]["enabled"] is True and r["enforce"]["blocked"] is True
    assert "GUANYU" in r["enforce"]["missing_signers"]
    # 审批行仍落账：签名可继续累积（不因拒绝而丢签）
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_enforce_allows_complete_four_party(tmp_path):
    ledger = tmp_path / "a.jsonl"
    enforce = {"m4_enforce": True}
    for op in ("ou_qa1", "ou_guanyu", "ou_verify1"):
        r = _sign(ledger, op, m4_policy=enforce)
        assert r["ok"] is False
        assert r["error"] == "m4_signoff_enforce_incomplete"
    last = _sign(ledger, "ou_a0", m4_policy=enforce)
    assert last["ok"] is True
    assert last["approved"] is True
    assert last["signoff_state"] == "complete"
    assert "error" not in last
    assert last["enforce"]["blocked"] is False


def test_enforce_env_override_blocks(tmp_path):
    r = _sign(tmp_path / "a.jsonl", "ou_qa1", m4_policy={"m4_enforce": False},
              m4_env={"M4_SIGNOFF_ENFORCE": "1"})
    assert r["ok"] is False and r["error"] == "m4_signoff_enforce_incomplete"


def test_enforce_does_not_block_reject(tmp_path):
    r = AC.handle_action(_payload("approval:reject"), ledger=tmp_path / "a.jsonl",
                         m4_policy={"m4_enforce": True}, m4_env=NO_ENV)
    assert r["ok"] is True and "error" not in r


def test_enforce_complete_duplicate_click_still_pass(tmp_path):
    ledger = tmp_path / "a.jsonl"
    enforce = {"m4_enforce": True}
    for op in ("ou_qa1", "ou_guanyu", "ou_verify1", "ou_a0"):
        _sign(ledger, op, m4_policy=enforce)
    dup = _sign(ledger, "ou_a0", m4_policy=enforce)
    assert dup["duplicate"] is True
    assert dup["ok"] is True and dup["approved"] is True
