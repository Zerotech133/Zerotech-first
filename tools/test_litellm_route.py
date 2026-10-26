# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import litellm_route as L  # noqa: E402


def test_deployments_masked_and_fallback():
    entries = [
        {"seat": "relay", "secret": "sk-abcdefghijklmnop", "base_url":
         "https://tokenegate.com/v1/chat/completions", "model": "gpt-6-sol", "label": "relay"},
        {"seat": "tianfeng", "secret": "zp-abcdefghijklmnop", "label": "glm"},
        {"seat": "zhaoyun", "secret": "ds-abcdefghijklmnop", "label": "ds"},
    ]
    deps = L.load_deployments(entries)
    assert len(deps) == 4  # 3 keys + local fallback
    assert deps[0]["litellm_params"]["api_base"] == "https://tokenegate.com/v1"
    assert deps[0]["model_info"]["key"] == "sk-***nop"
    text = str([d["model_info"] for d in deps])
    assert "abcdefghijklmnop" not in text


def test_norm_base():
    assert L._norm_base("https://x/v1/chat/completions") == "https://x/v1"
    assert L._norm_base("https://x/v1/") == "https://x/v1"


def test_list_no_keys():
    res = L.list_deployments(entries=[])
    assert res["ok"] is True and res["count"] == 1
    assert res["deployments"][0]["model_name"] == "local-fallback"
