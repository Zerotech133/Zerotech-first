# -*- coding: utf-8 -*-
"""LLM 绑定层：Ollama 客户端 + MODEL_PROFILES 自动路由 + NovaMax 特种 + 降级三态

模型分层（2026-10-18 自动路由改造，K-ORG-002 修正后重校）：
    A0 总管 = qwen3.6:35b-a3b（灰度上岗）/ 部门成员 = qwen3.5:9b /
    特种(视觉/结构化) = NovaMax 1234/1238 / 嵌入 = bge-m3(1024维)
本地库在役（2026-10-26 /api/tags 实测）：qwen3.6:35b-a3b / qwen3.5:9b / qwen3.5:4b / bge-m3。
qwen2.5:7b / qwen3:8b 已于 10-24 清理（config/ollama_model_route.json deleted_2026-10-17）；
旧绑定引用经 MODEL_ALIASES 兜底映射到 qwen3.5:9b，禁止再新增旧模型名引用。

自动路由设计（老田拍板：分工前期配好，运行时不需手动调）：
    binding 字符串 "ollama/<模型名>" 前缀由 interpreter 拆出模型名传入 chat()，
    chat() 内按模型名查 MODEL_PROFILES 自动配 num_predict/timeout/temperature——
    新模型上岗只需在本表加一行 profile，org.json 与业务代码零改动。

降级三态（对齐菜鸟 degraded）：normal / degraded / failed——LLM 不可用时解释器走规则兜底，不中断全链路。

异族通道（2026-10-21 老田异族编制裁决；2026-10-26 B 案落码；2026-10-27 B05 单轨化）：
    "openai/<模型>"        → OpenClawLLM —— **委托 OpenClaw agent-config 载体**，
                             与 L5/8796 同源（engine/openclaw_cli.run）；实际模型由
                             ~/.openclaw agents.entries[<agent>].model.primary 决定，
                             绑定串里的模型名只作请求口径记录；不直连 api.openai.com、
                             不需要 OPENAI_API_KEY（密钥由 OpenClaw secret store 承载）。
                             **OpenClaw agent-config 才是运行时真源**（A2-B05 口径）。
    "openai-direct/<模型>" → OpenAILLM —— 旧直连保留通道（https://api.openai.com/v1，
                             key 从 OPENAI_API_KEY / OPENAI_GPT6_KEY 读取，零落盘），
                             仅供迁移/对照；生产绑定禁止直接使用。
    "deepseek/<模型>"      → DeepSeekLLM —— 工具级 DeepSeek 云端通道
                             （engine/deepseek.py 的 DeepSeekClient 适配器），缺省模型
                             deepseek-flash；组织 `llm_fallback` 里的 deepseek 项即由
                             此承接（WO-LEGION-DEVEXEC2-20261027 席位级 fallback 链）。
    以上通道失败/空回一律抛 LLMError，**不静默降档本地 9b**（与 GLMCloudLLM 同纪律）。
"""

import json
import os
import time
import urllib.request
import logging

