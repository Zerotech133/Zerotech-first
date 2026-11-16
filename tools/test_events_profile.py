# -*- coding: utf-8 -*-
"""事件资料测试（2026-11-16 WO-EVENTS-PROFILE）。

覆盖：分类词表 / 窗口过滤 / 倒序 / attention / 空窗口 / 无记录 / slim 白名单。
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
        "events_profile_t", REPO / "tools" / "events_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ep = _load()
CODE = "300476.SZ"


def _write(dirpath: Path, rows: list[dict]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                   dirpath / "anns_d.parquet")


def _ann(day: str, title: str) -> dict:
    return {"ann_date": day, "ts_code": CODE, "name": "测试", "title": title}


def test_classify_categories():
    assert ep._classify("2026年三季度报告") == "业绩"
    assert ep._classify("关于股东减持计划的预披露公告") == "减持"
    assert ep._classify("关于回购公司股份的公告") == "增持回购"
    assert ep._classify("关于收到监管问询函的公告") == "监管问询"
    assert ep._classify("关于重大资产重组的进展公告") == "重组并购"
    assert ep._classify("第五届董事会决议公告") == "其他"


def test_window_filter_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "CURATED", tmp_path / "cur")
    _write(tmp_path / "cur", [
        _ann("20240101", "老公告"),                       # 窗口外
        _ann("20261016", "关于回购公司股份的公告"),
        _ann("20261030", "2026年三季度报告"),
    ])
    p = ep.profile(CODE, day="2026-11-16")
    assert p["available"] is True
    assert p["total"] == 3
    assert p["window_count"] == 2                      # 只算 90 天
    assert p["by_category"]["业绩"] == 1
    assert p["by_category"]["增持回购"] == 1
    assert p["last_date"] == "20261030"


def test_recent_is_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "CURATED", tmp_path / "cur")
    _write(tmp_path / "cur", [
        _ann("20261001", "早的公告"),
        _ann("20261020", "晚的公告"),
    ])
    p = ep.profile(CODE, day="2026-11-16")
    assert [r["date"] for r in p["recent"]] == ["20261020", "20261001"]


def test_attention_categories_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "CURATED", tmp_path / "cur")
    _write(tmp_path / "cur", [
        _ann("20261001", "关于股东减持计划的公告"),
        _ann("20261002", "关于收到监管问询函的公告"),
        _ann("20261003", "董事会决议"),
    ])
    p = ep.profile(CODE, day="2026-11-16")
    assert set(p["attention_categories"]) == {"减持", "监管问询"}
    assert "需自行判断影响" in p["attention_note"]


def test_empty_window_warns_not_silent(tmp_path, monkeypatch):
    """窗口内无公告 ≠ 无事项，必须给 warning。"""
    monkeypatch.setattr(ep, "CURATED", tmp_path / "cur")
    _write(tmp_path / "cur", [_ann("20200101", "很久以前")])
    p = ep.profile(CODE, day="2026-11-16")
    assert p["window_count"] == 0
    assert any("不等于无事项" in w for w in p["warnings"])


def test_no_record_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "CURATED", tmp_path / "empty")
    p = ep.profile(CODE, day="2026-11-16")
    assert p["available"] is False and "无此代码公告记录" in p["warnings"][0]


def test_slim_events_whitelist():
    spec = importlib.util.spec_from_file_location(
        "rd_ev_t", REPO / "tools" / "research_dispatch.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    raw = {"code": "X", "source": "drop", "generated": "drop", "as_of": "drop",
           "window_days": 90, "window_count": 2, "last_date": "20261030",
           "by_category": {"业绩": 1}, "recent": [{"t": 1}], "warnings": []}
    s = rd.slim_events(raw)
    assert set(s) == {"window_days", "window_count", "last_date", "by_category", "recent"}
    assert rd.slim_events({}) == {}
