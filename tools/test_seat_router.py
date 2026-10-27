# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import seat_router as R  # noqa: E402


def test_vk_create_resolve_disable(tmp_path):
    store = tmp_path / "vk.json"
    item = R.create_vk("tianfeng", "strong", ["auto", "strong"], path=store)
    assert item["token"].startswith("vk-")
    assert "token_sha256" not in item
    rows = R.list_vk(store)
    assert rows and "token" not in rows[0]
    ok = R.resolve("auto", item["token"], path=store)
    assert ok["ok"] is True and ok["owner"] == "tianfeng" and ok["alias"] == "auto"
    # 无 model 时按席位默认 strong
    ok2 = R.resolve("", item["token"], path=store)
    assert ok2["alias"] == "strong"
    # 不允许的别名
    bad = R.resolve("local", item["token"], path=store)
    assert bad["ok"] is False and bad["code"] == 403
    R.disable_vk(item["id"], path=store)
    after = R.resolve("auto", item["token"], path=store)
    assert after["ok"] is False


def test_invalid_vk_and_placeholder(tmp_path):
    store = tmp_path / "vk.json"
    assert R.resolve("auto", "vk-nope", path=store)["ok"] is False
    # 非 vk 占位符按匿名 auto 处理
    anon = R.resolve("auto", "placeholder", path=store)
    assert anon["ok"] is True and anon["alias"] == "auto"


def test_aliases_default():
    cfg = R.load_config()
    assert "auto" in cfg["aliases"] and "strong" in cfg["aliases"] and "local" in cfg["aliases"]
    assert cfg["aliases"]["strong"]["allow_local"] is False
