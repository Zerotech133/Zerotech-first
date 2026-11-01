# -*- coding: utf-8 -*-
"""tools/tests/test_daily_screening.py — 每日筛选流水线（dry-run，不注册定时任务）。

覆盖（任务验收 ≥5）：
  1. 最新交易日探测：daily/d_YYYYMMDD.parquet 取最大日期（忽略非法文件名）；
  2. 串联结构 schema：assemble_payload 含 日期/过滤统计/S02 研究排序/H18/量学/RS 段；
  3. dry-run：run(dry_run=True) 不写文件、outputs=None、payload.dry_run=True；
  4. 正常写盘：生成 daily_screening_<date>.json/md，JSON schema 合法、MD 含各段；
  5. RS 可选：--rs-mode C 时 RS 段 enabled/通过数/交集正确，off 时为未启用；
  6. 非法 RS 模式报错；MD 中 RS off 有说明。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_daily_screening.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))


def _load():
    p = ROOT / "tools" / "research" / "daily_screening.py"
    spec = importlib.util.spec_from_file_location("daily_screening_under_test", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()
scr = mod.scr


def _sources() -> dict:
    return {
        "selection": {"input": 5571, "eligible_after_S01": 2583,
                      "filters_dropped": {"ST": 120},
                      "picks": [{"ts_code": "000001.SZ", "name": "平安银行",
                                 "industry": "银行", "score": 1.23, "reason": "demo"}]},
        "h18": {"rule": "预增/预减", "count": 1,
                "items": [{"ts_code": "000001.SZ", "ann_date": "20261030",
                           "type": "预增", "direction": "改善", "delta_pp": 100.0}]},
        "pinxue": {"factors": {"LX20_牛股三绝": {"factor": "f1", "signal_count": 2,
                                                 "sample_codes": ["A.SZ", "B.SZ"]}},
                   "signals": {"A.SZ": ["LX20_牛股三绝"]},
                   "union_count": 1},
        "daily": pd.DataFrame({"ts_code": ["A.SZ", "B.SZ", "C.SZ"],
                               "pct_chg": [2.0, 5.0, 1.0]}),
        "nm": {"A.SZ": {"name": "甲", "industry": "行业A"},
               "B.SZ": {"name": "乙", "industry": "行业A"},
               "C.SZ": {"name": "丙", "industry": "行业A"}},
    }


# ------------------------------------------------------------------ 1. 最新交易日
def test_latest_trade_date(tmp_path, monkeypatch):
    for d in ("20261028", "20261029", "20261030"):
        pd.DataFrame({"ts_code": ["A"], "pct_chg": [1.0]}).to_parquet(
            tmp_path / f"d_{d}.parquet")
    (tmp_path / "d_bad.parquet").write_bytes(b"x")
    monkeypatch.setattr(scr, "DAILY_DIR", tmp_path)
    assert mod.latest_date() == "20261030"


# ------------------------------------------------------------------ 2. 串联 schema
def test_assemble_payload_schema():
    src = _sources()
    payload = mod.assemble_payload("20261030", src["selection"], src["h18"], src["pinxue"])
    for key in ("schema_version", "kind", "generated_at", "date", "universe",
                "S02_ranking_research_only", "S03_H18_watch", "pinxue_signals",
                "RS_overlay", "boundary_notes", "disclaimer"):
        assert key in payload, f"payload 缺字段 {key}"
    assert payload["date"] == "20261030"
    assert payload["universe"]["input"] == 5571
    assert payload["universe"]["eligible_after_S01"] == 2583
    assert payload["S02_ranking_research_only"]["picks"][0]["ts_code"] == "000001.SZ"
    assert payload["S03_H18_watch"]["count"] == 1
    assert payload["pinxue_signals"]["union_count"] == 1
    assert payload["RS_overlay"]["enabled"] is False      # 默认 off
    json.dumps(payload, ensure_ascii=False)


# ------------------------------------------------------------------ 3. dry-run 不写
def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "reports"
    payload = mod.run(date="20261030", dry_run=True, out_dir=out, sources=_sources())
    assert payload["dry_run"] is True and payload["outputs"] is None
    assert not out.exists() or not list(out.glob("daily_screening_*"))


# ------------------------------------------------------------------ 4. 正常写盘
def test_run_writes_json_and_md(tmp_path):
    out = tmp_path / "reports"
    payload = mod.run(date="20261030", dry_run=False, out_dir=out, sources=_sources())
    jp = Path(payload["outputs"]["json"])
    mp = Path(payload["outputs"]["md"])
    assert jp.name == "daily_screening_20261030.json"
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["kind"] == "daily_screening" and data["date"] == "20261030"
    md = mp.read_text(encoding="utf-8")
    for needle in ("S01 过滤统计", "S02 研究排序", "H18", "量学四因子", "量学信号 ∩ RS", "边界"):
        assert needle in md, f"MD 缺段落 {needle}"
    assert "RS 未启用" in md


# ------------------------------------------------------------------ 5. RS 可选
def test_rs_mode_c_overlay(tmp_path):
    src = _sources()
    payload = mod.run(date="20261030", rs_mode="C", rs_top=30, dry_run=True,
                      out_dir=tmp_path, sources=src)
    rs = payload["RS_overlay"]
    assert rs["enabled"] is True and rs["mode"] == "C"
    assert rs["universe"] == {"stocks": 3, "boards": 1}
    assert rs["pass_count"] == 3          # 行业A 等权 2.667%≥1 且三只个股 ≥1%
    assert rs["intersection_count"] == 1  # A.SZ 有量学信号
    it = rs["intersection"][0]
    for key in ("ts_code", "name", "industry", "pct_chg", "board_pct_chg",
                "excess_pp", "rs_flag", "signals"):
        assert key in it
    # off 时无 RS 计算
    off = mod.run(date="20261030", rs_mode="off", dry_run=True, out_dir=tmp_path, sources=src)
    assert off["RS_overlay"]["enabled"] is False


# ------------------------------------------------------------------ 6. 非法模式 / MD 说明
def test_invalid_mode_and_md_note():
    with pytest.raises(ValueError):
        mod.run(date="20261030", rs_mode="X", dry_run=True, sources=_sources())
    md = mod.render_md(mod.assemble_payload(
        "20261030", _sources()["selection"], _sources()["h18"], _sources()["pinxue"]))
    assert "RS 未启用" in md
