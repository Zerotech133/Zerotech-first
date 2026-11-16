# -*- coding: utf-8 -*-
"""picks_pool 单测：出票读取的「今天」校验、行尾纪律、代码归一。

判例来源（2026-11-16）：
  - CRLF 代码清单经 shell 传递后每只票尾随 \\r → 649 只全判「不适用该源」，报告看着像零缺口；
  - 08:00 去读「今天」的出票读到的是空（中台 18:20 出票），不校验就会把上一交易日
    的清单当今日清单用。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import picks_pool as P  # noqa: E402


@pytest.fixture()
def pickdir(tmp_path, monkeypatch):
    d = tmp_path / "picks_history"
    d.mkdir()
    payload = {
        "generated_at": "2026-11-13 18:20:16",
        "trade_basis": "2026-11-13 收盘",
        "counts": {"total": 3},
        "stocks": [
            {"code": "000001.SZ", "name": "平安银行", "tier": "T2", "n_hit": 2,
             "sources": [{"s": "chanlun", "tag": "1买"}, {"s": "qpzm"}]},
            {"code": "600519.SH", "name": "贵州茅台", "tier": "T3", "n_hit": 1,
             "sources": [{"s": "chanlun", "tag": "3买"}]},
            {"code": "600519.SH", "name": "贵州茅台", "tier": "T3", "n_hit": 1,
             "sources": [{"s": "weak"}]},
        ],
    }
    (d / "todays_20261113.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(P, "PICK_DIR", str(d))
    return d


def test_norm6_strips_cr_and_suffix():
    assert P.norm6("000011\r") == "000011"
    assert P.norm6("000001.SZ") == "000001"
    assert P.norm6("abc") == ""


def test_list_picks_only_todays(pickdir):
    assert list(P.list_picks()) == ["20261113"]


def test_load_picks_defaults_to_latest(pickdir):
    rep, recs = P.load_picks()
    assert rep["date"] == "20261113"
    assert rep["n_rows"] == 3
    assert rep["n_codes"] == 2, "同码重复行应去重计数"


def test_load_picks_rejects_missing_date(pickdir):
    with pytest.raises(ValueError):
        P.load_picks("20261116")


def test_staleness_flags_non_today(pickdir):
    rep, _ = P.load_picks()
    rep = P.staleness(rep, today="20261116")
    assert rep["is_today"] is False
    assert "尚未生成" in rep["note"]


def test_staleness_marks_today(pickdir):
    rep, _ = P.load_picks()
    rep = P.staleness(rep, today="20261113")
    assert rep["is_today"] is True


def test_write_codes_is_lf_sorted_unique(pickdir, tmp_path):
    _, recs = P.load_picks()
    out = tmp_path / "codes.txt"
    codes = P.write_codes(recs, str(out))
    raw = out.read_bytes()
    assert b"\r" not in raw, "CRLF 会让下游逐票判定全部落空"
    assert codes == ["000001", "600519"]
    assert raw == b"000001\n600519\n"


def test_min_hit_filter_via_main(pickdir, tmp_path, capsys):
    out = tmp_path / "hit2.txt"
    rc = P.main(["--min-hit", "2", "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8").split() == ["000001"]
