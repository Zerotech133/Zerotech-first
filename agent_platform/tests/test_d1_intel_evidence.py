# -*- coding: utf-8 -*-
"""D1 决策记录接 reviewed 情报证据（INTEL_TO_D1 A/B）单测（WO-INTEL-D1EVID / task-16）。

全隔离零网络：tmp news.db（自建 jev_review_queue schema）+ monkeypatch env；
不跑模型、不触网、不碰真仓 data/ledger。
覆盖：
  · off（默认）：build_ledger_row 输出与旧版逐字一致（黄金 dict 锁定），不新增键、
    不导入/不调用 intel_context；
  · on reviewed：done 行增加 `intel_evidence`（source=reviewed_jev + event_refs/
    review_refs/bad_rows），其余字段与 off 逐字一致；模型 l1_basis/裁决字段不动；
  · on fallback：无 reviewed → source=price_proxy + reason=no_reviewed_jev；
  · 坏行 fail-visible 且不阻塞落账；证据读取异常降级不炸；
  · news.db 只读（内容零改动）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent
for _p in (str(PLATFORM), str(PLATFORM / "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_shadow  # noqa: E402

TS = 1780000000.0
SCHEMA = """
CREATE TABLE IF NOT EXISTS jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT,
    title TEXT, stocks TEXT, original_jev TEXT,
    status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT, reviewed_at TEXT,
    decision TEXT, note TEXT)
