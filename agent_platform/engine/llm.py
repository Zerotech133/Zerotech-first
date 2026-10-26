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

异族通道（2026-10-21 老田异族编制裁决；2026-10-26 B 案落码）：
    "openai/<模型>" → OpenAILLM（诸葛亮 AI1 / GPT-6.0，OpenClaw 载体）。
    key 从环境变量 OPENAI_API_KEY / OPENAI_GPT6_KEY 读取（零落盘）；缺失或断链
    一律抛 LLMError，**不静默降档本地 9b**（与 GLMCloudLLM 同纪律）。
"""
import json
import os
import time
import urllib.request

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
    "qwen3.5:9b": {"num_predict": 400, "timeout": 300, "temperature": 0.4,
                   "num_ctx": 16384},
    "qwen3.5:4b": {"num_predict": 400, "timeout": 240, "temperature": 0.4},  # T2 轻量层(10-18 补下载落位)
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
        return Counter(s[i:i + 2] for i in range(len(s) - 1)) or Counter()

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    dot = sum(ga[g] * gb.get(g, 0) for g in ga)
    na = math.sqrt(sum(v * v for v in ga.values()))
    nb = math.sqrt(sum(v * v for v in gb.values()))
    return dot / (na * nb) if na * nb else 0.0


class OllamaLLM:
    def __init__(self, host="http://127.0.0.1:11434", timeout=300, retries=1):
        # timeout=300：解释器层内并行多路请求会在 Ollama 排队（实测 9b 冷加载 34s，
        # 3 路并行排队最坏 3×生成时长），90s 级超时会全军覆没（2026-10-08 live 首跑教训）
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.last_meta = None  # 最近一次调用元信息（done_reason/eval_count），供排障

    def chat(self, system, user, model="qwen3.5:9b"):
        """返回 (text, status)；重试耗尽抛 LLMError（由解释器转降级）

        10-18 自动路由：options 按 MODEL_PROFILES[model] 自动生成，
        每次调用的超时也随 profile 分级（35b 600s / 9b 300s）。
        10-18 全前缀分发（本类现为全 binding 兼容门面）：
        "glm/…" 委托云端 GLMCloudLLM、"nova/…" 委托 NovaMaxLLM、
        "ollama/…" 剥前缀——其余（含 huihui_ai/… 这类真名带斜杠的）原样走本地。
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
            if prefix == "openai":
                # 异族席位（AI1=诸葛亮）：key 缺失/断链必须抛错，不降档本地（同 glm 纪律）
                return OpenAILLM(model=rest).chat(system, user)
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
        per_call_timeout = prof["timeout"] or self.timeout
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
                self.last_meta = {k: d.get(k) for k in
                                  ("done_reason", "eval_count", "prompt_eval_count",
                                   "total_duration")}
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

    PORTS = {"1234": "unsloth_Qwen3.5-35B-A3B-GGUF",
             "1238": "unsloth_Qwen3-30B-A3B-Instruct-2507-GGUF"}

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
                self.last_meta = {"finish_reason": finish,
                                  "usage": d.get("usage")}
                # finish_reason=="length" 与 Ollama done_reason=="length" 同义：截断即降级
                status = "degraded" if finish == "length" else "normal"
                return choice["message"]["content"], status
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("novamax chat failed after retry: %s" % last)


def make_llm(binding):
    """按 binding 前缀路由到 provider（10-18 自动路由入口；10-26 B 案补 openai）

    "ollama/<模型>" → OllamaLLM；"nova/<端口>" → NovaMaxLLM；
    "glm/<模型>" → GLMCloudLLM（参谋长层云端判读，10-18 分工定案）；
    "openai/<模型>" → OpenAILLM（诸葛亮异族席，2026-10-21 裁决）；
    其余 → OllamaLLM 兜底（会走降级三态，行为与旧版一致）。
    """
    prefix, _, _rest = (binding or "").partition("/")
    if prefix == "nova":
        port = _rest.split("/", 1)[0] if "/" in _rest else _rest
        return NovaMaxLLM(port=port)
    if prefix == "glm":
        return GLMCloudLLM(model=binding.split("/", 1)[1])
    if prefix == "openai":
        return OpenAILLM(model=binding.split("/", 1)[1])
    return OllamaLLM()


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
            # engine/llm.py 的 parents[2] = 项目根（与 a0_agent 的 parent.parent 同点）
            p = Path(__file__).resolve().parents[2] / "portal" / "audit_ai_config.json"
            if p.exists():
                zk = json.loads(p.read_text(encoding="utf-8")).get("zhipu_key", "")
                if zk:
                    return zk
        except Exception:  # noqa: BLE001
            pass
        import os
        return os.environ.get("GLM_API_KEY", "")

    def chat(self, system, user, model=None):
        key = self._key()
        if not key:
            raise LLMError("智谱 key 未配置（audit_ai_config.json zhipu_key / "
                           "GLM_API_KEY 均空）——参谋长席位不静默降档")
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
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + key},
                )
                with op.open(req, timeout=self.timeout) as r:
                    d = json.load(r)
                msg = (d.get("choices") or [{}])[0].get("message") or {}
                out = (msg.get("content") or "").strip()
                self.last_meta = {"finish_reason": d.get("finish_reason"),
                                  "usage": d.get("usage")}
                if not out:
                    raise LLMError("glm 空回（思考型吞 content? finish=%s）"
                                   % d.get("finish_reason"))
                return out, "normal"
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("glm 通道调用失败 after retry: %s" % last)


class OpenAILLM:
    """OpenAI 兼容通道（异族席：诸葛亮 AI1 / GPT-6.0，2026-10-21 异族编制裁决）

    纪律（2026-10-26 B 案，与 GLMCloudLLM 同款）：
    - key 只从环境变量读：OPENAI_API_KEY 优先，回退 OPENAI_GPT6_KEY；零落盘。
    - base_url：OPENAI_BASE_URL 可覆盖（默认 https://api.openai.com/v1）。
    - key 缺失 / 空回 / 重试耗尽 → 抛 LLMError，**不静默降档本地 9b**：
      异族通道是否可用是席位有效性的一部分，调用方（会议厅/门面）自行决定
      是否显式降级并标注 degraded，不得让本类冒充成功。
    """

    DEFAULT_BASE = "https://api.openai.com/v1"
    KEY_ENVS = ("OPENAI_API_KEY", "OPENAI_GPT6_KEY")

    def __init__(self, model="gpt-6.0", timeout=240, retries=1,
                 base_url=None, api_key=None):
        self.model = model or "gpt-6.0"
        self.timeout = timeout
        self.retries = retries
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or self.DEFAULT_BASE).rstrip("/")
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
            raise LLMError("openai key 未配置（%s 均空）——异族席位不静默降档"
                           % " / ".join(self.KEY_ENVS))
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
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + key},
                )
                with op.open(req, timeout=self.timeout) as r:
                    d = json.load(r)
                choice = (d.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                out = (msg.get("content") or "").strip()
                finish = choice.get("finish_reason")
                self.last_meta = {"finish_reason": finish,
                                  "usage": d.get("usage")}
                if not out:
                    raise LLMError("openai 空回（finish=%s）" % finish)
                return out, ("degraded" if finish == "length" else "normal")
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5)
        raise LLMError("openai 通道调用失败 after retry: %s" % last)
