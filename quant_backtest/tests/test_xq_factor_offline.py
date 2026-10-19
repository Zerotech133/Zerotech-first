# -*- coding: utf-8 -*-
"""T7 纯离线审计：模拟真库与缓存，不调用行情网络。"""
import argparse
import ast
import builtins
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

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


def test_offline_cli_cache_override_reads_selected_source(tmp_path, monkeypatch):
    """Execute only the isolated CLI branch, never the module's real defaults."""
    db, cache_b, _ = _fixture(tmp_path)
    cache_a = tmp_path / "default_cache.json"
    cache_a.write_text('{"daily": {}, "_meta": {"days": 1}}', encoding="utf-8")
    cache_a_bytes = cache_a.read_bytes()
    cache_b_data = json.loads(cache_b.read_text(encoding="utf-8"))
    cache_b_data["_meta"]["days"] = 42
    cache_b.write_text(json.dumps(cache_b_data), encoding="utf-8")
    selected_bytes = cache_b.read_bytes()

    # The function's bound default still refers to the real cache. Guard both
    # that path and the synthetic CLI default against accidental reads.
    bound_default = factor.offline_main.__defaults__[1]
    forbidden = {os.path.normcase(os.path.abspath(os.fspath(path)))
                 for path in (bound_default, cache_a)}
    original_open = builtins.open

    def isolated_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(file)))
            if path in forbidden:
                raise AssertionError("offline CLI opened the default cache")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", isolated_open)
    monkeypatch.setattr(
        factor, "fetch_daily_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("offline CLI must not fetch daily prices")))
    calls = []

    def isolated_offline_main(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return factor.offline_main(
            db_path=str(db), output_dir=str(tmp_path), **kwargs)

    # Compile only the existing __main__ statement rather than runpy.run_path:
    # the full script would bind real DB/output defaults before isolation.
    source = Path(factor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=factor.__file__)
    cli = next(node for node in tree.body
               if isinstance(node, ast.If)
               and isinstance(node.test, ast.Compare)
               and isinstance(node.test.left, ast.Name)
               and node.test.left.id == "__name__"
               and len(node.test.comparators) == 1
               and isinstance(node.test.comparators[0], ast.Constant)
               and node.test.comparators[0].value == "__main__")
    cli_code = compile(ast.fix_missing_locations(ast.Module(
        body=[cli], type_ignores=[])), factor.__file__, "exec")
    monkeypatch.setattr(sys, "argv", [factor.__file__, "--offline", "--cache", str(cache_b)])
    namespace = {"__name__": "__main__", "argparse": argparse,
                 "os": os, "sys": sys, "CACHE_PATH": str(cache_a),
                 "offline_main": isolated_offline_main,
                 "main": lambda: pytest.fail("online branch called"),
                 "t3_retest": lambda: pytest.fail("retest branch called")}
    exec(cli_code, namespace)

    assert calls == [((), {"cache_path": str(cache_b)})]
    assert namespace["CACHE_PATH"] == str(cache_b)
    reports = list(tmp_path.glob("factor_t7-weekly-v2-offline_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["source"]["database"] == os.path.abspath(db)
    assert report["source"]["cache"] == os.path.abspath(cache_b)
    assert report["source"]["cache_sha256"] == hashlib.sha256(selected_bytes).hexdigest()
    assert report["source"]["cache_days_hint_not_coverage"] == 42
    assert cache_a.read_bytes() == cache_a_bytes
    assert cache_b.read_bytes() == selected_bytes


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
    assert result["funnel"]["cache_subset_computable_ic_sections"]["v1.3"]["senti"] == 0
    assert result["funnel"]["valid_ic_sections"]["v1.3"]["senti"] == 0
    assert result["funnel"]["diagnostic_only"] is True
    assert "非因子有效性" in result["funnel"]["ic_count_unit"]
    assert "INCONCLUSIVE" in result["diagnostic_labels"]["ic.n"]
    assert "INCONCLUSIVE" in result["diagnostic_labels"]["valid_ic_sections"]
    assert "不代表因子有效性" in result["diagnostic_labels"]["cache_subset_computable_sections"]
    assert result["versions"]["v1.3"]["senti"]["ic"]["diagnostic_only"] is True
    assert result["versions"]["v1.3"]["senti"]["ic"]["n"] == 0
    assert "非因子有效性" in result["versions"]["v1.3"]["senti"]["ic"]["count_unit"]
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


@pytest.mark.parametrize(
    "bad_rows, expected_reason",
    [
        ([{"trade_date": "2026101８", "open": 10, "close": 11}], "invalid_trade_date"),
        ([{"trade_date": "20260230", "open": 10, "close": 11}], "invalid_trade_date"),
        ([{"trade_date": "20261012", "open": True, "close": 11}], "invalid_open"),
        ([{"trade_date": "20261012", "open": 10, "close": float("inf")}], "invalid_close"),
        ([{"trade_date": "20261012", "open": 10, "close": -1}], "invalid_close"),
        ([{"trade_date": "20261012", "open": 10 ** 1000, "close": 11}], "invalid_open"),
        ([{"trade_date": "20261012", "open": 1e-300, "close": 1e300}], "invalid_close"),
    ],
)
def test_offline_invalid_candidate_cache_is_inconclusive(
        tmp_path, bad_rows, expected_reason):
    db, cache, stocks = _fixture(tmp_path)
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["daily"][stocks[0]] = [
        {"trade_date": "20261005", "open": 10, "close": 10}, *bad_rows]
    result = factor.offline_audit(factor.load_posts(str(db)), data,
                                  str(db), str(cache))
    assert result["audit_status"] == "incomplete_cache"
    assert result["coverage"] == "partial"
    assert result["audit_decision"] == "INCONCLUSIVE"
    assert result["eligible_for_effectiveness_conclusion"] is False
    assert result["cache_quality"]["counts"][expected_reason] >= 1
    assert result["cache_gaps"]["counts"]["invalid_cache_rows"] == 1
    assert result["cache_gaps"]["matched_week_symbol_pairs"].get("v1.3", 0) == 1
    assert result["funnel"]["forward_return_observations"] == 1
    assert result["versions"]["v1.3"]["senti"]["ic"]["ic_mean"] is None


@pytest.mark.parametrize("reverse_order", [False, True])
def test_offline_duplicate_trade_date_cannot_select_first_return(tmp_path, reverse_order):
    db, cache, stocks = _fixture(tmp_path)
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["daily"][stocks[0]].append(
        {"trade_date": "20261012", "open": 10, "close": 50})
    if reverse_order:
        data["daily"][stocks[0]].reverse()
    result = factor.offline_audit(factor.load_posts(str(db)), data,
                                  str(db), str(cache))
    assert result["cache_quality"]["counts"]["duplicate_trade_date"] >= 1
    assert result["cache_gaps"]["matched_week_symbol_pairs"].get("v1.3", 0) == 1
    assert result["funnel"]["forward_return_observations"] == 1
    assert result["audit_status"] == "incomplete_cache"
    assert result["eligible_for_effectiveness_conclusion"] is False


def test_offline_invalid_symbol_isolated_across_versions(tmp_path):
    db, cache, stocks = _fixture(tmp_path)
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO xq_status VALUES (?, ?)",
                       ("old_bad", "2026-10-05T13:00:00"))
    connection.execute("INSERT INTO xq_status_classified VALUES (?, ?, ?, ?)",
                       ("old_bad", "bullish", "v1.2-re", json.dumps([
                           {"kind": "stock_a", "canonical": stocks[0]}])))
    connection.commit()
    connection.close()
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["daily"][stocks[0]].append(
        {"trade_date": "20261012", "open": 10, "close": 50})
    result = factor.offline_audit(factor.load_posts(str(db)), data,
                                  str(db), str(cache))
    assert result["cache_gaps"]["counts"]["invalid_cache_rows"] == 2
    assert result["cache_gaps"]["matched_week_symbol_pairs"].get("v1.3", 0) == 1
    assert result["cache_gaps"]["matched_week_symbol_pairs"].get("v1.2-re", 0) == 0
    assert result["audit_decision"] == "INCONCLUSIVE"


def test_offline_valid_cache_has_no_quality_issues(tmp_path):
    db, cache, _ = _fixture(tmp_path)
    data = json.loads(cache.read_text(encoding="utf-8"))
    result = factor.offline_audit(factor.load_posts(str(db)), data,
                                  str(db), str(cache))
    assert result["cache_quality"]["counts"] == {
        "invalid_daily_rows": 0, "invalid_row": 0,
        "invalid_trade_date": 0, "invalid_open": 0,
        "invalid_close": 0, "duplicate_trade_date": 0}
    assert result["cache_gaps"]["matched_week_symbol_pairs"] == {"v1.3": 2}
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
