"""全市场取数：配置解析 + 慢抄 job（mock 网关，零外网）。"""
from __future__ import annotations


def test_full_markets_parse(monkeypatch):
    import zhice_os_v2.scheduler_jobs as jobs
    from zhice_os_v2.config.settings import get_settings

    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "US, CRYPTO ,")
    get_settings.cache_clear()
    assert jobs._full_markets() == {"US", "CRYPTO"}
    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "")
    get_settings.cache_clear()
    assert jobs._full_markets() == set()
    get_settings.cache_clear()


def test_fetch_defaults_unchanged(monkeypatch):
    """源码默认值回归：显式剥离本机配置污染（.env.local 文件 + scheduler_jobs 顶层
    load_dotenv 已写进 environ 的同名变量），用 _env_file=None 构造干净 Settings，
    保证断言在任何机器上都基于源码默认而非本机真实配置。"""
    from zhice_os_v2.config.settings import AppSettings

    for var in ("FETCH_DAILY_BATCH_SIZE", "FETCH_DAILY_THROTTLE_MS",
                "BACKFILL_DAILY_DAYS", "FETCH_DAILY_FULL_MARKETS"):
        monkeypatch.delenv(var, raising=False)
    s = AppSettings(_env_file=None)
    assert s.fetch_daily_batch_size == 200
    assert s.fetch_daily_throttle_ms == 0
    assert s.backfill_daily_days == 365
    assert s.fetch_daily_full_markets == ""


def test_sweep_noop_when_market_not_enabled(monkeypatch):
    import zhice_os_v2.scheduler_jobs as jobs
    from zhice_os_v2.config.settings import get_settings
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

    r1 = jobs.run_full_market_daily_sweep_job("US")
    assert r1["swept"] == 3 and r1["cursor"] == 3
    r2 = jobs.run_full_market_daily_sweep_job("US")
    assert r2["swept"] == 2 and r2["cursor"] == 0
    assert sum(upserts) == 5
    get_settings.cache_clear()
