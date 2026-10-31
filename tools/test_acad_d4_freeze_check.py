# -*- coding: utf-8 -*-
"""test_acad_d4_freeze_check.py — D4 冻结对账工具测试。

全部用 tmp_path 造小 parquet 假数据（不读/不写真实 quant_backtest/data）：
交易日缺口、manifest 行数不闭合、同键多版本（一致/冲突/并列 fetched_at）、
候选 0 行、去重 dry-run、只读零写、CLI 退出码。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import acad_d4_freeze_check as chk

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

CAL_SCHEMA = pa.schema([
    ("exchange", pa.large_string()), ("cal_date", pa.large_string()),
    ("is_open", pa.float64()), ("pretrade_date", pa.large_string()),
])
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


def _write_parquet(path: Path, rows: list[dict], schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _tl(ts_code, trade_date, net_amount=1.0, fetched="2026-10-01 00:00:00"):
    return dict(ts_code=ts_code, trade_date=trade_date, name=ts_code.split(".")[0],
                close=10.0, pct_change=1.0, net_amount=net_amount, l_buy=1.0,
                l_sell=1.0, amount=1.0, reason="r", _fetched_at=fetched)


def _ti(ts_code, trade_date, exalter="机构专用", side="0", net_buy=1.0,
        fetched="2026-10-01 00:00:00"):
    return dict(ts_code=ts_code, trade_date=trade_date, exalter=exalter, side=side,
                buy=1.0, sell=1.0, net_buy=net_buy, _fetched_at=fetched)


def _cal_rows() -> list[dict]:
    opens = ["20230731", "20230801", "20230802", "20230803", "20230804",
             "20230807", "20230808"]
    rows = [dict(exchange="SSE", cal_date=d, is_open=1.0, pretrade_date="") for d in opens]
    rows += [dict(exchange="SSE", cal_date="20230805", is_open=0.0, pretrade_date="20230804"),
             dict(exchange="SSE", cal_date="20230806", is_open=0.0, pretrade_date="20230804")]
    # 非 SSE 交易所同日的开市行不应计入 SSE 口径
    rows.append(dict(exchange="SZSE", cal_date="20230805", is_open=1.0, pretrade_date="20230804"))
    return rows


def make_fake_root(tmp_path: Path, *, manifest: bool = True,
                   with_candidate: bool = False) -> Path:
    """造一个最小 D4 数据根：
    - 交易日 20230731~20230804 共 5 个 SSE 开市日，D4 缺 20230802（覆盖 4 天）；
    - top_list 8 行 / 5 键：A 键冲突（fetched 并列）、B 键一致重复、E 键冲突（fetched 可择新）；
    - top_inst 7 行 / 5 键：A0 键冲突（并列）、A1 键一致重复；
    - manifest top_list 少 2 行（+2 不闭合），top_inst 对齐。
    """
    root = tmp_path / "data"
    _write_parquet(root / "tdx/ts_trade_cal/part_0000.parquet", _cal_rows(), CAL_SCHEMA)

    top_list = [
        _tl("A.SH", "20230731", net_amount=100.0),          # 冲突键（同一 fetched_at）
        _tl("A.SH", "20230731", net_amount=200.0),
        _tl("B.SH", "20230801", fetched="2026-10-01 00:00:00"),  # 完全一致重复
        _tl("B.SH", "20230801", fetched="2026-10-02 00:00:00"),
        _tl("C.SH", "20230803"),
        _tl("D.SH", "20230804"),
        _tl("E.SH", "20230804", net_amount=1.0, fetched="2026-10-01 00:00:00"),  # 冲突可择新
        _tl("E.SH", "20230804", net_amount=2.0, fetched="2026-10-09 00:00:00"),
    ]
    _write_parquet(root / "tdx/ts_top_list/part_0000.parquet", top_list[:5], TOP_LIST_SCHEMA)
    _write_parquet(root / "tdx/ts_top_list/part_0001.parquet", top_list[5:], TOP_LIST_SCHEMA)

    top_inst = [
        _ti("A.SH", "20230731", exalter="机构专用", side="0", net_buy=10.0),  # 冲突（并列）
        _ti("A.SH", "20230731", exalter="机构专用", side="0", net_buy=20.0),
        _ti("A.SH", "20230731", exalter="机构专用", side="1", net_buy=1.0),   # 一致重复
        _ti("A.SH", "20230731", exalter="机构专用", side="1", net_buy=1.0),
        _ti("B.SH", "20230801"),
        _ti("C.SH", "20230803"),
        _ti("D.SH", "20230804"),
    ]
    _write_parquet(root / "tdx/ts_top_inst/part_0000.parquet", top_inst, TOP_INST_SCHEMA)

    if with_candidate:
        cand = [_tl("600519.SH", "20230804", net_amount=9.0, fetched="2026-10-03 00:00:00")]
        _write_parquet(root / "tdx/ts_top_list/part_0002.parquet", cand, TOP_LIST_SCHEMA)
        top_list = top_list + cand

    if manifest:
        man = {
            "ts_top_list": {"offset": 0, "part": 3 if with_candidate else 2,
                            "rows": len(top_list) - 2, "done": True},
            "ts_top_inst": {"offset": 0, "part": 1, "rows": len(top_inst), "done": True},
        }
        (root / "tdx/_manifest.json").write_text(
            json.dumps(man, ensure_ascii=False), encoding="utf-8")
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (_sha(p), st.st_size)
    return out


# ---------------------------------------------------------------- 核心对账

def test_full_report_on_fake_data(tmp_path):
    root = make_fake_root(tmp_path)
    rep = chk.run_check(root)

    assert rep["schema"] == chk.SCHEMA
    assert rep["data_root"] == str(root)
    tl, ti = rep["tables"]["top_list"], rep["tables"]["top_inst"]
    assert (tl["files"], tl["rows"], tl["manifest_rows"], tl["manifest_row_diff"]) == (2, 8, 6, 2)
    assert tl["manifest_ok"] is False
    assert (ti["files"], ti["rows"], ti["manifest_rows"], ti["manifest_row_diff"]) == (1, 7, 7, 0)
    assert ti["manifest_ok"] is True

    # 交易日：范围 0731~0804，SSE 应开 5 天，覆盖 4 天，缺 20230802
    tc = rep["trade_cal"]
    assert tc["exchange"] == "SSE"
    assert tc["range"] == ["20230731", "20230804"]
    assert tc["expected_open_days"] == 5
    assert tc["covered_days"] == 4
    assert tc["missing_days"] == ["20230802"]
    assert tc["extra_days"] == []
    assert tc["coverage_ok"] is False

    # top_list 重复：A/B/E 三键；B 一致；A/E 冲突；A 并列 max(_fetched_at)、E 可唯一择新
    dl = rep["duplicates"]["top_list"]
    assert dl["rows"] == 8 and dl["keys"] == 5
    assert dl["dup_keys"] == 3 and dl["dup_extra_rows"] == 3
    assert dl["identical_keys"] == 1
    assert dl["conflicting_keys"] == 2
    assert dl["conflict_newest_tied"] == 1
    assert dl["conflict_newest_unique"] == 1
    assert len(dl["examples"]) == 2

    # top_inst 重复：A.side0 冲突（并列）、A.side1 一致
    di = rep["duplicates"]["top_inst"]
    assert di["rows"] == 7 and di["keys"] == 5
    assert di["dup_keys"] == 2 and di["dup_extra_rows"] == 2
    assert di["identical_keys"] == 1 and di["conflicting_keys"] == 1
    assert di["conflict_newest_tied"] == 1

    # 阻塞缺口 4 项：交易日缺失 / manifest 不闭合 / 冲突键 / 去重歧义
    assert set(rep["gaps"]) == {"trade_cal_missing_days", "manifest_mismatched_tables",
                                "duplicate_conflicting_keys", "dedup_ambiguous_keys"}
    assert rep["gaps_count"] == 4
    assert rep["findings"] == {"candidate_zero_rows": chk.DEFAULT_CANDIDATE}
    assert rep["findings_count"] == 1
    assert "D4 未闭合" in rep["verdict"]
    assert rep["readonly"]["unchanged"] is True
    assert rep["readonly"]["files"] > 0


def test_dedup_dryrun_plan_counts(tmp_path):
    root = make_fake_root(tmp_path)
    rep = chk.run_check(root)
    plan = rep["dedup_plan"]["tables"]
    assert rep["dedup_plan"]["mode"] == "dry-run（不写数据）"
    assert "max(_fetched_at)" in rep["dedup_plan"]["rule"]
    assert plan["top_list"] == {
        "rows_before": 8, "rows_after": 5, "rows_dropped": 3,
        "conflicting_keys": 2, "newest_unique_keys": 1, "newest_tied_keys": 1,
    }
    assert plan["top_inst"]["rows_before"] == 7
    assert plan["top_inst"]["rows_after"] == 5
    assert plan["top_inst"]["rows_dropped"] == 2


def test_candidate_absent_is_l3_unknown(tmp_path):
    root = make_fake_root(tmp_path)
    rep = chk.run_check(root)
    cand = rep["candidate"]
    assert cand["code"] == "600519.SH"
    assert cand["top_list_rows"] == 0 and cand["top_inst_rows"] == 0
    assert cand["l3"] == "unknown"
    assert "candidate_zero_rows" in rep["findings"]


def test_candidate_present_is_l3_present(tmp_path):
    root = make_fake_root(tmp_path, with_candidate=True)
    rep = chk.run_check(root, candidate="600519.SH")
    cand = rep["candidate"]
    assert cand["top_list_rows"] == 1 and cand["top_inst_rows"] == 0
    assert cand["l3"] == "present"
    assert "candidate_zero_rows" not in rep["findings"]


def test_custom_candidate_code(tmp_path):
    root = make_fake_root(tmp_path)
    rep = chk.run_check(root, candidate="A.SH")
    assert rep["candidate"]["top_list_rows"] == 2
    assert rep["candidate"]["l3"] == "present"


def test_manifest_missing_is_gap_not_crash(tmp_path):
    root = make_fake_root(tmp_path, manifest=False)
    rep = chk.run_check(root)
    assert "manifest_error" in rep["gaps"]
    assert rep["tables"]["top_list"]["manifest_rows"] is None
    assert rep["tables"]["top_list"]["rows"] == 8
    assert rep["readonly"]["unchanged"] is True


def test_manifest_aligned_no_manifest_gap(tmp_path):
    root = make_fake_root(tmp_path)
    man = json.loads((root / "tdx/_manifest.json").read_text(encoding="utf-8"))
    man["ts_top_list"]["rows"] = 8  # 对齐实际
    (root / "tdx/_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    rep = chk.run_check(root)
    assert "manifest_mismatched_tables" not in rep["gaps"]
    assert rep["tables"]["top_list"]["manifest_ok"] is True


# ---------------------------------------------------------------- 只读 / CLI

def test_readonly_zero_write(tmp_path):
    root = make_fake_root(tmp_path)
    before = _files(root)
    rep = chk.run_check(root)
    after = _files(root)
    assert before == after, "对账工具不得改写/新增/删除任何数据文件"
    assert rep["readonly"]["unchanged"] is True
    assert rep["readonly"]["files"] == len(before)


def test_cli_json_and_human(tmp_path, capsys):
    root = make_fake_root(tmp_path)
    assert chk.main(["--data-root", str(root), "--json"]) == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    assert rep["schema"] == chk.SCHEMA
    assert "gaps" in rep and "dedup_plan" in rep

    assert chk.main(["--data-root", str(root)]) == 0
    human = capsys.readouterr().out
    assert "D4 冻结对账" in human
    assert "top_list" in human and "候选 600519.SH" in human
    assert "D4 未闭合" in human
    lines = human.strip().splitlines()
    assert 8 <= len(lines) <= 12


def test_cli_missing_data_root_exit_1(tmp_path, capsys):
    rc = chk.main(["--data-root", str(tmp_path / "nope")])
    assert rc == 1
    assert "数据根不存在" in capsys.readouterr().out


def test_cli_missing_table_exit_1(tmp_path, capsys):
    root = tmp_path / "data"
    _write_parquet(root / "tdx/ts_trade_cal/part_0000.parquet", _cal_rows(), CAL_SCHEMA)
    rc = chk.main(["--data-root", str(root)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "D4 对账失败" in out
    assert "top_list" in out


def test_cli_usage_error_exit_2():
    with pytest.raises(SystemExit) as exc:
        chk.main(["--nope"])
    assert exc.value.code == 2


def test_resolve_data_root_env(tmp_path, monkeypatch):
    env_root = tmp_path / "env_data"
    env_root.mkdir()
    monkeypatch.setenv("ACAD_D4_DATA_ROOT", str(env_root))
    assert chk.resolve_data_root(None) == env_root.resolve()
    assert chk.resolve_data_root(str(tmp_path)) == tmp_path.resolve()
