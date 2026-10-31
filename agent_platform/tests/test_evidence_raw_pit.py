# -*- coding: utf-8 -*-
"""evidence_raw PIT（数据时点）测试（K-ORG-20261101-D1RAWPIT）。

覆盖：
- K 线 raw ref 的 pit_as_of = 上下文最新 K 线交易日（数据时点），
  与 as_of（采集时刻）分开；
- L4 raw ref 的 pit_as_of 取 L4 记录 as_of；坏 as_of 不伪造（记 issue）；
- validate_pit：缺/非法/数据时点晚于采集时点 → 明确 false；
- raw ref 缺文件仍然拒绝（不因 PIT 改造放松）；
- 采集报告含 PIT 分账（refs/issue），供 ledger/outcome 与审计消费。
全部 tmp_path 合成，不读生产、不联网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform" / "engine"))
sys.path.insert(0, str(WT / "agent_platform"))

import evidence_bundle as EB  # noqa: E402
import evidence_raw as ER  # noqa: E402


def _write_kline(root: Path, code: str = "600519", rows: int = 30):
    path = root / ER.ASHARE_REL / ("%s_SH.parquet" % code)
    path.parent.mkdir(parents=True, exist_ok=True)
    dates, closes = [], []
    for index in range(rows):
        dates.append("202610%02d" % (index + 1))
        closes.append(1500.0 + index)
    table = pa.table({
        "trade_date": dates,
        "open": [c - 2 for c in closes],
        "close": closes,
        "high": [c + 10 for c in closes],
        "low": [c - 10 for c in closes],
        "volume": [100.0 + i for i in range(rows)],
    })
    pq.write_table(table, path)
    lines = []
    for index in range(rows - 8, rows):
        iso = "%s-%s-%s" % (dates[index][:4], dates[index][4:6],
                            dates[index][6:])
        lines.append("%s 收%.2f 高%.2f 低%.2f 量%.2f"
                     % (iso, closes[index], closes[index] + 10,
                        closes[index] - 10, 100.0 + index))
    return path.relative_to(root), "\n".join(lines)


def _write_l4(root: Path, code: str = "600519", as_of="2026-10-31 02:10"):
    path = root / ER.L4_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "symbols": {code: {"status": "unresolved", "as_of": as_of}}},
        ensure_ascii=False), encoding="utf-8")
    return path


def _ctx(kline_text: str) -> dict:
    return {
        "market_technical": "近8日K线（前复权）:\n%s\n均线: MA5=1" % kline_text,
        "fundamental": "快照: 名称=测试股 现价=1500",
        "news_event": "情绪代理指标(v1): x",
        "final_decision": "基本面快照: x\nL4=存疑观察",
        "name": "测试股",
    }


def _collected(root: Path):
    _rel, text = _write_kline(root)
    _write_l4(root)
    return ER.collect_shadow_raw_refs("600519", _ctx(text), root=root)


# ---------------------------------------------------------------- K 线 PIT
def test_kline_pit_is_latest_trade_date_not_collection_time(tmp_path):
    collected = _collected(tmp_path)
    kline = next(ref for ref in collected["refs"]
                 if ref.kind == "kline_parquet")
    assert kline.pit_as_of == "2026-10-30"       # 数据时点=最新交易日
    assert kline.as_of != kline.pit_as_of        # 采集时刻 ≠ 数据时点
    ok, reason = EB.validate_pit(kline.as_of, kline.pit_as_of)
    assert ok and reason == ""
    # 报告 PIT 分账
    pit_ref = next(item for item in collected["report"]["pit"]["refs"]
                   if item["kind"] == "kline_parquet")
    assert pit_ref["pit_ok"] is True
    assert pit_ref["pit_as_of"] == "2026-10-30"
    assert pit_ref["as_of"] == kline.as_of


# ---------------------------------------------------------------- L4 PIT
def test_l4_pit_from_record_as_of(tmp_path):
    collected = _collected(tmp_path)
    l4 = next(ref for ref in collected["refs"] if ref.kind == "l4_record")
    assert l4.pit_as_of == "2026-10-31 02:10"
    ok, _ = EB.validate_pit(l4.as_of, l4.pit_as_of)
    assert ok
    assert collected["report"]["pit"]["issues"] == []


def test_bad_l4_as_of_not_fabricated(tmp_path):
    _rel, text = _write_kline(tmp_path)
    _write_l4(tmp_path, as_of="not-a-time")
    collected = ER.collect_shadow_raw_refs("600519", _ctx(text), root=tmp_path)
    l4 = next(ref for ref in collected["refs"] if ref.kind == "l4_record")
    assert l4.pit_as_of == ""                    # 不落非法值
    issues = collected["report"]["pit"]["issues"]
    assert any(item.get("source") == "l4_status"
               and item.get("reason") == "bad_pit_format"
               for item in issues)
    ok, reason = EB.validate_pit(l4.as_of, l4.pit_as_of)
    assert not ok and reason == "pit_missing"


# ---------------------------------------------------------------- 校验规则
def test_validate_pit_rules():
    assert EB.validate_pit("2026-10-31T18:00:00+0800", "2026-10-30") == (True, "")
    assert EB.validate_pit("2026-10-31T18:00:00+0800", "") == (False, "pit_missing")
    assert EB.validate_pit("2026-10-31", "2026/10/30") == (False, "bad_pit_format")
    assert EB.validate_pit("2026-10-29", "2026-10-30") == (False, "pit_after_as_of")
    # 同日边界：数据时点 00:00 不晚于采集 18:00
    assert EB.validate_pit("2026-10-30T18:00:00+0800", "2026-10-30") == (True, "")
    with pytest.raises(EB.RawRefError):
        EB.normalize_pit_as_of("2026-13-99")


def test_raw_ref_with_bad_pit_rejected_by_verify(tmp_path):
    _rel, text = _write_kline(tmp_path)
    _write_l4(tmp_path)
    collected = ER.collect_shadow_raw_refs("600519", _ctx(text), root=tmp_path)
    ref = next(r for r in collected["refs"] if r.kind == "kline_parquet")
    # 篡改 PIT：数据时点晚于采集时点 → 复核拒绝
    ref.pit_as_of = "2027-01-01"
    ok, reason = EB.verify_raw_ref(ref, root=tmp_path)
    assert not ok and reason == "pit_after_as_of"
    ref.pit_as_of = "bad-format"
    ok, reason = EB.verify_raw_ref(ref, root=tmp_path)
    assert not ok and reason == "bad_pit_format"


# ---------------------------------------------------------------- raw 拒绝
def test_missing_raw_file_still_rejected(tmp_path):
    with pytest.raises(EB.RawRefError) as excinfo:
        EB.make_raw_ref(source="market_technical",
                        path="quant_backtest/data/ashare/nope_SH.parquet",
                        kind="kline_parquet", row_range=[0, 8],
                        pit_as_of="2026-10-30", root=tmp_path)
    assert "not_found" in str(excinfo.value)


def test_raw_summary_counts_pit(tmp_path):
    rel, text = _write_kline(tmp_path)
    ref = EB.make_raw_ref(source="market_technical", path=rel,
                          kind="kline_parquet", row_range=[22, 30],
                          pit_as_of="2026-10-30", root=tmp_path)
    bundle = EB.EvidenceBundle.from_context("600519", "测试股", _ctx(text))
    bundle.attach_raw_refs([ref], root=tmp_path)
    summary = bundle.raw_summary()
    assert summary["status"] == "partial"
    assert summary["refs_with_pit_as_of"] == 1
    market = next(r for r in bundle.refs if r.source == "market_technical")
    assert market.pit_as_of == "2026-10-30"
    # 旧摘要 ref 没有 pit_as_of（默认空，读取方按缺失处理）
    legacy = EB.EvidenceRef.from_dict({
        "source": "fundamental", "uri": "make_context://600519/fundamental",
        "as_of": "2026-10-31T18:00:00+0800", "hash": "x" * 16})
    assert legacy.pit_as_of == ""
