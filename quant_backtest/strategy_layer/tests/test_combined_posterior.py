# -*- coding: utf-8 -*-
"""combined_posterior 单测：分层后验统计（M0 2026-10-19）。

全程离线：rows/行情/权重口径全部注入（tmp_path 假数据），禁连真实
PG、禁读真实 data/ 生产档、禁跑生产任务。
"""
from __future__ import annotations

import json
import os

import pytest

from strategy_layer import combined_posterior as cp


# ── 注入数据工厂 ───────────────────────────────────────────────────────
def _row(sym, td, contribs, divergence=False, action="buy"):
    return {"trade_date": td, "symbol": sym, "action": action,
            "score": 99.0, "confidence": 0.9, "contributions": contribs,
            "divergence": divergence, "rationale": ""}


def _fixture_rows():
    return [
        # A：双源（min 权重 0.4 → shadow），t1 +10%，t5 -10%
        _row("600000.SH", "20260105",
             {"kg_boost": {"weight": 0.6, "score": 50},
              "chan_monitor": {"weight": 0.4, "score": 49}}),
        # B：单源（权重 1.0 → full），t1 -10%，t5 +10%
        _row("000001.SZ", "20260105",
             {"kg_boost": {"weight": 1.0, "score": 50}}),
        # C：分歧票（单独一组，不进其他分层）
        _row("300001.SZ", "20260106",
             {"chan_monitor": {"weight": 0.5, "score": 50}},
             divergence=True),
    ]


def _fixture_quotes():
    return {
        ("600000.SH", "20260105"): {"close": 10.0, "t1": 11.0, "t5": 9.0},
        ("000001.SZ", "20260105"): {"close": 20.0, "t1": 18.0, "t5": 22.0},
        ("300001.SZ", "20260106"): {"close": 5.0, "t1": 5.5, "t5": 4.0},
    }


def _fetcher(quotes):
    def fetch(sym, td):
        return quotes.get((sym, td))
    return fetch


# ── 1. 分层统计数值（手工算期望值） ────────────────────────────────────
def test_layered_stats_values(tmp_path):
    doc = cp.build_combined_posterior(
        rows=_fixture_rows(), quote_fetcher=_fetcher(_fixture_quotes()))
    assert doc["status"] == "OK"
    assert doc["total_rows"] == 3
    assert doc["missing_total"] == 0
    assert doc["asof"] == "20260106"
    # kg_boost：A+B，t1 [10,-10] avg=0.0 win=50.0；t5 [-10,10] avg=0.0
    kg = doc["per_strategy"]["kg_boost"]
    assert kg["count"] == 2
    assert kg["avg_ret_t1"] == 0.0
    assert kg["win_rate"] == 50.0
    assert kg["avg_ret_t5"] == 0.0
    # chan_monitor：仅 A（C 是分歧票，不混入），t1 avg=10.0 win=100.0
    cm = doc["per_strategy"]["chan_monitor"]
    assert cm["count"] == 1
    assert cm["avg_ret_t1"] == 10.0
    assert cm["win_rate"] == 100.0
    # 权重组：A min(0.6,0.4)=0.4→shadow；B min(1.0)=1.0→full；C 不入组
    assert doc["per_weight_group"]["full"]["count"] == 1
    assert doc["per_weight_group"]["full"]["avg_ret_t1"] == -10.0
    assert doc["per_weight_group"]["full"]["win_rate"] == 0.0
    assert doc["per_weight_group"]["shadow"]["count"] == 1
    assert doc["per_weight_group"]["shadow"]["avg_ret_t1"] == 10.0
    assert doc["per_weight_group"]["shadow"]["win_rate"] == 100.0
    assert doc["per_weight_group"]["off"]["count"] == 0
    # overall：t1 [10,-10,10] avg=3.33 win=66.7
    assert doc["summary"]["windows"]["t1"]["count"] == 3
    assert doc["summary"]["windows"]["t1"]["avg_ret_t1"] == 3.33
    assert doc["summary"]["windows"]["t1"]["win_rate"] == 66.7


