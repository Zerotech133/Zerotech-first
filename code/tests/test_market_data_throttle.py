"""限流退避 helper：限流信号退避重试、超次上抛、非限流立即抛。"""
from __future__ import annotations

import pytest


def test_is_rate_limited():
    from zhice_os_v2.infrastructure.market_data.throttle import is_rate_limited
    assert is_rate_limited(Exception("HTTP 429 Too Many Requests"))
    assert is_rate_limited(Exception("每分钟请求超限"))
    assert not is_rate_limited(Exception("connection reset"))


class _GW:
    def __init__(self, fail_times, exc):
        self.calls = 0
        self.fail_times = fail_times
        self.exc = exc
    def get_daily_bars_batch(self, codes, limit=5, start=None, end=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return {c: [] for c in codes}


def test_backoff_retries_then_succeeds(monkeypatch):
    import zhice_os_v2.infrastructure.market_data.throttle as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    gw = _GW(fail_times=2, exc=Exception("rate limit"))
    out = mod.fetch_daily_chunk(gw, ["A", "B"], limit=5, max_retries=5, base_ms=10)
    assert gw.calls == 3 and out == {"A": [], "B": []}


def test_backoff_gives_up_after_max(monkeypatch):
    import zhice_os_v2.infrastructure.market_data.throttle as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    gw = _GW(fail_times=99, exc=Exception("429"))
    with pytest.raises(Exception, match="429"):
        mod.fetch_daily_chunk(gw, ["A"], limit=5, max_retries=3, base_ms=10)
    assert gw.calls == 4  # 1 + 3 retries


def test_non_rate_limit_raises_immediately(monkeypatch):
    import zhice_os_v2.infrastructure.market_data.throttle as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    gw = _GW(fail_times=99, exc=ValueError("connection reset"))
    with pytest.raises(ValueError):
        mod.fetch_daily_chunk(gw, ["A"], limit=5, max_retries=5, base_ms=10)
    assert gw.calls == 1
