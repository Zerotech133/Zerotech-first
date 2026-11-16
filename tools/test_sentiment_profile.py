# -*- coding: utf-8 -*-
"""N1 舆情资料测试（2026-11-16 WO-SENT-PROFILE）。

tmp 造小 parquet，覆盖：互动易窗口统计 / 风险词命中 / 户数变化 / 资金流全零告警 / 无数据。
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
        "sentiment_profile_t", REPO / "tools" / "sentiment_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = _load()
CODE = "600000.SH"


def _write(dirpath: Path, name: str, rows: list[dict]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dirpath / ("%s.parquet" % name))


def _qa(day: str, q: str, a: str = "感谢您的关注") -> dict:
    return {"ts_code": CODE, "trade_date": day, "q": q, "a": a, "pub_time": day}


def test_qa_window_and_risk_words(tmp_path, monkeypatch):
    cur = tmp_path / "curated"
    monkeypatch.setattr(sp, "CURATED", cur)
    _write(cur, "irm_qa_sz", [
        _qa("20250101", "很久以前的问题"),                       # 窗口外
        _qa("20261101", "请问股东人数？", "截至11月10日为1.2万户"),
        _qa("20261110", "公司是否有减持计划？", ""),              # 命中风险词 + 无实质答复
    ])
    p = sp.profile(CODE, day="2026-11-16")
    qa = p["qa"]
    assert qa["total_qa"] == 3
    assert qa["window_qa"] == 2                               # 只算 180 天内
    assert qa["window_answered"] == 1                         # 空答复不计
    assert "减持" in qa["risk_word_hits"]
    assert qa["last_date"] == "20261110"


def test_holder_change_direction(tmp_path, monkeypatch):
    cur = tmp_path / "curated"
    monkeypatch.setattr(sp, "CURATED", cur)
    _write(cur, "stk_holdernumber", [
        {"ts_code": CODE, "ann_date": "20260801", "end_date": "20260731", "holder_num": 10000.0},
        {"ts_code": CODE, "ann_date": "20260901", "end_date": "20260831", "holder_num": 9000.0},
    ])
    p = sp.profile(CODE, day="2026-11-16")
    h = p["holders"]
    assert h["holder_num"] == 9000.0 and h["change_pct"] == -10.0
    assert "集中" in h["note"]


def test_moneyflow_all_zero_warns(tmp_path, monkeypatch):
    """全零不能当成'资金平衡'，必须告警且标注不据以判断。"""
    cur = tmp_path / "curated"
    monkeypatch.setattr(sp, "CURATED", cur)
    _write(cur, "moneyflow", [
        {"ts_code": CODE, "trade_date": d, "buy_lg_amount": 0.0, "sell_lg_amount": 0.0,
         "buy_elg_amount": 0.0, "sell_elg_amount": 0.0} for d in ("20261112", "20261113")
    ])
    p = sp.profile(CODE, day="2026-11-16")
    assert p["moneyflow"]["main_net_wan"] == 0.0
    assert "全零" in p["moneyflow"]["note"]
    assert any("全为 0" in w for w in p["warnings"])


def test_moneyflow_nonzero(tmp_path, monkeypatch):
    cur = tmp_path / "curated"
    monkeypatch.setattr(sp, "CURATED", cur)
    _write(cur, "moneyflow", [
        {"ts_code": CODE, "trade_date": "20261113", "buy_lg_amount": 3e6, "sell_lg_amount": 1e6,
         "buy_elg_amount": 2e6, "sell_elg_amount": 0.0}
    ])
    p = sp.profile(CODE, day="2026-11-16")
    assert p["moneyflow"]["main_net_wan"] == 400.0            # (3+2-1-0) 百万 → 400 万
    assert "note" not in p["moneyflow"]


def test_profile_unavailable_when_no_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "CURATED", tmp_path / "empty")
    p = sp.profile(CODE, day="2026-11-16")
    assert p["available"] is False
    assert len(p["warnings"]) >= 2


def test_slim_sent_whitelist_in_dispatch():
    spec = importlib.util.spec_from_file_location(
        "rd_sent_t", REPO / "tools" / "research_dispatch.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    raw = {"code": "X", "source": "drop", "as_of": "drop", "generated": "drop",
           "qa": {"window_qa": 3}, "holders": {"holder_num": 1}, "warnings": ["w"]}
    s = rd.slim_sent(raw)
    assert set(s) == {"qa", "holders", "warnings"}
    assert rd.slim_sent({}) == {}
