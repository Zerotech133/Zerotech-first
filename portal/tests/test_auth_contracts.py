# -*- coding: utf-8 -*-
"""P3 后半段认证契约测试：SessionContext / RFC 8693 Token Exchange。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "agent_platform" / "contracts" / "reuse"


def _load(name):
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "schema_name,example_name",
    [
        ("session_context.schema.json", "session_context.example.json"),
        ("token_exchange_request.schema.json", "token_exchange_request.example.json"),
        ("token_exchange_response.schema.json", "token_exchange_response.example.json"),
    ],
)
def test_auth_contract_schema_and_example(schema_name, example_name):
    schema = _load(schema_name)
    Draft202012Validator.check_schema(schema)
    example = _load("examples/" + example_name)
    Draft202012Validator(schema).validate(example)


def test_session_contains_no_raw_credentials():
    raw = (CONTRACT_DIR / "examples" / "session_context.example.json").read_text(encoding="utf-8").lower()
    for bad in ("password", "passwd", "cookie", "refresh_token", "access_token"):
        assert bad not in raw


def test_token_exchange_requires_rfc8693_grant():
    schema = _load("token_exchange_request.schema.json")
    example = _load("examples/token_exchange_request.example.json")
    example["grant_type"] = "password"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(example)


def test_token_exchange_response_ttl_bounded():
    schema = _load("token_exchange_response.schema.json")
    example = _load("examples/token_exchange_response.example.json")
    example["expires_in"] = 86400
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(example)


def test_contracts_readme_links_auth_contracts():
    # 文档必须告诉后续实施者新增的三份契约
    readme = (CONTRACT_DIR / "README.md").read_text(encoding="utf-8")
    for name in ("session_context", "token_exchange_request", "token_exchange_response"):
        assert name in readme
