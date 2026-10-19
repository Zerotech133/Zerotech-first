# -*- coding: utf-8 -*-
"""T7 舆情因子 v1 单测：mock 小样本（10 帖 / 3 股 / 3 日），不碰真库不碰网。

覆盖四件事：
  1) 聚合：daily_factors 的 senti/mention 口径
  2) 分层：quintile_table 五分位 + monotonicity
  3) IC：ic_series / ic_summary 的 spearman 计算
  4) label_version 分层：filter_version / run_analysis 版本隔离
"""
import json
import os
import socket
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import xq_factor_v1 as factor  # noqa: E402

from xq_factor_v1 import (  # noqa: E402
    INDEX_POLLUTION, build_sections, daily_factors, filter_version,
    ic_series, ic_summary, monotonicity, next_day_return, parse_stock_keys,
    quintile_coverage, quintile_table, run_analysis, spearman,
)


def mock_posts():
    """10 帖 / 3 股 / 3 日（2026-10-05 周一 ~ 10-07 周三）。"""
    P = [
        # v1.3 主版本：SZ000001 3 帖 / SH600519 2 帖 / SZ000651 3 帖
        {"status_id": "1", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "2", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "3", "date": "2026-10-05", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "4", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SH600519"]},
        {"status_id": "5", "date": "2026-10-05", "sentiment": "neutral",
         "version": "v1.3", "stocks": ["SH600519"]},
        {"status_id": "6", "date": "2026-10-06", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000651"]},
        {"status_id": "7", "date": "2026-10-06", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000651"]},
        {"status_id": "8", "date": "2026-10-06", "sentiment": "neutral",
         "version": "v1.3", "stocks": ["SZ000001", "SZ000651"]},
        {"status_id": "9", "date": "2026-10-07", "sentiment": "bullish",
         "version": "v1.2-re", "stocks": ["SZ000001"]},   # 旧版本帖
        {"status_id": "10", "date": "2026-10-07", "sentiment": "neutral",
         "version": "v1.3", "stocks": []},                # 无标的帖
    ]
    return P


def mock_daily():
    """3 股日线：同周日线及次周 10-12，open/close 构造确定性收益。

    10-05(周一,帖子日,非提供日) → T+1=10-06。次日 open→close：
      SZ000001 10-06: open 10 → close 11  → +10%
      SH600519  10-06: open 100 → close 99 → -1%
      SZ000651  10-06: open 50 → close 50 → 0%
      SZ000001 10-07: open 11 → close 10.45 → -5%
      SZ000651  10-07: open 50 → close 52.5 → +5%
      SZ000001 10-08: open 10.45 → close 11.5 → +10%
    """
    def row(sym, d, o, c):
        return {"symbol": sym, "trade_date": d.replace("-", ""),
                "open": o, "close": c}
    return {
        "SZ000001": [row("SZ000001", "2026-10-06", 10, 11),
                     row("SZ000001", "2026-10-07", 11, 10.45),
                     row("SZ000001", "2026-10-08", 10.45, 11.495),
                     row("SZ000001", "2026-10-12", 12, 12.6)],
        "SH600519": [row("SH600519", "2026-10-06", 100, 99),
                     row("SH600519", "2026-10-12", 100, 98)],
        "SZ000651": [row("SZ000651", "2026-10-06", 50, 50),
                     row("SZ000651", "2026-10-07", 50, 52.5),
                     row("SZ000651", "2026-10-12", 50, 51)],
    }


# ── 1) 聚合 ─────────────────────────────────────────────────
def test_daily_factors_aggregation():
    f = daily_factors(mock_posts())
    # 帖1/2/3 同日同股：bull=2 bear=1 → senti=(2-1)/max(1,3)=1/3
    k = ("2026-10-05", "SZ000001")
    assert abs(f[k]["senti"] - 1 / 3) < 1e-9
    assert f[k]["mention"] == 3
    # 帖4/5：bull=1 neutral=1 → senti=(1-0)/max(1,1)=1.0
    assert f[("2026-10-05", "SH600519")]["senti"] == 1.0
    assert f[("2026-10-05", "SH600519")]["mention"] == 2
    # 帖8 一帖提两股：两键各计 1（neutral）
    assert f[("2026-10-06", "SZ000001")]["mention"] == 1
    assert f[("2026-10-06", "SZ000651")]["senti"] == -1.0  # 2 bear
    # 5 个 (date,sym) 键：10-05 两股 / 10-06 两股 / 10-07 一股
    assert len(f) == 5
    # 纯空标的帖不产生键；v1.2-re 帖也聚合（版本过滤在更上层）
    assert [k for k in f if k[0] == "2026-10-07"] == \
        [("2026-10-07", "SZ000001")]


def test_next_day_return():
    d = mock_daily()
    r = next_day_return(d["SZ000001"], "2026-10-05")
    assert abs(r - (11 / 10 - 1)) < 1e-9      # T+1 open→close
    # 跳过周末对齐到下周首个交易日；无行情则返回 None
    assert abs(next_day_return(d["SZ000001"], "2026-10-11") - .05) < 1e-9
    assert next_day_return(d["SZ000001"], "2026-10-12") is None
    assert next_day_return([], "2026-10-05") is None


# ── 2) 分层 ─────────────────────────────────────────────────
def _weekly_sections():
    return build_sections(mock_posts(), mock_daily(), freq="weekly")


def test_build_sections_weekly():
    secs = _weekly_sections()
    # 全部帖子在同一个 ISO 周（2026-10-05 周一）→ 1 个周截面
    assert len(secs) == 1
    assert secs[0]["key"] == "2026-10-05"
    syms = {o["sym"] for o in secs[0]["obs"]}
    assert syms == {"SZ000001", "SH600519", "SZ000651"}
    o1 = next(o for o in secs[0]["obs"] if o["sym"] == "SZ000001")
    # 同周多日因子仍聚合，收益只取周日后首个交易日，不混入周内收益
    assert abs(o1["senti"] - (1 / 3 + 0.0 + 1.0) / 3) < 1e-9
    assert abs(o1["ret"] - 0.05) < 1e-9
    assert o1["date"] == "2026-10-11"
    assert o1["mention"] == 5  # 同周帖数相加：3+1+1，非日均数
    # 只有周内行情时，不得把已发生的周内收益回填为周因子的前向收益
    intraweek_only = {sym: [r for r in rows if r["trade_date"] < "20261012"]
                      for sym, rows in mock_daily().items()}
    assert build_sections(mock_posts(), intraweek_only, freq="weekly") == []
    assert build_sections(mock_posts(), intraweek_only, freq="daily")


def test_weekly_return_delay_gate():
    daily = mock_daily()
    daily["SZ000001"] = [r for r in daily["SZ000001"]
                          if r["trade_date"] != "20261012"]
    daily["SZ000001"].append({"trade_date": "20261019", "open": 10,
                              "close": 12})
    diagnostics = {}
    sections = build_sections(mock_posts(), daily, freq="weekly",
                              diagnostics=diagnostics)
    assert "SZ000001" not in {o["sym"] for o in sections[0]["obs"]}
    assert diagnostics == {"skipped_delayed": 1}
    out = run_analysis(mock_posts(), daily)
    assert out["versions"]["v1.3"]["alignment"]["skipped_delayed"] == 1
    assert out["weekly_max_delay_days"] == 7
    assert "非整周收益" in out["forward_return"]


def test_quintile_and_monotonicity():
    # 构造 10 个截面、每截面 5 股，因子值与收益严格同序 → q5 最优、mono=1
    secs = [{"key": "w%d" % w,
             "obs": [{"sym": "S%d" % i, "senti": i * 0.2,
                      "mention": 5 - i, "ret": 0.02 * i}
                     for i in range(5)]}
            for w in range(10)]
    qtab = quintile_table(secs, "senti")
    assert [r["quintile"] for r in qtab] == [1, 2, 3, 4, 5]
    assert all(r["n"] == 10 for r in qtab)     # 每截面各贡献 1 观测
    means = [r["mean_ret"] for r in qtab]
    assert means == sorted(means)              # 严格单调递增
    assert monotonicity(qtab) == 1.0
    # mention 反向：q5 应最差
    qtab_m = quintile_table(secs, "mention")
    assert qtab_m[0]["mean_ret"] > qtab_m[4]["mean_ret"]
    # 小于五股、少于五种因子值的截面不强行拆成五分位
    tiny = [{"key": "t", "obs": [{"sym": "A", "senti": 0.5, "mention": 1,
                                  "ret": 0.01}]}]
    assert all(r["n"] == 0 for r in quintile_table(tiny, "senti"))
    ties = [{"key": "ties", "obs": [
        {"sym": "S%d" % i, "senti": i // 2, "ret": 0.01 * i}
        for i in range(6)]}]
    assert all(r["n"] == 0 for r in quintile_table(ties, "senti"))
    # 即使十股恰有五个离散值，只要有同分就整截面拒绝
    ties[0]["obs"] = [{"sym": "S%d" % i, "senti": i // 2, "ret": .01 * i}
                      for i in range(10)]
    assert all(r["n"] == 0 for r in quintile_table(ties, "senti"))
    coverage = quintile_coverage(secs + tiny + ties, "senti")
    assert coverage == {"eligible": 10, "skipped_small": 1,
                        "skipped_ties": 1}
    assert run_analysis(mock_posts(), mock_daily())["versions"]["v1.3"][
        "mention"]["quintile_coverage"]["skipped_small"] == 1


# ── 3) IC ───────────────────────────────────────────────────
def test_spearman_and_ic():
    # 完全同序 → 1；完全反序 → -1
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert abs(spearman([1, 2, 3, 4], [40, 30, 20, 10]) + 1.0) < 1e-12
    # 并列取平均秩
    assert spearman([1, 1, 2], [5, 6, 7]) is not None
    # n<3 / 零方差 → None
    assert spearman([1, 2], [1, 2]) is None
    assert spearman([1, 1, 1], [1, 2, 3]) is None

    ics = ic_series(_weekly_sections(), "senti", min_obs=3)
    # 周截面 3 股 → 可算 1 个 IC
    assert len(ics) == 1 and ics[0]["n"] == 3
    s = ic_summary(ics)
    assert s["n"] == 1 and s["ic_mean"] is None
    assert s["exploratory_ic"] == ics[0]["ic"]
    assert s["evidence"] == "insufficient_sections"
    # min_obs 过滤：min_obs=4 时截面(3 股)被跳过
    assert ic_series(_weekly_sections(), "senti", min_obs=4) == []
    # 恒定收益 → 零方差 → 无 IC → summary n=0
    flat = [{"key": "k", "obs": [{"sym": "A", "senti": 1, "mention": 1,
                                  "ret": 0.0},
                                 {"sym": "B", "senti": -1, "mention": 1,
                                  "ret": 0.0},
                                 {"sym": "C", "senti": 0, "mention": 1,
                                  "ret": 0.0}]}]
    assert ic_summary(ic_series(flat, "senti"))["n"] == 0


def test_ic_summary_minimum_valid_sections():
    one = [{"key": "w1", "ic": -0.2474, "n": 3}]
    sparse = ic_summary(one)
    assert sparse["n"] == 1
    assert sparse["ic_mean"] is None and sparse["icir"] is None
    assert sparse["exploratory_ic"] == -0.2474
    assert sparse["evidence"] == "insufficient_sections"

    enough = ic_summary([{"key": str(i), "ic": i / 10, "n": 5}
                         for i in range(5)])
    assert enough["n"] == 5 and enough["ic_mean"] == 0.2
    assert enough["evidence"] == "exploratory"


# ── 4) label_version 分层 ───────────────────────────────────
def test_version_stratification():
    P = mock_posts()
    assert len(filter_version(P, "v1.3")) == 9
    assert len(filter_version(P, "v1.2-re")) == 1
    assert filter_version(P, "v1.2") == []
    # run_analysis：版本隔离——v1.2-re 截面只含旧版帖的股票
    daily = mock_daily()
    out = run_analysis(P, daily, freq="weekly", min_obs=3)
    assert set(out["versions"]) == {"v1.3", "v1.2-re"}
    assert out["main_version"] == "v1.3"
    v13 = out["versions"]["v1.3"]
    assert v13["n_posts"] == 9
    # v1.2-re 只有 1 帖 1 股 → 截面 obs<min_obs → n_obs 仍记，IC n=0
    vr = out["versions"]["v1.2-re"]
    assert vr["n_posts"] == 1 and vr["senti"]["ic"]["n"] == 0
    # 版本间观测不串：v1.3 的观测数只含 v1.3 帖
    assert v13["n_obs"] == vr["n_obs"] or v13["n_obs"] >= vr["n_obs"]


def test_index_pollution_excluded():
    # SZ000300 等指数污染码在 parse_stock_keys 层剔除，不进入因子
    nt = json.dumps([
        {"raw": "沪深300", "kind": "stock_a", "canonical": "SZ000300"},
        {"raw": "中证500", "kind": "stock_a", "canonical": "SZ000905"},
        {"raw": "平安银行", "kind": "stock_a", "canonical": "SZ000001"},
        {"raw": "腾讯", "kind": "stock_hk", "canonical": "00700"},
        {"raw": "红利指数", "kind": "index", "canonical": "000922.SH"},
        {"raw": "坏数据", "kind": "stock_a", "canonical": None},
    ], ensure_ascii=False)
    assert parse_stock_keys(nt) == ["SZ000001"]
    P = [{"status_id": "x", "date": "2026-10-05", "sentiment": "bullish",
          "version": "v1.3", "stocks": parse_stock_keys(nt)}]
    f = daily_factors(P)
    assert set(k[1] for k in f) == {"SZ000001"}
    assert "SZ000300" in INDEX_POLLUTION and "SZ000905" in INDEX_POLLUTION


def mock_daily_router(monkeypatch, fetch_daily):
    """拦截延迟导入的路由模块，避免导入真实行情与凭据依赖。"""
    monkeypatch.setitem(sys.modules, "xq_quote_router",
                        types.SimpleNamespace(fetch_daily=fetch_daily))


def test_fetch_daily_cached_revalidates_old_cache_and_fetches_gap(tmp_path, monkeypatch):
    """历史脏键、错误身份行和空列表不能造成缓存命中。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    cache_path.write_text(json.dumps({
        "_meta": {"days": 10, "saved_at": "historical"},
        "daily": {
            "SH000001": [{"symbol": "SH000001", "trade_date": "20261012",
                          "open": 10, "close": 12}],
            "SZ000300": [{"symbol": "SZ000300", "trade_date": "20261012",
                          "open": 10, "close": 12}],
            "SZ000001": [mock_daily()["SZ000001"][0],
                         {"symbol": "SH000001", "trade_date": "20261012",
                          "open": 10, "close": 12}],
            "SH600519": [],
        },
    }), encoding="utf-8")
    calls = []

    def mocked_fetch(syms, days):
        calls.append((syms, days))
        return {"SZ000001": mock_daily()["SZ000001"],
                "SH600519": mock_daily()["SH600519"]}, "mock", []

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(
        ["SZ000001", "SH600519", "SH000001", "SZ000300"], 10)
    assert calls == [(["SZ000001", "SH600519"], 10)]
    assert set(daily) == {"SZ000001", "SH600519"}
    assert daily["SZ000001"] == mock_daily()["SZ000001"]
    assert any("SH000001" in error for error in errs)
    assert any("SZ000300" in error for error in errs)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["daily"] == daily
    assert saved["_meta"]["saved_at"] == "historical"
    factor.fetch_daily_cached(["SZ000001", "SH600519"], 10)
    assert len(calls) == 1


def test_fetch_daily_cached_filters_provider_keys_rows_and_ts_code(
        tmp_path, monkeypatch):
    """恶意回包只能留下与所请求股票同身份的有效日线行。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    valid = mock_daily()["SZ000001"][0]

    def mocked_fetch(syms, days):
        assert syms == ["SZ000001", "SH600519"] and days == 15
        return {
            "SH000001": [valid],
            "SZ000300": [valid],
            "SZ000001": [
                {**valid, "symbol": "SH000001"},
                {**valid, "ts_code": "000001.SH"},
                {**valid, "ts_code": "000001.SZ"},
                {"symbol": "SZ000001", "trade_date": "bad",
                 "open": 10, "close": 11},
            ],
            "SH600519": [{"symbol": "SZ000001", "trade_date": "20261012",
                          "open": 10, "close": 11}],
        }, "mock", []

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(["SZ000001", "SH600519"], 15)
    assert daily == {"SZ000001": [{**valid, "ts_code": "000001.SZ"}]}
    assert any("SH600519" in error for error in errs)
    assert any("SH000001" in error for error in errs)
    assert any("SZ000300" in error for error in errs)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["daily"] == daily
    assert saved["_missing"] == ["SH600519"]


def test_fetch_daily_cached_all_fail_and_invalid_rows_retry(tmp_path, monkeypatch):
    """全失败和旧缓存异常 rows 都明确缺码并可在下一轮重试。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    cache_path.write_text(json.dumps({"_meta": {"days": 20},
                                      "daily": {"SZ000001": {"bad": "rows"}}}),
                          encoding="utf-8")
    calls = []

    def all_failed(syms, days):
        calls.append((syms, days))
        return {"SZ000001": [{"symbol": "SH000001"}]}, "", [
            "SZ000001 上游失败"]

    mock_daily_router(monkeypatch, all_failed)
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 20)
    assert daily == {} and calls == [(["SZ000001"], 20)]
    assert any("SZ000001" in error for error in errs)
    assert json.loads(cache_path.read_text(encoding="utf-8"))["_missing"] == [
        "SZ000001"]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["daily"] == {}
    mock_daily_router(monkeypatch, lambda syms, days: (
        {"SZ000001": mock_daily()["SZ000001"]}, "mock", []))
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 20)
    assert daily == {"SZ000001": mock_daily()["SZ000001"]}
    assert errs == []


def test_fetch_daily_cached_persists_synthesized_full_failure(tmp_path, monkeypatch):
    """路由无结果且无错误时，补造错误须随缺码一同入档。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    mock_daily_router(monkeypatch, lambda syms, days: ({}, "mock", []))

    daily, errs = factor.fetch_daily_cached(["SZ000001"], 20)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert daily == saved["daily"] == {}
    assert saved["_missing"] == ["SZ000001"]
    assert saved["_errs"] == errs == ["SZ000001 全源失败(跳过)"]


def test_fetch_daily_cached_partial_router_failure_does_not_mislabel_success(
        tmp_path, monkeypatch):
    """一只备援成功、一只全源失败：错误保留但成功码非缺码。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))

    def mocked_fetch(syms, days):
        assert syms == ["SZ000001", "SH600519"]
        return {"SZ000001": mock_daily()["SZ000001"]}, "akshare×1", [
            "SZ000001 tushare 主源失败", "SH600519 两源失败"]

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(["SZ000001", "SH600519"], 10)
    assert set(daily) == {"SZ000001"}
    assert any("SZ000001 tushare 主源失败" in error for error in errs)
    assert any("SH600519 两源失败" in error for error in errs)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["_missing"] == ["SH600519"]
    assert "SZ000001" not in saved["_missing"]


def test_fetch_daily_cached_days_meta_invalidates_cache(tmp_path, monkeypatch):
    """days 漂移后旧缓存不可复用，且正常股票 SZ000001 可补取。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    cache_path.write_text(json.dumps({"_meta": {"days": 5},
                                      "daily": {"SZ000001": mock_daily()["SZ000001"]}}),
                          encoding="utf-8")
    calls = []

    def mocked_fetch(syms, days):
        calls.append((syms, days))
        return {"SZ000001": mock_daily()["SZ000001"]}, "mock", []

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 6)
    assert daily == {"SZ000001": mock_daily()["SZ000001"]}
    assert errs == [] and calls == [(["SZ000001"], 6)]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["_meta"]["days"] == 6


def test_next_day_return_rejects_nonfinite_price_ratio():
    """绕过在线缓存闸门的非有限收益不能进入因子统计。"""
    row = {"symbol": "SZ000001", "trade_date": "20261012",
           "open": 1e-308, "close": 1e308}
    assert next_day_return([row], "2026-10-11") is None
    assert abs(next_day_return([{**row, "open": 10, "close": 11}],
                               "2026-10-11") - 0.1) < 1e-12


def test_fetch_daily_cached_rejects_overflow_from_cache_and_router(
        tmp_path, monkeypatch):
    """旧缓存含坏行整码失效；路由坏行不落盘，正常行仍可保留。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    valid = mock_daily()["SZ000001"][0]
    overflowing = {**valid, "trade_date": "20261012",
                   "open": 1e-308, "close": 1e308}
    cache_path.write_text(json.dumps({
        "_meta": {"days": 21},
        "daily": {"SZ000001": [valid, overflowing]},
    }), encoding="utf-8")
    calls = []

    def mocked_fetch(syms, days):
        calls.append((syms, days))
        return {"SZ000001": [overflowing, valid]}, "mock", []

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 21)
    assert calls == [(["SZ000001"], 21)]
    assert daily == {"SZ000001": [valid]}
    assert any("SZ000001 cache: 开收盘价收益非有限值" in error
               for error in errs)
    assert any("SZ000001 router: 开收盘价收益非有限值" in error
               for error in errs)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["daily"] == daily
    assert saved["_missing"] == []
    assert saved["_errs"] == errs
    factor.fetch_daily_cached(["SZ000001"], 21)
    assert len(calls) == 1


def test_fetch_daily_cached_overflow_all_failed_persists_diagnostics(
        tmp_path, monkeypatch):
    """旧片与补取均只有极端有限价格时，保持全失败缺码和错误同档。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    overflowing = {"symbol": "SZ000001", "trade_date": "20261012",
                   "open": 1e-308, "close": 1e308}
    cache_path.write_text(json.dumps({
        "_meta": {"days": 21}, "daily": {"SZ000001": [overflowing]},
    }), encoding="utf-8")
    calls = []

    def mocked_fetch(syms, days):
        calls.append((syms, days))
        return {"SZ000001": [overflowing]}, "mock", []

    mock_daily_router(monkeypatch, mocked_fetch)
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 21)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert calls == [(["SZ000001"], 21)]
    assert daily == saved["daily"] == {}
    assert saved["_missing"] == ["SZ000001"]
    assert saved["_errs"] == errs
    assert any("SZ000001 cache: 开收盘价收益非有限值" in error
               for error in errs)
    assert any("SZ000001 router: 开收盘价收益非有限值" in error
               for error in errs)
    assert not any("SZ000001 全源失败(跳过)" in error for error in errs)


@pytest.mark.parametrize("failure", ["replace", "partial_write", "directory"])
@pytest.mark.parametrize("old_exists", [True, False])
def test_fetch_daily_cached_atomic_publish_failure_and_retry(
        tmp_path, monkeypatch, failure, old_exists):
    """发布失败不损旧字节或造目标，返回内存结果且下轮可补发。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    original_bytes = (b'{"_meta":{"days":22},"daily":{},"_errs":'
                      b'["historical"],"_missing":["SZ000001"]}\n')
    if old_exists:
        cache_path.write_bytes(original_bytes)
    calls = []

    def mocked_fetch(syms, days):
        calls.append((syms, days))
        return {"SZ000001": mock_daily()["SZ000001"]}, "mock", [
            "SZ000001 主源失败，备援成功"]

    mock_daily_router(monkeypatch, mocked_fetch)
    with monkeypatch.context() as patch:
        if failure == "replace":
            def failed_replace(source, target):
                assert os.path.dirname(source) == str(tmp_path)
                assert target == str(cache_path)
                with open(source, encoding="utf-8") as temporary:
                    assert json.load(temporary)["_missing"] == []
                raise OSError("模拟替换失败")

            patch.setattr(factor.os, "replace", failed_replace)
        elif failure == "partial_write":
            def failed_dump(cache, target):
                target.write('{"daily":')
                target.flush()
                raise OSError("模拟半写失败")

            patch.setattr(factor.json, "dump", failed_dump)
        else:
            def failed_makedirs(path, exist_ok):
                assert path == str(tmp_path) and exist_ok
                raise OSError("模拟建目录失败")

            patch.setattr(factor.os, "makedirs", failed_makedirs)
        daily, errs = factor.fetch_daily_cached(["SZ000001"], 22)
    assert daily == {"SZ000001": mock_daily()["SZ000001"]}
    assert errs[0] == "SZ000001 主源失败，备援成功"
    assert len([error for error in errs if error.startswith("缓存发布失败:")]) == 1
    assert (cache_path.read_bytes() == original_bytes if old_exists
            else not cache_path.exists())
    assert sorted(tmp_path.iterdir()) == ([cache_path] if old_exists else [])

    # 未发布的内存结果不算缓存命中，下轮需要重新向路由获取。
    retried_daily, retried_errs = factor.fetch_daily_cached(["SZ000001"], 22)
    assert calls == [(["SZ000001"], 22), (["SZ000001"], 22)]
    assert retried_daily == daily
    assert retried_errs == ["SZ000001 主源失败，备援成功"]
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["daily"] == retried_daily
    assert saved["_missing"] == []
    assert saved["_errs"] == retried_errs
    assert sorted(tmp_path.iterdir()) == [cache_path]


def test_fetch_daily_cached_cleanup_failure_is_reported(tmp_path, monkeypatch):
    """无法删除临时文件时显式报告，而不把发布失败伪装为成功。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))
    mock_daily_router(monkeypatch, lambda syms, days: ({}, "mock", []))
    with monkeypatch.context() as patch:
        patch.setattr(factor.os, "replace", lambda source, target: (
            (_ for _ in ()).throw(OSError("模拟替换失败"))))
        patch.setattr(factor.os, "unlink", lambda path: (
            (_ for _ in ()).throw(OSError("模拟清理失败"))))
        daily, errs = factor.fetch_daily_cached(["SZ000001"], 23)
    assert daily == {}
    assert not cache_path.exists()
    assert any(error.startswith("缓存发布失败:") for error in errs)
    assert any(error.startswith("缓存临时文件清理失败:") for error in errs)
    # 清理失败的残留限于本次唯一临时文件，不会被误认作目标缓存。
    leftover = list(tmp_path.iterdir())
    assert len(leftover) == 1 and leftover[0].name.startswith(".daily_cache_")
    assert str(leftover[0]) in errs[-1]
    leftover[0].unlink()


@pytest.fixture
def isolated_factor_io(tmp_path, monkeypatch):
    """限定作者新用例只能接触临时目录和合成行情。"""
    cache_path = tmp_path / "daily_cache.json"
    monkeypatch.setattr(factor, "ROOT", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(factor, "CACHE_PATH", str(cache_path))

    def forbidden(*args, **kwargs):
        pytest.fail("测试禁止访问真库、网络或凭据")

    monkeypatch.setattr(factor.sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=forbidden, post=forbidden, request=forbidden,
        Session=types.SimpleNamespace(request=forbidden)))
    for key in ("TUSHARE_TOKEN", "TUSHARE_API_TOKEN", "XQ_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    return cache_path


def test_cached_daily_fallback_success_has_error_but_no_missing(
        isolated_factor_io, monkeypatch):
    """主源报错而备援成功：错误条数 1 不等于失败股票数 0。"""
    cache_path = isolated_factor_io
    router_errors = ["SZ000001 主源失败，备援成功"]

    def fallback(syms, days):
        assert syms == ["SZ000001"] and days == 22
        return {"SZ000001": mock_daily()["SZ000001"]}, "backup", router_errors

    mock_daily_router(monkeypatch, fallback)
    daily, errs = factor.fetch_daily_cached(["SZ000001"], 22)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))

    assert daily == saved["daily"] == {"SZ000001": mock_daily()["SZ000001"]}
    assert saved["_missing"] == []
    assert saved["_errs"] == errs == router_errors
    assert len(errs) == 1 and len(saved["_missing"]) == 0


