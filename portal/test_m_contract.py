# -*- coding: utf-8 -*-
"""移动端契约与令牌单测（P0）：四契约校验、签名/验签、过期/作用域。"""
import os
import tempfile
import shutil
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import m_contract as mc  # noqa: E402
import m_tokens as mt  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _contracts():
    return {n: mc.load(n) for n in mc.FILES}


# ── 契约校验 ───────────────────────────────────────────────
def test_contracts_valid():
    s = mc.validate_all()
    assert s["routes"] >= 6 and s["scenes"] >= 6
    assert s["shells"] == 3 and s["events"] >= 4


def test_contract_rejects_bad_schema():
    c = _contracts()
    c["urls"]["schema"] = "bad"
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_rejects_unknown_card_scene():
    c = _contracts()
    c["cards"]["scenes"]["nope"] = {"primary": {"text": "x", "path": "/m/x"}}
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_rejects_too_many_buttons():
    c = _contracts()
    c["cards"]["rules"]["max_buttons"] = 4
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_rejects_absolute_card_url():
    c = _contracts()
    c["cards"]["scenes"]["chan"]["primary"]["path"] = "http://x"
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_rejects_sensitive_unknown_scene():
    c = _contracts()
    c["auth"]["restrictions"]["sensitive_scenes"] = ["ghost"]
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_rejects_bad_beacon():
    c = _contracts()
    c["beacon"]["endpoint"] = "/beacon"
    with pytest.raises(mc.ContractError):
        mc.validate(c)


def test_contract_check_cli():
    import subprocess
    p = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "m_contract.py"), "--check"],
                       capture_output=True, timeout=60)
    assert p.returncode == 0
    assert "契约校验通过" in p.stdout.decode("utf-8", "replace")


# ── 令牌 ───────────────────────────────────────────────────
def test_token_roundtrip(ws_tmp):
    sec = mt.load_secret(os.path.join(ws_tmp, "secret.txt"))
    tok, payload = mt.make_page_token("chan", id="605100", code="605100",
                                      secret=sec)
    got = mt.verify(tok, scope="page", secret=sec)
    assert got["scene"] == "chan" and got["id"] == "605100"
    assert payload["exp"] > payload["nbf"]


def test_token_expired(ws_tmp):
    sec = mt.load_secret(os.path.join(ws_tmp, "secret.txt"))
    tok, _ = mt.make_page_token("picks", ttl=-10, secret=sec)
    with pytest.raises(mt.TokenError):
        mt.verify(tok, secret=sec)


def test_token_wrong_scope(ws_tmp):
    sec = mt.load_secret(os.path.join(ws_tmp, "secret.txt"))
    tok, _ = mt.make_share_token("picks", secret=sec)
    with pytest.raises(mt.TokenError):
        mt.verify(tok, scope="page", secret=sec)
    assert mt.verify(tok, scope="share", secret=sec)["resource"] == "picks"


def test_token_tampered(ws_tmp):
    sec = mt.load_secret(os.path.join(ws_tmp, "secret.txt"))
    tok, _ = mt.make_page_token("chan", secret=sec)
    tampered = tok[:-2] + ("aa" if tok[-2:] != "aa" else "bb")
    with pytest.raises(mt.TokenError):
        mt.verify(tampered, secret=sec)


def test_token_garbage():
    with pytest.raises(mt.TokenError):
        mt.verify("not-a-token")


def test_secret_autocreate(ws_tmp):
    p = os.path.join(ws_tmp, "auto_secret.txt")
    s1 = mt.load_secret(p)
    s2 = mt.load_secret(p)
    assert s1 == s2 and len(s1) >= 32