# ---- 模型自动路由表（10-18 调优：按模型名自动配置，调用方零感知）----
# num_predict：35b 重岗（A0 判读/日报长文）需长输出；9b 判读位 400 够用（JSON/短评）
# timeout：35b 冷加载+长生成实测 >300s，给 600s；9b 沿用 300s（live 首跑教训值）
#
# 2026-10-23 实测修订（AI 底层能力审计）：
#   · 吞吐：4b/9b 23.5 tok/s，35b 7.15 tok/s（纯 CPU，无 GPU）
#   · 35b 的 num_predict=4000 需 559s，原 timeout=600 只剩 41s 余量（还含 38.5s 冷加载）
#     → timeout 提到 900s。不改 num_predict：A0 日报长文确实需要长输出。
#   · num_ctx 原先**谁都没设**，于是取模型默认值：4b/35b 是 65536，而 **9b 只有 4096**
#     ——9b 恰是平台默认模型，任何超长提示会被静默截断（不报错、只是答偏）。
#     2026-10-23 先给 9b 显式设 32768。
#
# 2026-10-24 收口（num_ctx 全仓统一 16384）：
#   · 问题不是"某个模型 ctx 小"，而是**各调用方各设各的**——512/2048/4096/8192/
#     16384/32768 六种值。Ollama 每次 num_ctx 变化都整模型重载（实测 15~46s），
#     10-24 一天重载 99 次，其中一次 37 秒内连重载 4 次。
#   · 平台原先的 32768 是六种值里最大的一档，最容易被别的调用方"踢回"小值。
#     统一到 16384：够覆盖长提示（约 1.2 万字），且与 M3/报告/分类/复盘同值。
#   · 35b/4b 不显式给 num_ctx，继续吃服务端默认 65536（同模型同值即不重载）；
#     iGPU/Vulkan + KV q8_0 实测 16384 与 4096 生成吞吐相同（33.5 vs 33.8 tok/s）。
MODEL_PROFILES = {
    "qwen3.6:35b-a3b": {"num_predict": 4000, "timeout": 900, "temperature": 0.4},
    "qwen3.5:9b": {
        "num_predict": 400,
        "timeout": 300,
        "temperature": 0.4,
        "num_ctx": 16384,
    },
    "qwen3.5:4b": {
        "num_predict": 400,
        "timeout": 240,
        "temperature": 0.4,
    },  # T2 轻量层(10-18 补下载落位)
}
DEFAULT_PROFILE = {"num_predict": 400, "timeout": 300, "temperature": 0.4}
# 2026-10-24 模型清理已删 qwen2.5:7b / qwen3:8b（见 config/ollama_model_route.json
# deleted_2026-10-17）；10-26 ai.error_rate 红=残留绑定仍发旧模型名 → /api/chat 404
# （实测 404 {"error":"model 'qwen2.5:7b' not found"}，耗时 ~3ms）。统一在此映射到
# 在役 9b，避免席位/HR 旧绑定静默 404 空转。
MODEL_ALIASES = {
    "qwen2.5:7b": "qwen3.5:9b",
    "qwen3:8b": "qwen3.5:9b",
}


def resolve_profile(model):
    """按模型名取 profile：精确匹配 → 子串模糊（容错 tag 变体）→ 默认档"""
    if not model:
        return dict(DEFAULT_PROFILE)
    if model in MODEL_PROFILES:
        return dict(MODEL_PROFILES[model])
    for key, prof in MODEL_PROFILES.items():
        if key in model or model in key:
            return dict(prof)
    return dict(DEFAULT_PROFILE)


class LLMError(Exception):
    pass


def similarity(a, b):
    """字符 bigram 余弦相似度（零依赖，PoC 用；后续可升级 bge-m3 嵌入 1024 维）"""
    import math
    from collections import Counter

    def grams(s):
        s = "".join(ch for ch in s if not ch.isspace())
        return Counter(s[i : i + 2] for i in range(len(s) - 1)) or Counter()

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    dot = sum(ga[g] * gb.get(g, 0) for g in ga)
    na = math.sqrt(sum(v * v for v in ga.values()))
    nb = math.sqrt(sum(v * v for v in gb.values()))
    return dot / (na * nb) if na * nb else 0.0


