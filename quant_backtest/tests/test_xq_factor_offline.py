# -*- coding: utf-8 -*-
"""T7 纯离线审计：模拟真库与缓存，不调用行情网络。"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import xq_factor_v1 as factor  # noqa: E402


def _fixture(tmp_path):
    db = tmp_path / "posts.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE xq_status (status_id TEXT, created_at TEXT)")
    connection.execute("CREATE TABLE xq_status_classified (status_id TEXT, sentiment TEXT, prompt_version TEXT, norm_tickers TEXT)")
    stocks = ["SH600000", "SH600001", "SH600002", "SH600003", "SH600004"]
    for i, sym in enumerate(stocks):
        connection.execute("INSERT INTO xq_status VALUES (?, ?)",
                           (str(i), "2026-10-05T13:00:00"))
        connection.execute("INSERT INTO xq_status_classified VALUES (?, ?, ?, ?)",
                           (str(i), "bullish", "v1.3", json.dumps([
                               {"kind": "stock_a", "canonical": sym}])))
    connection.commit()
    connection.close()
    daily = {sym: [{"trade_date": "20261005", "open": 10, "close": 10},
                   {"trade_date": "20261012", "open": 10,
                    "close": 11 + i}] for i, sym in enumerate(stocks[:2])}
    daily[stocks[2]] = [{"trade_date": "20261102", "open": 10, "close": 12}]
    daily[stocks[3]] = [{"trade_date": "20261005", "open": 10, "close": 10},
                        {"trade_date": "20261026", "open": 10, "close": 12}]
    daily[stocks[4]] = [{"trade_date": "20261005", "open": 10, "close": 10}]
    cache = tmp_path / "daily.json"
    cache.write_text(json.dumps({"_meta": {"days": 9999, "saved_at": "sample"},
                                 "daily": daily}), encoding="utf-8")
    return db, cache, stocks


def test_offline_missing_cache_does_not_fetch_or_touch_legacy(tmp_path, monkeypatch):
    db, cache, stocks = _fixture(tmp_path)
    data = json.loads(cache.read_text(encoding="utf-8"))
    del data["daily"][stocks[4]]
    cache.write_text(json.dumps(data), encoding="utf-8")
    previous = cache.read_bytes()
    legacy = tmp_path / "factor_v1_result.json"
    legacy.write_text("old algorithm", encoding="utf-8")
    monkeypatch.setattr(factor, "fetch_daily_cached",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("网络路径不可触发")))
    output = factor.offline_main(str(db), str(cache), str(tmp_path))
    result = json.loads(open(output, encoding="utf-8").read())
    assert result["audit_status"] == "incomplete_cache"
    assert result["audit_decision"] == "INCONCLUSIVE"
    assert result["coverage"] == "partial"
    assert result["effectiveness_conclusion"] is None
    assert result["eligible_for_effectiveness_conclusion"] is False
    assert result["cache_gaps"]["counts"] == {
        "missing_symbol": 1, "cache_start_after_cutoff": 1,
        "late_with_prior_rows": 1, "no_next_week_row": 0}
    assert result["cache_gaps"]["count_unit"] == "(version, ISO_week, symbol) 去重候选对"
    assert result["cache_gaps"]["candidate_week_symbol_pairs"] == 5
    assert result["cache_gaps"]["unique_missing_symbols"] == [stocks[4]]
    assert result["cache_gaps"]["details"]["missing_symbol"][0]["symbol"] == stocks[4]
    assert result["cache_gaps"]["details"]["cache_start_after_cutoff"][0]["symbol"] == stocks[2]
    assert result["cache_gaps"]["details"]["late_with_prior_rows"][0]["symbol"] == stocks[3]
    assert result["funnel"]["classified_posts"] == 5
    assert result["funnel"]["a_stock_posts"] == 5
    assert result["funnel"]["forward_return_observations"] == 2
    assert result["funnel"]["valid_ic_sections"]["v1.3"]["senti"] == 0
    assert result["versions"]["v1.3"]["senti"]["ic"]["ic_mean"] is None
    assert result["versions"]["v1.3"]["senti"]["quintiles"] == []
    assert result["cache_symbol_ranges"][stocks[2]]["first"] == "20261102"
    assert "cache_sha256" in result["source"]
    assert result["source"]["cache_days_hint_not_coverage"] == 9999
    assert result["versions"]["v1.3"]["funnel"]["forward_return_observations"] == 2
    assert legacy.read_text(encoding="utf-8") == "old algorithm"
    assert cache.read_bytes() == previous
    assert "factor_v1_result.json" not in output


def test_offline_gaps_count_by_version_week_symbol_not_posts(tmp_path):
    db, cache, stocks = _fixture(tmp_path)
    connection = sqlite3.connect(db)
    ticker = json.dumps([{"kind": "stock_a", "canonical": stocks[2]}])
    connection.execute("INSERT INTO xq_status VALUES (?, ?)",
                       ("duplicate", "2026-10-06T13:00:00"))
    connection.execute("INSERT INTO xq_status_classified VALUES (?, ?, ?, ?)",
                       ("duplicate", "bullish", "v1.3", ticker))
    connection.execute("INSERT INTO xq_status VALUES (?, ?)",
                       ("old_version", "2026-10-05T13:00:00"))
    connection.execute("INSERT INTO xq_status_classified VALUES (?, ?, ?, ?)",
                       ("old_version", "bullish", "v1.2-re", ticker))
    connection.execute("INSERT INTO xq_status VALUES (?, ?)",
                       ("next_week", "2026-10-12T13:00:00"))
    connection.execute("INSERT INTO xq_status_classified VALUES (?, ?, ?, ?)",
                       ("next_week", "bullish", "v1.3", ticker))
    connection.commit()
    connection.close()
    data = json.loads(cache.read_text(encoding="utf-8"))
    result = factor.offline_audit(factor.load_posts(str(db)), data,
                                  str(db), str(cache))
    gaps = result["cache_gaps"]
    assert gaps["count_unit"] == "(version, ISO_week, symbol) 去重候选对"
    assert gaps["counts"]["cache_start_after_cutoff"] == 3
    assert gaps["candidate_week_symbol_pairs"] == 7
    assert {(r["version"], r["week"], r["symbol"])
            for r in gaps["details"]["cache_start_after_cutoff"]} == {
        ("v1.3", "2026-10-05", stocks[2]),
        ("v1.2-re", "2026-10-05", stocks[2]),
        ("v1.3", "2026-10-12", stocks[2])}
    assert result["audit_decision"] == "INCONCLUSIVE"


def test_offline_rejects_bad_cache_without_fallback(tmp_path, monkeypatch):
    db, cache, _ = _fixture(tmp_path)
    cache.write_text('{"_meta": {"days": 9999}}', encoding="utf-8")
    monkeypatch.setattr(factor, "fetch_daily_cached",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不能联网")))
    try:
        factor.offline_main(str(db), str(cache), str(tmp_path))
    except ValueError as exc:
        assert "daily" in str(exc)
    else:
        raise AssertionError("损坏缓存不能被当作空缓存补网")
