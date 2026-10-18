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
