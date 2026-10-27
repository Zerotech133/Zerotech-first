# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "agent_platform"))
import ai_onboard as A  # noqa: E402
from legion_command import seat_router as R  # noqa: E402


def _setup(tmp_path):
    A.REG = tmp_path / "registry.json"
    R.STORE = tmp_path / "vk.json"


def test_add_list_disable(tmp_path):
    _setup(tmp_path)
    res = A.add("new-agent", "auto", ["auto", "local"])
    assert res["ok"] and res["token"].startswith("vk-")
    assert res["config"]["base_url"].endswith("/v1")
    rows = A.list_agents()
    assert rows[0]["name"] == "new-agent" and rows[0]["status"] == "active"
    # token 不落注册表
    assert "token" not in rows[0]
    # 重复启用拒绝
    try:
        A.add("new-agent", "auto")
        assert False
    except ValueError:
        pass
    A.disable("new-agent")
    assert A.list_agents()[0]["status"] == "disabled"


def test_snippet_does_not_contain_real_token(tmp_path):
    _setup(tmp_path)
    res = A.add("a2", "strong", ["strong"])
    token = res["token"]
    snip = A._snippet("a2", "strong")
    assert token not in snip
    assert "http://127.0.0.1:8796/v1" in snip
