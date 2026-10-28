# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import seat_router as S  # noqa: E402


def test_quota_usage_enable(tmp_path):
    store = tmp_path / "vk.json"
    item = S.create_vk("DEV3", "auto", path=store, quota=1)
    assert item["quota"] == 1 and item["usage"]["requests"] == 0
    tok = item["token"]
    first = S.resolve("", tok, store)
    assert first["ok"] and first["vk_id"] == item["id"]
    S.record_usage(item["id"], ok=True, model="local9", path=store)
    second = S.resolve("", tok, store)
    assert second["ok"] is False and second["code"] == 429
    rows = S.list_vk(store)
    assert rows[0]["usage"] == {"requests": 1, "success": 1, "fail": 0}
    assert rows[0]["remaining"] == 0
    S.set_quota(item["id"], 5, store)
    assert S.resolve("", tok, store)["ok"] is True
    S.disable_vk(item["id"], store)
    assert S.resolve("", tok, store)["code"] == 401
    S.enable_vk(item["id"], store)
    assert S.resolve("", tok, store)["ok"] is True
