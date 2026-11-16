# -*- coding: utf-8 -*-
"""F1 基本面画像测试（2026-11-16 WO-FIN-PROFILE）。

在 tmp 造小 parquet，覆盖：有数据 / 无记录 / 字段缺失 / NaN / 滞后 warning / 白名单。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "finance_profile_t", REPO / "tools" / "finance_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fp = _load()


def _write(dirpath: Path, name: str, rows: list[dict]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dirpath / ("%s.parquet" % name))


def _fina_rows(code="600000.SH"):
    base = {"ts_code": code, "ann_date": "20260828", "end_date": "20260630",
            "roe": 8.5, "roe_dt": 8.1, "grossprofit_margin": 30.2, "netprofit_margin": 12.4,
            "debt_to_assets": 45.0, "current_ratio": 1.8, "or_yoy": 15.0,
            "netprofit_yoy": 9.0, "q_sales_yoy": 12.0, "ocf_yoy": 20.0,
            "ocfps": 1.2, "cash_ratio": 0.7}
    older = dict(base, end_date="20251231", ann_date="20260401", roe=7.0)
    return [older, base]


def test_profile_full_when_data_present(tmp_path, monkeypatch):
    cur = tmp_path / "tushare_curated"
    monkeypatch.setattr(fp, "CURATED", cur)
    _write(cur, "fina_indicator", _fina_rows())
    _write(cur, "daily_basic", [{"ts_code": "600000.SH", "trade_date": "20261114",
                                 "pe_ttm": 12.3, "pb": 1.1, "ps_ttm": 2.2, "turnover_rate": 0.8}])
    _write(cur, "stock_basic", [{"ts_code": "600000.SH", "name": "浦发银行",
                                 "industry": "银行", "market": "主板", "list_date": "19991110"}])
    p = fp.profile("600000.SH")
    assert p["available"] is True
    assert p["profitability"]["roe"] == 8.5
    assert p["growth"]["revenue_yoy"] == 15.0
    assert p["valuation"]["pe_ttm"] == 12.3
    assert p["basic"]["industry"] == "银行"
    assert len(p["trend"]) == 2
    assert p["report"]["end_date"] == "20260630"


def test_profile_no_record_returns_unavailable(tmp_path, monkeypatch):
    cur = tmp_path / "tushare_curated"
    monkeypatch.setattr(fp, "CURATED", cur)
    _write(cur, "fina_indicator", _fina_rows(code="600000.SH"))   # 只有别的代码
    p = fp.profile("999999.SZ")
    assert p["available"] is False
    assert p["warnings"] and "无此代码记录" in p["warnings"][0]


def test_profile_missing_file_returns_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "CURATED", tmp_path / "empty")       # 目录都不存在
    p = fp.profile("600000.SH")
    assert p["available"] is False


def test_profile_drops_nan_fields(tmp_path, monkeypatch):
    cur = tmp_path / "tushare_curated"
    monkeypatch.setattr(fp, "CURATED", cur)
    row = dict(_fina_rows()[1])
    row["roe"] = None
    row["netprofit_margin"] = float("nan")
    _write(cur, "fina_indicator", [row])
    p = fp.profile("600000.SH")
    assert p["profitability"]["roe"] is None          # 缺就是 None，不补 0
    assert p["profitability"]["net_margin"] is None


def test_profile_warns_on_stale_report(tmp_path, monkeypatch):
    cur = tmp_path / "tushare_curated"
    monkeypatch.setattr(fp, "CURATED", cur)
    row = dict(_fina_rows()[1], end_date="20250101")   # 远超 120 天
    _write(cur, "fina_indicator", [row])
    p = fp.profile("600000.SH")
    assert any("未入库" in w for w in p["warnings"])


def test_profile_notes_missing_valuation(tmp_path, monkeypatch):
    cur = tmp_path / "tushare_curated"
    monkeypatch.setattr(fp, "CURATED", cur)
    _write(cur, "fina_indicator", _fina_rows())        # 故意不写 daily_basic
    p = fp.profile("600000.SH")
    assert "valuation" not in p
    assert any("估值缺失" in w for w in p["warnings"])


def test_slim_fin_whitelist_in_dispatch():
    """分发侧 slim_fin 只保留 F1/T2 用得上的字段。"""
    spec = importlib.util.spec_from_file_location(
        "rd_fin_t", REPO / "tools" / "research_dispatch.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    raw = {"code": "X", "source": "drop-me", "generated": "drop-me",
           "report": {"end_date": "20260630"}, "profitability": {"roe": 1},
           "growth": {}, "solvency": {}, "cash": {}, "valuation": {"pe_ttm": 9},
           "basic": {"industry": "I"}, "warnings": [],
           "trend": [{"i": 1}, {"i": 2}, {"i": 3}, {"i": 4}, {"i": 5}]}
    s = rd.slim_fin(raw)
    assert "source" not in s and "generated" not in s and "code" not in s
    assert s["profitability"]["roe"] == 1 and s["valuation"]["pe_ttm"] == 9
    assert len(s["trend"]) == 4              # 只留最近 4 期
    assert rd.slim_fin({}) == {}
