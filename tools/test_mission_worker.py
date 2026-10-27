# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def test_safe_scope_only_ui_docs():
    assert W._safe_scope("agent_platform/legion_command/static/keypool.html")
    assert W._safe_scope("portal/ide.html")
    assert W._safe_scope("docs/plans/x.md")
    assert not W._safe_scope("agent_platform/legion_command/keypool.py")
    assert not W._safe_scope("config/credential_registry.json")
    assert not W._safe_scope("C:/Windows/system32/x.txt")


def test_scope_ok_rules():
    scopes = ["agent_platform/legion_command/static/keypool.html"]
    assert W._scope_ok("agent_platform/legion_command/static/keypool.html", scopes)
    assert not W._scope_ok("agent_platform/legion_command/static/other.html", scopes)
    assert not W._scope_ok("../outside.md", scopes)
    assert not W._scope_ok("agent_platform/legion_command/static/x.exe", scopes)


def test_secret_pattern():
    assert W.SECRET_PAT.search("sk-abcdefghijklmnopqrstuvwx")
    assert W.SECRET_PAT.search("AKIAABCDEFGHIJKLMNOP")
    assert not W.SECRET_PAT.search("hello world")
