# -*- coding: utf-8 -*-
"""OSLINK-W2 量化板块 StatusPoint 契约测试（隔离树·待签）。

纪律：
  - 全部用 tmp_path 注入，不碰真实状态点文件。
  - 缺源负例是核心：缺失/损坏/结构不符/缺 source_ref 都必须显式报
    no_source+reason，不得静默回落空态、不得回填零值或演示值。
  - 不断言"QS1 有活动"——当前诚实状态就是 no_writer。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector import status_point as sp


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _active_point(**over):
    point = {
        "schema": sp.SCHEMA,
        "writer": "QS1",
        "state": sp.STATE_ACTIVE,
        "as_of": _iso(datetime.now(timezone.utc)),
        "source_ref": "quant_backtest/reports/qs1_daily.json",
        "trace_id": "trace-test-001",
        "detail": "测试用活动状态点",
    }
    point.update(over)
    return point


# ---- a) 缺源负例：文件缺失 → no_source，且 point 为 None（不造数）----
def test_missing_file_is_no_source_not_silent_empty(tmp_path):
    target = tmp_path / "absent.json"
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_SOURCE
    assert result["point"] is None
    assert "缺失" in result["reason"]


# ---- b) 缺源负例：文件损坏 → no_source，且说明损坏而非静默 ----
def test_corrupt_file_is_no_source_with_reason(tmp_path):
    target = tmp_path / "status_point.json"
    target.write_text("{not-json", encoding="utf-8")
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_SOURCE
    assert result["point"] is None
    assert "不可读" in result["reason"]


# ---- c) 缺源负例：顶层非对象 ----
def test_non_object_top_level_is_no_source(tmp_path):
    target = tmp_path / "status_point.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_SOURCE
    assert "顶层不是对象" in result["reason"]


# ---- d) 缺源负例：active 态缺 source_ref → 不得视为有源状态 ----
def test_active_without_source_ref_is_no_source(tmp_path):
    target = tmp_path / "status_point.json"
    sp.write_status_point({
        "writer": "QS1", "state": sp.STATE_NO_WRITER,
        "as_of": _iso(datetime.now(timezone.utc)),
    }, target)
    # 直接改文件模拟"自称 active 但无证据定位"的坏点
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["state"] = sp.STATE_ACTIVE
    raw.pop("source_ref", None)
    target.write_text(json.dumps(raw), encoding="utf-8")
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_SOURCE
    assert "source_ref" in result["reason"]


# ---- e) emit_current 写诚实 no_writer 态，读回一致 ----
def test_emit_current_is_no_writer_and_readable(tmp_path):
    target = tmp_path / "status_point.json"
    sp.emit_current(target)
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_WRITER
    assert result["point"]["writer"] == "QS1"
    assert result["point"]["receipt"] is None
    assert "无运行时写入者" in result["reason"]


# ---- f) active 新近点 → active，age 合理 ----
def test_fresh_active_point_reads_active(tmp_path):
    target = tmp_path / "status_point.json"
    sp.write_status_point(_active_point(), target)
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_ACTIVE
    assert 0 <= result["age_sec"] <= 5
    assert result["point"]["trace_id"] == "trace-test-001"


# ---- g) 过期点 → 读者降级 stale，且不改原文件 ----
def test_stale_point_degrades_without_mutating(tmp_path):
    target = tmp_path / "status_point.json"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=sp.STALE_AFTER_SEC + 60))
    sp.write_status_point(_active_point(as_of=old), target)
    before = target.read_text(encoding="utf-8")
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_STALE
    assert result["age_sec"] > sp.STALE_AFTER_SEC
    assert target.read_text(encoding="utf-8") == before  # 只读，不修数


# ---- h) 写入口校验：缺必填字段拒写 ----
def test_write_rejects_missing_required_fields(tmp_path):
    target = tmp_path / "status_point.json"
    with pytest.raises(ValueError, match="缺必填字段"):
        sp.write_status_point({"writer": "QS1"}, target)
    assert not target.exists()


# ---- i) 写入口校验：拒绝预填未来时点 ----
def test_write_rejects_future_as_of(tmp_path):
    target = tmp_path / "status_point.json"
    future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
    with pytest.raises(ValueError, match="未来时点"):
        sp.write_status_point(_active_point(as_of=future), target)
    assert not target.exists()


# ---- j) 写入口校验：state 词表外取值拒写 ----
def test_write_rejects_unknown_state(tmp_path):
    target = tmp_path / "status_point.json"
    with pytest.raises(ValueError, match="state"):
        sp.write_status_point(_active_point(state="passed"), target)
    assert not target.exists()


# ---- k) 原子写：不留 tmp 残留 ----
def test_atomic_write_leaves_no_tmp_files(tmp_path):
    target = tmp_path / "status_point.json"
    sp.write_status_point(_active_point(), target)
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith(".status_point")]
    assert leftovers == []
    assert target.exists()


# ---- l) as_of 不可解析（active 态）→ no_source ----
def test_active_with_unparsable_as_of_is_no_source(tmp_path):
    target = tmp_path / "status_point.json"
    target.write_text(json.dumps({
        "schema": sp.SCHEMA, "writer": "QS1", "state": sp.STATE_ACTIVE,
        "as_of": "昨天下午", "source_ref": "x",
    }), encoding="utf-8")
    result = sp.read_status_point(target)
    assert result["state"] == sp.STATE_NO_SOURCE
    assert "as_of" in result["reason"]


# ---- m) 读者绝不返回结论性状态（词表封闭）----
def test_reader_vocabulary_is_closed(tmp_path):
    seen = set()
    seen.add(sp.read_status_point(tmp_path / "nope.json")["state"])
    t2 = tmp_path / "b.json"
    t2.write_text("{bad", encoding="utf-8")
    seen.add(sp.read_status_point(t2)["state"])
    t3 = tmp_path / "c.json"
    sp.emit_current(t3)
    seen.add(sp.read_status_point(t3)["state"])
    t4 = tmp_path / "d.json"
    sp.write_status_point(_active_point(), t4)
    seen.add(sp.read_status_point(t4)["state"])
    t5 = tmp_path / "e.json"
    sp.write_status_point(_active_point(
        as_of=_iso(datetime.now(timezone.utc) - timedelta(seconds=99999))), t5)
    seen.add(sp.read_status_point(t5)["state"])
    assert seen <= {sp.STATE_NO_SOURCE, sp.STATE_NO_WRITER,
                    sp.STATE_ACTIVE, sp.STATE_STALE}
    # 无任何"通过/正常/OK"式结论词
    assert not (seen & {"passed", "ok", "healthy", "verified"})
