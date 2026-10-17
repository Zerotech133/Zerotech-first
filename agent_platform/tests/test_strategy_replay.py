# -*- coding: utf-8 -*-
"""strategy_replay 只读聚合视图单元测试（K-ORG-167 第三批）。

纪律：全部用例用 tmp_path 构造假账本，绝不写 agent_platform/data/；
被测模块红线=零写面，幂等用例断言调用前后文件字节不变。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.strategy_replay import (  # noqa: E402
    _cli, aggregate, load_shadow_index)

SLEDGER_REL = Path("strategy") / "strategy_runs.jsonl"
HLEDGER_REL = Path("shadow") / "shadow_ledger.jsonl"


# ---------------------------------------------------------------- 构造工具

def _verdict_row(run_id="r1", sid="trend_sma20", symbol="600519.SH",
                 rec="buy", trade_date="2026-10-16",
                 recorded_at="2026-10-17T09:00:00", **kw):
    row = {
        "verdict_key": "k_%s_%s_%s" % (sid, symbol, trade_date),
        "strategy_id": sid, "symbol": symbol, "recommendation": rec,
        "confidence": 0.8, "stance": "bull", "score": 12.5,
        "rationale": "sma20 上穿", "trace_id": "", "run_id": run_id,
        "trade_date": trade_date, "recorded_at": recorded_at,
    }
    row.update(kw)
    return json.dumps(row, ensure_ascii=False)


def _shadow_row(run_id="r1", symbol="600519.SH", date="2026-10-16",
                **kw):
    row = {
        "date": date, "ts": 1790000000.0, "symbol": symbol,
        "name": "贵州茅台", "status": "done", "run_id": run_id,
        "price_at_decision": 1500.0, "recommendation": "buy",
        "confidence": 0.7, "score": 10.0, "target_range": None,
        "parse_ok": True, "degraded": False,
        "schema_version": "m5.v1", "latency_s": 30.0,
    }
    row.update(kw)
    return json.dumps(row, ensure_ascii=False)


def _build_root(tmp_path, strategy_lines=(), shadow_lines=()):
    """假数据根：按需落两账本（仅 tmp_path，绝不碰真仓 data/）。"""
    s = tmp_path / SLEDGER_REL
    h = tmp_path / HLEDGER_REL
    if strategy_lines is not None:
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_text("".join(l + "\n" for l in strategy_lines),
                     encoding="utf-8")
    if shadow_lines is not None:
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_text("".join(l + "\n" for l in shadow_lines),
                     encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- 用例

def test_correct_join(tmp_path):
    """判决行 run_id 命中影子 m5.v1 行：joined/hit_rate/分布全对。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[
            _verdict_row(run_id="r1", rec="buy", sid="trend_sma20"),
            _verdict_row(run_id="r1", rec="sell", sid="momentum20"),
        ],
        shadow_lines=[_shadow_row(run_id="r1")],
    )
    view = aggregate(root)
    assert view["missing"] == []
    assert view["totals"]["verdicts"] == 2
    assert view["totals"]["joined"] == 2
    assert view["totals"]["hit_rate"] == 1.0
    assert view["strategies"]["trend_sma20"]["recommendations"]["buy"] == 1
    assert view["strategies"]["momentum20"]["recommendations"]["sell"] == 1


