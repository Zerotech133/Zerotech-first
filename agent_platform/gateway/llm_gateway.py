# -*- coding: utf-8 -*-
"""LLM 稳定门面（WO-LEGION-S21）。

给外部板块（飞书中台等）的唯一 LLM 调用入口：binding → engine/llm.py
make_llm → chat。不建第二调用层——路由/降级三态/超时档位全部复用军团
engine 语义，本层只做信封化与异常归一：

  LLMError（重试耗尽）→ ok=False 信封（code="llm_error"），不抛穿；
  status=="degraded"  → degraded=True 透传（云端故障自动落本地 9b 判例）。

binding 词表（10-18 分工定案 + S20 异族裁决）：
  "ollama/qwen3.5:9b"（默认主力）/ "ollama/qwen3.6:35b-a3b"（判读重档）
  / "ollama/bge-m3"（嵌入）/ "glm/glm-5.3"（田丰参谋长云端）
  / "openai/gpt-6.0"（诸葛亮异族）：本门面的 openai 前缀仍走 engine 的直连
  OpenAILLM（须 env OPENAI_API_KEY，缺=快速 LLMError）；**运行时 L5/8796 的
  AI1 已改走 OpenClaw agent-config 载体（DeepSeek 中转，见 engine/openclaw_cli.py），
  二者口径不同，勿混用**（2026-10-26 A2-B05 记录）。
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
#   "glm/<模型>" → GLMCloudLLM(model="<模型>")
#   "nova/<端口>" → NovaMaxLLM(port=..., model=PORTS[端口])
#   "openai/<模型>" → OpenAILLM(model="<模型>")（2026-10-26 B 案）
# 因此 chat 时不能再把整串 binding 当 model 传回去。
_PREFIX_OWNS_MODEL = frozenset({"glm", "nova", "openai"})


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
        return make_envelope(kind=KIND, ok=False,
                             error={"code": "llm_error",
                                    "message": "make_llm failed: %s" % exc})
    prefix, sep, rest = binding.partition("/")
    try:
        if prefix in _PREFIX_OWNS_MODEL and sep and rest:
            text, status = llm.chat(system, user)
        elif prefix == "ollama" and sep and rest:
            text, status = llm.chat(system, user, model=rest)
        else:
            text, status = llm.chat(system, user, model=binding)
    except LLMError as exc:
        return make_envelope(kind=KIND, ok=False,
                             error={"code": "llm_error", "message": str(exc)})
    return make_envelope(kind=KIND, ok=True, degraded=(status == "degraded"),
                         data={"binding_resolved": binding,
                               "text": text, "status": status})
