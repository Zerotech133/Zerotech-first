# -*- coding: utf-8 -*-
"""LLM 绑定层：Ollama 客户端 + Mock + 降级三态（K-STOCK-004）

模型分层（2026-10-08 /api/tags 实测，K-ORG-002 修正）：
    A0 总管 = qwen3.5:35b / 部门成员 = qwen3.5:9b / 轻量路由 = qwen3.5:4b / 嵌入 = bge-m3(1024维)
本地库中【无】qwen2.5:7b 与 nomic-embed-text，引用前一律先打 /api/tags 核实。

降级三态（对齐菜鸟 degraded）：normal / degraded / failed——LLM 不可用时解释器走规则兜底，不中断全链路。
"""
import json
import time
import urllib.request


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
        """返回 (text, status)；重试耗尽抛 LLMError（由解释器转降级）"""
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,  # qwen3.5 思考模型：必须关思考（实测 400 token 全被 thinking 吃掉，content 为空）
            "options": {"temperature": 0.4, "num_predict": 400},
        }
        last = None
        for _ in range(self.retries + 1):
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    self.host + "/api/chat",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with op.open(req, timeout=self.timeout) as r:
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