def test_strategy_run_id_no_shadow_match(tmp_path):
    """run_id 在影子账本无匹配：joined=0、hit_rate=0，判决仍计数。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[_verdict_row(run_id="r_missing")],
        shadow_lines=[_shadow_row(run_id="r1")],
    )
    view = aggregate(root)
    st = view["strategies"]["trend_sma20"]
    assert st["total"] == 1 and st["with_run_id"] == 1
    assert st["joined"] == 0 and st["hit_rate"] == 0.0
    assert view["totals"]["hit_rate"] == 0.0


def test_no_anchor_verdicts(tmp_path):
    """空 run_id 判决：无回放锚点，不进 with_run_id 分母（no_anchor 单列）。"""
    root = _build_root(tmp_path, strategy_lines=[_verdict_row(run_id="")])
    view = aggregate(root)
    assert view["totals"]["verdicts"] == 1
    assert view["totals"]["no_anchor"] == 1
    assert view["totals"]["with_run_id"] == 0
    assert view["totals"]["hit_rate"] is None
    st = view["strategies"]["trend_sma20"]
    assert st["with_run_id"] == 0 and st["hit_rate"] is None


def test_both_ledgers_missing_fail_visible(tmp_path):
    """两账本均缺：空视图 + missing/hints 显式，不抛异常。"""
    view = aggregate(tmp_path / "nope")
    assert set(view["missing"]) == {"strategy", "shadow"}
    assert len(view["hints"]) == 2
    assert view["totals"]["verdicts"] == 0
    assert view["strategies"] == {}


def test_one_ledger_missing_partial_view(tmp_path):
    """仅缺影子账本：判决仍聚合，join 侧空索引（命中率恒 0）。"""
    root = _build_root(tmp_path,
                       strategy_lines=[_verdict_row(run_id="r1")],
                       shadow_lines=None)
    view = aggregate(root)
    assert view["missing"] == ["shadow"]
    assert view["totals"]["verdicts"] == 1
    assert view["totals"]["joined"] == 0


def test_empty_ledger_files(tmp_path):
    """两账本存在但为空文件：missing 空、聚合全零，不算缺失。"""
    root = _build_root(tmp_path, strategy_lines=[], shadow_lines=[])
    view = aggregate(root)
    assert view["missing"] == []
    assert view["totals"]["verdicts"] == 0
    assert view["bad_lines"] == {"strategy_runs": 0, "shadow_ledger": 0}


def test_bad_lines_counted_and_skipped(tmp_path):
    """坏行（烂 JSON/非对象行）跳过但计入 bad_lines，好行照常聚合。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[
            "{broken json",
            json.dumps([1, 2]),
            _verdict_row(run_id="r1"),
        ],
        shadow_lines=["not a json at all", _shadow_row(run_id="r1")],
    )
    view = aggregate(root)
    assert view["bad_lines"]["strategy_runs"] == 2
    assert view["bad_lines"]["shadow_ledger"] == 1
    assert view["totals"]["verdicts"] == 1
    assert view["totals"]["joined"] == 1


def test_idempotent_readonly_bytes_unchanged(tmp_path):
    """幂等只读断言：aggregate 前后两账本字节完全不变。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[_verdict_row(run_id="r1"), _verdict_row(run_id="r2",
                                                                sid="momentum20")],
        shadow_lines=[_shadow_row(run_id="r1")],
    )
    s_path = root / SLEDGER_REL
    h_path = root / HLEDGER_REL
    before_s = s_path.read_bytes()
    before_h = h_path.read_bytes()
    for _ in range(3):
        view = aggregate(root)
    assert s_path.read_bytes() == before_s
    assert h_path.read_bytes() == before_h
    assert view["totals"]["verdicts"] == 2


def test_zero_write_surface_no_dir_creation(tmp_path):
    """零写面红线：对空根聚合不创建任何目录/文件。"""
    empty = tmp_path / "virgin"
    view = aggregate(empty)
    assert not empty.exists()
    assert set(view["missing"]) == {"strategy", "shadow"}


def test_v0_shadow_rows_excluded_from_join(tmp_path):
    """影子 v0 存量行（无 schema_version）不参与 join，m5.v1 行参与。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[
            _verdict_row(run_id="r_v0", sid="trend_sma20"),
            _verdict_row(run_id="r_m5", sid="momentum20"),
        ],
        shadow_lines=[
            _shadow_row(run_id="r_v0", schema_version=None),
            _shadow_row(run_id="r_m5", schema_version="m5.v1"),
        ],
    )
    view = aggregate(root)
    assert view["strategies"]["trend_sma20"]["joined"] == 0
    assert view["strategies"]["momentum20"]["joined"] == 1


