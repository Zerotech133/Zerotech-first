# -*- coding: utf-8 -*-
"""HK R1 每日跑批脚本单测（解析/干跑/台账表格插入，不执行真实跑批）。"""
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "hk_r1_daily.py"


def _load():
    spec = importlib.util.spec_from_file_location("hk_r1_daily", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE = """HK 账本 hk_qingping 撮合: 成交 3 / 搁置 0（refresh 1 项）
  #826 03690.HK buy 1888 股 @ 71.85（费 176.35）
  #827 01810.HK buy 4670 股 @ 25.80（费 156.63）
  #828 01299.HK buy 1511 股 @ 71.50（费 140.45）
现金 635,351.27 HKD | 持仓市值 360,352.00 | 总资产 995,703.27 | 仓位 36.2% | 对账 OK
快照 2026-10-28: 总资产 995,703.27 HKD 仓位 36.2% 待撮合 0 对账 OK
"""

LEDGER = """# HK R1 台账

| 日期 | 湖最新 | 成交笔数 | 费用合计 | 现金 | 持仓市值 | 总资产 | 仓位 | 对账 | 备注 |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 2026-10-28 | 20261028 | 3 | 473.43 | 1 | 2 | 3 | 36.2% | OK | D0 |

## 异常记录

- 无
"""


def test_parse_run_fills_and_equity():
    m = _load()
    p = m.parse_run(SAMPLE)
    assert p["fill_count"] == 3
    assert round(p["fee_total"], 2) == 473.43
    assert p["cash"] == 635351.27
    assert p["total"] == 995703.27
    assert round(p["pos_pct"] * 100, 1) == 36.2
    assert p["reconcile"] == "OK"
    assert p["pending"] == 0


def test_build_row_format():
    m = _load()
    p = m.parse_run(SAMPLE)
    row = m.build_row(p, "20261028")
    assert row.startswith("| ")
    assert "20261028" in row and "473.43" in row and "OK" in row


def test_dry_run_does_not_touch_ledger(tmp_path):
    m = _load()
    ledger = tmp_path / "hk_r1_observation_v1.md"
    ledger.write_text("# 台账\n", encoding="utf-8")
    rep = m.run(append=False, ledger=ledger)
    assert rep["ok"] and rep["dry_run"] is True
    assert ledger.read_text(encoding="utf-8") == "# 台账\n"
    assert len(rep["commands"]) == 4


def test_parse_failed_returns_empty():
    m = _load()
    p = m.parse_run("nothing useful")
    assert p["fill_count"] == 0 and "total" not in p


def test_insert_row_into_table_and_dedupe(tmp_path):
    m = _load()
    p = tmp_path / "ledger.md"
    p.write_text(LEDGER, encoding="utf-8")
    row = "| 2026-10-29 | 20261029 | 0 | 0.00 | 1 | 2 | 3 | 36.1% | OK | 自动跑批；0 成交 |"
    rep = m.insert_row(p, row)
    assert rep["ok"] and rep["skipped"] is False
    lines = p.read_text(encoding="utf-8").splitlines()
    # 行必须落在「表格内、异常记录之前」
    assert row in lines
    assert lines.index(row) < lines.index("## 异常记录")
    assert lines[lines.index(row) - 1].startswith("| 2026-10-28")
    # 同日重复 → 跳过，不重复写
    rep2 = m.insert_row(p, row)
    assert rep2["ok"] and rep2["skipped"] is True
    assert p.read_text(encoding="utf-8").count(row) == 1