# ── 2. 空行集：空 summary 不抛 ────────────────────────────────────────
def test_empty_rows_empty_summary_no_raise():
    doc = cp.build_combined_posterior(rows=[], quote_fetcher=_fetcher({}))
    assert doc["status"] == "OK"
    assert doc["total_rows"] == 0
    assert doc["per_strategy"] == {}
    assert doc["per_weight_group"]["full"]["count"] == 0
    assert doc["per_weight_group"]["full"]["win_rate"] is None
    assert doc["divergence_group"]["count"] == 0
    assert doc["missing_total"] == 0
    assert isinstance(doc["summary"], dict)
    assert doc["summary"]["asof"] == ""


# ── 3. 行情缺失票进 missing，不污染 win_rate 分母 ─────────────────────
def test_missing_quote_not_in_denominator():
    rows = _fixture_rows()[:2]
    quotes = {("600000.SH", "20260105"): {"close": 10.0, "t1": 11.0, "t5": 9.0}}
    doc = cp.build_combined_posterior(rows=rows, quote_fetcher=_fetcher(quotes))
    assert doc["missing_total"] == 1
    kg = doc["per_strategy"]["kg_boost"]
    assert kg["count"] == 1          # 缺失票不计入分母
    assert kg["missing"] == 1
    assert kg["win_rate"] == 100.0   # 分母只剩 +10% 一票
    assert kg["avg_ret_t1"] == 10.0
    assert doc["per_weight_group"]["full"]["count"] == 0
    assert doc["per_weight_group"]["full"]["missing"] == 1


# ── 4. quote_fetcher 抛异常：单票降级，整体继续 ────────────────────────
def test_fetcher_error_single_row_degrades():
    rows = _fixture_rows()[:2]

    def fetch(sym, td):
        if sym == "000001.SZ":
            raise RuntimeError("行情源抖动")
        return {"close": 10.0, "t1": 11.0, "t5": 9.0}

    doc = cp.build_combined_posterior(rows=rows, quote_fetcher=fetch)
    assert doc["missing_total"] == 1
    kg = doc["per_strategy"]["kg_boost"]
    assert kg["count"] == 1
    assert kg["missing"] == 1
    assert kg["avg_ret_t1"] == 10.0   # 好票统计完整
    assert doc["summary"]["windows"]["t1"]["count"] == 1


