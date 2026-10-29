# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import model_allocator as M  # noqa: E402


def test_task_forces_local():
    r = M.allocate(task="simple", messages=[{"role": "user", "content": "翻译这句话"}])
    assert r["tier"] == "local" and r["allow_local"] is True and r["seats"] == []


def test_reasoning_forces_strong():
    r = M.allocate(task="reasoning", messages=[{"role": "user", "content": "分析架构"}])
    assert r["tier"] == "strong" and r["allow_local"] is False


def test_role_default():
    assert M.allocate(agent="A0")["tier"] == "strong"
    assert M.allocate(agent="ZHAOYUN")["tier"] == "strong"


def test_unknown_role_auto():
    r = M.allocate(agent="NEW-AI", messages=[{"role": "user", "content": "你好"}])
    assert r["tier"] == "auto" and r["allow_local"] is True


def test_keyword_strong():
    r = M.allocate(messages=[{"role": "user", "content": "请帮我审计这段代码"}])
    assert r["tier"] == "strong"


def test_gpt6():
    r = M.allocate(requested="gpt6")
    assert r["tier"] == "gpt6" and r["seats"] == ["relay"] and r["allow_local"] is False
