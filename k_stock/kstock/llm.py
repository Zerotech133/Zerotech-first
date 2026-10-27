# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · LLM 客户端

优先级（S2 乙方案，2026-10-12 落地）：GLM 智谱官方 API 直连（coding 端点，
GLM_API_KEY 环境变量；探针实测 glm-5.3/5.3-flash/4-flash 全通，标准端点对
5.3 系报 429 余额不足——key 为 coding 计划 key，勿改 base）→ Ollama 11434
（qwen3.5:9b，keep_alive 1800）→ mock（测试/降级）。
旧 8098 网关宿主已删（K274，源码归档 projects-archive/盯盘机器人/ai_os/），
甲方案恢复网关本体为独立后置决策，与本链解耦。
接口 OpenAI 兼容 /chat/completions，标准库实现零依赖。
降级三态：primary 失败→fallback；全失败→raise LLMUnavailable（编排器转 mock 或跳过）。
"""

import json
import os
import urllib.request

# S2 乙方案路由默认值（环境变量可覆盖；标准端点对 5.3 系 429，故默认 coding 端点）
GLM_OFFICIAL_BASE = os.environ.get(
    "KSTOCK_GLM_BASE", "https://open.bigmodel.cn/api/coding/paas/v4"
)
GLM_CHAT_MODEL = os.environ.get("KSTOCK_GLM_MODEL", "glm-5.3-flash")


class LLMUnavailable(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        primary=GLM_OFFICIAL_BASE,
        fallback="http://127.0.0.1:11434",
        model_fallback="qwen3.5:9b",
        timeout=300,
        mock=False,
        account=None,
        glm_model=None,
    ):
        self.primary = primary.rstrip("/")
        self.fallback = fallback.rstrip("/")
        self.model_fallback = model_fallback
        self.glm_model = glm_model or GLM_CHAT_MODEL
        self.timeout = timeout
        self.mock = mock
        self.account = account  # 077w M2 后半：成本计量归属账本（None=不记账）
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, messages, temperature=0.3, json_mode=True, max_tokens=2000):
        """返回 (text, provider)。provider ∈ {glm, ollama, mock}。"""
        if self.mock:
            return self._mock(messages), "mock"
        for provider, base, model in (
            ("glm", self.primary, self.glm_model),
            ("ollama", self.fallback, self.model_fallback),
        ):
            try:
                return self._chat_one(
                    base, model, messages, temperature, json_mode, max_tokens
                ), provider
            except Exception:
                continue
        raise LLMUnavailable("glm 官方与 ollama 均不可达")

    def _chat_one(self, base, model, messages, temperature, json_mode, max_tokens):
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if base == self.fallback:
            # Ollama 专属：30 分钟保活（默认 5m 过期即卸载，6GB 冷启动 70s+）；
            # 智谱官方对未知字段宽容性未知，不带
            body["keep_alive"] = 1800
        path = "/chat/completions" if "bigmodel.cn" in base else "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if "bigmodel.cn" in base:
            key = (
                os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY") or ""
            ).strip()
            if not key:
                raise LLMUnavailable("GLM 主链未配置 GLM_API_KEY，跳向 fallback")
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        r = self._opener.open(req, timeout=self.timeout)
        d = json.loads(r.read())
        self._meter(base, model, d)  # 077w M2 后半：计量失败不阻塞主调用
        return d["choices"][0]["message"]["content"]

    def _meter(self, base, model, d):
        """成本记账（077w K-STOCK-101 M2 后半）：从 OpenAI 兼容 usage 取 token 数，
        落 llm_cost 独立账本。account 未设置或计量异常均静默跳过。"""
        if not self.account:
            return
        try:
            usage = d.get("usage") or {}
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if pt is None or ct is None:
                return
            binding = "zhipu/" + model if base == self.primary else f"ollama/{model}"
            try:
                from k_stock.paper import llm_cost
            except ImportError:
                import llm_cost
            llm_cost.record_cost(self.account, binding, int(pt), int(ct))
        except Exception:
            pass

    @staticmethod
    def _mock(messages):
        """确定性 mock：按节点 analysis_type 返回合法 JSON（测试用）。"""
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        at = "final_decision"
        for key in (
            "market_technical",
            "news_event",
            "fundamental",
            "investment_advice",
            "final_decision",
        ):
            if key in sys_msg:
                at = key
                break
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        import re

        m = re.search(r"股票[：:]\s*(\S+)", user_msg)
        code = m.group(1) if m else "000000"
        fixtures = {
            "market_technical": {
                "stance": "BULL",
                "confidence": 0.6,
                "key_points": ["均线多头排列", "量能温和放大"],
                "risks": ["短线超买"],
            },
            "news_event": {
                "stance": "NEUTRAL",
                "confidence": 0.5,
                "key_points": ["无重大新闻"],
                "risks": ["情绪面平淡"],
            },
            "fundamental": {
                "stance": "BULL",
                "confidence": 0.55,
                "key_points": ["ROE 稳定"],
                "risks": ["估值偏高"],
            },
            "investment_advice": {
                "stance": "BULL",
                "confidence": 0.55,
                "key_points": ["技术面与基本面共振"],
                "risks": ["注意仓位"],
            },
            "final_decision": {
                "action": "BUY",
                "confidence": 0.55,
                "target_price_low": 10.0,
                "target_price_high": 12.0,
                "risk_level": "MEDIUM",
                "reason": "多空辩论后多头占优",
                "stock": code,
            },
        }
        return json.dumps(fixtures[at], ensure_ascii=False)
