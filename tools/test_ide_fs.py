# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import ide_fs  # noqa: E402


def test_list_and_read_repo():
    root = ide_fs.list_dir(".")
    assert root["ok"] and any(x["path"] == "agent_platform" for x in root["items"])
    f = ide_fs.read_file("agent_platform/legion_command/ide_fs.py")
    assert f["ok"] and "def list_dir" in f["content"]


def test_deny_escape_and_secrets():
    for path in ("../secret", "C:/Windows/win.ini", ".env", "_vault/x", ".git/config"):
        try:
            ide_fs.read_file(path)
        except ide_fs.FsError:
            pass
        else:
            raise AssertionError("should deny " + path)


def test_search_repo():
    r = ide_fs.search("def list_dir", glob="*.py", path="agent_platform/legion_command")
    assert r["ok"] and r["count"] >= 1
