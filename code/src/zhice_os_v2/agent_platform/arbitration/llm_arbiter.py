"""LlmArbiter — qwen 兜底仲裁（Ollama REST + 令牌桶 + 保守回退）。

纪律（架构稿 §4.3 / §8）：
- 全平台唯一 LLM 入口；令牌桶 660 判定/h，超限 → path=DROPPED 保守 WAIT。
- 超时 5s / 低置信 / 解析失败 → 保守 WAIT（DROPPED 仅用于未获得判定的情况）。
- 回测一律 MockLlmArbiter（确定性）；--real-llm-sample N 才允许真调用小样本对照。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from zhice_os_v2.agent_platform import metrics
from zhice_os_v2.agent_platform.config import LlmArbiterConfig

if TYPE_CHECKING:
    from zhice_os_v2.agent_platform.methods.base import MethodVote, WatchContext

logger = logging.getLogger(__name__)


# ── 令牌桶 ─────────────────────────────────────────────────────────────────
class TokenBucket:
    """小时级滑动窗口令牌桶（refill = bucket_per_hour/3600 每秒）。"""

    def __init__(self, per_hour: int, now_fn=time.monotonic) -> None:
        self.capacity = float(per_hour)
        self.rate = per_hour / 3600.0
        self.tokens = float(per_hour)
        self.per_hour = per_hour
        self._now = now_fn
        self._last = self._now()
        self.dropped = 0

    def allow(self) -> bool:
        now = self._now()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        self.dropped += 1
        return False


# ── 结果对象 ───────────────────────────────────────────────────────────────
@dataclass
class LlmVerdict:
    """LLM 判定结果（或保守回退的哨兵）。"""

    action: str                    # SELL / HOLD / WAIT
    confidence: float
    rationale: str
    source: str                    # MODEL | FALLBACK_TIMEOUT | FALLBACK_TOKENS | FALLBACK_PARSE | MOCK
    dropped: bool = False          # True → 上层记 path=DROPPED


@dataclass
class LlmStats:
    calls: int = 0
    ok: int = 0
    timeout: int = 0
    token_dropped: int = 0
    parse_fail: int = 0
    total_latency_s: float = 0.0


# ── 客户端协议（可注入 mock） ──────────────────────────────────────────────
class LlmClient(Protocol):
    def complete(self, prompt: str, timeout_s: float) -> str: ...


class OllamaClient:
    """Ollama REST /api/generate（jev_gate 分发模式复刻，httpx）。"""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, prompt: str, timeout_s: float) -> str:
        import httpx  # 局部 import：仅真实 LLM 路径需要网络栈

        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 200},
            },
            timeout=timeout_s,
        )
        resp.raise_for_status()
        return str(resp.json().get("response", ""))


class MockLlmClient:
    """确定性 mock：保守 WAIT（回测/单测唯一允许的 LLM 路径）。"""

    def __init__(self, action: str = "WAIT", confidence: float = 0.5,
                 rationale: str = "mock:保守观望") -> None:
        self.action = action
        self.confidence = confidence
        self.rationale = rationale

    def complete(self, prompt: str, timeout_s: float) -> str:
        return json.dumps(
            {"action": self.action, "confidence": self.confidence, "rationale": self.rationale},
            ensure_ascii=False,
        )


# ── prompt 构造 ────────────────────────────────────────────────────────────
def build_arbitration_prompt(
    votes: list[MethodVote], ctx: WatchContext
) -> str:
    """结构化仲裁 prompt：全部票 + regime + 近 N 根 K 线摘要（jev noul/choice 模式）。"""
    df = ctx.bars_df.tail(30) if ctx.bars_df is not None and len(ctx.bars_df) else None
    if df is not None and len(df):
        closes = [f"{v:.6g}" for v in df["close"].astype(float).tolist()]
        kline_summary = ",".join(closes[-30:])
    else:
        kline_summary = "(无K线数据)"
    vote_lines = "\n".join(
        f"- {v.method_id}: {v.direction} conf={v.confidence:.2f} | {'; '.join(v.reasons[:2])}"
        for v in votes
    )
    return (
        "你是加密货币盯盘中台的卖点仲裁员。多个方法对同一标的给出方向票，发生分歧。\n"
        f"标的: {ctx.ts_code} 市场: {ctx.market} regime_risk_off={ctx.regime_risk_off}\n"
        f"近30根收盘价: {kline_summary}\n"
        f"方法票:\n{vote_lines}\n"
        "请只输出 JSON: {\"action\":\"SELL|HOLD|WAIT\",\"confidence\":0~1,"
        "\"rationale\":\"一句话理由\"}"
    )


_JSON_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}", re.S)


def parse_verdict(raw: str) -> LlmVerdict | None:
    """解析模型输出为 LlmVerdict；无法解析返回 None。"""
    if not raw:
        return None
    match = _JSON_RE.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    action = str(data.get("action", "")).upper()
    if action not in ("SELL", "HOLD", "WAIT"):
        return None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return LlmVerdict(
        action=action,
        confidence=min(1.0, max(0.0, conf)),
        rationale=str(data.get("rationale", ""))[:500],
        source="MODEL",
    )


# ── LlmArbiter 本体 ────────────────────────────────────────────────────────
class LlmArbiter:
    """兜底仲裁器：令牌桶护栏 + 超时/低置信保守回退。"""

    def __init__(self, client: LlmClient | None = None,
                 cfg: LlmArbiterConfig | None = None) -> None:
        self.cfg = cfg or LlmArbiterConfig()
        self.client = client or OllamaClient(self.cfg.base_url, self.cfg.model)
        self.bucket = TokenBucket(self.cfg.bucket_per_hour)
        self.stats = LlmStats()

    def arbitrate(self, votes: list[MethodVote], ctx: WatchContext) -> LlmVerdict:
        """带护栏的 LLM 判定；任何失败路径都返回保守 WAIT 的 LlmVerdict。"""
        if not self.bucket.allow():
            self.stats.token_dropped += 1
            return LlmVerdict("WAIT", 0.0, "令牌桶耗尽(660/h)，保守回退",
                              source="FALLBACK_TOKENS", dropped=True)
        prompt = build_arbitration_prompt(votes, ctx)
        self.stats.calls += 1
        t0 = time.monotonic()
        try:
            raw = self.client.complete(prompt, self.cfg.timeout_s)
        except TimeoutError:
            return self._fallback(f"超时(>{self.cfg.timeout_s:.0f}s)保守回退", "FALLBACK_TIMEOUT")
        except Exception as exc:
            logger.warning("llm_arbiter 调用失败: %s", exc)
            return self._fallback(f"调用失败保守回退: {type(exc).__name__}", "FALLBACK_TIMEOUT")
        finally:
            latency = time.monotonic() - t0
            self.stats.total_latency_s += latency
            metrics.observe_llm_latency(latency)

        verdict = parse_verdict(raw)
        if verdict is None:
            self.stats.parse_fail += 1
            return self._fallback("输出无法解析，保守回退", "FALLBACK_PARSE")
        if verdict.confidence < self.cfg.min_conf:
            return LlmVerdict("WAIT", verdict.confidence,
                              f"低置信({verdict.confidence:.2f}<{self.cfg.min_conf})保守回退: "
                              + verdict.rationale, source="FALLBACK_PARSE")
        self.stats.ok += 1
        return verdict

    def _fallback(self, rationale: str, source: str) -> LlmVerdict:
        return LlmVerdict("WAIT", 0.0, rationale, source=source)

    def available_tokens(self) -> float:
        return self.bucket.tokens


# ── 回测用：确定性 Mock 仲裁器 ─────────────────────────────────────────────
class MockLlmArbiter(LlmArbiter):
    """Mock 客户端版 LlmArbiter——回测/单测一律走它（确定性，无网络，零延迟）。

    覆写 arbitrate 直返固定判定（不消耗令牌桶——回测 10 万 bar 不允许桶耗尽污染路径统计；
    令牌桶护栏语义由真实 OllamaClient 路径与专项单测覆盖）。
    """

    def __init__(self, action: str = "WAIT", confidence: float = 0.5,
                 rationale: str = "mock仲裁:保守观望", cfg: LlmArbiterConfig | None = None) -> None:
        super().__init__(client=MockLlmClient(action, confidence, rationale), cfg=cfg)
        self.mock_action = action
        self.mock_confidence = confidence
        self.mock_rationale = rationale

    def arbitrate(self, votes: list[MethodVote], ctx: WatchContext) -> LlmVerdict:
        return LlmVerdict(self.mock_action, self.mock_confidence,
                          self.mock_rationale, source="MOCK")


class RealLlmSampleArbiter(LlmArbiter):
    """--real-llm-sample N 小样本真调用对照：前 N 次真调用，之后自动降级 mock。

    与生产 LlmArbiter 共享同一令牌桶纪律（不绕过；超限/超时同样 DROPPED）。
    """

    def __init__(self, sample_budget: int, cfg: LlmArbiterConfig | None = None) -> None:
        super().__init__(client=OllamaClient(cfg.base_url if cfg else LlmArbiterConfig().base_url,
                                             cfg.model if cfg else "qwen2.5:7b"), cfg=cfg)
        self.sample_budget = max(0, sample_budget)
        self._mock = MockLlmClient()
        self.real_calls = 0

    def arbitrate(self, votes: list[MethodVote], ctx: WatchContext) -> LlmVerdict:
        if self.real_calls >= self.sample_budget or not self.bucket.allow():
            return LlmVerdict("WAIT", 0.5, "sample预算尽/令牌耗尽→mock保守", source="MOCK")
        self.real_calls += 1
        return super().arbitrate(votes, ctx)
