# -*- coding: utf-8 -*-
"""chanlun_annotate 的结构标注契约测试。

守的是四条语义，不是「跑通就行」：
1. 严格截断 —— 改 d0 之后的 bar，d0 及以前的标注必须逐字节不变；
2. 幂等 —— 同参数两跑逐字节相同；
3. 确认日口径 —— lag_days >= 1，且 visible_at 确实是「首次可见日」
   （前一交易日不存在、当日存在）；
4. schema —— 必填字段齐全、类型正确、结构内部自洽（zg>zd、dir∈{±1}…）。
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import chanlun_annotate as ca  # noqa: E402

CUT = 180          # 截断点：bars[CUT:] 视为「未来」
N_BARS = 260


def _mk_bars(n=N_BARS, seed=11, p0=100.0):
    """确定性随机游走 OHLCV（无外部数据依赖，dt 为工作日）。"""
    rng = random.Random(seed)
    px = p0
    d = date(2024, 1, 2)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            o = px * (1 + rng.uniform(-0.012, 0.012))
            c = o * (1 + rng.uniform(-0.035, 0.035))
            h = max(o, c) * (1 + rng.uniform(0.001, 0.018))
            l = min(o, c) * (1 - rng.uniform(0.001, 0.018))
            out.append({
                "dt": d.isoformat(), "o": round(o, 4), "h": round(h, 4),
                "l": round(l, 4), "c": round(c, 4),
                "v": float(10000 + rng.randint(0, 8000)), "pc": round(px, 4),
            })
            px = c
        d += timedelta(days=1)
    return out


def _lines(rows):
    return [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]


@pytest.fixture(scope="module")
def bars():
    return _mk_bars()


@pytest.fixture(scope="module")
def rows(bars):
    end = bars[CUT - 1]["dt"]
    r, _ = ca.annotate_bars(bars, code="TEST", start=bars[100]["dt"], end=end,
                            window=0)
    return r


def _st(bars, n):
    from chanlun_signal import analyze

    return analyze(bars[:n])


# ── ① 截断：未来数据不得影响 d0 及以前的任何一行 ─────────────
def test_truncation_future_bars_do_not_leak(bars):
    end = bars[CUT - 1]["dt"]
    base, _ = ca.annotate_bars(bars, code="TEST", start=bars[100]["dt"],
                               end=end, window=0)

    # 把 CUT 之后所有 bar 改成另一条随机游走（价格量级完全不同）
    rng = random.Random(4242)
    mutated = [dict(b) for b in bars]
    px = 30.0
    for i in range(CUT, len(mutated)):
        o = px * (1 + rng.uniform(-0.05, 0.05))
        c = o * (1 + rng.uniform(-0.09, 0.09))
        mutated[i].update(o=round(o, 4), c=round(c, 4),
                          h=round(max(o, c) * 1.06, 4),
                          l=round(min(o, c) * 0.94, 4), pc=round(px, 4))
        px = c
    after, _ = ca.annotate_bars(mutated, code="TEST", start=bars[100]["dt"],
                                end=end, window=0)

    assert len(base) == len(after) > 0
    assert [r["date"] for r in base] == [r["date"] for r in after]
    for a, b in zip(_lines(base), _lines(after)):
        assert a == b

    # 再把未来整段删掉（bars 只剩到 d0），同一个 d0 行仍必须一致
    cut_only, _ = ca.annotate_bars(bars[:CUT], code="TEST", start=bars[100]["dt"],
                                   end=end, window=0)
    assert _lines(base) == _lines(cut_only)


# ── ② 幂等：两跑逐字节相同（含落盘） ─────────────────────────
def test_idempotent_bytes(bars, tmp_path):
    end = bars[CUT - 1]["dt"]
    a, _ = ca.annotate_bars(bars, code="TEST", start=bars[100]["dt"], end=end)
    b, _ = ca.annotate_bars(bars, code="TEST", start=bars[100]["dt"], end=end)
    assert _lines(a) == _lines(b)

    p1, p2 = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    ca._write_rows(str(p1), a)
    ca._write_rows(str(p2), b)
    assert p1.read_bytes() == p2.read_bytes()
    # 行内容里不得含时间戳类字段（否则幂等只能靠「排除字段」实现）
    for r in a:
        assert "generated_at" not in r and "elapsed_s" not in r


# ── ③ lag_days >= 1，且 visible_at 是「首次可见日」 ───────────
def test_lag_days_ge_one(rows):
    n = 0
    for r in rows:
        if r["lag_days"] is not None:
            assert r["lag_days"] >= 1
            assert r["visible_at"] <= r["date"]
            n += 1
        for f in r["fractals"]:
            assert f["lag_days"] >= 1 and f["visible_at"] > f["dt"]
        for b in r["bi"]:
            assert b["lag_days"] >= 1 and b["confirm_dt"] > b["i1_dt"]
        for p in r["pivots"]:
            assert p["lag_days"] >= 1 and p["confirm_dt"] > p["end_dt"]
        for t in r["points"]:
            assert t["lag"] >= 1 and t["detect_date"] > t["dt"]
    assert n > 0 and any(r["bi"] for r in rows) and any(r["pivots"] for r in rows)


def test_visible_at_is_first_seen(bars, rows):
    """confirm 当天结构在、前一日不在（lag>1 时前一日必然不在）。"""
    checked = 0
    for r in rows:
        for b in r["bi"]:
            if checked >= 4:
                break
            i1 = next(i for i, x in enumerate(bars) if x["dt"] == b["i1_dt"])
            ic = next(i for i, x in enumerate(bars) if x["dt"] == b["confirm_dt"])
            assert ic - i1 == b["lag_days"] >= 1
            key = ("b", b["i0_dt"], b["i1_dt"])

            def keys_at(n):
                st = _st(bars, n)
                return {("b", x["s_dt"], x["e_dt"]) for x in st["bis"]}

            assert key in keys_at(ic + 1)               # 确认日可见
            assert key not in keys_at(i1)               # 端点的前一日不存在
            if b["lag_days"] > 1:
                assert key not in keys_at(ic)           # 更早一天还看不见
            checked += 1
    assert checked > 0


# ── ④ schema：必填字段齐全且类型正确 ─────────────────────────
def test_schema_required_fields(rows):
    assert rows, "区间内没有产出任何行"
    top = {"schema_version", "code", "date", "visible_at", "lag_days",
           "fractals", "bi", "pivots", "points"}
    for r in rows:
        assert set(r) == top
        assert r["schema_version"] == "ann-v1"
        assert isinstance(r["code"], str) and r["code"] == "TEST"
        assert isinstance(r["date"], str) and len(r["date"]) == 10
        assert isinstance(r["lag_days"], int) and r["lag_days"] >= 1
        for k in ("fractals", "bi", "pivots", "points"):
            assert isinstance(r[k], list)
        for f in r["fractals"]:
            assert set(f) == {"type", "dt", "price", "visible_at", "lag_days"}
            assert f["type"] in ("顶", "底")
            assert isinstance(f["price"], float) and f["price"] > 0
            assert isinstance(f["lag_days"], int)
        for b in r["bi"]:
            assert set(b) == {"i0_dt", "i1_dt", "dir", "high", "low",
                              "confirm_dt", "lag_days"}
            assert b["dir"] in (1, -1)
            assert isinstance(b["dir"], int)
            assert b["high"] > b["low"] > 0
            assert b["i0_dt"] < b["i1_dt"] < b["confirm_dt"]
        for p in r["pivots"]:
            assert set(p) == {"zg", "zd", "start_dt", "end_dt", "n_bi",
                              "confirm_dt", "lag_days"}
            assert p["zg"] > p["zd"] > 0
            assert isinstance(p["n_bi"], int) and p["n_bi"] >= 3
            assert p["start_dt"] < p["end_dt"] < p["confirm_dt"]
        for t in r["points"]:
            assert set(t) == {"type", "dt", "price", "detect_date", "lag"}
            assert t["type"] in ("1买", "2买", "3买", "1卖", "2卖", "3卖")
            assert isinstance(t["lag"], int) and t["lag"] >= 1
            assert t["dt"] < t["detect_date"]


def test_one_row_per_bar_in_range(bars, rows):
    start = next(i for i, b in enumerate(bars) if b["dt"] == rows[0]["date"])
    end = next(i for i, b in enumerate(bars) if b["dt"] == rows[-1]["date"])
    assert [r["date"] for r in rows] == [b["dt"] for b in bars[start:end + 1]]
    assert end == CUT - 1  # 行只到 --end 为止，不越界到未来


def test_short_bars_rejected():
    with pytest.raises(ValueError):
        ca.annotate_bars(_mk_bars(60), code="TEST")
