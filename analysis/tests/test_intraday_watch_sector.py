# -*- coding: utf-8 -*-
"""`analysis.intraday_watch` 板块/东财映射修复的补充单测（离线）。

覆盖：板块榜分页取全量、拉取失败保留部分、东财 f127 行业名解析、
行业名当日缓存、板块规则优先用 em_industry、跌幅榜触发、板块历史只写池内板块、
板块榜节流缓存。
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])
if WT not in sys.path:
    sys.path.insert(0, WT)

from analysis import intraday_watch as iw  # noqa: E402

CST = iw.CST
NOW = datetime(2026, 11, 5, 10, 0, 0, tzinfo=CST)


def flat_cfg(**over):
    cfg = json.loads(json.dumps(iw.DEFAULT_CONFIG))
    cfg.update({"stops": {}, "cooldown_min": {k: 0 for k in cfg["cooldown_min"]}})
    cfg.update(over)
    return cfg


def sector_payload(total, rows):
    return json.dumps({"data": {"total": total, "diff": rows}}, ensure_ascii=False)


def sector_row(idx, pct=None):
    return {"f12": "BK%04d" % idx, "f14": "板块%04d" % idx,
            "f3": float(pct if pct is not None else 100 - idx), "f62": 0}


def test_fetch_sectors_paginates_until_total(monkeypatch):
    calls = []

    def fake_get(url, timeout=10, headers=None):
        page = int(re.search(r"pn=(\d+)", url).group(1))
        calls.append(page)
        if page == 1:
            return sector_payload(250, [sector_row(i) for i in range(100)])
        if page == 2:
            return sector_payload(250, [sector_row(i) for i in range(100, 200)])
        return sector_payload(250, [sector_row(i) for i in range(200, 250)])

    monkeypatch.setattr(iw, "_http_get", fake_get)
    out = iw.fetch_sectors()
    assert len(out) == 250
    assert calls == [1, 2, 3]
    assert [s["pct"] for s in out] == sorted((s["pct"] for s in out), reverse=True)
    assert out[0]["name"] == "板块0000"


def test_fetch_sectors_keeps_partial_on_page_error(monkeypatch):
    def fake_get(url, timeout=10, headers=None):
        page = int(re.search(r"pn=(\d+)", url).group(1))
        if page == 1:
            return sector_payload(496, [sector_row(i) for i in range(100)])
        raise RuntimeError("boom")

    monkeypatch.setattr(iw, "_http_get", fake_get)
    out = iw.fetch_sectors()
    assert len(out) == 100  # 不抛异常，保留已取到的部分


def test_fetch_em_snapshot_parses_em_fields(monkeypatch):
    payload = {"data": {"f43": 3.36, "f48": 12345678.0, "f50": 0.84, "f168": 0.6,
                        "f170": 2.44, "f127": "家居用品", "f128": "江西板块",
                        "f129": "电商概念,智能家居"}}

    def fake_get(url, timeout=10, headers=None):
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(iw, "_http_get", fake_get)
    snap = iw.fetch_em_snapshot("600337.SH")
    assert snap["em_industry"] == "家居用品"
    assert snap["em_region"] == "江西板块"
    assert snap["em_concepts"] == ["电商概念", "智能家居"]
    assert snap["vol_ratio"] == 0.84


def test_ensure_em_industries_caches_per_day(monkeypatch, tmp_path):
    calls = []

    def fake_snapshot(code, timeout=8):
        calls.append(code)
        return {"em_industry": "家居用品", "em_concepts": ["电商概念"]}

    monkeypatch.setattr(iw, "fetch_em_snapshot", fake_snapshot)
    store = iw.StateStore(path=str(tmp_path / "s.db"))
    try:
        quotes = [{"code": "600337.SH"}]
        iw.ensure_em_industries(quotes, store)
        assert quotes[0]["em_industry"] == "家居用品"
        assert len(calls) == 1
        # 同一交易日第二轮：应命中缓存，不再请求
        quotes2 = [{"code": "600337.SH"}]
        iw.ensure_em_industries(quotes2, store)
        assert quotes2[0]["em_industry"] == "家居用品"
        assert len(calls) == 1
    finally:
        store.close()


def test_sector_rule_prefers_em_industry(monkeypatch):
    sectors = [{"code": "BK1", "name": "家居用品", "pct": 5.0, "net_inflow": 0}]
    sectors += [{"code": "BK%02d" % i, "name": "板块%02d" % i, "pct": 0.0,
                 "net_inflow": 0} for i in range(2, 10)]

    def boom(*args, **kwargs):
        raise AssertionError("有 em_industry 时不应回退申万映射")

    monkeypatch.setattr(iw, "sector_map_for", boom)
    quote = {"code": "600337.SH", "name": "ST美克", "price": 3.36, "pct": 2.44,
             "em_industry": "家居用品"}
    store = iw.StateStore(dry_run=True)
    try:
        out = iw._eval_sectors([quote], sectors, store, flat_cfg(), NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"),
                               NOW.strftime("%Y-%m-%d"))
    finally:
        store.close()
    assert len(out) == 1
    assert out[0].rule == "sector"
    assert out[0].sector["name"] == "家居用品"
    assert out[0].sector["rank"] == 1


def test_sector_bottom_rank_triggers(monkeypatch):
    sectors = [{"code": "BK%02d" % i, "name": "板块%02d" % i, "pct": 1.0,
                "net_inflow": 0} for i in range(9)]
    sectors.append({"code": "BK99", "name": "电信运营商", "pct": -3.27, "net_inflow": 0})
    quote = {"code": "600337.SH", "name": "ST美克", "price": 3.36, "pct": -2.0,
             "em_industry": "电信运营商"}
    store = iw.StateStore(dry_run=True)
    try:
        out = iw._eval_sectors([quote], sectors, store, flat_cfg(), NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"),
                               NOW.strftime("%Y-%m-%d"))
    finally:
        store.close()
    assert any(s.rule == "sector" and s.sector["rank"] == 10 for s in out)


def test_sector_hist_written_only_for_pool_boards(tmp_path):
    sectors = [{"code": "BK%02d" % i, "name": "板块%02d" % i, "pct": float(i),
                "net_inflow": 0} for i in range(20)]
    quote = {"code": "600337.SH", "name": "ST美克", "price": 3.36, "pct": 2.44,
             "em_industry": "板块05"}
    store = iw.StateStore(path=str(tmp_path / "s.db"))
    try:
        iw._eval_sectors([quote], sectors, store, flat_cfg(), NOW,
                         NOW.strftime("%Y-%m-%d %H:%M:%S"), NOW.strftime("%Y-%m-%d"))
        rows = store.con.execute(
            "SELECT key FROM state WHERE key LIKE 'sector_hist:%'").fetchall()
        keys = [r[0] for r in rows]
    finally:
        store.close()
    assert keys == ["sector_hist:板块05"]


def test_sectors_cached_throttles_fetch(monkeypatch, tmp_path):
    calls = []
    fresh = [{"code": "BK1", "name": "新板块", "pct": 9.9, "net_inflow": 0}]

    def fake_fetch(timeout=10, max_pages=8):
        calls.append(1)
        return fresh

    monkeypatch.setattr(iw, "fetch_sectors", fake_fetch)
    store = iw.StateStore(path=str(tmp_path / "s.db"))
    try:
        cached = [{"code": "BK0", "name": "旧板块", "pct": 1.0, "net_inflow": 0}]
        store.set_json("sector_cache", cached)
        store.set_kv("sector_fetch_ts",
                     (NOW - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S"))
        got = iw._sectors_cached(store, NOW, refresh_sec=180)
        assert got == cached and calls == []
        store.set_kv("sector_fetch_ts",
                     (NOW - timedelta(seconds=400)).strftime("%Y-%m-%d %H:%M:%S"))
        got2 = iw._sectors_cached(store, NOW, refresh_sec=180)
        assert got2 == fresh and len(calls) == 1
    finally:
        store.close()
