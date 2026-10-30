# -*- coding: utf-8 -*-
"""LDE 会话持久化/owner 绑定回归（K-QUANT-20261030-IDEDEEP）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from legion_ide import rw_guard as G  # noqa: E402


def _reset():
    G._SESSIONS.clear()
    G._SESSIONS_LOADED = False


def test_session_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    _reset()
    token, owner = G.new_session("zero_bound:test")
    assert owner == "zero_bound:test"
    h = {"Cookie": "ide_session=" + token}
    assert G.session_info(h)["owner"] == "zero_bound:test"

    # 模拟 8103 重启：内存清空，从磁盘恢复
    _reset()
    info = G.session_info(h)
    assert info["owner"] == "zero_bound:test"
    # 2026-10-30 P0-2（WO-LEGION-IDE-P0-2-SEC-20261030）：写/执行角色门引入
    # LOCAL_DEVELOPER 兜底角色（本地无 identity roles 时），会话 roles 不再为空；
    # 旧断言 == [] 属契约变更前口径，已漂移。
    assert info["roles"] == ["LOCAL_DEVELOPER"]


def test_auth_roles_mapping(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    _reset()
    monkeypatch.setattr(G, "auth_identity", lambda h: {
        "sub": "zero_bound:owner", "roles": ["ADMIN"]})
    token, owner = G.new_session(headers={"Cookie": "zb_session=x"})
    assert owner == "zero_bound:owner"
    info = G.session_info({"Cookie": "ide_session=" + token})
    assert info["roles"] == ["ADMIN"]


def test_token_not_stored_in_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    _reset()
    token, _owner = G.new_session("u")
    raw = (tmp_path / "sessions.json").read_text(encoding="utf-8")
    assert token not in raw
    assert "u" in raw


def test_owner_mismatch_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    _reset()
    token, owner = G.new_session("owner-a")
    h = {"Cookie": "ide_session=" + token}
    assert G.session_owner(h) == owner
    assert G.session_owner({"Cookie": "ide_session=bad"}) == ""
