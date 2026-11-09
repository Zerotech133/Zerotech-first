# -*- coding: utf-8 -*-
"""盘中盯盘：腾讯行情兜底（量比/换手）测试。"""
from __future__ import annotations

import pytest

from analysis import intraday_watch as w


TX_SAMPLE = (
    'v_sh600337="1~ST美克~600337~3.37~3.36~3.37~20511~13021~7453~3.36~2632~3.35~507~'
    '3.34~617~3.33~1733~3.32~922~3.37~502~3.38~511~3.39~791~3.40~774~3.41~813~~'
    '20261105094359~0.01~0.30~3.43~3.36~3.37/20511/6936127~20511~694~0.14~-1.73~~'
    '3.43~3.36~2.08~48.43~48.43~9.13~3.70~3.02~3.30~3020~3.38~-4.00~-2.30~~~0.91~'
    '693.6127~0.0000~0~~~A~GP-A";\n'
)


def test_parse_tencent_payload_extracts_fields():
    rows = w.parse_tencent_payload(TX_SAMPLE)
    assert "sh600337" in rows
    fields = rows["sh600337"]
    assert len(fields) >= 50
    assert fields[w.TX_FIELDS["vol_ratio"]] == "3.30"
    assert fields[w.TX_FIELDS["turnover"]] == "0.14"
    assert fields[w.TX_FIELDS["amount_wan"]] == "694"


def test_fetch_tencent_snapshots_parses_quote(monkeypatch):
    monkeypatch.setattr(w, "_http_get", lambda *a, **k: TX_SAMPLE)
    snaps = w.fetch_tencent_snapshots(["600337.SH", "bad"])
    assert set(snaps) == {"600337.SH"}
    rec = snaps["600337.SH"]
    assert rec["vol_ratio"] == pytest.approx(3.30)
    assert rec["turnover"] == pytest.approx(0.14)
    assert rec["amount"] == pytest.approx(694 * 10000.0)
    assert rec["pct"] == pytest.approx(0.30)
    assert rec["price"] == pytest.approx(3.37)
    assert rec["ts"] == "2026-11-05 09:43:59"


def test_fetch_tencent_snapshots_network_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(w, "_http_get", boom)
    assert w.fetch_tencent_snapshots(["600337.SH"]) == {}


def test_enrich_quotes_uses_tencent(monkeypatch):
    monkeypatch.setattr(w, "fetch_tencent_snapshots", lambda codes: {
        "600337.SH": {"vol_ratio": 3.3, "turnover": 0.14, "amount": 6_940_000.0},
    })
    quotes = [{"code": "600337.SH", "pct": 0.3}, {"code": "605258.SH", "pct": -0.2}]
    out = w.enrich_quotes(quotes, limit=5)
    first = next(q for q in out if q["code"] == "600337.SH")
    assert first["vol_ratio"] == pytest.approx(3.3)
    assert first["turnover"] == pytest.approx(0.14)
    assert first["amount"] == pytest.approx(6_940_000.0)
    second = next(q for q in out if q["code"] == "605258.SH")
    assert "vol_ratio" not in second
