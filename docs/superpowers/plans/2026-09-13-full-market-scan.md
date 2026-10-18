# 可配置全市场取数 + 限流退避 + 1年回补 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让日线取数覆盖全市场（分批+限流退避+分时扰平的"慢抄"任务）并把历史回补到 1 年，使既有 registry 全市场扫描实际覆盖全市场；全部硬编码值改为 env 可配、可秒回滚。**扫描逻辑不改**。

**Architecture:** 新增 settings 配置 + 限流退避 helper（`market_data/throttle.py`）；新增全市场慢抄 job `run_full_market_daily_sweep_job`（游标切片 over `daily_universe` 全 ACTIVE，批间 sleep + 429 退避，游标存 `v2_app_state`）在 scheduler 按 `fetch_daily_full_markets` 注册；新增回补例程 `market_data/backfill.py` + 一次性脚本 `scripts/backfill_daily_history.py`。落库复用幂等 `ingest.upsert_daily`（ON CONFLICT）。**无迁移**。

**Tech Stack:** Python/APScheduler/SQLAlchemy/psycopg2；后端根 `src/zhice_os_v2/`。跑测试：`PYTHONPATH=src .venv/bin/python -m pytest`。⚠️ Fact-Forcing hook：改文件前答 4 问（1.调用方 2.无重复 3.数据结构 4.逐字引用用户指令）。分支 `feat/full-market-scan`（基于 main 9efb5b1）。

**关键既有签名（照用）：**
- `daily_universe(market)->list[str]`（`market_data/universe.py`，全 ACTIVE，psycopg2 直连）
- `get_data_gateway(market)`（`gateways/registry.py`）→ `.get_daily_bars_batch(codes, limit=, start=, end=)->dict[str,list[DailyBarDTO]]` + `.close()`
- `upsert_daily(rows: list[DailyBarDTO], market)->int`（幂等 ON CONFLICT）；`backfill_pre_close(market)->int`；`_chunked(seq,size)`（均在 `scheduler_jobs.py`/`ingest.py`）
- `app_state_store._load_state(key, default:dict)->dict` / `_save_state(key, value:dict)`（`api/routers/app_state_store.py`，v2_app_state，DB+内存回退）
- `get_settings()`（`config/settings.py`，lru_cache 单例；测试用 `monkeypatch.setenv`+`get_settings.cache_clear()`）

---

## File Structure

**新建**
- `src/zhice_os_v2/infrastructure/market_data/throttle.py` — `is_rate_limited` + `fetch_daily_chunk`（退避）
- `src/zhice_os_v2/infrastructure/market_data/backfill.py` — `backfill_daily_history`
- `scripts/backfill_daily_history.py` — 一次性回补 CLI
- `tests/test_market_data_throttle.py`、`tests/test_full_market_sweep.py`、`tests/test_backfill_daily_history.py`

**修改**
- `src/zhice_os_v2/config/settings.py` — 7 个新字段
- `src/zhice_os_v2/scheduler_jobs.py` — `_full_markets()` helper + `run_full_market_daily_sweep_job`
- `src/zhice_os_v2/scheduler.py` — 注册 sweep job
- `tests/test_scheduler_jobs_registration.py` — sweep 注册断言

---

## 单元 1：配置项 + 限流退避 helper

### Task 1：settings 新字段 + `_full_markets()`

**Files:** Modify `src/zhice_os_v2/config/settings.py`、`src/zhice_os_v2/scheduler_jobs.py`；Test `tests/test_full_market_sweep.py`

- [ ] **Step 1: 写失败测试** `tests/test_full_market_sweep.py`

