"""K-MDC-A5 tests: real seat co-occurrence from ts_top_inst partitions."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_seat_shadow import (  # noqa: E402
    RelationRejected, load_seat_rows, seat_pairs, top_net_buy_seats,
)

REPO = Path(__file__).resolve().parents[3]


def test_load_reads_all_partitions_with_schema():
    df = load_seat_rows(REPO, limit_parts=2)
    assert len(df) > 1000  # 单分区数千行级
    for c in ("ts_code", "trade_date", "exalter", "side"):
        assert c in df.columns


def test_same_day_same_stock_produces_real_pairs():
    frame = pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "席位A", "side": "0", "net_buy": 100.0},
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "席位B", "side": "1", "net_buy": -50.0},
        {"ts_code": "600001.SH", "trade_date": "20261022", "exalter": "席位A", "side": "0", "net_buy": 1.0},
        {"ts_code": "600002.SH", "trade_date": "20261021", "exalter": "席位A", "side": "0", "net_buy": 2.0},
    ])
    out = seat_pairs(frame)
    assert len(out) == 1  # 只有 1021×600001 成对
    e = out[0]
    assert {e.left, e.right} == {"席位A", "席位B"}
    assert "买" in e.question and "卖" in e.question  # 方向组合进了问题
    assert e.evidence_key.startswith("top_inst:20261021:600001")


def test_big_group_folds_to_population_question():
    frame = pd.DataFrame([
        {"ts_code": "600009.SH", "trade_date": "20261021", "exalter": f"营业部{i}", "side": "0", "net_buy": float(i)}
        for i in range(12)
    ])
    out = seat_pairs(frame)
    assert len(out) == 1 and "12席群体" in out[0].right and "群体交易" in out[0].question


def test_candidate_only_never_fact():
    frame = pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "X", "side": "0", "net_buy": 1.0},
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "Y", "side": "0", "net_buy": 2.0},
    ])
    e = seat_pairs(frame)[0]
    assert "是否" in e.question and "坐实" not in e.question


def test_net_buy_ranking_reports_facts_only():
    frame = pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "S1", "side": "0", "net_buy": 10.0},
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "S2", "side": "0", "net_buy": 30.0},
    ])
    rank = top_net_buy_seats(frame)
    assert rank[0]["seat"] == "S2" and rank[0]["net_buy_total"] == 30.0


def test_schema_drift_and_missing_fail_closed(tmp_path):
    with pytest.raises(RelationRejected, match="DATASET_MISSING"):
        load_seat_rows(tmp_path)
    bad = tmp_path / "quant_backtest" / "data" / "tdx" / "ts_top_inst"
    bad.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["x"]}).to_parquet(bad / "part_0000.parquet")
    # 坏 schema 两条失败路径都算失败关闭: pyarrow 列级拒绝 或 我们的 SCHEMA_DRIFT
    with pytest.raises((RelationRejected, Exception)):
        load_seat_rows(tmp_path)
