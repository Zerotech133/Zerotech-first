"""日线取数限流退避：识别限流信号并指数退避重试。网关本身无内建退避。"""
from __future__ import annotations

import time
from typing import Any

_RATE_KEYWORDS = ("429", "rate", "limit", "too many", "每分钟", "每天", "抱歉")


def is_rate_limited(exc: Exception) -> bool:
    s = str(exc)
    low = s.lower()
    return any(k in low or k in s for k in _RATE_KEYWORDS)


def fetch_daily_chunk(
    gateway: Any, chunk: list[str], *, limit: int, start=None, end=None,
    max_retries: int, base_ms: int,
) -> dict:
    """调 get_daily_bars_batch；限流信号→指数退避(base×2^n，封顶30s)重试；
    超 max_retries 或非限流异常 → 上抛（调用方决定跳过与否）。"""
    for attempt in range(max_retries + 1):
        try:
            return gateway.get_daily_bars_batch(chunk, limit=limit, start=start, end=end)
        except Exception as e:
            if attempt < max_retries and is_rate_limited(e):
                time.sleep(min(base_ms * (2 ** attempt), 30000) / 1000)
                continue
            raise
    raise RuntimeError("unreachable")  # mypy: range(max_retries+1) 必非空,循环内必 return/raise