```python
"""全市场取数：配置解析 + 慢抄 job（mock 网关，零外网）。"""
from __future__ import annotations


def test_full_markets_parse(monkeypatch):
    from zhice_os_v2.config.settings import get_settings
    import zhice_os_v2.scheduler_jobs as jobs

    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "US, CRYPTO ,")
    get_settings.cache_clear()
    assert jobs._full_markets() == {"US", "CRYPTO"}
    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "")
    get_settings.cache_clear()
    assert jobs._full_markets() == set()
    get_settings.cache_clear()


def test_fetch_defaults_unchanged(monkeypatch):
    from zhice_os_v2.config.settings import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.fetch_daily_batch_size == 200
    assert s.fetch_daily_throttle_ms == 0
    assert s.backfill_daily_days == 365
    assert s.fetch_daily_full_markets == ""
```

- [ ] **Step 2: 跑确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_full_market_sweep.py -k "full_markets_parse or defaults" -q`
Expected: FAIL（字段/`_full_markets` 不存在）

- [ ] **Step 3: 加配置字段**（`settings.py` `AppSettings` 内，紧邻 `scheduler_markets` 附近）

```python
    fetch_daily_full_markets: str = ""
    fetch_daily_batch_size: int = 200
    fetch_daily_throttle_ms: int = 0
    fetch_daily_sweep_slice: int = 500
    fetch_backoff_max_retries: int = 5
    fetch_backoff_base_ms: int = 1000
    backfill_daily_days: int = 365
```

`scheduler_jobs.py` 顶部（已 `from zhice_os_v2.config.settings import get_settings`）加 helper：
```python
def _full_markets() -> set[str]:
    raw = get_settings().fetch_daily_full_markets or ""
    return {m.strip().upper() for m in raw.split(",") if m.strip()}
```

- [ ] **Step 4: 跑确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_full_market_sweep.py -k "full_markets_parse or defaults" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/config/settings.py src/zhice_os_v2/scheduler_jobs.py tests/test_full_market_sweep.py
git commit -m "feat(full-market-scan): 取数配置项 + _full_markets 解析"
```

### Task 2：限流退避 helper `throttle.py`

**Files:** Create `src/zhice_os_v2/infrastructure/market_data/throttle.py`；Test `tests/test_market_data_throttle.py`

- [ ] **Step 1: 写失败测试** `tests/test_market_data_throttle.py`

```python
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
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)  # 不真睡
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
```

- [ ] **Step 2: 跑确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_market_data_throttle.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 helper**

```python
# src/zhice_os_v2/infrastructure/market_data/throttle.py
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
```

- [ ] **Step 4: 跑确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_market_data_throttle.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/infrastructure/market_data/throttle.py tests/test_market_data_throttle.py
git commit -m "feat(full-market-scan): 限流退避 helper"
```

---

## 单元 2：全市场慢抄任务

### Task 3：`run_full_market_daily_sweep_job` + scheduler 注册

**Files:** Modify `src/zhice_os_v2/scheduler_jobs.py`、`src/zhice_os_v2/scheduler.py`；Test 追加 `tests/test_full_market_sweep.py`、`tests/test_scheduler_jobs_registration.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_full_market_sweep.py`）

```python
def test_sweep_noop_when_market_not_enabled(monkeypatch):
    from zhice_os_v2.config.settings import get_settings
    import zhice_os_v2.scheduler_jobs as jobs
    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "sql")
    get_settings.cache_clear()
    assert jobs.run_full_market_daily_sweep_job("US") == {"swept": 0}
    get_settings.cache_clear()


