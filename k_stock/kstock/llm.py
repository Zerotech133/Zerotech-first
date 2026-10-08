# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · LLM 客户端

优先级：GLM 8098（宿主=盯盘机器人 ai_os 网关，K274 已删、源码归档 projects-archive/盯盘机器人/ai_os/，
       恢复=S2 决策点：归档重部署或改 GLM 官方 API）→ Ollama 11434（现役 qwen3.5:9b，keep_alive 1800）→ mock（测试/降级）。
接口 OpenAI 兼容 /v1/chat/completions，标准库实现零依赖。
降级三态：primary 失败→fallback；全失败→raise LLMUnavailable（编排器转 mock 或跳过）。
"""
import json
import urllib.request


class LLMUnavailable(Exception):
    pass


class LLMClient:
    def __init__(self, primary="http://127.0.0.1:8098", fallback="http://127.0.0.1:11434",
                 model_fallback="qwen3.5:9b", timeout=300, mock=False):
        self.primary = primary.rstrip("/")
        self.fallback = fallback.rstrip("/")
        self.model_fallback = model_fallback
        self.timeout = timeout
        self.mock = mock
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, messages, temperature=0.3, json_mode=True, max_tokens=2000):
        """返回 (text, provider)。provider ∈ {glm, ollama, mock}。"""
        if self.mock:
            return self._mock(messages), "mock"
        for provider, base, model in (
            ("glm", self.primary, "glm-4-flash"),
            ("ollama", self.fallback, self.model_fallback),
        ):
            try:
                return self._chat_one(base, model, messages, temperature, json_mode, max_tokens), provider
            except Exception:
                continue
        raise LLMUnavailable("primary 与 fallback 均不可达")

    def _chat_one(self, base, model, messages, temperature, json_mode, max_tokens):
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "keep_alive": 1800,  # 30 分钟保活：默认 5m 过期即卸载，重载 6GB 冷启动 70s+
        }
        # 注：Ollama 对 response_format json_object 兼容性不稳（qwen3.5 实测偶发空输出），
        # JSON 约束靠 system 提示词 + orchestrator._parse 正则兜底，不用 response_format。
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        r = self._opener.open(req, timeout=self.timeout)
        d = json.loads(r.read())
        return d["choices"][0]["message"]["content"]

    @staticmethod
    def _mock(messages):
        """确定性 mock：按节点 analysis_type 返回合法 JSON（测试用）。"""
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        at = "final_decision"
        for key in ("market_technical", "news_event", "fundamental",
                    "investment_advice", "final_decision"):
            if key in sys_msg:
                at = key
                break
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        import re
        m = re.search(r"股票[：:]\s*(\S+)", user_msg)
        code = m.group(1) if m else "000000"
        fixtures = {
            "market_technical": {"stance": "BULL", "confidence": 0.6,
                                 "key_points": ["均线多头排列", "量能温和放大"],
                                 "risks": ["短线超买"]},
            "news_event": {"stance": "NEUTRAL", "confidence": 0.5,
                           "key_points": ["无重大新闻"], "risks": ["情绪面平淡"]},
            "fundamental": {"stance": "BULL", "confidence": 0.55,
                            "key_points": ["ROE 稳定"], "risks": ["估值偏高"]},
            "investment_advice": {"stance": "BULL", "confidence": 0.55,
                                  "key_points": ["技术面与基本面共振"], "risks": ["注意仓位"]},
            "final_decision": {"action": "BUY", "confidence": 0.55,
                               "target_price_low": 10.0, "target_price_high": 12.0,
                               "risk_level": "MEDIUM", "reason": "多空辩论后多头占优",
                               "stock": code},
        }
        return json.dumps(fixtures[at], ensure_ascii=False)