"""

DEC = {
    "recommendation": "BUY",
    "confidence": 0.8,
    "score": 0.8,
    "target_price_range": [90.0, 96.0],
    "rationale": "技术面放量突破，情绪中性偏暖",
    "l1_basis": "T1/T2 攻防未定 + N1/F1 佐证不足（模型原文，不得改）",
}
POLICY = {
    "decision_id": "D1-20261031-abcdef0123",
    "watch_reason": "",
    "policy_checks": [{"name": "confidence", "ok": True}],
}


# ---------------------------------------------------------------- 夹具
@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("INTEL_TO_D1", "INTEL_N1_DB", "INTEL_N1_LIMIT",
                "INTEL_N1_STOCK_NAMES"):
        monkeypatch.delenv(key, raising=False)


def _build(**over):
    args = dict(
        today="2026-10-31", symbol="600519", name="贵州茅台", run_id="rid-1",
        price_at_decision=1500.0, dec=dict(DEC), parse_ok=True,
        source_status={"web": "normal"}, latency_s=12.3, ts=TS,
        model="glm/glm-5.3", fallback=False, summary={}, bundle=None,
        policy=dict(POLICY),
    )
    args.update(over)
    return run_shadow.build_ledger_row(**args)


def _expected_off_row():
    """旧版 build_ledger_row 的逐字黄金输出（字段/schema m5.v1 冻结）。"""
    return {
        "date": "2026-10-31",
        "ts": TS,
        "symbol": "600519",
        "name": "贵州茅台",
        "status": "done",
        "run_id": "rid-1",
        "price_at_decision": 1500.0,
        "recommendation": "BUY",
        "confidence": 0.8,
        "score": 0.8,
        "target_range": [90.0, 96.0],
        "parse_ok": True,
        "degraded": False,
        "schema_version": "m5.v1",
        "latency_s": 12.3,
        "model": "glm/glm-5.3",
        "fallback": False,
        "summary": {},
        "decision_id": "D1-20261031-abcdef0123",
        "watch_reason": "",
        "confidence_raw": 0.8,
        "evidence_refs": [],
        "policy_checks": [{"name": "confidence", "ok": True}],
        "evidence_sufficient": False,
        "evidence_raw_refs": [],
        "evidence_raw_refs_summary": {},
    }


def _jev(bias="bull", conf=0.62):
    return json.dumps({
        "bias": bias, "conf": conf, "event_type": "earnings",
        "event_cn": "业绩", "companies": [],
        "reason": "复核摘要"}, ensure_ascii=False)


def _insert(db: Path, nid, *, status="resolved", stocks=None,
            original_jev=None, decision="correct", title="600519 测试事件"):
    con = sqlite3.connect(str(db))
    try:
        con.execute(SCHEMA)
        con.execute(
            "INSERT INTO jev_review_queue (id, queued_at, conf, bias, title, "
            "stocks, original_jev, status, reviewer, reviewed_at, decision, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, "2026-10-30 17:00", 0.62, "bull", title,
             json.dumps(stocks if stocks is not None else ["600519.SH"]),
             original_jev if original_jev is not None else _jev(),
             status, "N1", "2026-10-31 09:00", decision, ""))
        con.commit()
    finally:
        con.close()
    return db


# ---------------------------------------------------------------- off：逐字不变
def test_flag_default_off():
    assert run_shadow._intel_to_d1_enabled() is False
    for off in ("0", "false", "off", "no"):
        assert run_shadow._intel_to_d1_enabled() is False, off


def test_off_row_byte_identical_golden(capsys):
    row = _build()
    assert row == _expected_off_row()
    assert "intel_evidence" not in row
    assert capsys.readouterr().out == ""      # off 无额外打印


def test_off_env_variants_identical_and_no_intel_import(monkeypatch, capsys):
    # off 时即使 intel_context 模块不可用也不得影响（不导入/不调用）
    monkeypatch.setitem(sys.modules, "agent_platform.intel_context", None)
    monkeypatch.setitem(sys.modules, "intel_context", None)
    base = _build()
    out = capsys.readouterr().out
    assert out == "" and "intel_evidence" not in base
    for val in ("0", "false", "off", "no"):
        monkeypatch.setenv("INTEL_TO_D1", val)
        assert _build() == base, val


# ---------------------------------------------------------------- on：reviewed
def test_on_reviewed_adds_evidence_without_touching_fields(monkeypatch, tmp_path):
    db = _insert(tmp_path / "news.db", "ev:1")
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    dec = dict(DEC)                            # 模型输出对象：on 前后必须一致
    row = _build(dec=dec)
    ev = row["intel_evidence"]
    assert ev["source"] == "reviewed_jev"
    assert ev["reason"] == ""
    assert ev["event_refs"] == ["ev:1"]
    assert len(ev["review_refs"]) == 1 and ev["review_refs"][0].startswith("rv-")
    assert ev["bad_rows"] == [] and ev["errors"] == []
    # 除新增字段外，其余字段与 off 逐字一致
    without = {k: v for k, v in row.items() if k != "intel_evidence"}
    assert without == _expected_off_row()
    # 模型 l1_basis/裁决文本未被改动、未塞进证据字段
    assert dec == DEC and "l1_basis" not in row
    assert "T1/T2" not in json.dumps(ev, ensure_ascii=False)


def test_on_reviewed_does_not_write_news_db(monkeypatch, tmp_path):
    db = _insert(tmp_path / "news.db", "ev:1")
    before = db.read_bytes()
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    _build()
    assert db.read_bytes() == before


# ---------------------------------------------------------------- on：fallback
def test_on_no_reviewed_is_explicit_price_proxy(monkeypatch, tmp_path, capsys):
    db = _insert(tmp_path / "news.db", "p:1", status="pending")
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    row = _build()
    ev = row["intel_evidence"]
    assert ev["source"] == "price_proxy"
    assert ev["reason"] == "no_reviewed_jev"
    assert ev["event_refs"] == [] and ev["review_refs"] == []
    assert ev["bad_rows"] == []
    # 不伪造“已核验/复核通过”证据（reason 里的 no_reviewed_jev 是显式失败说明）
    dump = json.dumps(ev, ensure_ascii=False)
    assert "已核验" not in dump and "rv-" not in dump
    without = {k: v for k, v in row.items() if k != "intel_evidence"}
    assert without == _expected_off_row()
    assert "source=price_proxy" in capsys.readouterr().out


def test_on_missing_db_degrades_visible(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(tmp_path / "missing.db"))
    row = _build()                             # 不炸
    ev = row["intel_evidence"]
    assert ev["source"] == "price_proxy"
    assert ev["reason"] == "news_db_missing"
    assert {k: v for k, v in row.items() if k != "intel_evidence"} == \
        _expected_off_row()
    assert "source=price_proxy" in capsys.readouterr().out


# ---------------------------------------------------------------- 坏行 / 异常
def test_on_bad_rows_fail_visible_but_not_blocking(monkeypatch, tmp_path, capsys):
    db = _insert(tmp_path / "news.db", "bad:1", original_jev="{not-json")
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    row = _build()                             # 落账/裁决不被坏行阻塞
    ev = row["intel_evidence"]
    assert row["status"] == "done" and row["recommendation"] == "BUY"
    assert ev["source"] == "price_proxy"
    assert ev["bad_rows"] == [{"id": "bad:1", "error": "bad_original_jev"}]
    out = capsys.readouterr().out
    assert "坏行" in out and "bad:1" in out   # fail-visible


def test_on_mixed_bad_rows_still_uses_valid_evidence(monkeypatch, tmp_path):
    db = _insert(tmp_path / "news.db", "bad:1", original_jev="oops")
    _insert(db, "ok:1")
    monkeypatch.setenv("INTEL_TO_D1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    ev = _build()["intel_evidence"]
    assert ev["source"] == "reviewed_jev"
    assert ev["event_refs"] == ["ok:1"]
    assert [x["id"] for x in ev["bad_rows"]] == ["bad:1"]


def test_on_evidence_reader_crash_degrades_without_blocking(monkeypatch, capsys):
    monkeypatch.setenv("INTEL_TO_D1", "1")

    def _boom():
        raise RuntimeError("simulated intel import/query crash")

    monkeypatch.setattr(run_shadow, "_intel_context_module", _boom)
    row = _build()                             # 不抛
    ev = row["intel_evidence"]
    assert ev["source"] == "price_proxy"
    assert ev["reason"] == "intel_error"
    assert ev["errors"] and "simulated" in ev["errors"][0]
    assert {k: v for k, v in row.items() if k != "intel_evidence"} == \
        _expected_off_row()
    assert "降级 price_proxy" in capsys.readouterr().out


# ---------------------------------------------------------------- 直连 builder 单测
def test_build_intel_evidence_shape():
    class _FakeMod:
        @staticmethod
        def reviewed_news_event(code):
            return {"source": "reviewed_jev", "reason": "",
                    "event_ids": ["e1", "e2"], "review_ids": ["rv-1"],
                    "bad_rows": [{"id": "b1", "error": "bad_stocks_json"}],
                    "counts": {"scanned": 3, "matched": 2, "usable": 2},
                    "errors": []}

    ev = run_shadow.build_intel_evidence("600519", module=_FakeMod)
    assert ev["source"] == "reviewed_jev"
    assert ev["event_refs"] == ["e1", "e2"]
    assert ev["review_refs"] == ["rv-1"]
    assert ev["bad_rows"][0]["id"] == "b1"
    assert set(ev) == {"source", "reason", "event_refs", "review_refs",
                       "bad_rows", "counts", "errors"}
