# -*- coding: utf-8 -*-
"""M4 签字角色映射接线测试（2026-10-31 WO-M4-SIGNERMAP-20261031）。

覆盖工单要求：映射命中 / 未命中 / 坏配置 / 空配置；单签 incomplete、四签
complete；以及 ``feishu_bot_ws._handle_approval_card`` 把 ``signoff_roles``
传给 ``approval_control.handle_action`` 的接线。

纪律：全部夹具落系统临时目录（tmp_path），**不写真实 approvals.jsonl**、
不伪造签字；真实 ``approval_signers.json`` 只读断言（当前应为空 map）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "portal", ROOT / "agent_platform" / "engine"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import approval_control as AC  # noqa: E402
import feishu_bot_ws as WS  # noqa: E402

REAL_CONFIG = ROOT / "agent_platform" / "config" / "approval_signers.json"


def _write_config(tmp_path, mapping, name="approval_signers.json", note="测试夹具"):
    p = tmp_path / name
    p.write_text(json.dumps({"map": mapping, "note": note},
                            ensure_ascii=False), encoding="utf-8")
    return p


def _rows(*ops, task="M4"):
    return [{"ts": "2026-10-31T09:%02d:00" % i, "task": task,
             "action": "approve", "operator": op}
            for i, op in enumerate(ops)]


FULL_MAP = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU",
            "ou_verify1": "VERIFY1", "ou_a0": "A0"}


# ----------------------------------------------------------------------
# 契约：映射加载（命中/未命中/坏配置/空配置，缓存）
# ----------------------------------------------------------------------
def test_load_signers_mapped_and_normalized(tmp_path):
    p = _write_config(tmp_path, {
        "ou_qa1": "qa1",          # 小写 → 归一
        "ou_a0": " A0 ",          # 空白 → 归一
        "ou_bad": 3,              # 非字符串角色 → 丢弃
        "": "QA1",                # 空 open_id → 丢弃
        "ou_blank": "   ",        # 空角色 → 丢弃
    })
    assert WS._load_approval_signers(str(p), use_cache=False) == {
        "ou_qa1": "QA1", "ou_a0": "A0"}


def test_load_signers_missing_file_returns_empty(tmp_path):
    assert WS._load_approval_signers(
        str(tmp_path / "nope.json"), use_cache=False) == {}


def test_load_signers_bad_json_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert WS._load_approval_signers(str(p), use_cache=False) == {}


def test_load_signers_non_dict_returns_empty(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[]", encoding="utf-8")
    assert WS._load_approval_signers(str(p), use_cache=False) == {}


def test_load_signers_empty_map_allowed(tmp_path):
    p = _write_config(tmp_path, {})
    assert WS._load_approval_signers(str(p), use_cache=False) == {}


def test_load_signers_cache_invalidated_when_config_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(WS, "_APPROVAL_SIGNERS_CACHE",
                        {"stamp": None, "map": {}})
    p = _write_config(tmp_path, {"ou_qa1": "QA1"})
    assert WS._load_approval_signers(str(p)) == {"ou_qa1": "QA1"}
    _write_config(tmp_path, {"ou_qa1": "QA1", "ou_a0": "A0"})   # size 变化 → 失效
    assert WS._load_approval_signers(str(p)) == {
        "ou_qa1": "QA1", "ou_a0": "A0"}


def test_load_signers_cache_returns_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(WS, "_APPROVAL_SIGNERS_CACHE",
                        {"stamp": None, "map": {}})
    p = _write_config(tmp_path, {"ou_qa1": "QA1"})
    first = WS._load_approval_signers(str(p))
    first["injected"] = "A0"        # 污染返回值不得污染缓存
    assert "injected" not in WS._load_approval_signers(str(p))


def test_real_config_exists_and_map_is_empty():
    """真实配置存在且为**空 map**：pilot 未配置 → 谁都不认识（不伪造签字）。"""
    assert REAL_CONFIG.is_file()
    doc = json.loads(REAL_CONFIG.read_text(encoding="utf-8"))
    assert doc["map"] == {}
    assert str(doc.get("note") or "").strip()
    assert WS._load_approval_signers(str(REAL_CONFIG), use_cache=False) == {}


# ----------------------------------------------------------------------
# 判定：单签 incomplete / 四签 complete / 未命中不猜
# ----------------------------------------------------------------------
def test_mapped_single_sign_incomplete():
    r = AC.evaluate_signoff(_rows("ou_qa1"), "M4", role_of=FULL_MAP)
    assert r["state"] == "incomplete" and r["ok"] is False
    assert r["signed_roles"] == ["QA1"]
    assert r["missing_signers"] == ["GUANYU", "VERIFY1", "A0"]
    assert r["distinct_signers"] == 1


def test_mapped_four_signers_complete():
    r = AC.evaluate_signoff(
        _rows("ou_qa1", "ou_guanyu", "ou_verify1", "ou_a0"), "M4",
        role_of=FULL_MAP)
    assert r["state"] == "complete" and r["ok"] is True
    assert r["signed_roles"] == ["QA1", "GUANYU", "VERIFY1", "A0"]
    assert r["missing_signers"] == []


def test_unknown_open_id_not_guessed():
    """未命中映射的 open_id：角色留空 → roles_unresolved，不猜成任何席位。"""
    rows = _rows("ou_qa1", "ou_unknown_a", "ou_unknown_b", "ou_unknown_c")
    r = AC.evaluate_signoff(rows, "M4", role_of=FULL_MAP)
    assert r["state"] == "incomplete"
    assert r["signed_roles"] == ["QA1"]
    assert [s["role"] for s in r["signers"]] == ["QA1", "", "", ""]
    assert r["reason"] == "roles_unresolved"


def test_empty_map_never_complete():
    rows = _rows("ou_qa1", "ou_guanyu", "ou_verify1", "ou_a0")
    for role_of in ({}, None):
        r = AC.evaluate_signoff(rows, "M4", role_of=role_of)
        assert r["state"] == "incomplete"
        assert r["reason"] == "roles_unresolved"
        assert r["signed_roles"] == []


# ----------------------------------------------------------------------
# 接线：_handle_approval_card 必须把映射传给 handle_action
# ----------------------------------------------------------------------
def _capture_handle(monkeypatch):
    captured = {}

    def fake_handle(payload, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AC, "handle_action", fake_handle)
    return captured


def test_handle_approval_card_passes_signoff_roles(monkeypatch):
    captured = _capture_handle(monkeypatch)
    monkeypatch.setattr(WS, "_load_approval_signers",
                        lambda *a, **k: {"ou_qa1": "QA1"})
    monkeypatch.delenv("APPROVAL_OPERATORS", raising=False)
    out = WS._handle_approval_card(
        {"tag": "approval:approve", "task": "M4"}, "ou_qa1")
    assert out == {"ok": True}
    assert captured["signoff_roles"] == {"ou_qa1": "QA1"}
    assert captured["payload"]["operator"]["open_id"] == "ou_qa1"
    assert captured["payload"]["action"]["value"]["task"] == "M4"


def test_handle_approval_card_empty_config_passes_none(monkeypatch):
    captured = _capture_handle(monkeypatch)
    monkeypatch.setattr(WS, "_load_approval_signers", lambda *a, **k: {})
    WS._handle_approval_card({"tag": "approval:approve", "task": "M4"}, "ou_x")
    assert captured["signoff_roles"] is None    # 空映射 → handler 侧 roles_unresolved


def test_handle_approval_card_loader_error_fails_open(monkeypatch):
    """映射加载异常不得挡审批记账（fail-open，空映射）。"""
    captured = _capture_handle(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("config boom")

    monkeypatch.setattr(WS, "_load_approval_signers", boom)
    out = WS._handle_approval_card(
        {"tag": "approval:approve", "task": "M4"}, "ou_qa1")
    assert out == {"ok": True}
    assert captured["signoff_roles"] is None


def test_handle_approval_card_source_wired():
    """源码护栏：函数体确实传 signoff_roles，且红线动作词不得出现。"""
    src = (ROOT / "portal" / "feishu_bot_ws.py").read_text(encoding="utf-8")
    assert "approval_signers.json" in src
    body = src.split("def _handle_approval_card", 1)[1].split(
        "\ndef _handle_card", 1)[0]
    assert "_load_approval_signers" in body
    assert "signoff_roles=signoff_roles or None" in body
    for banned in ("restart", "publish", "deploy", "shutdown"):
        assert banned not in body.lower()


def test_handle_approval_card_end_to_end_four_signs(tmp_path, monkeypatch):
    """真接线（真 approval_control）：四方逐个点 → incomplete…complete。"""
    monkeypatch.setattr(AC, "LEDGER", tmp_path / "approvals.jsonl")
    monkeypatch.delenv("APPROVAL_OPERATORS", raising=False)
    monkeypatch.setattr(WS, "_load_approval_signers", lambda *a, **k: FULL_MAP)
    states = []
    for op in ("ou_qa1", "ou_guanyu", "ou_verify1", "ou_a0"):
        r = WS._handle_approval_card(
            {"tag": "approval:approve", "task": "M4"}, op)
        states.append((r["signoff_state"], r["approved"]))
    assert states == [("incomplete", False), ("incomplete", False),
                      ("incomplete", False), ("complete", True)]
    # 单签阶段台账不含「通过」裁决（只有签名行）
    text = (tmp_path / "approvals.jsonl").read_text(encoding="utf-8")
    assert "通过" not in text


def test_load_signers_tolerates_utf8_bom(tmp_path):
    """Windows/PowerShell 写出的 UTF-8 BOM 不得让映射静默失效。"""
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"map": {"ou_qa1": "QA1"}}).encode("utf-8"))
    assert WS._load_approval_signers(str(p), use_cache=False) == {
        "ou_qa1": "QA1"}
