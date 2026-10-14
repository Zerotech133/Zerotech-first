"""本地 Ollama 模型路由契约。

应用层只从这里读取模型角色和默认值，避免普通聊天、RAG、嵌入各自维护一套
模型名称。``installed`` 是可选的 ``/api/tags`` 结果；传入后会在选择时给出
可读的缺失模型或降级提示，但不会发起网络请求。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


CHAT_MODELS = ("qwen3.5:4b", "qwen3.5:9b", "qwen3.5:35b")
DEFAULT_CHAT_MODEL = "qwen3.5:9b"
RAG_CHAT_MODEL = "qwen3.5:9b"
EMBED_MODEL = "bge-m3"
# 语义别名便于调用方按角色读取，实际值仍由上面的单一常量维护。
CHAT_MODEL_4B, CHAT_MODEL_9B, CHAT_MODEL_35B = CHAT_MODELS
RAG_MODEL = RAG_CHAT_MODEL
EMBEDDING_MODEL = EMBED_MODEL
MODEL_ROUTES = {
    "chat": {"models": CHAT_MODELS, "default": DEFAULT_CHAT_MODEL},
    "rag": {"models": (RAG_CHAT_MODEL,), "default": RAG_CHAT_MODEL},
    "embedding": {"models": (EMBED_MODEL,), "default": EMBED_MODEL},
}


class ModelRoutingError(ValueError):
    """请求了不属于本地路由契约的模型或能力。"""


@dataclass(frozen=True)
class ModelSelection:
    """一次路由选择及其可展示诊断。"""

    capability: str
    model: str
    requested: str | None
    degraded: bool = False
    message: str = ""


def normalize_model(model: str) -> str:
    """统一 Ollama 名称，兼容 ``ollama/`` 前缀和嵌入 ``:latest`` 标签。"""
    if not isinstance(model, str) or not model.strip():
        raise ModelRoutingError("模型名称不能为空")
    value = model.strip()
    if value.lower().startswith("ollama/"):
        value = value.split("/", 1)[1]
    if value.lower() == "bge-m3:latest":
        return EMBED_MODEL
    return value


def _installed(installed: Iterable[str] | None) -> set[str] | None:
    if installed is None:
        return None
    return {normalize_model(name) for name in installed if isinstance(name, str) and name.strip()}


def _missing_message(model: str, capability: str) -> str:
    labels = {"chat": "普通聊天", "rag": "RAG", "embedding": "嵌入"}
    return (
        f"{labels.get(capability, capability)}模型 {model} 未安装，无法降级；"
        f"请先执行 ollama pull {model}"
    )


def select_model(
    capability: str,
    requested: str | None = None,
    installed: Iterable[str] | None = None,
) -> ModelSelection:
    """按能力选择模型；``installed`` 为空时只做静态校验。

    普通聊天可在 9b 缺失时降级到已安装的 4b。RAG 和嵌入保持固定模型，缺失时
    返回 ``degraded=True`` 与提示，绝不静默改用其他模型。
    """
    if capability not in {"chat", "rag", "embedding"}:
        raise ModelRoutingError(f"未知模型能力：{capability}")

    available = _installed(installed)
    if capability == "chat":
        if requested is None:
            target = DEFAULT_CHAT_MODEL
        else:
            target = normalize_model(requested)
            if target not in CHAT_MODELS:
                raise ModelRoutingError(
                    f"普通聊天仅支持 {CHAT_MODELS[0]} 或 {CHAT_MODELS[1]}，收到 {target}"
                )
        if available is None or target in available:
            return ModelSelection(capability, target, requested)
        if target == DEFAULT_CHAT_MODEL and CHAT_MODELS[0] in available:
            return ModelSelection(
                capability,
                CHAT_MODELS[0],
                requested,
                degraded=True,
                message=f"模型 {target} 未安装，已降级为 {CHAT_MODELS[0]}",
            )
        return ModelSelection(capability, target, requested, degraded=True, message=_missing_message(target, capability))

    target = RAG_CHAT_MODEL if capability == "rag" else EMBED_MODEL
    if requested is not None and normalize_model(requested) != target:
        label = "RAG 对话" if capability == "rag" else "嵌入"
        raise ModelRoutingError(f"{label}固定使用 {target}，不支持 {requested}")
    if available is None or target in available:
        return ModelSelection(capability, target, requested)
    return ModelSelection(capability, target, requested, degraded=True, message=_missing_message(target, capability))


def validate_model(capability: str, model: str | None = None) -> str:
    """只校验静态路由契约并返回规范化名称。"""
    return select_model(capability, model).model


__all__ = [
    "CHAT_MODELS",
    "DEFAULT_CHAT_MODEL",
    "RAG_CHAT_MODEL",
    "EMBED_MODEL",
    "CHAT_MODEL_4B",
    "CHAT_MODEL_9B",
    "CHAT_MODEL_35B",
    "RAG_MODEL",
    "EMBEDDING_MODEL",
    "MODEL_ROUTES",
    "ModelRoutingError",
    "ModelSelection",
    "normalize_model",
    "select_model",
    "validate_model",
]