class OllamaLLM:
    def __init__(self, host="http://127.0.0.1:11434", timeout=None, retries=1):
        # timeout=None：不设调用方预算，按 MODEL_PROFILES 的模型档超时（35b 900s / 9b 300s）。
        # timeout=<秒>：调用方预算 = 硬上限（2026-10-26 A2-B01：旧实现 profile 会覆盖
        # 调用方显式传入的 12s/25s，L5 本地降级与意图分类最坏挂 ~601s）。
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.last_meta = None  # 最近一次调用元信息（done_reason/eval_count），供排障

    def chat(self, system, user, model="qwen3.5:9b"):
        """返回 (text, status)；重试耗尽抛 LLMError（由解释器转降级）

        10-18 自动路由：options 按 MODEL_PROFILES[model] 自动生成，
        每次调用的超时也随 profile 分级（35b 600s / 9b 300s）。
        10-18 全前缀分发（本类现为全 binding 兼容门面）：
        "glm/…" 委托云端 GLMCloudLLM、"nova/…" 委托 NovaMaxLLM；
        "openai/…" 委托 OpenClaw 载体 OpenClawLLM（2026-10-27 B05，与 L5/8796 同源）；
        "openai-direct/…" 委托旧直连 OpenAILLM、"ollama/…" 剥前缀——
        其余（含 huihui_ai/… 这类真名带斜杠的）原样走本地。
        """
        if model and "/" in model:
            prefix, rest = model.split("/", 1)
            if prefix == "glm":
                try:
                    return GLMCloudLLM(model=rest).chat(system, user)
                except LLMError:
                    # 云端额度/网络不可用 → 自动降级本地 9b（10-18：包年 key 周限至 10-20 18:57，
                    # 期间 7+1 云端席位全部由本档兜底；10-20 后云端自动恢复优先）
                    text, st = OllamaLLM().chat(system, user, model="qwen3.5:9b")
                    return text, ("degraded" if st == "normal" else st)
            if prefix == "nova":
                try:
                    port = rest.split("/", 1)[0]
                    return NovaMaxLLM(port=port).chat(system, user)
                except LLMError:
                    text, st = OllamaLLM().chat(system, user, model="qwen3.5:9b")
                    return text, ("degraded" if st == "normal" else st)
            if prefix == "openai-direct":
                # 旧直连保留通道（2026-10-27 B05）：须 env key，缺失/断链抛错、不降档
                return OpenAILLM(model=rest).chat(system, user)
            if prefix == "openai":
                # 异族席位（AI1=诸葛亮）：委托 OpenClaw agent-config 载体（运行时真源）
                return OpenClawLLM(model=rest).chat(system, user)
            if prefix == "ollama":
                model = rest
        model = MODEL_ALIASES.get(model or "", model)
        prof = resolve_profile(model)
        options = {
            "temperature": prof["temperature"],
            "num_predict": prof["num_predict"],
        }
        # num_ctx 只在 profile 显式给了才下发：不给时 Ollama 用模型自带默认值，
        # 而各模型默认差异极大（9b 只有 4096）。少下发一个键，就会让长提示静默截断。
        if prof.get("num_ctx"):
            options["num_ctx"] = prof["num_ctx"]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,  # qwen3.5 思考模型：必须关思考（实测 400 token 全被 thinking 吃掉，content 为空）
            "options": options,
        }
        per_call_timeout = prof["timeout"] or 300
        if self.timeout:
            # 调用方显式预算优先（取更小者）：profile 只是模型档默认，不是硬覆盖。
            per_call_timeout = min(per_call_timeout, self.timeout)
        last = None
        for _ in range(self.retries + 1):
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    self.host + "/api/chat",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with op.open(req, timeout=per_call_timeout) as r:
                    d = json.load(r)
                # 保留生成元信息：旧实现只要 content，done_reason 被丢掉——
                # 于是「被 num_predict 截断」与「模型没守格式」两种故障在证据上
                # 完全无法区分（10-17 排障时只能靠外部探针反推）。
                self.last_meta = {
                    k: d.get(k)
                    for k in (
                        "done_reason",
                        "eval_count",
                        "prompt_eval_count",
                        "total_duration",
                    )
                }
                # done_reason=="length"：生成被上限截断，内容不完整（JSON 常在尾部，
                # 截断必致 parse 失败）→ 显式降级，不当作正常样本入库。
                status = "degraded" if d.get("done_reason") == "length" else "normal"
                return d["message"]["content"], status
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("ollama chat failed after retry: %s" % last)