def test_sweep_cursor_advance_and_wrap(monkeypatch):
    import zhice_os_v2.scheduler_jobs as jobs
    from zhice_os_v2.config.settings import get_settings
    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "US")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "sql")
    monkeypatch.setenv("FETCH_DAILY_SWEEP_SLICE", "3")
    monkeypatch.setenv("FETCH_DAILY_BATCH_SIZE", "2")
    get_settings.cache_clear()

    codes = ["A", "B", "C", "D", "E"]
    monkeypatch.setattr(jobs, "daily_universe", lambda m: list(codes))
    class _G:
        def get_daily_bars_batch(self, chunk, limit=5, start=None, end=None):
            return {c: [object()] for c in chunk}
        def close(self): ...
    monkeypatch.setattr(jobs, "get_data_gateway", lambda m: _G())
    upserts = []
    monkeypatch.setattr(jobs, "upsert_daily", lambda rows, market: (upserts.append(len(rows)) or len(rows)))
    monkeypatch.setattr(jobs, "backfill_pre_close", lambda m: 0)
    state = {}
    monkeypatch.setattr(jobs, "_load_state", lambda k, d: dict(state.get(k, d)))
    monkeypatch.setattr(jobs, "_save_state", lambda k, v: state.__setitem__(k, dict(v)))

    r1 = jobs.run_full_market_daily_sweep_job("US")  # slice A,B,C → cursor 3
    assert r1["swept"] == 3 and r1["cursor"] == 3
    r2 = jobs.run_full_market_daily_sweep_job("US")  # slice D,E → cursor 0(回绕)
    assert r2["swept"] == 2 and r2["cursor"] == 0
    assert sum(upserts) == 5
    get_settings.cache_clear()
```

- [ ] **Step 2: 跑确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_full_market_sweep.py -k "sweep" -q`
Expected: FAIL

- [ ] **Step 3: 写 job**（`scheduler_jobs.py`）

> 前提：`daily_universe / get_data_gateway / upsert_daily / backfill_pre_close / _chunked` 需为**模块级可访问名**（供测试 monkeypatch）。`run_fetch_daily_job` 目前在函数体内 import 它们——本任务在 `scheduler_jobs.py` **模块顶部**补 import（若尚未）：
> ```python
> from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
> from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
> from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily
> from zhice_os_v2.infrastructure.market_data.throttle import fetch_daily_chunk
> from zhice_os_v2.infrastructure.market_data.universe import daily_universe
> ```
> （`backfill_pre_close`、`_chunked` 已是本模块函数；`time` 已 import，若无则加 `import time`。补顶部 import 时确认不产生循环 import——这些是 infrastructure/api.routers 叶子模块，scheduler_jobs 已依赖同类，安全。）

```python
_SWEEP_CURSOR_KEY = "daily_sweep_cursor"


def run_full_market_daily_sweep_job(market: str = "US") -> dict:
    """全市场日线慢抄：按游标切片扫 daily_universe(全 ACTIVE)，批间节流+429退避，
    幂等 upsert。仅 sql 后端且 market 在 fetch_daily_full_markets 时生效；分时扰平靠低频调度。"""
    s = get_settings()
    if s.persistence_backend != "sql" or market not in _full_markets():
        return {"swept": 0}
    codes = daily_universe(market)
    if not codes:
        return {"swept": 0}
    state = _load_state(_SWEEP_CURSOR_KEY, {})
    off = int(state.get(market, 0)) % len(codes)
    sl = codes[off: off + s.fetch_daily_sweep_slice]
    g = get_data_gateway(market)
    written = errors = 0
    try:
        for chunk in _chunked(sl, s.fetch_daily_batch_size):
            try:
                batch = fetch_daily_chunk(
                    g, chunk, limit=5, max_retries=s.fetch_backoff_max_retries,
                    base_ms=s.fetch_backoff_base_ms)
                rows = [b for bars in batch.values() for b in bars]
                written += upsert_daily(rows, market)
            except Exception:
                errors += 1
                logger.exception("sweep 批失败 market=%s", market)
            if s.fetch_daily_throttle_ms > 0:
                time.sleep(s.fetch_daily_throttle_ms / 1000)
    finally:
        g.close()
    if written:
        backfill_pre_close(market)
    cursor = (off + len(sl)) % len(codes)
    _save_state(_SWEEP_CURSOR_KEY, {**state, market: cursor})
    result = {"swept": len(sl), "written": written, "errors": errors, "cursor": cursor}
    logger.info("full_market_sweep %s", result)
    return result
```