def test_cached_daily_one_missing_symbol_can_have_multiple_errors(
        isolated_factor_io, monkeypatch):
    """一只缺码可对应多条错误，_missing 不可由 _errs 条数推断。"""
    cache_path = isolated_factor_io
    router_errors = ["SH600519 主源失败", "SH600519 备援失败"]

    def mixed(syms, days):
        assert syms == ["SZ000001", "SH600519"] and days == 22
        return {"SZ000001": mock_daily()["SZ000001"]}, "partial", router_errors

    mock_daily_router(monkeypatch, mixed)
    daily, errs = factor.fetch_daily_cached(["SZ000001", "SH600519"], 22)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))

    assert set(daily) == set(saved["daily"]) == {"SZ000001"}
    assert saved["_missing"] == ["SH600519"]
    assert saved["_errs"] == errs == router_errors
    assert len(errs) == 2 and len(saved["_missing"]) == 1


def test_cached_daily_replace_failure_does_not_claim_new_errors_persisted(
        isolated_factor_io, monkeypatch):
    """旧 JSON 原字节不变；未发布错误只存在返回值而非旧 _errs。"""
    cache_path = isolated_factor_io
    original = (b'{"_meta":{"days":22},"daily":{},'
                b'"_missing":["SZ000001"],"_errs":["old error"]}\n')
    cache_path.write_bytes(original)

    def fallback(syms, days):
        assert syms == ["SZ000001"] and days == 22
        return {"SZ000001": mock_daily()["SZ000001"]}, "backup", [
            "SZ000001 主源失败，备援成功"]

    mock_daily_router(monkeypatch, fallback)

    def failed_replace(source, target):
        assert target == str(cache_path)
        with open(source, encoding="utf-8") as pending:
            unpublished = json.load(pending)
        assert unpublished["_missing"] == []
        assert unpublished["_errs"] == ["SZ000001 主源失败，备援成功"]
        raise OSError("模拟原子替换失败")

    with monkeypatch.context() as patch:
        patch.setattr(factor.os, "replace", failed_replace)
        daily, errs = factor.fetch_daily_cached(["SZ000001"], 22)

    assert set(daily) == {"SZ000001"}
    assert errs == ["SZ000001 主源失败，备援成功",
                    "缓存发布失败: 模拟原子替换失败"]
    assert cache_path.read_bytes() == original
    assert json.loads(cache_path.read_bytes())["_errs"] == ["old error"]
    assert list(cache_path.parent.iterdir()) == [cache_path]