# ── 5. 原子写失败不留半文件 ────────────────────────────────────────────
def test_atomic_write_failure_no_half_file(tmp_path, monkeypatch):
    out = str(tmp_path / "posterior.json")

    orig_dump = cp.json.dump
    calls = {"n": 0}

    def boom(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("盘满")
        return orig_dump(*a, **kw)

    monkeypatch.setattr(cp.json, "dump", boom)
    with pytest.raises(RuntimeError):
        cp.build_combined_posterior(rows=_fixture_rows(), out_path=out)
    assert not os.path.exists(out)
    assert not os.path.exists(out + ".tmp")
    # 恢复后可正常落盘
    doc = cp.build_combined_posterior(rows=_fixture_rows(), out_path=out)
    assert os.path.exists(out)
    assert json.load(open(out, encoding="utf-8"))["status"] == "OK"
    assert doc["out_path"] == os.path.abspath(out)


# ── 6. 产出档被 xunyou.read_posterior 读为 status=OK ──────────────────
def test_read_posterior_compat_status_ok(tmp_path, monkeypatch):
    from strategy_layer import combined_verdicts
    from strategy_layer.xunyou import read_posterior

    out = str(tmp_path / "posterior.json")
    cp.build_combined_posterior(rows=_fixture_rows(),
                                quote_fetcher=_fetcher(_fixture_quotes()),
                                out_path=out)
    monkeypatch.setattr(combined_verdicts, "OUT_DEFAULT", out)
    res = read_posterior()
    assert res["status"] == "OK"
    assert res["asof"] == "20260106"
    assert res["windows"]["t1"]["count"] == 3
    assert set(res["by_strategy"]) == {"kg_boost", "chan_monitor"}
    assert set(res["by_weight_group"]) == {"full", "shadow", "off"}
    assert res["by_shadow"]["divergence"]["count"] == 1


# ── 7. divergence 组独立统计 ──────────────────────────────────────────
def test_divergence_group_independent():
    rows = _fixture_rows()
    doc = cp.build_combined_posterior(
        rows=rows, quote_fetcher=_fetcher(_fixture_quotes()))
    dv = doc["divergence_group"]
    assert dv["count"] == 1
    assert dv["avg_ret_t1"] == 10.0
    assert dv["avg_ret_t5"] == -20.0
    assert dv["win_rate"] == 100.0
    # 分歧票不混入其他分层分母：chan_monitor 只剩 A，分组里没有 300001.SZ
    assert doc["per_strategy"]["chan_monitor"]["count"] == 1
    total_grouped = sum(b["count"] for b in doc["per_weight_group"].values())
    assert total_grouped == 2
    # summary.by_shadow 镜像
    assert doc["summary"]["by_shadow"]["divergence"] == dv


# ── 8. rows-file 注入路径跑通（含 CLI --rows-file/--out/--limit） ──────
def test_rows_file_injection_and_cli(tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps({"rows": _fixture_rows()},
                                    ensure_ascii=False), encoding="utf-8")
    direct = cp.build_combined_posterior(
        rows=_fixture_rows(), quote_fetcher=_fetcher(_fixture_quotes()))
    via_file = cp.build_combined_posterior(
        rows_file=str(rows_file), quote_fetcher=_fetcher(_fixture_quotes()))
    assert via_file["total_rows"] == 3
    assert via_file["per_strategy"] == direct["per_strategy"]
    assert via_file["per_weight_group"] == direct["per_weight_group"]

    out = tmp_path / "cli_out.json"
    rc = cp.main(["--rows-file", str(rows_file),
                  "--out", str(out), "--limit", "2"])
    assert rc == 0
    doc = json.load(open(out, encoding="utf-8"))
    assert doc["total_rows"] == 2
    assert doc["status"] == "OK"


# ── 9. 权重分带口径（registry 回退 + off 组） ──────────────────────────
def test_weight_group_bands(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"weights": {"kg_boost": 0.5,
                                           "chan_monitor": 0.5}}),
                   encoding="utf-8")
    rows = [
        # 条目权重 0.5 → full
        _row("600000.SH", "20260105", {"kg_boost": {"weight": 0.5}}),
        # 条目权重 0.3 → shadow
        _row("000001.SZ", "20260105", {"kg_boost": {"weight": 0.3}}),
        # 条目权重 0 → off
        _row("600001.SH", "20260105", {"kg_boost": {"weight": 0}}),
        # 条目无 weight → 回退 registry 0.5 → full
        _row("600002.SH", "20260105", {"chan_monitor": {}}),
        # 无贡献 → off
        _row("600003.SH", "20260105", {}),
    ]
    quotes = {(r["symbol"], "20260105"): {"close": 10.0, "t1": 10.0, "t5": 10.0}
              for r in rows}
    doc = cp.build_combined_posterior(rows=rows, quote_fetcher=_fetcher(quotes),
                                      registry_path=str(reg))
    g = doc["per_weight_group"]
    assert g["full"]["count"] == 2
    assert g["shadow"]["count"] == 1
    assert g["off"]["count"] == 2
    # 0 权重与缺贡献均归 off
    assert cp.weight_group_of(0.5) == "full"
    assert cp.weight_group_of(0.3) == "shadow"
    assert cp.weight_group_of(0) == "off"
    assert cp.weight_group_of(None) == "off"


# ── 10. trade_dates 过滤 ──────────────────────────────────────────────
def test_trade_dates_filter():
    doc = cp.build_combined_posterior(
        trade_dates=["20260105"], rows=_fixture_rows(),
        quote_fetcher=_fetcher(_fixture_quotes()))
    assert doc["total_rows"] == 2
    assert doc["asof"] == "20260105"
    assert doc["divergence_group"]["count"] == 0
    assert "chan_monitor" not in doc["per_strategy"] or \
        doc["per_strategy"]["chan_monitor"]["count"] == 1