- [ ] **Step 4: 跑确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_full_market_sweep.py -q`
Expected: PASS

- [ ] **Step 5: scheduler 注册 + 测试**

`scheduler.py`：`scheduler_jobs` import 块加 `run_full_market_daily_sweep_job,`；在 `build_scheduler` 的 `for market in MARKETS:` 循环内（`run_fetch_daily_job` 注册附近）加：
```python
        from zhice_os_v2.scheduler_jobs import _full_markets as _fm
        if market in _fm():
            # 全市场日线慢抄（仅 fetch_daily_full_markets 市场；低频扰平避限流）。
            scheduler.add_job(
                run_full_market_daily_sweep_job, IntervalTrigger(minutes=4),
                args=[market], id=f"full_market_sweep_{market}",
                max_instances=1, coalesce=True,
            )
```
追加 `tests/test_scheduler_jobs_registration.py`：
```python
def test_full_market_sweep_registered_when_enabled(monkeypatch):
    from zhice_os_v2.config.settings import get_settings
    import zhice_os_v2.scheduler as sch
    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "US")
    monkeypatch.setenv("SCHEDULER_MARKETS", "US,CRYPTO")
    get_settings.cache_clear()
    sch._apply_market_filter()
    s = sch.build_scheduler()
    assert s.get_job("full_market_sweep_US") is not None
    get_settings.cache_clear()
```
Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_full_market_sweep.py tests/test_scheduler_jobs_registration.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/scheduler_jobs.py src/zhice_os_v2/scheduler.py tests/test_full_market_sweep.py tests/test_scheduler_jobs_registration.py
git commit -m "feat(full-market-scan): 全市场日线慢抄 job + 注册"
```

---

## 单元 3：历史回补

### Task 4：`backfill_daily_history` + 脚本

**Files:** Create `src/zhice_os_v2/infrastructure/market_data/backfill.py`、`scripts/backfill_daily_history.py`；Test `tests/test_backfill_daily_history.py`

- [ ] **Step 1: 写失败测试** `tests/test_backfill_daily_history.py`

```python
"""历史回补：已覆盖跳过 / 未覆盖按窗口取并 upsert / 二次跑幂等（mock 网关+DB）。"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def test_backfill_skip_covered_and_fetch_missing(monkeypatch):
    import zhice_os_v2.infrastructure.market_data.backfill as bf

    codes = ["COVERED", "MISSING"]
    monkeypatch.setattr(bf, "daily_universe", lambda m: list(codes))
    today = datetime.now(UTC).date()
    monkeypatch.setattr(bf, "_min_trade_date",
                        lambda market, sym: (today - timedelta(days=400)) if sym == "COVERED" else None)

    fetched = []
    class _G:
        def get_daily_bars_batch(self, chunk, limit=5, start=None, end=None):
            fetched.append((chunk[0], start, end))
            return {chunk[0]: [object()]}
        def close(self): ...
    monkeypatch.setattr(bf, "get_data_gateway", lambda m: _G())
    ups = []
    monkeypatch.setattr(bf, "upsert_daily", lambda rows, market: (ups.append(len(rows)) or len(rows)))
    monkeypatch.setattr(bf, "backfill_pre_close", lambda m: 0)
    state = {}
    monkeypatch.setattr(bf, "_load_state", lambda k, d: dict(state.get(k, d)))
    monkeypatch.setattr(bf, "_save_state", lambda k, v: state.__setitem__(k, dict(v)))

    r = bf.backfill_daily_history("US", days=365, slice_size=10, batch_size=1,
                                  throttle_ms=0, max_retries=3, base_ms=10)
    assert r["skipped"] == 1        # COVERED 跳过
    assert r["processed"] == 1      # 只取 MISSING
    assert fetched and fetched[0][0] == "MISSING"
    assert fetched[0][2] >= today - timedelta(days=1)   # end≈today
    assert sum(ups) == 1


def test_backfill_idempotent_second_run_skips_all(monkeypatch):
    import zhice_os_v2.infrastructure.market_data.backfill as bf
    monkeypatch.setattr(bf, "daily_universe", lambda m: ["A", "B"])
    today = date.today()
    monkeypatch.setattr(bf, "_min_trade_date", lambda market, sym: today - timedelta(days=400))
    monkeypatch.setattr(bf, "get_data_gateway",
                        lambda m: (_ for _ in ()).throw(AssertionError("不应取数")))
    monkeypatch.setattr(bf, "upsert_daily", lambda rows, market: 0)
    monkeypatch.setattr(bf, "backfill_pre_close", lambda m: 0)
    st = {}
    monkeypatch.setattr(bf, "_load_state", lambda k, d: dict(st.get(k, d)))
    monkeypatch.setattr(bf, "_save_state", lambda k, v: st.__setitem__(k, dict(v)))
    r = bf.backfill_daily_history("US", days=365, slice_size=10, batch_size=1,
                                  throttle_ms=0, max_retries=3, base_ms=10)
    assert r["skipped"] == 2 and r["processed"] == 0
```

