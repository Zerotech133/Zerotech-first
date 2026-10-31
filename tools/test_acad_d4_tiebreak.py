# -*- coding: utf-8 -*-
"""test_acad_d4_tiebreak.py — D4 tie-break 策略模拟测试。

全部 tmp_path 造假 parquet（不读/不写真实数据）：
同一 _fetched_at 并列、各策略选行差异、top_inst side 偏好、无冲突、
缺业务字段、键定义诊断（reason 纳入键）、幂等、只读零写、CLI。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import acad_d4_tiebreak as tie

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

TOP_LIST_SCHEMA = pa.schema([
    ("ts_code", pa.large_string()), ("trade_date", pa.large_string()), ("name", pa.large_string()),
    ("close", pa.float64()), ("pct_change", pa.float64()), ("net_amount", pa.float64()),
    ("l_buy", pa.float64()), ("l_sell", pa.float64()), ("amount", pa.float64()),
    ("reason", pa.large_string()), ("_fetched_at", pa.large_string()),
])
TOP_INST_SCHEMA = pa.schema([
    ("ts_code", pa.large_string()), ("trade_date", pa.large_string()), ("exalter", pa.large_string()),
    ("side", pa.large_string()), ("buy", pa.float64()), ("sell", pa.float64()),
    ("net_buy", pa.float64()), ("_fetched_at", pa.large_string()),
])
CAL_SCHEMA = pa.schema([
    ("exchange", pa.large_string()), ("cal_date", pa.large_string()),
    ("is_open", pa.float64()), ("pretrade_date", pa.large_string()),
])


def _write(path: Path, rows: list[dict], schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _tl(ts_code, trade_date, *, reason="日涨幅偏离值达到7%的前5只证券", net_amount=1.0,
        amount=10.0, fetched="2026-10-04 18:32:26"):
    return dict(ts_code=ts_code, trade_date=trade_date, name=ts_code.split(".")[0],
                close=10.0, pct_change=1.0, net_amount=net_amount, l_buy=1.0,
                l_sell=1.0, amount=amount, reason=reason, _fetched_at=fetched)


def _ti(ts_code, trade_date, *, exalter="机构专用", side="0", buy=1.0, sell=1.0,
        net_buy=0.0, fetched="2026-10-04 18:32:26"):
    return dict(ts_code=ts_code, trade_date=trade_date, exalter=exalter, side=side,
                buy=buy, sell=sell, net_buy=net_buy, _fetched_at=fetched)


def make_root(tmp_path: Path, *, manifest=True) -> Path:
    """假 D4 数据根：
    top_list（5 键 / 8 行）：
      - A.SH 20260930：同股同日 **两个不同 reason**（合法多行）→ base 键冲突 1
      - B.SH 20260930：同 reason 两行 payload 不同（真重复）→ base 键冲突 1，alt 键仍冲突
      - C.SH 20260930：单行
      - D.SH 20261009：两行（不同 fetched_at，可择新）→ 冲突 1
    top_inst（4 键 / 8 行）：
      - E.SH 机构专用 side0：buy 10 / 30（同批）→ prefer-side 取 buy 大者
      - E.SH 机构专用 side1：sell 5 / 50（同批）→ prefer-side 取 sell 大者
      - F.SH 机构专用 side0：两行完全一致 → identical
      - G.SH 机构专用 side0：单行
    """
    root = tmp_path / "data"
    _write(root / "tdx/ts_trade_cal/part_0000.parquet",
           [dict(exchange="SSE", cal_date="20260930", is_open=1.0, pretrade_date="20260929"),
            dict(exchange="SSE", cal_date="20261009", is_open=1.0, pretrade_date="20260930")],
           CAL_SCHEMA)

    tl_rows = [
        _tl("A.SH", "20260930", reason="日涨幅偏离值达到7%的前5只证券", net_amount=100.0, amount=1000.0),
        _tl("A.SH", "20260930", reason="连续三个交易日内，涨幅偏离值累计达到20%的证券",
            net_amount=200.0, amount=2000.0),
        _tl("B.SH", "20260930", reason="日换手率达到20%的前5只证券", net_amount=300.0),
        _tl("B.SH", "20260930", reason="日换手率达到20%的前5只证券", net_amount=400.0),
        _tl("C.SH", "20260930", net_amount=500.0),
        _tl("D.SH", "20261009", net_amount=600.0, fetched="2026-10-05 10:00:00"),
        _tl("D.SH", "20261009", net_amount=700.0, fetched="2026-10-06 10:00:00"),
    ]
    _write(root / "tdx/ts_top_list/part_0000.parquet", tl_rows[:5], TOP_LIST_SCHEMA)
    _write(root / "tdx/ts_top_list/part_0001.parquet", tl_rows[5:], TOP_LIST_SCHEMA)

    ti_rows = [
        _ti("E.SH", "20260930", side="0", buy=10.0, sell=1.0, net_buy=9.0),
        _ti("E.SH", "20260930", side="0", buy=30.0, sell=2.0, net_buy=28.0),
        _ti("E.SH", "20260930", side="1", buy=1.0, sell=5.0, net_buy=-4.0),
        _ti("E.SH", "20260930", side="1", buy=1.0, sell=50.0, net_buy=-49.0),
        _ti("F.SH", "20260930", side="0", buy=7.0, sell=3.0, net_buy=4.0),
        _ti("F.SH", "20260930", side="0", buy=7.0, sell=3.0, net_buy=4.0),
        _ti("G.SH", "20260930", side="0", buy=1.0, sell=1.0, net_buy=0.0),
    ]
    _write(root / "tdx/ts_top_inst/part_0000.parquet", ti_rows, TOP_INST_SCHEMA)

    if manifest:
        (root / "tdx/_manifest.json").write_text(json.dumps({
            "ts_top_list": {"rows": len(tl_rows) - 1, "part": 2, "done": True},
            "ts_top_inst": {"rows": 6, "part": 1, "done": True},
        }), encoding="utf-8")
    return root


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _files(root: Path) -> dict[str, tuple[str, int]]:
    return {str(p.relative_to(root)): (_sha(p), p.stat().st_size)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _record(info: dict, key: list) -> dict:
    for rec in info["keys_detail"]:
        if rec["key"] == key:
            return rec
    raise AssertionError("未找到键：%s" % key)


# ---------------------------------------------------------------- 核心模拟

def test_strategy_selection_and_counts(tmp_path):
    rep = tie.run_sim(make_root(tmp_path))
    tl = rep["tables"]["top_list"]
    assert (tl["rows"], tl["keys"], tl["dup_keys"], tl["dup_extra_rows"]) == (7, 4, 3, 3)
    assert (tl["identical_keys"], tl["conflicting_keys"]) == (0, 3)
    # reason 纳入键后：B.SH（同 reason 真重复）与 D.SH（同 reason 两行）仍重复 = 2
    assert tl["alt_key_analysis"]["dup_keys"] == 2

    ti = rep["tables"]["top_inst"]
    assert (ti["rows"], ti["keys"], ti["dup_keys"], ti["dup_extra_rows"]) == (7, 4, 3, 3)
    assert (ti["identical_keys"], ti["conflicting_keys"]) == (1, 2)
    assert ti["identical_extra_rows"] == 1
    assert ti["alt_key_analysis"] == {}

    # keep-last 为基准：0 改选；keep-first 与基准在冲突键上必有差异
    assert ti["strategies"]["keep-last"]["keys_changed_vs_baseline"] == 0
    assert ti["strategies"]["keep-first"]["keys_changed_vs_baseline"] == 2
    # keep-newest-fetched：E 键并列 → 与 keep-last 相同
    assert ti["strategies"]["keep-newest-fetched"]["keys_changed_vs_baseline"] == 0
    # top_list D 键 fetched_at 可择新 → keep-newest 与 keep-last 相同（last 即最新）
    assert tl["strategies"]["keep-newest-fetched"]["keys_changed_vs_baseline"] == 0
    assert tl["strategies"]["keep-newest-fetched"]["aggregates"]["net_amount"] == \
        tl["strategies"]["keep-last"]["aggregates"]["net_amount"]


def test_prefer_side_picks_largest_side_field(tmp_path):
    rep = tie.run_sim(make_root(tmp_path))
    ti = rep["tables"]["top_inst"]
    side0 = _record(ti, ["20260930", "E.SH", "机构专用", "0"])
    side1 = _record(ti, ["20260930", "E.SH", "机构专用", "1"])
    # 候选行版本：side0 行 0/1，prefer-side 取 buy 大的第 1 行
    assert side0["versions"][0]["payload"]["buy"] == 10.0
    assert side0["versions"][1]["payload"]["buy"] == 30.0
    assert side0["selected"]["prefer-side"] == 1
    assert side0["dropped"]["prefer-side"] == [0]
    # side1 取 sell 大的第 1 行
    assert side1["versions"][1]["payload"]["sell"] == 50.0
    assert side1["selected"]["prefer-side"] == 1
    # 基准 keep-last 亦为 1；keep-first 为 0
    assert side0["selected"]["keep-first"] == 0
    assert side0["selected"]["keep-last"] == 1


def test_business_field_strategies_choose_extremes(tmp_path):
    rep = tie.run_sim(make_root(tmp_path))
    tl = rep["tables"]["top_list"]
    b = _record(tl, ["20260930", "B.SH"])
    # keep-max-net 取 net_amount 大的第 1 行；keep-min-net 取第 0 行
    assert b["selected"]["keep-max-net"] == 1
    assert b["selected"]["keep-min-net"] == 0
    # keep-last 即取末行：A/B/D 的末行恰好都是 net 最大 → keep-max-net 0 改选
    assert tl["strategies"]["keep-max-net"]["keys_changed_vs_baseline"] == 0
    # keep-min-net 三个冲突键都改选首行（A=100/B=300/D=600 均非末行）
    assert tl["strategies"]["keep-min-net"]["keys_changed_vs_baseline"] == 3


def test_no_conflict_table(tmp_path):
    root = tmp_path / "data"
    _write(root / "tdx/ts_trade_cal/part_0000.parquet",
           [dict(exchange="SSE", cal_date="20260930", is_open=1.0, pretrade_date="")], CAL_SCHEMA)
    _write(root / "tdx/ts_top_list/part_0000.parquet",
           [_tl("A.SH", "20260930"), _tl("B.SH", "20260930")], TOP_LIST_SCHEMA)
    _write(root / "tdx/ts_top_inst/part_0000.parquet",
           [_ti("A.SH", "20260930")], TOP_INST_SCHEMA)
    rep = tie.run_sim(root)
    for name in ("top_list", "top_inst"):
        info = rep["tables"][name]
        assert info["dup_keys"] == 0 and info["keys_detail"] == []
        assert all(st["keys_changed_vs_baseline"] == 0 for st in info["strategies"].values())
        assert all(st["rows_dropped"] == 0 for st in info["strategies"].values())


def test_missing_business_field_reported(tmp_path):
    root = tmp_path / "data"
    _write(root / "tdx/ts_trade_cal/part_0000.parquet",
           [dict(exchange="SSE", cal_date="20260930", is_open=1.0, pretrade_date="")], CAL_SCHEMA)
    # 去掉 net_amount / amount 列的最小 top_list
    schema = pa.schema([("ts_code", pa.large_string()), ("trade_date", pa.large_string()),
                        ("reason", pa.large_string()), ("_fetched_at", pa.large_string())])
    _write(root / "tdx/ts_top_list/part_0000.parquet", [
        dict(ts_code="A.SH", trade_date="20260930", reason="r1", _fetched_at="2026-10-04 18:32:26"),
        dict(ts_code="A.SH", trade_date="20260930", reason="r2", _fetched_at="2026-10-04 18:32:26"),
    ], schema)
    _write(root / "tdx/ts_top_inst/part_0000.parquet", [_ti("A.SH", "20260930")], TOP_INST_SCHEMA)
    rep = tie.run_sim(root)
    tl = rep["tables"]["top_list"]
    assert "net_amount" in tl["fields_missing"] and "amount" in tl["fields_missing"]
    assert tl["conflicting_keys"] == 1
    # 缺字段时策略仍可确定性产出（不崩、聚合为 0）
    assert tl["strategies"]["keep-max-net"]["aggregates"]["net_amount"] == 0.0


def test_manifest_projection(tmp_path):
    rep = tie.run_sim(make_root(tmp_path))
    tl_mp = rep["tables"]["top_list"]["manifest_projection"]
    assert tl_mp["manifest_rows"] == 6
    assert tl_mp["rows_after_all_dedup"] == 4
    assert tl_mp["rows_after_safe_dedup"] == 7
    assert tl_mp["projected_diff_all_dedup"] == -2
    assert tl_mp["projected_diff_safe_dedup"] == 1
    ti_mp = rep["tables"]["top_inst"]["manifest_projection"]
    assert ti_mp["rows_after_safe_dedup"] == 6
    assert ti_mp["projected_diff_safe_dedup"] == 0


# ---------------------------------------------------------------- 只读 / 幂等 / CLI

def test_readonly_zero_write(tmp_path):
    root = make_root(tmp_path)
    before = _files(root)
    rep = tie.run_sim(root)
    after = _files(root)
    assert before == after
    assert rep["dry_run"] is True
    assert rep["readonly"]["unchanged"] is True
    assert rep["readonly"]["files"] == len(before)


def test_idempotent_report(tmp_path):
    root = make_root(tmp_path)
    r1 = tie.run_sim(root)
    r2 = tie.run_sim(root)
    r1.pop("generated_at")
    r2.pop("generated_at")
    assert r1 == r2


def test_cli_json_and_human(tmp_path, capsys):
    root = make_root(tmp_path)
    assert tie.main(["--data-root", str(root), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["schema"] == tie.SCHEMA
    assert rep["dry_run"] is True
    assert set(rep["tables"]) == {"top_list", "top_inst"}
    assert rep["strategies"] == list(tie.STRATEGIES)
    assert rep["baseline"] == tie.BASELINE
    assert any("top_list" in x for x in rep["recommendations"])

    assert tie.main(["--data-root", str(root)]) == 0
    human = capsys.readouterr().out
    assert "tie-break 策略模拟" in human
    assert "prefer-side" in human and "keep-max-net" in human
    assert "键诊断" in human and "只读" in human
    lines = human.strip().splitlines()
    assert 10 <= len(lines) <= 24


def test_cli_missing_root_exit_1(tmp_path, capsys):
    assert tie.main(["--data-root", str(tmp_path / "nope")]) == 1
    assert "数据根不存在" in capsys.readouterr().out


def test_cli_usage_error_exit_2():
    with pytest.raises(SystemExit) as exc:
        tie.main(["--bogus"])
    assert exc.value.code == 2
