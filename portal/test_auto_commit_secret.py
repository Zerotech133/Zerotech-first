# -*- coding: utf-8 -*-
"""K-STOCK-185：agent_auto_commit 敏感内容守卫正则自测（纯函数级，不碰 git）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_auto_commit import SECRET_BODY_RE, SECRET_FILE_RE  # noqa: E402


def _fname_hits(path: str) -> bool:
    return bool(SECRET_FILE_RE.search(path.replace("\\", "/")))


def test_secret_filenames_blocked():
    for p in [".env", ".env.local", "app/.env", "server.pem", "id_rsa",
              "id_rsa.pub" if False else "id_rsa_4096", ".netrc",
              "x.pfx", "y.keystore", "a/b/api_token",
              "config/credentials.json", "secrets.yaml", "api_key.txt",
              "my_secret_key.cfg", "private_key.pem"]:
        assert _fname_hits(p), f"应拦截: {p}"


def test_benign_filenames_pass():
    for p in ["portal/portal_api.py", "tokenizer.py", "tokenize.cjs",
              "docs/token_design.md", "src/tokenizer_test.py",
              "README.md", "k_stock/paper/engine.py", "sub_keys.json",
              "monitoring.py", "keys.go"]:
        assert not _fname_hits(p), f"误伤: {p}"


def test_secret_bodies_blocked():
    for s in ["-----BEGIN RSA PRIVATE KEY-----",
              "-----BEGIN OPENSSH PRIVATE KEY-----",
              "Authorization: Bearer sk-abc123XYZ456abc789XYZ",
              "token=ghp_abcdef1234567890abcdef1234567890abcd",
              "AKIAIOSFODNN7EXAMPLE",
              "xoxb-123-456-abcdef",
              'GLM_API_KEY = "a1b2c3d4e5f6g7h8"']:
        assert SECRET_BODY_RE.search(s), f"应拦截: {s[:30]}"


def test_benign_bodies_pass():
    for s in ["GLM_API_KEY = os.environ.get('GLM_API_KEY')",
              "api_key = os.environ['API_KEY']",
              "BEGIN PUBLIC KEY-----",
              "sk-learn LogisticRegression",
              "prefix sk-short"]:
        assert not SECRET_BODY_RE.search(s), f"误伤: {s[:30]}"


if __name__ == "__main__":
    test_secret_filenames_blocked()
    test_benign_filenames_pass()
    test_secret_bodies_blocked()
    test_benign_bodies_pass()
    print("ALL_SECRET_GUARD_TESTS_PASS")
