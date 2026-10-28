# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import model_prefs as MP  # noqa: E402
from legion_command import zhaoyun_drill as ZD  # noqa: E402


def test_model_catalog_has_all_18():
    ids = [m["id"] for m in MP.MODELS]
    assert len(ids) == 18
    for mid in ("glm-4-flash", "glm-5.3", "glm-5", "deepseek-v4-pro",
                "deepseek-flash", "deepseek-chat", "qwen-plus", "qwen3-max"):
        assert mid in ids


def test_zhaoyun_candidates_use_catalog():
    view = ZD.model_view(HERE.parent / "agent_platform")
    assert view["candidate_models"] == [m["id"] for m in MP.MODELS]
    assert view["candidate_labels"]["glm-5.3"]
