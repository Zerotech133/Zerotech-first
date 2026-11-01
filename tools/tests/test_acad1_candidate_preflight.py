# -*- coding: utf-8 -*-
"""ACAD1 候选生成门预检测试（WO-ACAD1-PREFLIGHT-20261031）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import acad1_candidate_preflight as p  # noqa: E402


def _clean(**kw):
    base = {
        "candidate_id": "c_clean", "market": "CN", "strategy": "chan_monitor",
        "signal_type": "3买", "center_low": 2.5, "center_high": 2.67,
        "close": 3.02, "trigger_close": 2.8, "trigger_date": "2026-10-28",
        "asof": "2026-10-30", "strategy_timeframe": "1d",
        "evidence_timeframe": "1d", "rule_rsi_period": 14,
        "evidence_rsi_period": 14, "volume_ratio": 1.2, "daily_return": 1.5,
        "pre_close": 2.97, "score": 60.0, "score_method": "percentile",
        "corporate_action": True, "rationale": "close 3.02 > 2.67",
        # WO-GUARD-PROVENANCE-20261101 配套：guard G3/G4 新增 provenance 事实口径，
        # 干净卡需声明 bar_asof/bar_source/复权/RSI 方法/完整日线，否则 G3 unknown。
        "bar_asof": "2026-10-30", "bar_source": "test",
        "price_adjust": "none", "rsi_method": "wilder",
        "evidence_rsi_method": "wilder", "complete_bar": True,
    }
    base.update(kw)
    return base


def test_review_ready_clean():
    rep = p.build_preflight([_clean()], asof="2026-10-30")
    assert rep["summary"]["status"] == "review_ready"
    assert rep["summary"]["review_ready"] == 1
    assert rep["not_a_verdict"] is True
    assert rep["reviewed_mutated"] is False
    assert rep["handoff_mutated"] is False


def test_blocked_fail():
    rep = p.build_preflight([_clean(score=100.0)], asof="2026-10-30")
    assert rep["summary"]["blocked"] >= 1
    assert rep["summary"]["status"] == "blocked"


def test_unknown_external():
    rep = p.build_preflight([{"candidate_id": "x", "market": "CN"}])
    assert rep["summary"]["need_external"] >= 1
    assert rep["summary"]["status"] in ("need_external", "blocked")


def test_pool_blocking_marks_no_ready():
    cards = [_clean(candidate_id="a", score=100.0), _clean(candidate_id="b", score=100.0)]
    rep = p.build_preflight(cards, asof="2026-10-30")
    assert rep["summary"]["pool_blocking"] >= 1
    assert rep["summary"]["review_ready"] == 0


def test_cli_stdout_clean(tmp_path):
    f = tmp_path / "cards.json"
    f.write_text(json.dumps({"cards": [_clean()]}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "acad1_candidate_preflight.py"),
                        "--cards", str(f), "--json"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert json.loads(r.stdout)["summary"]["review_ready"] == 1


def test_cli_out_dir_writes(tmp_path):
    f = tmp_path / "cards.json"
    f.write_text(json.dumps([_clean()]), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "acad1_candidate_preflight.py"),
                        "--cards", str(f), "--out-dir", str(out)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert len(list(out.glob("ACAD1候选生成门预检_*.json"))) == 1
    assert len(list(out.glob("ACAD1候选生成门预检_*.md"))) == 1


def test_cli_bad_input_rc2(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{oops", encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "acad1_candidate_preflight.py"),
                        "--cards", str(f)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2
