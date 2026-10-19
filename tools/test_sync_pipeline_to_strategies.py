# -*- coding: utf-8 -*-
"""sync_pipeline_to_strategies 单测：全 tmp_path 替身，禁碰真实 portal/strategies.json。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_pipeline_to_strategies as sp  # noqa: E402


def _write_strategies(tmp_path, entry_ids=("chanlun",), ts="2026-10-19T00:00:00"):
    data = {
        "ts": ts,
        "_meta": {"what": "替身"},
        "strategies": [{"id": i, "name": i} for i in entry_ids],
    }
    p = tmp_path / "strategies.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _write_summary(tmp_path, trade_date="20261019", names=("ma_cross",),
                   recorded=38, n_total=174, pool=None):
    data = {
        "trade_date": trade_date,
        "pool": pool if pool is not None else ["600519.SH"],
        "strategies": {n: {"recorded": recorded, "replayed": 0} for n in names},
        "stats": {n: {"n_total": n_total} for n in names},
    }
    p = tmp_path / ("daily_shadow_summary_%s.json" % trade_date)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ── 无匹配 no-op ─────────────────────────────────────────────
def test_no_match_noop(tmp_path, capsys):
    """映射表为空（当前真实状态）→ no-op 打印提示退出 0，文件不动。"""
    st = _write_strategies(tmp_path)
    _write_summary(tmp_path)
    before = st.read_text(encoding="utf-8")
    rc = sp.main(["--file", str(st), "--summary-dir", str(tmp_path)])
    assert rc == 0
    assert "无匹配条目" in capsys.readouterr().out
    assert st.read_text(encoding="utf-8") == before  # 未写盘


# ── 有匹配正常回写 ───────────────────────────────────────────
def test_match_writes_pipeline(tmp_path, monkeypatch):
    st = _write_strategies(tmp_path, entry_ids=("ma_cross", "chanlun"))
    _write_summary(tmp_path)
    monkeypatch.setattr(sp, "STRATEGY_MAP", {
        "ma_cross": {"entry": "ma_cross", "stage": "S4", "stage_cn": "影子期"}})
    rc = sp.main(["--file", str(st), "--summary-dir", str(tmp_path)])
    assert rc == 0
    data = _read(st)
    by_id = {s["id"]: s for s in data["strategies"]}
    assert "pipeline" in by_id["ma_cross"]
    pl = by_id["ma_cross"]["pipeline"]
    assert pl["stage"] == "S4" and pl["stage_cn"] == "影子期"
    assert pl["as_of"] == "20261019"
    assert pl["note"] == "影子 174 条 · 池 1 只"
    assert pl["source"] == "strategy_layer/onboarding"
    # 不增删条目、不动 _meta、不改 ts
    assert [s["id"] for s in data["strategies"]] == ["ma_cross", "chanlun"]
    assert data["_meta"] == {"what": "替身"}
    assert data["ts"] == "2026-10-19T00:00:00"
    # 未匹配条目不挂 pipeline
    assert "pipeline" not in by_id["chanlun"]


# ── 幂等零 diff ──────────────────────────────────────────────
def test_idempotent_zero_diff(tmp_path, monkeypatch, capsys):
    st = _write_strategies(tmp_path, entry_ids=("ma_cross",))
    _write_summary(tmp_path)
    monkeypatch.setattr(sp, "STRATEGY_MAP", {
        "ma_cross": {"entry": "ma_cross", "stage": "S4", "stage_cn": "影子期"}})
    assert sp.main(["--file", str(st), "--summary-dir", str(tmp_path)]) == 0
    before = st.read_text(encoding="utf-8")
    capsys.readouterr()
    rc = sp.main(["--file", str(st), "--summary-dir", str(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "零 diff" in out.out
    assert st.read_text(encoding="utf-8") == before  # 重跑零 diff


# ── dry-run 不落盘 ───────────────────────────────────────────
def test_dry_run_no_write(tmp_path, monkeypatch, capsys):
    st = _write_strategies(tmp_path, entry_ids=("ma_cross",))
    _write_summary(tmp_path)
    monkeypatch.setattr(sp, "STRATEGY_MAP", {
        "ma_cross": {"entry": "ma_cross", "stage": "S4", "stage_cn": "影子期"}})
    before = st.read_text(encoding="utf-8")
    rc = sp.main(["--file", str(st), "--summary-dir", str(tmp_path), "--dry-run"])
    out = capsys.readouterr()
    assert rc == 0
    assert "[计划]" in out.out and "未写盘" in out.out
    assert st.read_text(encoding="utf-8") == before  # 未落盘
    assert "pipeline" not in _read(st)["strategies"][0]


# ── fail-visible ─────────────────────────────────────────────
def test_missing_summary_nonzero_exit(tmp_path, capsys):
    """summary 缺失 → 非零退出 + stderr。"""
    st = _write_strategies(tmp_path)
    with pytest.raises(SystemExit) as ei:
        sp.main(["--file", str(st), "--summary", str(tmp_path / "nope.json")])
    assert ei.value.code != 0
    assert "summary" in capsys.readouterr().err


def test_bad_json_nonzero_exit(tmp_path, capsys):
    st = _write_strategies(tmp_path)
    bad = tmp_path / "daily_shadow_summary_20261019.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        sp.main(["--file", str(st), "--summary", str(bad)])
    assert ei.value.code != 0
    assert "解析失败" in capsys.readouterr().err


def test_missing_field_nonzero_exit(tmp_path, capsys):
    st = _write_strategies(tmp_path)
    bad = tmp_path / "daily_shadow_summary_20261019.json"
    bad.write_text(json.dumps({"pool": []}), encoding="utf-8")  # 缺 trade_date/strategies
    with pytest.raises(SystemExit) as ei:
        sp.main(["--file", str(st), "--summary", str(bad)])
    assert ei.value.code != 0
    assert "trade_date" in capsys.readouterr().err


# ── 原子写 ───────────────────────────────────────────────────
def test_atomic_write_no_tmp_left(tmp_path, monkeypatch):
    st = _write_strategies(tmp_path, entry_ids=("ma_cross",))
    _write_summary(tmp_path)
    monkeypatch.setattr(sp, "STRATEGY_MAP", {
        "ma_cross": {"entry": "ma_cross", "stage": "S4", "stage_cn": "影子期"}})
    assert sp.main(["--file", str(st), "--summary-dir", str(tmp_path)]) == 0
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert _read(st)["strategies"][0]["pipeline"]["as_of"] == "20261019"


# ── 取最新一份 ───────────────────────────────────────────────
def test_latest_summary_picked(tmp_path, monkeypatch):
    _write_summary(tmp_path, trade_date="20261018")
    _write_summary(tmp_path, trade_date="20261019")
    st = _write_strategies(tmp_path, entry_ids=("ma_cross",))
    monkeypatch.setattr(sp, "STRATEGY_MAP", {
        "ma_cross": {"entry": "ma_cross", "stage": "S4", "stage_cn": "影子期"}})
    assert sp.main(["--file", str(st), "--summary-dir", str(tmp_path)]) == 0
    assert _read(st)["strategies"][0]["pipeline"]["as_of"] == "20261019"
