# -*- coding: utf-8 -*-
"""xq_r3_calibration 单元测试：账本解析 / 对比判定 / 历史回测回放。

跑法：pytest tests/test_xq_r3_calibration.py -q
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "research"))

import xq_r3_calibration as m
import logging


# ── normalize_security ────────────────────────────────────────

def test_normalize_a_share():
    assert m.normalize_security("300808.SZ") == "300808"

def test_normalize_hk_5digit():
    assert m.normalize_security("00700.HK") == "00700"

def test_normalize_bad():
    assert m.normalize_security("AAPL") is None
    assert m.normalize_security(None) is None
    assert m.normalize_security("") is None


# ── load_r3_blocks ────────────────────────────────────────────

def _write_ledger(tmp_path, lines):
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)

def test_load_r3_blocks_filters(tmp_path):
    ledger = _write_ledger(tmp_path, [
        {"rule_id": "R1", "action": "block", "security": "600000.SH", "ts": "2026-10-01 10:00:00", "score": 0.9},
        {"rule_id": "R3", "action": "pass", "security": "600000.SH", "ts": "2026-10-01 10:00:00", "score": 0.1},
        {"rule_id": "R3", "action": "block", "security": "300808.SZ", "ts": "2026-09-30 09:30:00", "score": 0.65,
         "reason": "bear事件10 绝对数闸中"},
        {"rule_id": "R3", "action": "block", "security": "300808.SZ", "ts": "2026-09-30 14:00:00", "score": 0.4,
         "reason": "insufficient 样本不足"},
        {"rule_id": "R3", "action": "block", "security": "300808.SZ", "ts": "2026-09-30 15:00:00", "score": None,
         "reason": "-"},
        {"rule_id": "R3", "action": "block", "security": "BADSEC", "ts": "2026-09-30 15:00:00", "score": 0.5},
    ])
    events, stats = m.load_r3_blocks(ledger)
    assert stats["lines"] == 6
    assert stats["r3_total"] == 5
    assert stats["r3_pass"] == 1
    assert stats["blocked_records"] == 1
    assert list(events) == ["300808"]
    rec = events["300808"]["2026-09-30"]
    assert rec["n"] == 1 and rec["max_score"] == 0.65

def test_load_r3_blocks_missing(tmp_path):
    try:
        m.load_r3_blocks(str(tmp_path / "nope.jsonl"))
        assert False, "should raise"
    except FileNotFoundError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tests/test_xq_r3_calibration.py", exc)


# ── compare_around_block / verdict_of ─────────────────────────

def _daily(ticker_days):
    """{iso_date: (posts, bearish, bearish_hc)} → 内部聚合结构。"""
    out = {}
    for d, (p, b, hc) in ticker_days.items():
        out[date.fromisoformat(d)] = {"posts": p, "bearish": b, "bearish_hc": hc}
    return out

def test_compare_windows():
    daily = _daily({
        "2026-10-05": (10, 1, 0), "2026-10-06": (10, 2, 1), "2026-10-07": (10, 3, 0),
        "2026-10-08": (20, 8, 3),
    })
    cmp_res = m.compare_around_block(daily, date(2026, 10, 8))
    assert cmp_res["pre"]["posts"] == 30
    assert cmp_res["post"]["posts"] == 20
    assert abs(cmp_res["pre_bearish_rate"] - 6 / 30) < 1e-9
    assert abs(cmp_res["post_bearish_rate"] - 8 / 20) < 1e-9

def test_verdict_consistent():
    daily = _daily({
        "2026-10-05": (10, 1, 0), "2026-10-06": (10, 1, 0), "2026-10-07": (10, 2, 0),
        "2026-10-08": (20, 8, 3),
    })
    assert m.verdict_of(m.compare_around_block(daily, date(2026, 10, 8))) == "consistent"

def test_verdict_missed():
    daily = _daily({
        "2026-10-05": (10, 5, 2), "2026-10-06": (10, 5, 2), "2026-10-07": (10, 5, 2),
        "2026-10-08": (20, 2, 0),
    })
    assert m.verdict_of(m.compare_around_block(daily, date(2026, 10, 8))) == "missed"

def test_verdict_not_yet():
    daily = _daily({"2026-10-05": (10, 1, 0)})
    assert m.verdict_of(m.compare_around_block(daily, date(2026, 10, 8))) == "not_yet"


# ── build_name_matcher（stock_names.json 名称集构建） ─────────

def _write_names(tmp_path):
    p = tmp_path / "stock_names.json"
    p.write_text(json.dumps({
        "code2name": {"300808.SZ": "久量股份", "600289.SH": "ST信通"},
        "name2code": {"亿阳信通": "600289.SH", "信通": "600289.SH", "久量股份": "300808.SZ"},
        "alias_of": {"G信通": "600289.SH", "*ST信通": "600289.SH"},
    }, ensure_ascii=False), encoding="utf-8")
    return str(p)

def test_build_name_matcher(tmp_path):
    nm = m.build_name_matcher(["300808", "600289"], _write_names(tmp_path))
    assert nm["300808"] == ["久量股份"]
    # ST/G/别名列出，但 2 字「信通」被压制
    assert "亿阳信通" in nm["600289"] and "信通" not in nm["600289"]


# ── backtest_replay（历年同窗回放） ───────────────────────────

def test_backtest_replay_alignment():
    # 2023 年同窗（09-27~09-29 pre / 09-30 post）构造 consistent 序列
    daily = _daily({
        "2023-09-27": (10, 1, 0), "2023-09-28": (10, 1, 0), "2023-09-29": (10, 1, 0),
        "2023-09-30": (20, 8, 3),
    })
    events = {"300808": {"2026-09-30": {"n": 1, "max_score": 0.65, "first_ts": "", "reasons": []}}}
    cases = m.backtest_replay({"300808": daily}, events, 2019, 2023)
    assert len(cases) == 5
    by_year = {y: (cmp_res, v) for _, _, y, cmp_res, v in cases}
    assert by_year[2023][1] == "consistent"
    assert by_year[2022][1] == "not_yet"

def test_backtest_replay_feb29_anchor():
    events = {"300808": {"2028-02-29": {"n": 1, "max_score": 0.5, "first_ts": "", "reasons": []}}}
    cases = m.backtest_replay({}, events, 2024, 2024)
    # 2024 非闰年 → 锚点回落 2/28，不抛异常
    assert len(cases) == 1


# ── raw_name_coverage（内存库探针） ───────────────────────────

def test_raw_name_coverage():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE xq_status (created_at TEXT, title TEXT, desc TEXT)")
    con.executemany("INSERT INTO xq_status VALUES (?,?,?)", [
        ("2020-05-01 10:00:00", "久量股份(300808)遭遇减持", ""),
        ("2021-06-01 10:00:00", "", "聊聊久量股份的现金流"),
        ("2021-06-02 10:00:00", " unrelated post ", "no hit"),
    ])
    hits = m.raw_name_coverage(con, ["300808"], {"300808": ["久量股份"]})
    assert hits["300808"]["posts"] == 2
    assert hits["300808"]["years"] == {"2020": 1, "2021": 1}
