# -*- coding: utf-8 -*-
"""HQ 数据简报：数据健康 + 挖掘信号 + 决策问题。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "datawarehouse"))
import hq_brief as H  # noqa: E402


def _inputs():
    dw = {
        "state": "ok", "data_date": "2026-11-02",
        "catalog_version": 38, "catalog_tables": 304,
        "catalog_updated": "2026-11-04T03:59:30+08:00",
        "registry_physical": 304, "registry_display": 258,
        "contract_artifacts": {"total": 4, "active": 1, "mismatch": 3},
        "stats_v2": {
            "file_inventory": {"total": 314, "registered": 265, "exposed": 72},
            "pg": {"total": 84, "registered": 84, "available": 84},
            "all": {"total": 399, "registered": 350, "exposed": 80},
        },
    }
    ops = {"collectors": [
        {"name": "TDX", "level": "red", "lag_hours": 58.0, "last_output_at": "2026-11-01 19:15:37"},
        {"name": "Xueqiu", "level": "green", "today_incr": 0,
         "last_output_at": "2026-11-03 18:00:44", "output_metrics": {"xq": {}}},
    ], "pools": [{"db": "tdx/tdx_info.db",
                  "tables": [{"name": "stock_notice", "level": "red", "lag_hours": 58.0}]}]}
    factor = {"latest_date": "20261102", "owner": "FACTOR1",
              "factors": [{"factor": "mom_20", "verdict": "四关 PASS（LS）"},
                          {"factor": "rev_5", "verdict": "四关 FAIL"}],
              "decay_alerts": [{"factor": "rev_5"}]}
    selection = {"date": "20261102", "picks": [],
                 "strategies": [{"id": "S01", "approved": False, "status": "validated_filter_no_alpha"}]}
    learning = {"overall": {"files": 8, "units": 800, "candidate_pool": 539,
                            "cards_total": 370, "units_with_domain": 258}}
    return dw, ops, factor, selection, learning


def test_data_brief_has_health_alarms_signals_questions():
    # 19:15 是采集窗口（18:00）之后：当日增量为 0 才应报 today_zero
    b = H.build_brief(*_inputs(), now="2026-11-04T19:15:00+08:00")
    assert b["schema"] == H.SCHEMA and b["owner"]["name"] == "张松"
    assert b["data_health"]["assets"]["all"] == 399
    assert b["data_health"]["catalog"]["tables"] == 304
    assert any(a["code"] == "collector_lag" for a in b["alarms"])
    assert any(a["code"] == "today_zero" for a in b["alarms"])
    assert any(s["id"] == "factor.decay" for s in b["mining_signals"])
    assert any(s["id"] == "selection.executability" for s in b["mining_signals"])
    assert any(s["id"] == "learning.yield" for s in b["mining_signals"])
    assert any(q["to"] == "DATA1" for q in b["questions"])
    # 边界必须显式，防止信号被当交易建议
    assert any("不是交易建议" in x for x in b["boundaries"])
    assert json.dumps(b, ensure_ascii=False)  # 可序列化


def test_data_brief_does_not_leak_raw_paths():
    b = H.build_brief(*_inputs(), now="2026-11-04T05:15:00+08:00")
    blob = json.dumps(b, ensure_ascii=False)
    assert "C:\\" not in blob and "quant_backtest/data" not in blob


def test_catalog_registry_mismatch_is_red():
    dw, ops, factor, selection, learning = _inputs()
    dw["catalog_tables"] = 256
    b = H.build_brief(dw, ops, factor, selection, learning, now="2026-11-04T07:10:00+08:00")
    assert any(a["code"] == "catalog_registry_mismatch" and a["severity"] == "red" for a in b["alarms"])
    assert any(q["to"] == "DATA1" and "registry" in q["q"] for q in b["questions"])


# ---------- R12 D2：catalog / registry 必须同单位比较（物理表 vs 资产全量） ----------

def test_catalog_registry_same_scope_no_red_alarm():
    """catalog 258 == display 258，physical 306 是资产全量 → 不得再报红（原假红根因）。"""
    dw, ops, factor, selection, learning = _inputs()
    dw.update({"catalog_tables": 258, "registry_physical": 306, "registry_display": 258})
    b = H.build_brief(dw, ops, factor, selection, learning, now="2026-11-06T10:40:00+08:00")
    assert not any(a["code"] == "catalog_registry_mismatch" for a in b["alarms"])
    drift = [a for a in b["alarms"] if a["code"] == "catalog_registry_drift"]
    assert drift and drift[0]["severity"] == "info"
    assert drift[0]["evidence"]["physical_minus_display"] == 48
    assert b["data_health"]["registry"]["drift"]["registry_physical"] == 306
    assert not any("registry" in q["q"] for q in b["questions"])


def test_catalog_registry_display_mismatch_is_red():
    """同口径（display）真不一致时必须报红——防未来真漂移。"""
    dw, ops, factor, selection, learning = _inputs()
    dw.update({"catalog_tables": 258, "registry_physical": 306, "registry_display": 260})
    b = H.build_brief(dw, ops, factor, selection, learning, now="2026-11-06T10:40:00+08:00")
    red = [a for a in b["alarms"] if a["code"] == "catalog_registry_mismatch"]
    assert red and red[0]["severity"] == "red"
    assert red[0]["evidence"]["registry_display"] == 260
    assert not any(a["code"] == "catalog_registry_drift" for a in b["alarms"])
    assert any(q["to"] == "DATA1" and "registry display" in q["q"] for q in b["questions"])


# ---------- R12 D3：当日增量必须区分「窗口未到」与「断流」 ----------

def test_today_zero_before_collect_window_is_pending_not_alarm():
    """10:40 查当日增量必为 0——属时点常态，不得报 amber today_zero（原实现天天误报）。"""
    b = H.build_brief(*_inputs(), now="2026-11-06T10:40:00+08:00")
    assert not any(a["code"] == "today_zero" for a in b["alarms"])
    pend = [a for a in b["alarms"] if a["code"] == "today_pending"]
    assert pend and pend[0]["severity"] == "info"
    assert b["data_health"]["freshness"]["collect_window_open"] is False


def test_today_zero_after_collect_window_is_amber():
    b = H.build_brief(*_inputs(), now="2026-11-06T18:30:00+08:00")
    z = [a for a in b["alarms"] if a["code"] == "today_zero"]
    assert z and z[0]["severity"] == "amber"
    assert not any(a["code"] == "today_pending" for a in b["alarms"])
    assert b["data_health"]["freshness"]["collect_window_open"] is True


def test_freshness_separates_inventory_date_from_trade_date():
    """data_date 是盘点文件 mtime，不是交易日期——两者必须各自带来源标注。"""
    dw, ops, factor, selection, learning = _inputs()
    dw.update({"data_date": "2026-11-02", "data_date_source": "inventory_mtime",
               "inventory_age_days": 4, "latest_trade_date": "2026-11-05",
               "latest_trade_date_source": "inventory_max_latest（盘点时点口径，非实时）"})
    b = H.build_brief(dw, ops, factor, selection, learning, now="2026-11-06T10:40:00+08:00")
    fr = b["data_health"]["freshness"]
    assert fr["data_date"] == "2026-11-02" and fr["data_date_source"] == "inventory_mtime"
    assert fr["inventory_age_days"] == 4 and fr["age_days"] == 4
    assert fr["latest_trade_date"] == "2026-11-05"
    assert "盘点时点口径" in fr["latest_trade_date_source"]
    # 提问措辞必须说清是「盘点版本」而非「数据断流」
    q = [x["q"] for x in b["questions"] if x["to"] == "DATA1" and "盘点" in x["q"]]
    assert q and "不代表数据断流" in q[0]