class MockLLM:
    """冒烟测试专用：按角色关键词返回模板文本，零 GPU 占用"""

    REPLIES = {
        "t1_bull": "技术面看多：均线多头排列，量能温和放大，突破平台确认，短线上行动能充足，回踩即买点。",
        "t2_bear": "技术面看空：上方面临前高压力，MACD 顶背离迹象，量价配合不佳，谨防冲高回落，宜减仓观望。",
        "n1_sentiment": "情绪面中性偏暖：近期无重大利空事件，行业政策面平稳，市场情绪指数处于中位区间。",
        "f1_fundamental": "基本面稳健：营收与利润保持增长，估值处于历史中低分位，经营现金流健康。",
        "d1_decision": (
            '综合裁决：{"recommendation": "BUY", "confidence": 0.62, '
            '"target_price_range": [88.0, 96.0], '
            '"rationale": "多空辩论两轮后技术面动能论据占优，情绪面中性偏暖，基本面无重大风险，给予买入建议"}'
        ),
    }

    def chat(self, system, user, model=None):
        for key, text in self.REPLIES.items():
            if key in (system or ""):
                return text, "normal"
        return self.REPLIES["n1_sentiment"], "normal"


class NovaMaxLLM:
    """NovaMax 特种通道（llama.cpp，OpenAI 兼容 /v1/chat/completions）

    10-18 供给方案：视觉岗 → 1234 (Qwen3.5-35B-A3B 视觉版)；
    结构化/JSON 质检岗 → 1238 (Qwen3-30B-A3B-Instruct-2507)。
    binding 约定："nova/1234/<模型名>" 或 "nova/1238/<模型名>"——
    端口即路由，模型名透传给 llama-server（须与其实际加载的 GGUF 一致）。
    由 NovaMax 面板(3001)+watchdog nova_patrol 保活，进程死亡自动拉起。
    """

    PORTS = {
        "1234": "unsloth_Qwen3.5-35B-A3B-GGUF",
        "1238": "unsloth_Qwen3-30B-A3B-Instruct-2507-GGUF",
    }

    def __init__(self, port=1238, timeout=600, retries=1):
        if isinstance(port, str) and port.isdigit():
            port = int(port)
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.model = self.PORTS.get(str(port))
        self.last_meta = None

    def chat(self, system, user, model=None):
        model = model or self.model
        body = {
            "model": model or "default",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": 0.4,
            "max_tokens": 4000,
        }
        last = None
        for _ in range(self.retries + 1):
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/v1/chat/completions" % self.port,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with op.open(req, timeout=self.timeout) as r:
                    d = json.load(r)
                choice = d["choices"][0]
                finish = choice.get("finish_reason")
                self.last_meta = {"finish_reason": finish, "usage": d.get("usage")}
                # finish_reason=="length" 与 Ollama done_reason=="length" 同义：截断即降级
                status = "degraded" if finish == "length" else "normal"
                return choice["message"]["content"], status
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("novamax chat failed after retry: %s" % last)


def _maybe_wrap_llm(llm):
    """D3 / ADR-005：可观测单点包装。默认完全 no-op；DSH_OTEL_ENABLED=1 且
    opentelemetry 可用时才加 gen_ai span；任何异常都回退原对象（不反噬主链路）。"""
    try:
        try:
            from agent_platform.observability.genai import wrap_llm
        except ImportError:  # 以 agent_platform 为 sys.path 根时
            from observability.genai import wrap_llm
        return wrap_llm(llm)
    except Exception:  # noqa: BLE001
        return llm