- [ ] **Step 2: 跑确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_backfill_daily_history.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 backfill 例程**

```python
# src/zhice_os_v2/infrastructure/market_data/backfill.py
"""日线历史回补：对全市场标的确保有 ≥days 天日线；已覆盖跳过；节流+退避+幂等，可续跑。"""
from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta

from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily
from zhice_os_v2.infrastructure.market_data.universe import daily_universe

logger = logging.getLogger(__name__)
_CURSOR_KEY = "backfill_daily_cursor"


def backfill_pre_close(market: str) -> int:
    from zhice_os_v2.scheduler_jobs import backfill_pre_close as _b
    return _b(market)


def _min_trade_date(market: str, symbol: str) -> date | None:
    from sqlalchemy import text
    s = get_session()
    try:
        return s.execute(
            text("SELECT min(trade_date) FROM v2_bar_daily WHERE market=:m AND symbol=:s"),
            {"m": market, "s": symbol},
        ).scalar()
    finally:
        s.close()


def backfill_daily_history(
    market: str, *, days: int, slice_size: int, batch_size: int, throttle_ms: int,
    max_retries: int, base_ms: int, max_symbols: int | None = None,
) -> dict:
    from zhice_os_v2.infrastructure.market_data.throttle import fetch_daily_chunk

    codes = daily_universe(market)
    if not codes:
        return {"processed": 0, "written": 0, "skipped": 0, "errors": 0, "cursor": 0}
    state = _load_state(_CURSOR_KEY, {})
    off = int(state.get(market, 0)) % len(codes)
    end_idx = min(len(codes), off + (max_symbols or slice_size))
    window = codes[off:end_idx]
    today = datetime.now(UTC).date()
    lo = today - timedelta(days=days)
    g = get_data_gateway(market)
    processed = written = skipped = errors = 0
    try:
        for sym in window:
            mn = _min_trade_date(market, sym)
            if mn is not None and mn <= lo:
                skipped += 1
                continue
            try:
                batch = fetch_daily_chunk(g, [sym], limit=days + 15, start=lo, end=today,
                                          max_retries=max_retries, base_ms=base_ms)
                rows = [b for bars in batch.values() for b in bars]
                written += upsert_daily(rows, market)
                processed += 1
            except Exception:
                errors += 1
                logger.exception("backfill 失败 %s:%s", market, sym)
            if throttle_ms > 0:
                time.sleep(throttle_ms / 1000)
    finally:
        g.close()
    if written:
        backfill_pre_close(market)
    cursor = end_idx % len(codes)
    _save_state(_CURSOR_KEY, {**state, market: cursor})
    return {"processed": processed, "written": written, "skipped": skipped,
            "errors": errors, "cursor": cursor}
```

- [ ] **Step 4: 跑确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_backfill_daily_history.py -q`
Expected: PASS

- [ ] **Step 5: 写 CLI 脚本 + 提交**

```python
# scripts/backfill_daily_history.py
"""一次性/可重入日线历史回补。用法：
  PYTHONPATH=src python scripts/backfill_daily_history.py --market US --days 365 [--max-symbols 400] [--throttle-ms 800]
已覆盖(≥days 历史)的标的自动跳过；不带 --max-symbols 时循环跑到游标回绕(全覆盖)。"""
from __future__ import annotations