def test_main_daily_fail_keeps_error_count_compatibility(
        isolated_factor_io, monkeypatch, capsys):
    """main 保留 daily_fail=错误条数，不将其改写为失败股票数。"""
    root = isolated_factor_io.parent
    (root / "data" / "xueqiu").mkdir(parents=True)
    posts = [{"date": "2026-10-05", "stocks": ["SZ000001", "SH600519"]}]
    daily = {"SZ000001": mock_daily()["SZ000001"]}
    errs = ["SH600519 主源失败", "SH600519 备援失败"]
    monkeypatch.setattr(factor, "load_posts", lambda: posts)

    def mocked_daily(syms, days):
        assert syms == ["SH600519", "SZ000001"] and isinstance(days, int)
        return daily, errs

    def mocked_analysis(got_posts, got_daily, freq, min_obs):
        assert got_posts is posts and got_daily is daily
        assert (freq, min_obs) == ("weekly", 3)
        return {"main_version": None, "versions": {}}

    monkeypatch.setattr(factor, "fetch_daily_cached", mocked_daily)
    monkeypatch.setattr(factor, "run_analysis", mocked_analysis)
    result = factor.main()
    saved = json.loads((root / "data" / "xueqiu" / "factor_v1_result.json")
                       .read_text(encoding="utf-8"))

    assert result == saved
    assert result["coverage"]["daily_ok"] == 1
    assert result["coverage"]["daily_fail"] == 2
    assert result["daily_errs"] == errs
    assert "daily ok=1 fail=2" in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
