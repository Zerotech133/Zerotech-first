"""tdx_tail_sync.select_stale 纯逻辑测试（2026-11-05，不出网）。"""

import tdx_tail_sync as tail


def _patch_mtime(monkeypatch, mtimes):
    monkeypatch.setattr(tail, "latest_mtime", lambda t: mtimes.get(t, 0.0))


def test_picks_stale_done_table(monkeypatch):
    manifest = {"ts_top_list": {"done": True, "rows": 100}}
    _patch_mtime(monkeypatch, {"ts_top_list": 1.0})  # 远古 mtime（非 0，0 会被判无目录）
    picked, _ = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == ["ts_top_list"]


def test_skips_skip_default(monkeypatch):
    manifest = {"tdx_sector_min": {"done": True, "rows": 1}, "ts_rt_min": {"done": True, "rows": 1}}
    _patch_mtime(monkeypatch, {"tdx_sector_min": 0.0, "ts_rt_min": 0.0})
    picked, skipped = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == []
    assert any(x.startswith("tdx_sector_min ") and "SKIP_DEFAULT" in x for x in skipped)


def test_skips_not_done(monkeypatch):
    manifest = {"ts_cyq_chips": {"done": False, "rows": 0}}
    _patch_mtime(monkeypatch, {})
    picked, _ = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == []


def test_skips_zero_rows(monkeypatch):
    manifest = {"ts_etf_index": {"done": True, "rows": 0}}
    _patch_mtime(monkeypatch, {"ts_etf_index": 0.0})
    picked, _ = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == []


def test_skips_fresh_table(monkeypatch):
    manifest = {"ts_daily": {"done": True, "rows": 100}}
    _patch_mtime(monkeypatch, {"ts_daily": 1_000_000 - 3600})  # 1h 前
    picked, _ = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == []


def test_skips_missing_dir(monkeypatch):
    manifest = {"stock_news": {"done": True, "rows": 100}}
    _patch_mtime(monkeypatch, {})  # latest_mtime=0 → 无目录
    picked, skipped = tail.select_stale(manifest, max_age_hours=36, now=1_000_000)
    assert picked == []
    assert any(x.startswith("stock_news ") and "无落盘目录" in x for x in skipped)
