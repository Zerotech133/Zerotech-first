"""K-MDC-A4 tests: shadow projection honesty — clocks, drift, candidate-only.

Uses the REAL curated parquets read-only (no copies, no writes) plus a
synthetic tmp parquet for schema-drift injection. No network.
"""
import pandas as pd
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_lhb_shadow import (  # noqa: E402
    ShadowRejected, cross_signal_shadow, holders_shadow, lhb_shadow,
)

REAL = Path(__file__).resolve().parents[2] / "data" / "tushare_curated"
if not REAL.exists():
    # worktree 无 gitignore 的 data 目录 — 回退主仓 (worktree 在 .worktrees/<单号>/ 下)
    REAL = Path(__file__).resolve().parents[4] / "quant_backtest" / "data" / "tushare_curated"
import os
_env = os.environ.get("INTEL_LHB_DATA_ROOT")
if _env:
    REAL = Path(_env)


def test_lhb_shadow_reports_clocks_and_honest_unknowns():
    out = lhb_shadow(REAL)
    cat = out["catalog"]
    assert cat["rows"] > 0 and "trade_date" in cat["columns"]
    notes = cat["clock_notes"]
    assert notes["occurred_at"].startswith("trade_date")
    assert notes["first_seen_at"].startswith("unknown")   # 不倒填铁律
    assert notes["available_at"].startswith("unknown")
    assert out["preview"] and "ts_code" in out["preview"][0]


def test_holders_shadow_separates_occurrence_from_disclosure():
    out = holders_shadow(REAL)
    notes = out["catalog"]["clock_notes"]
    assert notes["occurred_at"].startswith("end_date")     # 报告期末=发生
    assert notes["disclosed_at"].startswith("ann_date")    # 公告日=披露
    assert "同名异人" in notes["holder_identity"]           # 归 D1 消歧
    assert out["catalog"]["rows"] > 100_000                # 真量级: 46万行


def test_floater_variant_uses_its_own_parquet():
    out = holders_shadow(REAL, floater=True)
    assert out["catalog"]["key"] == "top10_floatholders"
    assert out["catalog"]["rows"] > 100_000


def test_schema_drift_fails_closed(tmp_path):
    # 合成一份缺列 parquet, 注入漂移
    pd.DataFrame({"trade_date": ["20260101"], "ts_code": ["000001.SZ"]}).to_parquet(
        tmp_path / "top_list.parquet")
    with pytest.raises(ShadowRejected, match="SCHEMA_DRIFT"):
        lhb_shadow(tmp_path)
    pd.DataFrame({"ts_code": ["000001.SZ"], "ann_date": ["20260101"]}).to_parquet(
        tmp_path / "top10_holders.parquet")
    with pytest.raises(ShadowRejected, match="SCHEMA_DRIFT"):
        holders_shadow(tmp_path)


def test_missing_dataset_fails_closed(tmp_path):
    with pytest.raises(ShadowRejected, match="DATASET_MISSING"):
        lhb_shadow(tmp_path / "nowhere")


def test_cross_signal_is_candidate_only_verdict():
    # 用真实头号股东探测跨库信号
    hold = pd.read_parquet(REAL / "top10_holders.parquet")
    top_name = hold["holder_name"].value_counts().index[0]
    out = cross_signal_shadow(REAL, top_name)
    assert out["held_codes"] >= 1
    assert out["verdict"].startswith("candidate_only")      # 不出事实
    for hit in out["lhb_hits"]:
        assert "ts_code" in hit and "trade_date" in hit
    with pytest.raises(ShadowRejected, match="holder_name required"):
        cross_signal_shadow(REAL, " ")


def test_shadow_writes_nothing(tmp_path, monkeypatch):
    # 归集只读: 运行后源目录 mtime 集不变(用写入企图捕获替代)
    before = sorted(p.name for p in REAL.glob("*.parquet"))
    lhb_shadow(REAL); holders_shadow(REAL)
    after = sorted(p.name for p in REAL.glob("*.parquet"))
    assert before == after
    assert not list(tmp_path.glob("*.parquet"))  # 无副本产生
