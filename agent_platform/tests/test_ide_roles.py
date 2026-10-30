# -*- coding: utf-8 -*-
"""P0-2：LDE 写/执行角色门测试（本地 fallback + 角色拒绝 + 环境覆盖）。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW = ROOT / "agent_platform" / "legion_ide" / "rw_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("rw_guard_test", GW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_session_gets_local_role(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    m = _load()
    token, owner = m.new_session(headers={})
    info = m.session_info({"Cookie": "ide_session=" + token})
    assert info is not None
    assert m.LOCAL_ROLE in info["roles"]
    assert info.get("role_source") == "local_fallback"
    hdr = {"Cookie": "ide_session=" + token, "Origin": "http://127.0.0.1:8103"}
    assert m.check_access(hdr, required_roles=m.write_roles()) is None


def test_role_forbidden(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    m = _load()
    token, _ = m.new_session(headers={})
    key = m._token_key(token)
    m._SESSIONS[key]["roles"] = ["VIEWER"]
    hdr = {"Cookie": "ide_session=" + token}
    err = m.check_access(hdr, required_roles={"DEVELOPER", "LOCAL_DEVELOPER"})
    assert err and err.get("error") == "role_forbidden"
    assert "VIEWER" in err["detail"]["roles"]


def test_write_roles_env_override(monkeypatch):
    monkeypatch.setenv("IDE_WRITE_ROLES", "CUSTOM_ROLE,QA1")
    m = _load()
    roles = m.write_roles()
    assert "CUSTOM_ROLE" in roles and "QA1" in roles
    assert "LOCAL_DEVELOPER" in roles  # 默认仍保留


def test_local_token_required_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("IDE_LOCAL_TOKEN", "tok-e2e-123")
    m = _load()
    token, _ = m.new_session(headers={})
    hdr = {"Cookie": "ide_session=" + token}
    err = m.check_access(hdr, required_roles=m.write_roles())
    assert err and err.get("error") == "local_token_required"
    hdr["X-IDE-Token"] = "tok-e2e-123"
    assert m.check_access(hdr, required_roles=m.write_roles()) is None
    bad = {"Cookie": "ide_session=" + token, "X-IDE-Token": "wrong"}
    assert m.check_access(bad, required_roles=m.write_roles())["error"] == "local_token_required"
    cookie = {"Cookie": "ide_session=" + token + "; ide_local_token=tok-e2e-123"}
    assert m.check_access(cookie, required_roles=m.write_roles()) is None


def test_local_token_file(monkeypatch, tmp_path):
    tok = tmp_path / "local_token"
    tok.write_text("file-token-xyz\n", encoding="utf-8")
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("IDE_LOCAL_TOKEN", raising=False)
    monkeypatch.setenv("IDE_LOCAL_TOKEN_FILE", str(tok))
    m = _load()
    token, _ = m.new_session(headers={})
    hdr = {"Cookie": "ide_session=" + token, "X-IDE-Token": "file-token-xyz"}
    assert m.check_access(hdr, required_roles=m.write_roles()) is None


def test_check_cap_required_and_scope(monkeypatch, tmp_path):
    key = tmp_path / "cap.key"; key.write_bytes(b"z" * 32)
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(key))
    monkeypatch.delenv("IDE_LOCAL_TOKEN", raising=False)
    m = _load()
    token, _ = m.new_session(headers={})
    hdr = {"Cookie": "ide_session=" + token}
    err = m.check_cap(hdr, "ide:write:T1", "T1")
    assert err and err.get("error") == "cap_required"
    cap = m._cap().issue(["ide:write:T1"], task="T1", path=key)
    ok = {"Cookie": "ide_session=" + token, "X-ZB-Cap": cap}
    assert m.check_cap(ok, "ide:write:T1", "T1") is None
    wrong = {"Cookie": "ide_session=" + token, "X-ZB-Cap": cap}
    assert m.check_cap(wrong, "ide:write:T2", "T2")["error"] == "cap_scope"


def test_check_cap_skips_identity_sessions(monkeypatch, tmp_path):
    key = tmp_path / "cap.key"; key.write_bytes(b"q" * 32)
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(key))
    m = _load()
    token, _ = m.new_session(headers={})
    m._SESSIONS[m._token_key(token)]["role_source"] = "identity"
    hdr = {"Cookie": "ide_session=" + token}
    assert m.check_cap(hdr, "ide:write:T1", "T1") is None