def test_latest_verdict_per_symbol(tmp_path):
    """按 symbol 最新判决：trade_date 大者胜；同日按 recorded_at 决胜。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[
            _verdict_row(run_id="r1", rec="buy",
                         trade_date="2026-10-15",
                         recorded_at="2026-10-16T09:00:00"),
            _verdict_row(run_id="r2", rec="sell",
                         trade_date="2026-10-16",
                         recorded_at="2026-10-17T09:00:00"),
            _verdict_row(run_id="r3", rec="hold", sid="momentum20",
                         trade_date="2026-10-16",
                         recorded_at="2026-10-17T08:00:00"),
            _verdict_row(run_id="r4", rec="strong_sell", sid="momentum20",
                         trade_date="2026-10-16",
                         recorded_at="2026-10-17T10:00:00"),
        ],
    )
    view = aggregate(root)
    latest = view["strategies"]["trend_sma20"]["latest_by_symbol"]["600519.SH"]
    assert latest["recommendation"] == "sell"
    assert latest["run_id"] == "r2"
    m = view["strategies"]["momentum20"]["latest_by_symbol"]["600519.SH"]
    assert m["recommendation"] == "strong_sell"


def test_enum_outliers_land_in_other_bucket(tmp_path):
    """枚举外 recommendation/stance 落 other 桶（fail-visible，不冒充枚举值）。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[
            _verdict_row(run_id="r1", rec="super_buy", stance="sideways"),
            _verdict_row(run_id="r2", rec="hold", stance="neutral"),
        ],
    )
    view = aggregate(root)
    rec = view["strategies"]["trend_sma20"]["recommendations"]
    st = view["strategies"]["trend_sma20"]["stances"]
    assert rec["other"] == 1 and rec["hold"] == 1
    assert st["other"] == 1 and st["neutral"] == 1


def test_verdict_rows_without_strategy_id_counted(tmp_path):
    """无主判决（strategy_id 空）：不入策略桶，但 no_strategy_id 显式计数。"""
    root = _build_root(tmp_path,
                       strategy_lines=[_verdict_row(sid="", run_id="r1"),
                                       _verdict_row(run_id="r2")])
    view = aggregate(root)
    assert view["totals"]["no_strategy_id"] == 1
    assert view["totals"]["verdicts"] == 1


def test_load_shadow_index_first_seen_wins(tmp_path):
    """同 run_id 多条 m5.v1 影子行：首见为准（append-only 重放语义）。"""
    root = _build_root(
        tmp_path,
        shadow_lines=[
            _shadow_row(run_id="r1", price_at_decision=100.0),
            _shadow_row(run_id="r1", price_at_decision=999.0),
        ],
    )
    idx, bad = load_shadow_index(root)
    assert bad == 0
    assert idx["r1"]["price_at_decision"] == 100.0


def test_cli_exit0_and_json_output(tmp_path, capsys):
    """CLI 正常路径：退出码 0，stdout 为可解析 JSON 且 totals 正确。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[_verdict_row(run_id="r1")],
        shadow_lines=[_shadow_row(run_id="r1")],
    )
    rc = _cli(["--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    view = json.loads(out)
    assert view["totals"]["joined"] == 1
    assert "trend_sma20" in view["strategies"]


def test_cli_exit2_when_both_missing(tmp_path, capsys):
    """CLI fail-visible：两账本均缺退出码 2，仍打印空视图 JSON。"""
    rc = _cli(["--root", str(tmp_path / "nothing")])
    out = capsys.readouterr().out
    assert rc == 2
    view = json.loads(out)
    assert set(view["missing"]) == {"strategy", "shadow"}


def test_cli_table_format(tmp_path, capsys):
    """CLI --format table：文本表格路径退出码 0 且含 TOTAL 行。"""
    root = _build_root(
        tmp_path,
        strategy_lines=[_verdict_row(run_id="r1")],
        shadow_lines=[_shadow_row(run_id="r1")],
    )
    rc = _cli(["--root", str(root), "--format", "table"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "TOTAL" in out and "trend_sma20" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
