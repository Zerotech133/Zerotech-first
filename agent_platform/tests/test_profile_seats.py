# -*- coding: utf-8 -*-
"""不占编 profile 席位档案验收（WO-HZ-ZY-PROFILE-20261030）。

承重口径（D5-C：治理席以 profile 角色存在、不占编）：
1) `registry/profile_seats.json` 的席位必须能被 `profile_index` / `fallbacks_index`
   读到（ZHAOYUN 正式绑定 deepseek/deepseek-v4-pro 不再回落默认 9b）；
2) 优先级不颠倒：departments > chief > team_* > profile_seats（先到先得，不覆盖正式档案）；
3) 不占编：profile_seats 不进 org.json 部门编制表，也不改变既有席位绑定；
4) 会议覆盖不变：resolve_meeting_binding(ZHAOYUN) 仍为 ollama/qwen3.5:9b；
5) 文件坏 JSON/缺文件容错（不得把绑定索引打崩）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
_ROOT = _PLATFORM.parent
sys.path.insert(0, str(_PLATFORM))

from engine import org_profiles as P  # noqa: E402
from legion_command import serve as srv  # noqa: E402


def _write(root: Path, rel: str, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _mini_root(tmp_path: Path, seats=None) -> Path:
    root = tmp_path / "repo"
    _write(root, "agent_platform/registry/org.json", {
        "departments": [{
            "dept": "dev",
            "agents": ["DEV1"],
            "agent_profiles": [{
                "agent_id": "DEV1",
                "llm_binding": "glm/glm-5.3",
                "llm_fallback": ["ollama/qwen3.5:9b"],
            }],
        }],
        "chief": {"agent_id": "A0", "llm_binding": "glm/glm-5.3"},
    })
    _write(root, "agent_platform/registry/team_dev.json", {
        "agents": [{"agent_id": "DEV2", "llm_binding": "ollama/qwen3.6:35b-a3b"}],
    })
    if seats is not None:
        _write(root, "agent_platform/registry/profile_seats.json", seats)
    return root / "agent_platform"


def test_profile_seats_binding_resolved(tmp_path):
    plat = _mini_root(tmp_path, {"seats": [{
        "agent_id": "ZHAOYUN", "cn_name": "赵云",
        "llm_binding": "deepseek/deepseek-v4-pro",
        "llm_fallback": ["ollama/qwen3.5:9b"],
    }]})
    idx = P.profile_index(plat)
    assert idx["ZHAOYUN"]["llm_binding"] == "deepseek/deepseek-v4-pro"
    assert idx["ZHAOYUN"]["source"] == P.SRC_PROFILE
    assert idx["ZHAOYUN"]["cn_name"] == "赵云"
    assert P.fallbacks_index(plat)["ZHAOYUN"] == ["ollama/qwen3.5:9b"]
    assert plat / "registry" / "profile_seats.json" in P.source_paths(plat)


def test_profile_seats_does_not_override_formal(tmp_path):
    """先到先得：部门/team 已有档案时，profile_seats 不得覆盖。"""
    plat = _mini_root(tmp_path, {"seats": [
        {"agent_id": "DEV1", "llm_binding": "deepseek/deepseek-v4-pro",
         "llm_fallback": ["ollama/qwen3.5:9b"]},
        {"agent_id": "DEV2", "llm_binding": "deepseek/deepseek-v4-pro"},
    ]})
    idx = P.profile_index(plat)
    assert idx["DEV1"]["llm_binding"] == "glm/glm-5.3"
    assert idx["DEV1"]["source"] == P.SRC_DEPT
    assert idx["DEV2"]["llm_binding"] == "ollama/qwen3.6:35b-a3b"
    assert idx["DEV2"]["source"] == P.SRC_TEAM
    fb = P.fallbacks_index(plat)
    assert fb["DEV1"] == ["ollama/qwen3.5:9b"]  # 部门原链保留
    assert "ZHAOYUN" not in fb


def test_profile_seats_dict_form_and_bad_json(tmp_path):
    """容错：seats 用 dict(agent_id->档案) 或坏 JSON 时不崩。"""
    plat = _mini_root(tmp_path, {"seats": {"ZHAOYUN": {
        "llm_binding": "deepseek/deepseek-v4-pro", "cn_name": "赵云"}}})
    assert P.profile_index(plat)["ZHAOYUN"]["llm_binding"] == "deepseek/deepseek-v4-pro"
    (plat / "registry" / "profile_seats.json").write_text("{bad json", encoding="utf-8")
    assert "ZHAOYUN" not in P.profile_index(plat)
    assert P.fallbacks_index(plat)["DEV1"] == ["ollama/qwen3.5:9b"]


def test_real_repo_zhaoyun_formal_binding():
    """真实仓：ZHAOYUN 正式绑定可解析；会议覆盖不变；不占编。"""
    idx = P.profile_index(_PLATFORM)
    assert idx["ZHAOYUN"]["llm_binding"] == "deepseek/deepseek-v4-pro"
    assert idx["ZHAOYUN"]["source"] == P.SRC_PROFILE
    assert P.fallbacks_index(_PLATFORM)["ZHAOYUN"] == ["ollama/qwen3.5:9b"]
    assert srv.resolve_binding("ZHAOYUN", _PLATFORM) == "deepseek/deepseek-v4-pro"
    assert srv.resolve_meeting_binding("ZHAOYUN", _PLATFORM) == "ollama/qwen3.5:9b"
    # 不占编：org.json 部门编制表不含 ZHAOYUN；既有席位绑定不变
    org = json.loads((_PLATFORM / "registry" / "org.json").read_text(encoding="utf-8"))
    dept_ids = {p.get("agent_id") for d in org.get("departments") or []
                for p in d.get("agent_profiles") or []}
    assert "ZHAOYUN" not in dept_ids
    assert srv.resolve_binding("A0", _PLATFORM) == "glm/glm-5.3"