import argparse

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.market_data.backfill import backfill_daily_history


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--market", required=True)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--max-symbols", type=int, default=None)
    p.add_argument("--throttle-ms", type=int, default=None)
    a = p.parse_args()
    s = get_settings()
    total = {"processed": 0, "written": 0, "skipped": 0, "errors": 0}
    while True:
        r = backfill_daily_history(
            a.market, days=a.days or s.backfill_daily_days,
            slice_size=400, batch_size=s.fetch_daily_batch_size,
            throttle_ms=a.throttle_ms if a.throttle_ms is not None else max(s.fetch_daily_throttle_ms, 500),
            max_retries=s.fetch_backoff_max_retries, base_ms=s.fetch_backoff_base_ms,
            max_symbols=a.max_symbols,
        )
        for k in total:
            total[k] += r[k]
        print("batch", r, "| total", total)
        if a.max_symbols or r["cursor"] == 0:  # 指定 max_symbols=只跑一批；否则回绕即全跑完
            break
    print("DONE", total)


if __name__ == "__main__":
    main()
```
```bash
git add src/zhice_os_v2/infrastructure/market_data/backfill.py scripts/backfill_daily_history.py tests/test_backfill_daily_history.py
git commit -m "feat(full-market-scan): 1年历史回补例程 + CLI"
```

---

## 单元 4：门禁总核 + 部署手册补记

### Task 5：全量门禁

- [ ] **Step 1: 后端门禁**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/zhice_os_v2/infrastructure/market_data/throttle.py \
  src/zhice_os_v2/infrastructure/market_data/backfill.py
PYTHONPATH=src .venv/bin/alembic heads   # 不变(无迁移)
```
Expected: 全绿；mypy 本特性文件无新增错误。

- [ ] **Step 2: 提交（如有零散）**

```bash
git add -A && git commit -m "chore(full-market-scan): 门禁总核" || echo "无待提交"
```

### Task 6：部署手册补记（207，仅记录，部署需另行授权）

- [ ] 在 [[207-prod-ops]] 记忆追加"全市场取数"节：`.env` 设 `FETCH_DAILY_FULL_MARKETS=US,CRYPTO`、`FETCH_DAILY_THROTTLE_MS=800`；部署后**后台跑一次回补** `PYTHONPATH=src python scripts/backfill_daily_history.py --market US --days 365`（容器内 `docker exec` 或宿主 venv），再 CRYPTO；慢抄 job 随 scheduler 自动跑；验收 `v2_bar_daily` distinct symbols→近全集、min(trade_date)→~1年前。回滚：`FETCH_DAILY_FULL_MARKETS=""`。

---

## 自检

- **Spec 覆盖**：§3 配置→Task1；§4 退避→Task2；§5 慢抄→Task3；§6 回补→Task4；§7 测试→各 Task；§8 门禁/部署→Task5/6。
- **占位符**：无 TBD；每步含真实代码/命令/期望。
- **类型一致**：`fetch_daily_chunk(gateway, chunk, *, limit, start, end, max_retries, base_ms)`（Task2）↔ 慢抄(Task3)/回补(Task4)调用一致；`_load_state(key, default)/_save_state(key, value)` 用法与既有签名一致；`upsert_daily(rows, market)`、`daily_universe(market)`、`backfill_pre_close(market)`、`get_data_gateway(market)` 对齐既有；settings 字段名（Task1）↔ 消费点（Task3/4）一致。

## 备注
- **未部署**：仅本地实现+验证。207 部署（含回补脚本执行）需另行明确授权。
- **执行顺序**：单元 1→4 线性（Task3 依赖 Task1/2；Task4 依赖 Task1/2）。
- 关键：慢抄/回补都用**模块级名字**引用 daily_universe/get_data_gateway/upsert_daily/backfill_pre_close/_load_state/_save_state/fetch_daily_chunk，以便测试 monkeypatch。
