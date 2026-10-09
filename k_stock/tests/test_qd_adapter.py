# -*- coding: utf-8 -*-
"""双刀 adapter 测试：缓存→产物转换与 regime 否决（FakeMarket 落单链复用引擎用例已覆盖）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

import qd_adapter  # noqa: E402


def _write_cache(tmp_path, signals):
    p = tmp_path / "qd_scan_cache.json"
    p.write_text(json.dumps({"ts": "2026-10-09 09:37:29", "signals_now": signals},
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_cache_to_run_basic_and_regime_veto(tmp_path):
    cache = _write_cache(tmp_path, [
        {"code": "600593.SH", "regime": "range", "name": "大连圣亚", "close": 48.97,
         "tech": "多头排列"},
        {"code": "000001.SZ", "regime": "trend_down", "name": "平安银行", "close": 11.0},
        {"code": "", "regime": "range"},  # 空代码跳过
    ])
    out = qd_adapter.cache_to_run(cache, out_dir=tmp_path)
    items = json.loads(out.read_text(encoding="utf-8"))
    assert len(items) == 1
    assert items[0]["stock"] == "600593.SH"
    assert items[0]["outputs"]["D1"]["action"] == "BUY"
    assert items[0]["outputs"]["D1"]["confidence"] == qd_adapter.DOUBLE_KNIFE_CONF
    assert items[0]["_meta"]["source"] == "double_knife"
    assert out.name.startswith("poc_qd_")


def test_cache_to_run_empty_signals_raises(tmp_path):
    cache = _write_cache(tmp_path, [])
    try:
        qd_adapter.cache_to_run(cache, out_dir=tmp_path)
        assert False, "应 raise"
    except ValueError as e:
        assert "无当日信号" in str(e)


def test_full_drop_into_engine(tmp_path):
    """产物喂给引擎 exec_run（strategy=double_knife 贯通）。"""
    sys.path.insert(0, str(PAPER_DIR))
    from engine import PaperEngine  # noqa: E402
    from market import FakeMarket   # noqa: E402

    cache = _write_cache(tmp_path, [
        {"code": "600519.SH", "regime": "range", "close": 1247.0}])
    run_path = qd_adapter.cache_to_run(cache, out_dir=tmp_path)
    bars = {"600519.SH": [
        {"trade_date": "20261008", "open": 1244.6, "high": 1250, "low": 1240,
         "close": 1247.0, "pre_close": 1246.2},
        {"trade_date": "20261009", "open": 1250.0, "high": 1255, "low": 1245,
         "close": 1252.0, "pre_close": 1247.0},
    ]}
    eng = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    r = eng.exec_run(run_path, strategy="double_knife")
    assert len(r["orders"]) == 1 and r["orders"][0]["status"] == "pending"
    od = eng.con.execute("SELECT strategy, conf FROM orders").fetchone()
    assert od["strategy"] == "double_knife"
    assert od["conf"] == qd_adapter.DOUBLE_KNIFE_CONF
