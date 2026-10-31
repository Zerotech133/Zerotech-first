# -*- coding: utf-8 -*-
"""EvidenceBundle raw refs 测试（2026-10-31 WO-EVIDRAW）。

覆盖验收四点：
1. raw ref 可核验（文件 sha256 + parquet 行区间 + 上下文 close 对得上）；
2. 缺文件拒绝（make_raw_ref 抛错 / attach 拒绝 / 不写入 refs、不假成功）；
3. 摘要 ref 不再被当 raw（旧 make_context ref 一律 is_raw=false / verify=not_raw）；
4. 旧 bundle/旧 ref dict 兼容（新增字段全部有默认值，is_sufficient 不变）。

全测试只用 tmp_path 合成文件：不读生产数据、不写任何数据、不联网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "agent_platform"
sys.path.insert(0, str(PLATFORM / "engine"))
sys.path.insert(0, str(PLATFORM))

import evidence_bundle as EB  # noqa: E402
import evidence_raw as ER  # noqa: E402
import run_shadow  # noqa: E402


# ---------------------------------------------------------------- 合成夹具
def _write_kline(root: Path, code: str = "600519", rows: int = 40):
    """写一份 ashare 形状的日 K parquet，返回 (相对路径, 最后 8 行上下文文本)。"""
    path = root / ER.ASHARE_REL / ("%s_SH.parquet" % code)
    path.parent.mkdir(parents=True, exist_ok=True)
    dates, opens, closes, highs, lows, vols = [], [], [], [], [], []
    for index in range(rows):
        day = 1 + index
        month = 7 + day // 28
        date = "2026%02d%02d" % (month, ((day - 1) % 28) + 1)
        close = 1500.0 + index
        dates.append(date)
        opens.append(close - 2)
        closes.append(close)
        highs.append(close + 10)
        lows.append(close - 10)
        vols.append(100.0 + index)
    table = pa.table({
        "trade_date": dates, "open": opens, "close": closes,
        "high": highs, "low": lows, "volume": vols,
    })
    pq.write_table(table, path)
    lines = []
    for index in range(rows - 8, rows):
        iso = "%s-%s-%s" % (dates[index][:4], dates[index][4:6],
                            dates[index][6:])
        lines.append("%s 收%.2f 高%.2f 低%.2f 量%.2f"
                     % (iso, closes[index], highs[index], lows[index],
                        vols[index]))
    return path.relative_to(root), "\n".join(lines)


def _write_l4(root: Path, code: str = "600519"):
    path = root / ER.L4_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "symbols": {code: {"status": "unresolved", "source": "xsec/v2"}}},
        ensure_ascii=False), encoding="utf-8")
    return path


def _ctx(kline_text: str, include_fund: bool = True) -> dict:
    ctx = {
        "market_technical": "近8日K线（前复权）:\n%s\n均线: MA5=1 MA10=2 MA20=3"
                            % kline_text,
        "news_event": "情绪代理指标(v1): 近5日涨跌=1.0%",
        "final_decision": "基本面快照: PE=20\nL4=存疑观察",
        "name": "测试股",
    }
    if include_fund:
        ctx["fundamental"] = "快照: 名称=测试股 现价=1500 PE=20 PB=3"
    return ctx


def _bundle(ctx) -> EB.EvidenceBundle:
    return EB.EvidenceBundle.from_context("600519", ctx.get("name", ""), ctx)


# ---------------------------------------------------------------- 1. raw 可核验
def test_make_raw_ref_is_verifiable(tmp_path):
    rel_kline, _text = _write_kline(tmp_path)
    ref = EB.make_raw_ref(source="market_technical", path=rel_kline,
                          kind="kline_parquet", row_range=[32, 40],
                          root=tmp_path)
    assert ref.is_raw and ref.raw_verified
    assert ref.schema == EB.RAW_SCHEMA
    assert ref.uri.startswith("raw://kline_parquet/")
    assert ref.raw_uri == rel_kline.as_posix()
    assert len(ref.raw_sha256) == 64
    assert ref.raw_row_range == [32, 40]
    assert ref.raw_bytes == (tmp_path / rel_kline).stat().st_size
    assert EB.verify_raw_ref(ref, root=tmp_path) == (True, "")
    # 可 JSON 落账
    json.dumps(ref.to_dict(), ensure_ascii=False)


def test_tampered_file_fails_verification(tmp_path):
    rel_kline, _text = _write_kline(tmp_path)
    ref = EB.make_raw_ref(source="market_technical", path=rel_kline,
                          kind="kline_parquet", row_range=[32, 40],
                          root=tmp_path)
    path = tmp_path / rel_kline
    path.write_bytes(path.read_bytes() + b"x")
    assert EB.verify_raw_ref(ref, root=tmp_path) == (False,
                                                     "sha256_mismatch")
    bundle = _bundle(_ctx(_text))
    report = bundle.attach_raw_refs([ref], root=tmp_path)
    assert report["rejected"][0]["reason"] == "sha256_mismatch"
    assert bundle.raw_refs_status == "invalid"
    assert all(not item.is_raw for item in bundle.refs)  # 不假成功


def _write_ctx_text(tmp_path):
    _rel, text = _write_kline(tmp_path)
    return text


# ---------------------------------------------------------------- 2. 缺文件拒绝
def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(EB.RawRefError) as excinfo:
        EB.make_raw_ref(source="market_technical",
                        path="quant_backtest/data/ashare/600519_SH.parquet",
                        kind="kline_parquet", row_range=[0, 1],
                        root=tmp_path)
    assert "not_found" in str(excinfo.value)
    forged = {
        "source": "market_technical", "uri": "raw://kline_parquet/x.parquet",
        "as_of": "2026-10-31T11:00:00+0800", "hash": "0" * 16,
        "schema": EB.RAW_SCHEMA, "kind": "kline_parquet",
        "raw_uri": "quant_backtest/data/ashare/x.parquet",
        "raw_sha256": "0" * 64, "raw_verified": True,
    }
    assert EB.verify_raw_ref(forged, root=tmp_path) == (False, "not_found")
    bundle = _bundle(_ctx(_write_ctx_text(tmp_path)))
    before = [ref.to_dict() for ref in bundle.refs]
    report = bundle.attach_raw_refs([forged], root=tmp_path)
    assert report["rejected"][0]["reason"] == "not_found"
    assert bundle.raw_refs_status == "invalid"
    assert [ref.to_dict() for ref in bundle.refs] == before  # refs 未被污染


def test_row_range_and_selector_guards(tmp_path):
    rel_kline, _text = _write_kline(tmp_path)
    with pytest.raises(EB.RawRefError) as excinfo:
        EB.make_raw_ref(source="market_technical", path=rel_kline,
                        kind="kline_parquet", row_range=[0, 100],
                        root=tmp_path)
    assert "row_range_out_of_bounds" in str(excinfo.value)
    with pytest.raises(EB.RawRefError):
        EB.make_raw_ref(source="market_technical", path=rel_kline,
                        kind="kline_parquet", row_range=[-1, 2],
                        root=tmp_path)
    l4 = _write_l4(tmp_path)
    ref = EB.make_raw_ref(source="l4_status", path=l4, kind="l4_record",
                          selector="symbols.600519", root=tmp_path)
    assert ref.is_raw and EB.verify_raw_ref(ref, root=tmp_path) == (True, "")
    with pytest.raises(EB.RawRefError) as excinfo:
        EB.make_raw_ref(source="l4_status", path=l4, kind="l4_record",
                        selector="symbols.000001", root=tmp_path)
    assert "selector_not_found" in str(excinfo.value)


# ---------------------------------------------------------------- 3. 摘要≠raw
def test_summary_refs_are_not_raw():
    bundle = _bundle(_ctx("2026-10-30 收1500.00 高1510.00 低1490.00 量100.00"))
    assert bundle.is_sufficient()
    assert all(not ref.is_raw for ref in bundle.refs)
    assert all(ref.schema == EB.SUMMARY_SCHEMA for ref in bundle.refs)
    assert all(ref.uri.startswith("make_context://") for ref in bundle.refs)
    assert EB.verify_raw_ref(bundle.refs[0]) == (False, "not_raw")
    summary = bundle.raw_summary()
    assert summary["status"] == "none"
    assert summary["raw_refs_in_bundle"] == 0
    # W3 预检 B 口径：raw_ref_ok = 全部 refs 都非 make_context（摘要 ref 未被冒充）
    non_summary = [ref for ref in bundle.refs
                   if not str(ref.uri).startswith("make_context://")]
    raw_ref_ok = bool(bundle.refs) and len(non_summary) == len(bundle.refs)
    assert raw_ref_ok is False


# ---------------------------------------------------------------- 4. 升级不放松充分性
def test_attach_upgrades_same_source_without_relaxing_sufficiency(tmp_path):
    rel_kline, text = _write_kline(tmp_path)
    ctx = _ctx(text)
    bundle = _bundle(ctx)
    before_sources = [ref.source for ref in bundle.refs]
    before_count = len(bundle.refs)
    ref = EB.make_raw_ref(source="market_technical", path=rel_kline,
                          kind="kline_parquet", row_range=[32, 40],
                          root=tmp_path)
    report = bundle.attach_raw_refs([ref], root=tmp_path)
    assert report["upgraded"] == ["market_technical"]
    assert len(bundle.refs) == before_count
    assert [r.source for r in bundle.refs] == before_sources
    assert bundle.is_sufficient() is True  # 不放松也不收紧
    market = next(r for r in bundle.refs if r.source == "market_technical")
    assert market.is_raw and market.raw_row_range == [32, 40]
    assert market.raw_selector == ""
    summary = bundle.raw_summary()
    assert summary["status"] == "partial"
    assert summary["raw_refs_in_bundle"] == 1


def test_raw_cannot_fill_missing_required_segment(tmp_path):
    """raw ref 不得补出缺失段（否则变相放松 is_sufficient）。"""
    rel_kline, text = _write_kline(tmp_path)
    bundle = _bundle(_ctx(text, include_fund=False))
    assert not bundle.is_sufficient()
    l4 = _write_l4(tmp_path)
    raw_l4 = EB.make_raw_ref(source="l4_status", path=l4, kind="l4_record",
                             selector="symbols.600519", root=tmp_path)
    report = bundle.attach_raw_refs([raw_l4], root=tmp_path)
    assert report["extra"] == ["l4_status"]  # L4 只进 extra，不进 refs
    assert not bundle.is_sufficient()  # 仍然不足
    assert "fundamental" not in [r.source for r in bundle.refs]
    # 伪造 fundamental 段 raw（即使文件可核验）也被拒
    raw_jsonl = tmp_path / "news.jsonl"
    raw_jsonl.write_text('{"title": "x"}\n', encoding="utf-8")
    fake = EB.make_raw_ref(source="fundamental", path=raw_jsonl,
                           kind="news_record", selector="line:0",
                           root=tmp_path)
    report2 = bundle.attach_raw_refs([fake], root=tmp_path)
    assert report2["rejected"][0]["reason"] == "source_not_in_bundle"
    assert not bundle.is_sufficient()


# ---------------------------------------------------------------- 5. 旧格式兼容
def test_old_ref_and_bundle_dict_compatible():
    old_ref = {"source": "market_technical",
               "uri": "make_context://600519/market_technical",
               "as_of": "2026-10-31T10:00:00+0800",
               "hash": "abcdef0123456789", "freshness_s": 0}
    ref = EB.EvidenceRef.from_dict(old_ref)
    assert ref.source == "market_technical" and ref.is_raw is False
    data = ref.to_dict()
    for key in ("source", "uri", "as_of", "hash", "freshness_s"):
        assert data[key] == old_ref[key]  # 旧字段原样
    old_bundle = {
        "symbol": "600519", "name": "贵州茅台",
        "refs": [old_ref,
                 dict(old_ref, source="fundamental")],
        "l4_status": "unresolved", "l4_source": "x",
        "input_degraded": "missing:fundamental",
    }
    bundle = EB.EvidenceBundle.from_dict(old_bundle)
    assert bundle.symbol == "600519"
    assert all(not r.is_raw for r in bundle.refs)
    assert bundle.raw_refs_status == "none"
    assert not bundle.is_sufficient()  # 只有 2 段，旧语义不变
    json.dumps(bundle.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------- 6. 采集/接线
def test_collect_shadow_raw_refs_and_bundle_upgrade(tmp_path):
    rel_kline, text = _write_kline(tmp_path)
    _write_l4(tmp_path)
    ctx = _ctx(text)
    collected = ER.collect_shadow_raw_refs("600519", ctx, root=tmp_path)
    assert Path(collected["root"]) == tmp_path.resolve()
    kinds = sorted(ref.kind for ref in collected["refs"])
    assert kinds == ["kline_parquet", "l4_record"]
    unavailable = {item["source"] for item in
                   collected["report"]["unavailable"]}
    assert unavailable >= {"fundamental", "news_event", "final_decision"}

    bundle = _bundle(ctx)
    report = bundle.attach_raw_refs(collected["refs"],
                                    root=collected["root"])
    assert "market_technical" in report["upgraded"]
    assert "l4_status" in report["extra"]
    market = next(r for r in bundle.refs if r.source == "market_technical")
    assert market.is_raw and market.raw_row_range == [32, 40]
    assert EB.verify_raw_ref(market, root=tmp_path) == (True, "")
    assert len(bundle.raw_refs_extra) == 1
    assert bundle.raw_refs_extra[0].raw_selector == "symbols.600519"
    assert bundle.is_sufficient() is True
    assert bundle.raw_summary()["status"] == "partial"


def test_collect_degrades_when_files_missing(tmp_path):
    ctx = _ctx("2026-10-30 收1500.00 高1510.00 低1490.00 量100.00")
    collected = ER.collect_shadow_raw_refs("600519", ctx, root=tmp_path)
    assert collected["refs"] == []
    reasons = {item["reason"] for item in collected["report"]["unavailable"]}
    assert "kline_parquet_missing" in reasons
    assert "l4_file_missing" in reasons
    bundle = _bundle(ctx)
    bundle.attach_raw_refs(collected["refs"], root=collected["root"])
    assert bundle.raw_refs_status == "none"
    assert bundle.is_sufficient() is True  # 降级不影响充分性判定


def test_context_mismatch_does_not_fabricate_raw(tmp_path):
    rel_kline, text = _write_kline(tmp_path)
    # 篡改上下文最后一根 close，使其与本地 parquet 不一致
    tampered = text.replace("收1539.00", "收9999.00")
    ctx = _ctx(tampered)
    collected = ER.collect_shadow_raw_refs("600519", ctx, root=tmp_path)
    assert all(ref.kind != "kline_parquet" for ref in collected["refs"])
    reasons = {item["reason"] for item in collected["report"]["unavailable"]}
    assert "context_close_mismatch" in reasons


def test_run_shadow_wire_and_ledger_fields(tmp_path):
    rel_kline, text = _write_kline(tmp_path)
    _write_l4(tmp_path)
    ctx = _ctx(text)
    bundle = _bundle(ctx)
    wired = run_shadow._wire_raw_refs(bundle, "600519", ctx, root=tmp_path)
    assert wired is bundle
    assert wired.raw_refs_error == ""
    row = run_shadow.build_ledger_row(
        "2026-10-31", "600519", "测试股", "run-raw-1", 1500.0,
        {"recommendation": "WATCH", "confidence": 0.5}, True,
        {"d1_decision": "normal"}, 1.2,
        bundle=wired, policy={"decision_id": "D1-x",
                              "watch_reason": "l4_unresolved",
                              "policy_checks": []})
    assert row["schema_version"] == "m5.v1"  # 旧 schema 不变
    assert row["evidence_sufficient"] is True  # 不变
    refs = row["evidence_refs"]
    market = next(ref for ref in refs if ref["source"] == "market_technical")
    assert market["schema"] == EB.RAW_SCHEMA
    assert market["raw_uri"].endswith("600519_SH.parquet")
    assert len(market["raw_sha256"]) == 64
    assert row["evidence_raw_refs_summary"]["status"] == "partial"
    assert row["evidence_raw_refs"][0]["kind"] == "l4_record"
    json.dumps(row, ensure_ascii=False)  # 可落账


def test_run_shadow_wire_failopen(monkeypatch):
    ctx = _ctx("2026-10-30 收1500.00 高1510.00 低1490.00 量100.00")
    bundle = _bundle(ctx)

    def boom(*args, **kwargs):
        raise RuntimeError("injected raw collector failure")

    monkeypatch.setattr(run_shadow, "collect_shadow_raw_refs", boom)
    out = run_shadow._wire_raw_refs(bundle, "600519", ctx)
    assert out is bundle
    assert out.raw_refs_error.startswith("injected")
    assert out.raw_refs_status == "none"  # 降级可见，摘要 ref 保留
    assert all(not ref.is_raw for ref in out.refs)
    assert out.is_sufficient() is True
