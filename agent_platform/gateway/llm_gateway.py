# -*- coding: utf-8 -*-
"""LLM 稳定门面（WO-LEGION-S21）。

给外部板块（飞书中台等）的唯一 LLM 调用入口：binding → engine/llm.py
make_llm → chat。不建第二调用层——路由/降级三态/超时档位全部复用军团
engine 语义，本层只做信封化与异常归一：

  LLMError（重试耗尽）→ ok=False 信封（code="llm_error"），不抛穿；
  status=="degraded"  → degraded=True 透传（云端故障自动落本地 9b 判例）。

binding 词表（10-18 分工定案 + S20 异族裁决 + 2026-10-27 B05 单轨化）：
  "ollama/qwen3.5:9b"（默认主力）/ "ollama/qwen3.6:35b-a3b"（判读重档）
  / "ollama/bge-m3"（嵌入）/ "glm/glm-5.3"（田丰参谋长云端）
  / "openai/gpt-6.0"（诸葛亮异族）：2026-10-27 B05 起 engine 的 openai 前缀
  **委托 OpenClaw agent-config 载体**（OpenClawLLM → engine/openclaw_cli.run），
  与 L5/8796 同源；实际模型由 ~/.openclaw agent 配置决定，不再直连
  api.openai.com、无需 OPENAI_API_KEY——**OpenClaw agent-config 才是运行时真源**。
  / "openai-direct/<模型>"（旧直连保留）：engine.OpenAILLM，须 env
  OPENAI_API_KEY / OPENAI_GPT6_KEY，缺=快速 LLMError；仅供迁移/对照，
  生产路由禁止使用。
  / "deepseek/deepseek-flash"（席位级降级链主力）：engine.DeepSeekLLM
  （工具级 DeepSeekClient 适配器）；org `llm_fallback` 的 deepseek 项由此承接，
  glm 429 时由调用方显式降级并标注 degraded（本层不自动改写绑定）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLATFORM = str(Path(__file__).resolve().parents[1])
if _PLATFORM not in sys.path:
    sys.path.insert(0, _PLATFORM)

from engine.llm import LLMError, make_llm  # noqa: E402

from gateway.envelope import make_envelope, timed  # noqa: E402

KIND = "llm_chat"

DEFAULT_BINDING = "ollama/qwen3.5:9b"

# 这些 provider 在 make_llm 构造时**已经**吃下了自己的模型/端口：
#   "glm/<模型>"           → GLMCloudLLM(model="<模型>")
#   "nova/<端口>"          → NovaMaxLLM(port=..., model=PORTS[端口])
#   "openai/<模型>"        → OpenClawLLM(model="<模型>"，实际模型由 agent-config 决定；
#                            2026-10-27 B05 单轨化：与 L5/8796 同源，不直连、不需 env key)
#   "openai-direct/<模型>" → OpenAILLM(model="<模型>")（旧直连保留，须 env key）
#   "deepseek/<模型>"      → DeepSeekLLM(model="<模型>")（席位级降级链目标；
#                            2026-10-27 起 org llm_fallback 的 deepseek 项由它承接）
# 因此 chat 时不能再把整串 binding 当 model 传回去。
_PREFIX_OWNS_MODEL = frozenset({"glm", "nova", "openai", "openai-direct", "deepseek"})


@timed
def chat(binding=None, system="", user=""):
    """LLM 调用信封门面。返回标准信封（见 envelope.make_envelope）。

    data: {binding_resolved, text, status}
    error: {code:"llm_error", message} —— 重试耗尽/通道不可用，调用方应回落自身兜底。

    model 实参的口径（2026-10-21 修）：
      旧实现一律 `llm.chat(system, user, model=binding)`，把 `glm/glm-5.3` 整串
      当模型名发给智谱 → **HTTP 400「模型不存在」**。实测对照：
      `GLMCloudLLM(model="glm-5.3").chat(..., model="glm/glm-5.3")` → 400；
      同实例不传 model → 200（直连 `glm-5.3-flash` / `glm-4.6` 亦均 200）。
      即 S38「glm-5.3-flash 模型名不存在」的判例**是这条传参缺陷造成的误判**。
    """
    binding = binding or DEFAULT_BINDING
    try:
        llm = make_llm(binding)
    except Exception as exc:  # 构造失败（如 nova 端口非法）也归一为信封
        return make_envelope(
            kind=KIND,
            ok=False,
            error={"code": "llm_error", "message": "make_llm failed: %s" % exc},
        )
    prefix, sep, rest = binding.partition("/")
    try:
        if prefix in _PREFIX_OWNS_MODEL and sep and rest:
            text, status = llm.chat(system, user)
        elif prefix == "ollama" and sep and rest:
            text, status = llm.chat(system, user, model=rest)
        else:
            text, status = llm.chat(system, user, model=binding)
    except LLMError as exc:
        return make_envelope(
            kind=KIND, ok=False, error={"code": "llm_error", "message": str(exc)}
        )
    data = {"binding_resolved": binding, "text": text, "status": status}
    # 2026-10-27 /llm/chat 接诸葛亮验证：OpenClawLLM 暴露 last_meta（载体/中转信息），
    # 透传给本地网关调用方，便于核验「中转替代（relay=true）/ 真身」而不必猜配置。
    meta = getattr(llm, "last_meta", None)
    if isinstance(meta, dict) and meta:
        for key in ("mode", "agent", "carrier", "relay", "requested_model"):
            if key in meta:
                data[key] = meta[key]
    return make_envelope(kind=KIND, ok=True, degraded=(status == "degraded"), data=data)
