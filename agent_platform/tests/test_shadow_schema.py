# -*- coding: utf-8 -*-
"""M5 刀 1：影子账本 schema 收口测试。

覆盖三件事：
1. build_ledger_row 纯函数——schema_version=m5.v1 标记、degraded 扩义
   （数据源异常 ∪ 决策解析失败，此前 parse_ok=false 行 degraded=False 失明缺口）、
   字段齐平（m5.v1 行全字段必有）；
2. validate_json --profile shadow——m5.v1 行语义规则、v0 旧行语义豁免
   （不重写原则）、坏行 FAIL；
3. already_done 旧 schema 兼容——无 schema_version/parse_ok 的 v0 行照常识别。
"""
import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))  # run_shadow 的 engine path 注入同款

from run_shadow import build_ledger_row, already_done  # noqa: E402

TOOLS = PLATFORM.parent / "tools"
sys.path.insert(0, str(TOOLS))
import validate_json as vj  # noqa: E402

SRC_STATUS_OK = {"web": "normal", "kline": "normal"}
SRC_STATUS_BAD = {"web": "normal", "kline": "stale"}


# ---------------------------------------------------------------- build_ledger_row

def _row(**kw):
    base = dict(today="2026-10-19", symbol="600519", name="贵州茅台",
                run_id="abcd1234ef56", price_at_decision="1500.0",
                dec={"recommendation": "BUY", "confidence": 0.8, "score": 0.8},
                parse_ok=True, source_status=SRC_STATUS_OK, latency_s=3.2)
    base.update(kw)
    return build_ledger_row(**base)


def test_new_row_carries_schema_version():
    r = _row()
    assert r["schema_version"] == "m5.v1"


def test_healthy_row_not_degraded():
    r = _row()
    assert r["parse_ok"] is True
    assert r["degraded"] is False


def test_parse_fail_implies_degraded():  # ★核心缺口回归：此前 degraded=False
    r = _row(dec={}, parse_ok=False)
    assert r["parse_ok"] is False
    assert r["degraded"] is True
    assert r["recommendation"] is None


def test_source_degraded_implies_degraded():
    r = _row(source_status=SRC_STATUS_BAD)
    assert r["degraded"] is True


def test_both_bad_still_degraded_once():
    r = _row(dec={}, parse_ok=False, source_status=SRC_STATUS_BAD)
    assert r["degraded"] is True


def test_none_source_status_tolerated():
    r = _row(source_status=None)
    assert r["degraded"] is False  # 无状态信息不误报


def test_m5_row_field_complete():
    required = {"date", "ts", "symbol", "name", "status", "run_id",
                "price_at_decision", "recommendation", "confidence", "score",
                "target_range", "parse_ok", "degraded", "schema_version",
                "latency_s"}
    assert required <= set(_row().keys())


def test_ts_override_idempotent():
    assert _row(ts=1000.0)["ts"] == 1000.0


# ---------------------------------------------------------------- validate profile

def _run_profile(lines):
    raw = "\n".join(json.dumps(l, ensure_ascii=False) if isinstance(l, dict) else l
                    for l in lines) + "\n"
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = vj._validate_jsonl(raw, "<test>", profile="shadow")
    return ok, buf.getvalue()


def test_v1_healthy_row_pass():
    ok, out = _run_profile([{**_row()}])
    assert ok, out


def test_v0_legacy_row_semantic_exempt():  # 旧行不重写：无 schema_version 豁免
    legacy = {"date": "2026-10-14", "symbol": "300750", "status": "done",
              "run_id": "76cf6d30358f", "recommendation": None,
              "parse_ok": False, "degraded": False}
    ok, out = _run_profile([legacy])
    assert ok, out  # 无 schema_version 不触发语义规则


def test_v1_parse_fail_with_fields_fails():
    bad = _row(dec={"recommendation": "BUY", "confidence": 0.8, "score": 0.8},
               parse_ok=False)
    ok, out = _run_profile([bad])
    assert not ok
    assert "残留决策字段" in out


def test_v1_done_missing_parse_ok_fails():
    bad = _row()
    del bad["parse_ok"]
    ok, out = _run_profile([bad])
    assert not ok
    assert "缺 parse_ok" in out


def test_v1_missing_degraded_fails():
    bad = _row()
    del bad["degraded"]
    ok, out = _run_profile([bad])
    assert not ok
    assert "缺 degraded" in out


def test_unknown_schema_version_fails():
    bad = _row()
    bad["schema_version"] = "m99"
    ok, out = _run_profile([bad])
    assert not ok
    assert "未知 schema_version" in out


def test_mixed_v0_v1_ledger_pass():
    legacy = {"date": "2026-10-08", "symbol": "600036", "status": "done"}
    ok, out = _run_profile([legacy, _row()])
    assert ok, out


# ---------------------------------------------------------------- v0 兼容读侧

def test_already_done_reads_v0_row(tmp_path, monkeypatch):
    ledger = tmp_path / "shadow_ledger.jsonl"
    ledger.write_text(json.dumps(
        {"date": "2026-10-14", "symbol": "300750", "status": "done"},
        ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr("run_shadow.LEDGER", ledger)
    assert already_done("2026-10-14", "300750") is True
    assert already_done("2026-10-14", "600519") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
