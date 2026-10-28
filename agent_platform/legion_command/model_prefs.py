# -*- coding: utf-8 -*-
"""研发板块模型手动切换偏好：agent → model 别名。

可用模型：与 config/seat_routing.json 的 18 个别名一致（自动/强模型/本地/智谱/DeepSeek/百炼/中转）
存储：work/models/preferences.json（只存偏好，不含密钥）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = REPO / "config" / "rd_model_switch.json"
STORE = REPO / "work" / "models" / "preferences.json"

MODELS = [
    {"id": "auto", "label": "自动（智能分配）"},
    {"id": "strong", "label": "强模型（云端优先）"},
    {"id": "local", "label": "本地自动"},
    {"id": "local4", "label": "本地 · qwen3.5:4b（轻量）"},
    {"id": "local9", "label": "本地 · qwen3.5:9b（默认）"},
    {"id": "local35", "label": "本地 · qwen3.6:35b-a3b（复杂）"},
    {"id": "zhipu", "label": "智谱自动（GLM-4-Flash）"},
    {"id": "glm-4-flash", "label": "智谱 GLM-4-Flash"},
    {"id": "glm-5.3", "label": "智谱 GLM-5.3"},
    {"id": "glm-5", "label": "智谱 GLM-5"},
    {"id": "deepseek", "label": "DeepSeek 自动"},
    {"id": "deepseek-chat", "label": "DeepSeek Chat"},
    {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    {"id": "deepseek-flash", "label": "DeepSeek Flash"},
    {"id": "qwen-plus", "label": "通义千问 Plus"},
    {"id": "qwen3-max", "label": "通义千问 3 Max"},
    {"id": "gpt6", "label": "中转站 · GPT-6"},
    {"id": "gpt-6-sol", "label": "中转站 · GPT-6 Sol"},
]
MODEL_IDS = {m["id"] for m in MODELS}


def agents() -> list:
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
        return d.get("agents") or []
    except Exception:
        return []


def load_prefs() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_prefs(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE)


def get(agent: str) -> str:
    return str(load_prefs().get(str(agent or "").upper(), "auto") or "auto")


def set_pref(agent: str, model: str) -> dict:
    a = str(agent or "").strip()
    m = str(model or "").strip()
    if not a:
        raise ValueError("agent 必填")
    if m not in MODEL_IDS:
        raise ValueError("model 必须是 %s" % sorted(MODEL_IDS))
    d = load_prefs()
    d[a.upper()] = m
    d["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_prefs(d)
    return {"ok": True, "agent": a.upper(), "model": m}


def state() -> dict:
    d = load_prefs()
    return {"ok": True, "models": MODELS, "agents": agents(),
            "prefs": {k: v for k, v in d.items() if k != "updated_at"},
            "updated_at": d.get("updated_at")}
