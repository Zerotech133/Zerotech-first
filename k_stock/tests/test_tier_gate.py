# -*- coding: utf-8 -*-
"""077w K-STOCK-101：T4/T5 纪律门槛（dd_circuit_pct / judge_min_grade）测试。
临时 db + monkeypatch judge_reviews 目录，不碰真库。"""
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "paper"))

import trainee                        # noqa: E402
from engine import PaperEngine        # noqa: E402
from market import ParquetMarket      # noqa: E402


PROF = {"id": "t4x", "name": "测试T4", "status": "training",
        "initial_cash": 1_000_000,
        "params": {"dd_circuit_pct": 5.0, "judge_min_grade": "A"}}


def _eng(tmp_path):
    return PaperEngine(tmp_path / "paper.db", ParquetMarket.__new__(ParquetMarket),
                       account="t4x")


def _marks(eng, totals):
    for i, t in enumerate(totals):
        eng.con.execute(
            "INSERT OR REPLACE INTO equity_marks(date,account_id,total,cash,"
            "market_value,pos_pct,pending,verify_ok,ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"2026-10-{10+i:02d}", "t4x", t, t, 0.0, 0.0, 0, 1,
             f"2026-10-{10+i:02d}T15:00:00"))
    eng.con.commit()


def test_dd_circuit_triggers(tmp_path):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 940_000])          # 回撤 6% > 5%
    gate = trainee._tier_gate(PROF, eng)
    assert gate and "回撤熔断" in gate and "6.00%" in gate


def test_dd_circuit_passes_within_threshold(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 960_000])          # 回撤 4% ≤ 5%
    # 只留 dd 判定：DATA_DIR 钉到 tmp（judge_reviews 缺失→宽限不拦）。
    # 隔离修复：原先裸读真实 DATA_DIR，真实 judge 评审落盘（如当日 B 级）
    # 会令本用例被 judge 门槛误拦——测试不得依赖真库状态（K-STOCK-177）。
    monkeypatch.setattr(trainee, "DATA_DIR", tmp_path)
    assert trainee._tier_gate(PROF, eng) is None


def test_judge_gate_blocks_when_grade_low(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 1_010_000])        # 无回撤
    jdir = tmp_path / "judge_reviews"
    jdir.mkdir()
    (jdir / "20261010.json").write_text(json.dumps(
        {"ok": True, "date": "2026-10-10", "score": 80.0, "grade": "B"}),
        encoding="utf-8")
    monkeypatch.setattr(trainee, "DATA_DIR", tmp_path)
    gate = trainee._tier_gate(PROF, eng)
    assert gate and "judge 门槛" in gate and "B" in gate


def test_judge_gate_none_when_grade_meets(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 1_010_000])
    jdir = tmp_path / "judge_reviews"
    jdir.mkdir()
    (jdir / "20261010.json").write_text(json.dumps(
        {"ok": True, "date": "2026-10-10", "score": 98.0, "grade": "A"}),
        encoding="utf-8")
    monkeypatch.setattr(trainee, "DATA_DIR", tmp_path)
    assert trainee._tier_gate(PROF, eng) is None


def test_judge_gate_missing_reviews_is_lenient(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 1_010_000])
    monkeypatch.setattr(trainee, "DATA_DIR", tmp_path)   # judge_reviews 不存在→宽限
    assert trainee._tier_gate(PROF, eng) is None


def test_gate_skipped_when_params_absent(tmp_path):
    eng = _eng(tmp_path)
    _marks(eng, [1_000_000, 500_000])          # 深回撤但无参数→不拦
    prof = {"id": "t4x", "name": "裸", "params": {}}
    assert trainee._tier_gate(prof, eng) is None
