"""本地聊天角色配置，借鉴菜鸟 ai_chat_role 的最小可用字段。"""
from __future__ import annotations

import json
from pathlib import Path


class ProfileError(ValueError):
    """角色配置不存在或格式不正确。"""


_PROFILE_PATH = Path(__file__).with_name("chat_profiles.json")
_DEFAULT_PROFILE = "general"


def _load_profiles() -> dict[str, dict]:
    try:
        data = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError("聊天角色配置不可用") from exc
    if not isinstance(data, dict) or not data:
        raise ProfileError("聊天角色配置为空")
    for profile_id, profile in data.items():
        if not isinstance(profile_id, str) or not isinstance(profile, dict):
            raise ProfileError("聊天角色配置格式无效")
        if not isinstance(profile.get("system_prompt"), str) or not profile["system_prompt"].strip():
            raise ProfileError(f"聊天角色 {profile_id} 缺少 system_prompt")
        if not isinstance(profile.get("model"), str) or not profile["model"].strip():
            raise ProfileError(f"聊天角色 {profile_id} 缺少 model")
        if not isinstance(profile.get("rag"), bool):
            raise ProfileError(f"聊天角色 {profile_id} 的 rag 必须为布尔值")
    return data


def list_profiles() -> tuple[str, ...]:
    """返回可供前端选择的稳定角色编号。"""
    return tuple(_load_profiles())


def list_profile_specs() -> tuple[dict, ...]:
    """返回前端展示所需的角色元数据，保留稳定编号作为 value。"""
    return tuple(
        {
            "id": profile_id,
            "name": profile.get("name", profile_id),
            "model": profile["model"],
            "rag": profile["rag"],
        }
        for profile_id, profile in _load_profiles().items()
    )


def get_profile(profile_id: str | None = None) -> dict:
    """读取角色配置并返回副本，避免调用方修改全局配置。"""
    selected = profile_id or _DEFAULT_PROFILE
    profiles = _load_profiles()
    if selected not in profiles:
        raise ProfileError(f"未知聊天角色：{selected}")
    return dict(profiles[selected], id=selected)


__all__ = ["ProfileError", "get_profile", "list_profiles", "list_profile_specs"]