def make_llm(binding):
    """按 binding 前缀路由到 provider（10-18 自动路由入口；10-27 B05 双轨拆分）

    前缀语义（完整口径见文件头；**OpenClaw agent-config 才是运行时真源**）：
    "ollama/<模型>"        → OllamaLLM（本地降级三态）
    "nova/<端口>"          → NovaMaxLLM（特种视觉/结构化）
    "glm/<模型>"           → GLMCloudLLM（参谋长层云端判读）
    "openai/<模型>"        → OpenClawLLM（诸葛亮异族席，2026-10-21 裁决；
                             委托 OpenClaw agent-config 载体，实际模型由 agent 配置决定，
                             不直连、不需要 env key）
    "openai-direct/<模型>" → OpenAILLM（旧直连保留：api.openai.com + env key，仅供
                             迁移/对照；2026-10-27 B05 起不再被 AI1 绑定使用）
    "deepseek/<模型>"      → DeepSeekLLM（工具级 DeepSeek 云端；缺模型名走
                             deepseek-flash——org `llm_fallback` 的降级链目标）
    其余 → OllamaLLM 兜底（会走降级三态，行为与旧版一致）。
    """
    prefix, _, rest = (binding or "").partition("/")
    if prefix == "nova":
        port = rest.split("/", 1)[0] if "/" in rest else rest
        return _maybe_wrap_llm(NovaMaxLLM(port=port))
    if prefix == "glm":
        return _maybe_wrap_llm(GLMCloudLLM(model=rest))
    if prefix == "openai-direct":
        return _maybe_wrap_llm(OpenAILLM(model=rest or "gpt-6.0"))
    if prefix == "openai":
        return _maybe_wrap_llm(OpenClawLLM(model=rest))
    if prefix == "deepseek":
        return _maybe_wrap_llm(DeepSeekLLM(model=rest or DEFAULT_DEEPSEEK_MODEL))
    return _maybe_wrap_llm(OllamaLLM())


class GLMCloudLLM:
    """智谱云端通道（参谋长层判读席位，K-ORG-130→136 判例直译）

    coding 计划端点 /api/coding/paas/v4（OpenAI 兼容），模型 glm-5.3 思考型。
    key 优先级与 a0_agent._glm_key 同口径：portal/audit_ai_config.json 的
    zhipu_key > GLM_API_KEY 环境变量；key 缺失直接抛 LLMError，不静默降档——
    席位模型是判读有效性的组成部分（K-ORG-136）。
    返回 (text, status) 与 OllamaLLM 同构；engine 侧 status 二值（云端无截断
    中间态，空回/失败一律抛错走降级三态）。
    """

    ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"

    def __init__(self, model="glm-5.3", timeout=240, retries=1):
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.last_meta = None

    @staticmethod
    def _key():
        try:
            from pathlib import Path

            import os as _os
            # engine/llm.py 的 parents[2] = 项目根（与 a0_agent 的 parent.parent 同点）
            for p in (Path(_os.environ.get("AUDIT_AI_CONFIG_PATH")
                           or r"C:\项目\_vault\secrets\portal_audit_ai_config.json"),
                      Path(__file__).resolve().parents[2] / "portal" / "audit_ai_config.json"):
                if p.exists():
                    zk = json.loads(p.read_text(encoding="utf-8")).get("zhipu_key", "")
                    if zk:
                        return zk
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/llm.py", exc)
        import os

        return os.environ.get("GLM_API_KEY", "")

    def chat(self, system, user, model=None):
        key = self._key()
        if not key:
            raise LLMError(
                "智谱 key 未配置（audit_ai_config.json zhipu_key / "
                "GLM_API_KEY 均空）——参谋长席位不静默降档"
            )
        body = {
            "model": model or self.model,
            "max_tokens": 16384,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        last = None
        for _ in range(self.retries + 1):
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    self.ENDPOINT,
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + key,
                    },
                )
                with op.open(req, timeout=self.timeout) as r:
                    d = json.load(r)
                msg = (d.get("choices") or [{}])[0].get("message") or {}
                out = (msg.get("content") or "").strip()
                self.last_meta = {
                    "finish_reason": d.get("finish_reason"),
                    "usage": d.get("usage"),
                }
                if not out:
                    raise LLMError(
                        "glm 空回（思考型吞 content? finish=%s）"
                        % d.get("finish_reason")
                    )
                return out, "normal"
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("glm 通道调用失败 after retry: %s" % last)


DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


def _deepseek_module():
    """延迟导入 engine/deepseek（工具级 DeepSeek 客户端，WO-LEGION-LLMDEEPSEEK）。

    与 `_openclaw_module` 同法兼容两种导入上下文：包上下文（engine.llm）与
    engine/ 直接进 sys.path 的扁平上下文。延迟导入还保证两条纪律：
    ① `make_llm("deepseek/…")` 的构造期不读 key、不联网；
    ② 测试可 monkeypatch `engine.deepseek.*`（取到同一个模块实例）。
    """
    try:
        from engine import deepseek as mod
    except ImportError:  # engine/ 目录直接位于 sys.path
        import deepseek as mod
    return mod


class DeepSeekLLM:
    """DeepSeek 云端通道（`deepseek/<模型>` 前缀；工具级 DeepSeekClient 适配器）

    定位（WO-LEGION-DEVEXEC2-20261027 席位级 fallback 链）：把组织
    `llm_fallback` 里的 `deepseek/deepseek-flash` 真正接通——glm 主绑失败时，
    会议/指挥台按席位降级链先落 DeepSeek、再落本地 9b，而不是一律硬降 9b。

    契约与 OllamaLLM 同构（调用方可整体替换）：
    - `chat(system="", user="", model=None, **kw)` → `(text, status)`；
      status 二值：`"normal"` / `"degraded"`（finish_reason=="length" 视为截断）；
    - 空回 / 缺 key / 网络失败 / 响应结构异常 → 抛 `LLMError`，**不静默降档
      本地 9b**（与 GLMCloudLLM / OpenAILLM 同纪律：降级由调用方显式决定并标注）；
    - `last_meta` 记录 model/finish_reason/usage，供排障与诚实标签；
    - 惰性构造 DeepSeekClient：构造期不读 key、不联网——`make_llm` 在无 key 的
      环境下仍能安全构造，缺 key 到首次 `chat` 才报 `LLMError`（fail-visible）。

    密钥纪律沿用 engine/deepseek.py：DEEPSEEK_API_KEY → DEEPSEEK_API_KEY_FILE →
    默认私有文件；本类只透传，不打印、不落盘任何密钥。
    """

    # 一句话级 ≥64，结构化抽取 ≥512（推理模型 reasoning token 与正文共用预算，
    # 短预算会把正文饿空——见 engine/deepseek.py chat_full 判例）。
    DEFAULT_MAX_TOKENS = 2048

    def __init__(
        self,
        model=None,
        timeout=180,
        retries=1,
        api_key=None,
        transport=None,
        client=None,
    ):
        self.model = model or DEFAULT_DEEPSEEK_MODEL
        self.timeout = timeout
        self.retries = retries
        self.api_key = api_key
        self.transport = transport  # 注入离线 transport（透传 DeepSeekClient）
        self._client = client  # 注入现成 client/测试桩；None → 首次 chat 惰性建
        self.last_meta = None

    def _ensure_client(self):
        """惰性构造 DeepSeekClient；构造失败（如缺 key）归一为 LLMError。"""
        if self._client is not None:
            return self._client
        mod = _deepseek_module()
        try:
            self._client = mod.DeepSeekClient(
                api_key=self.api_key,
                model=self.model,
                timeout=self.timeout,
                retries=self.retries,
                transport=self.transport,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError("deepseek 客户端不可用：%s——云端席位不静默降档" % exc)
        return self._client

    def chat(self, system="", user="", model=None, **kw):
        client = self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": str(user)})
        opts = {"temperature": 0.4, "max_tokens": self.DEFAULT_MAX_TOKENS}
        for key in ("temperature", "max_tokens", "response_format"):
            if key in kw:  # 已知档位透传；未知 kw 忽略（不猜语义）
                opts[key] = kw[key]
        model = model or self.model
        mod = _deepseek_module()
        try:
            full = client.chat_full(messages, model=model, **opts)
        except mod.DeepSeekError as exc:
            raise LLMError("deepseek 通道调用失败：%s——云端席位不静默降档" % exc)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(
                "deepseek 通道调用失败：%s: %s——云端席位不静默降档"
                % (type(exc).__name__, exc)
            )
        text = str((full or {}).get("content") or "").strip()
        finish = (full or {}).get("finish_reason")
        self.last_meta = {
            "model": (full or {}).get("model") or model,
            "finish_reason": finish,
            "usage": (full or {}).get("usage") or {},
        }
        if not text:
            # 假成功判例：HTTP 200 但正文为空（推理 token 吃光预算）≠ 可用
            self.last_meta["error"] = "empty_content"
            raise LLMError(
                "deepseek 空回（finish=%s；推理 token 可能吃光 max_tokens）"
                "——云端席位不静默降档" % finish
            )
        return text, ("degraded" if finish == "length" else "normal")


def _openclaw_module():
    """延迟导入 engine/openclaw_cli。

    兼容两种导入上下文：包上下文（engine.llm）与扁平上下文（engine/ 直接进 sys.path，
    如 python engine/xxx.py 或部分测试）。延迟导入同时保证测试可 monkeypatch
    `engine.openclaw_cli.run`（取到的是同一个模块实例）。
    """
    try:
        from engine import openclaw_cli as mod
    except ImportError:  # engine/ 目录直接位于 sys.path
        import openclaw_cli as mod
    return mod


class OpenClawLLM:
    """OpenClaw agent-config 载体（诸葛亮 AI1 异族席的**运行时真源**；2026-10-27 B05）

    背景（A2-B05 双轨）：同一绑定串 `openai/gpt-6.0` 曾在不同层含义不同——L5/8796
    走 OpenClaw CLI（GPT6 跨境不可达期间由 deepseek 中转），engine 侧却直连
    api.openai.com。现 engine 的 `openai/` 前缀与会议厅同源、**单轨化**：一律委托
    `engine/openclaw_cli.run(agent, prompt)`，实际模型由 OpenClaw agent-config
    （`~/.openclaw/openclaw.json` 的 agents.entries[agent].model.primary）决定；
    绑定串里的模型名只作请求口径记录，不再直连、不需要 OPENAI_API_KEY。
    旧直连保留为 `openai-direct/<模型>`（OpenAILLM），仅供迁移/对照。

    纪律（与 GLMCloudLLM / 旧 OpenAILLM 一致）：
    - 失败 / 超时 / 空回 → 抛 LLMError，**不静默降档本地 9b**（异族席位不冒充成功）；
    - 成功返回 (text, "normal")；last_meta 记录 mode/agent/carrier/relay
      （carrier=agent-config 实际模型，relay=是否非 GPT6 中转），供排障与诚实标签；
    - 零密钥落盘：本层不读任何 key env，密钥由 OpenClaw secret store 承载。
    """

    AGENT = "zhuge"  # 诸葛亮在 OpenClaw agent-config 中的 agent 名
    PROMPT_LIMIT = 16000  # 与 8796 /api/chat、portal 口径一致：单行提示上限

    def __init__(self, agent=None, model=None, timeout=None):
        self.agent = agent or self.AGENT
        self.model = model or ""  # 仅请求口径记录；实际模型由 agent-config 决定
        self.timeout = timeout  # None → openclaw_cli.DEFAULT_TIMEOUT（env 可覆盖）
        self.last_meta = None

    @classmethod
    def compose_prompt(cls, system, user):
        """system + user 拼单行后截 PROMPT_LIMIT（与 openclaw_cli.run 的空白归一幂等）"""
        one_line = " ".join(("%s %s" % (system or "", user or "")).split())
        return one_line[: cls.PROMPT_LIMIT]

    def chat(self, system, user, model=None):
        prompt = self.compose_prompt(system, user)
        try:
            oc = _openclaw_module()
        except Exception as exc:  # noqa: BLE001
            raise LLMError("openclaw_cli 不可用：%s——异族席位不静默降档" % exc)
        carrier = oc.agent_primary_model(self.agent)
        self.last_meta = {
            "mode": "openclaw",
            "agent": self.agent,
            "carrier": carrier,
            "relay": oc.is_relay(carrier),
            "requested_model": model or self.model or "",
            "prompt_chars": len(prompt),
        }
        if not prompt:
            self.last_meta["error"] = "empty_prompt"
            raise LLMError(
                "openclaw 载体提示为空（agent=%s）——异族席位不静默降档" % self.agent
            )
        # 不传 model：实际模型由 agent-config 决定（--model 仅调试覆盖用，运行时不用）
        if self.timeout is None:
            text, err = oc.run(self.agent, prompt)
        else:
            text, err = oc.run(self.agent, prompt, timeout=self.timeout)
        if err or not text:
            self.last_meta["error"] = err or "empty_output"
            raise LLMError(
                "openclaw 载体不可用（agent=%s, carrier=%s）：%s"
                "——异族席位不静默降档"
                % (self.agent, carrier or "unknown", err or "empty_output")
            )
        return text, "normal"


class OpenAILLM:
    """OpenAI 直连通道（**旧直连保留**：2026-10-27 B05 起仅经 `openai-direct/` 触达）

    异族席：诸葛亮 AI1 / GPT-6.0，2026-10-21 异族编制裁决。运行时 AI1 绑定
    `openai/gpt-6.0` 已改走 OpenClaw agent-config 载体（见 OpenClawLLM）——本类
    不再承接该绑定，只作迁移/对照通道；**OpenClaw agent-config 才是运行时真源**。

    纪律（2026-10-26 B 案，与 GLMCloudLLM 同款）：
    - key 只从环境变量读：OPENAI_API_KEY 优先，回退 OPENAI_GPT6_KEY；零落盘。
    - base_url：OPENAI_BASE_URL 可覆盖（默认 https://api.openai.com/v1）。
    - key 缺失 / 空回 / 重试耗尽 → 抛 LLMError，**不静默降档本地 9b**：
      异族通道是否可用是席位有效性的一部分，调用方（会议厅/门面）自行决定
      是否显式降级并标注 degraded，不得让本类冒充成功。
    """

    DEFAULT_BASE = "https://api.openai.com/v1"
    KEY_ENVS = ("OPENAI_API_KEY", "OPENAI_GPT6_KEY")

    def __init__(
        self, model="gpt-6.0", timeout=240, retries=1, base_url=None, api_key=None
    ):
        self.model = model or "gpt-6.0"
        self.timeout = timeout
        self.retries = retries
        self.base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or self.DEFAULT_BASE
        ).rstrip("/")
        self.api_key = api_key or ""
        self.last_meta = None

    @classmethod
    def _key(cls):
        for name in cls.KEY_ENVS:
            v = os.environ.get(name, "")
            if v:
                return v
        return ""

    def chat(self, system, user, model=None):
        key = self.api_key or self._key()
        if not key:
            raise LLMError(
                "openai key 未配置（%s 均空）——异族席位不静默降档"
                % " / ".join(self.KEY_ENVS)
            )
        body = {
            "model": model or self.model,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        last = None
        for _ in range(self.retries + 1):
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + key,
                    },
                )
                with op.open(req, timeout=self.timeout) as r:
                    d = json.load(r)
                choice = (d.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                out = (msg.get("content") or "").strip()
                finish = choice.get("finish_reason")
                self.last_meta = {"finish_reason": finish, "usage": d.get("usage")}
                if not out:
                    raise LLMError("openai 空回（finish=%s）" % finish)
                return out, ("degraded" if finish == "length" else "normal")
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("openai 通道调用失败 after retry: %s" % last)
