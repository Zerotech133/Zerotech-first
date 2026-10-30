# -*- coding: utf-8 -*-
"""本地管理员（vault PBKDF2 哈希）回归。"""
import hashlib
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import auth_gateway as A  # noqa: E402


def _write_cfg(tmp_path, monkeypatch, password="12345678"):
    salt = "a1b2c3d4e5f60718"
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), A.ADMIN_PBKDF2_ITER).hex()
    p = tmp_path / "admin.env"
    p.write_text(
        "ZB_ADMIN_USERNAME=admin\nZB_ADMIN_SALT=%s\nZB_ADMIN_HASH=%s\n" % (salt, h),
        encoding="utf-8")
    monkeypatch.setattr(A, "ADMIN_FILE", str(p))
    for k in ("ZB_ADMIN_USERNAME", "ZB_ADMIN_SALT", "ZB_ADMIN_HASH"):
        monkeypatch.delenv(k, raising=False)


def test_local_admin_hash_ok(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch)
    assert A._local_admin_password_ok("admin", "12345678") is True
    assert A._local_admin_password_ok("admin", "bad") is False
    assert A._local_admin_password_ok("other", "12345678") is False


def test_local_admin_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "ADMIN_FILE", str(tmp_path / "missing.env"))
    for k in ("ZB_ADMIN_USERNAME", "ZB_ADMIN_SALT", "ZB_ADMIN_HASH"):
        monkeypatch.delenv(k, raising=False)
    assert A._admin_cfg() == {}
    assert A._local_admin_password_ok("admin", "12345678") is False
