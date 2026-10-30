# -*- coding: utf-8 -*-
"""B2 runtime 包装层测试（不修改 auth_oidc.py）。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
sys.path.insert(0, str(PORTAL))
import auth_oidc  # noqa: E402
import auth_oidc_runtime  # noqa: E402


def test_apply_runtime_shadow(tmp_path, monkeypatch):
    f = tmp_path / "oidc_runtime.env"
    f.write_text(
        "AUTH_OIDC_MODE=shadow\n"
        "OIDC_ISSUER=http://localhost:1411\n"
        "OIDC_CLIENT_ID=cid\n"
        "OIDC_CLIENT_SECRET=csec\n"
        "OIDC_REDIRECT_URI=http://localhost:8734/api/auth/oidc/callback\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_oidc_runtime, "RUNTIME_FILE", f)
    monkeypatch.setattr(auth_oidc, "MODE", "off")
    out = auth_oidc_runtime.apply()
    assert out["applied"] is True
    assert auth_oidc.MODE == "shadow"
    assert auth_oidc.CLIENT_ID == "cid"
    assert auth_oidc.CLIENT_SECRET == "csec"


def test_apply_missing_file_keeps_off(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_oidc_runtime, "RUNTIME_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(auth_oidc, "MODE", "off")
    out = auth_oidc_runtime.apply()
    assert out["applied"] is False
    assert auth_oidc.MODE == "off"
