"""归档目录拆分回归：回填重算集不得再落进台账归档目录。

背景（2026-11-08）：`chanlun_paper_archive/closed_CN_<YYYYMM>.jsonl` 曾被两个
写入者共用——`chanlun_scan._archive_closed()` 写台账 CLOSED_CAP 溢出（产线实际
入场），`chanlun_ledger_maint.archive()` 写池+parquet 重算的回填结算集（"假如
每条信号都入场 20 日"的模拟）。实测 9,938 行里 9,745 行是回填，任何整读该目录
的代码都会把模拟当真实入场，台账被虚增 1,678 → 2,560。

本测试锁三件事：
  1. 两个目录必须是不同的路径（回填不得写回台账归档）；
  2. `split()` 能把混写的文件拆干净，且幂等、有备份；
  3. `archive()` 只写 BACKFILL_DIR，且在 `(code,type,dt)` 上去重。
"""
import importlib.util
import json
from pathlib import Path

import pytest


def _load(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "chanlun_ledger_maint.py"
    spec = importlib.util.spec_from_file_location("chanlun_ledger_maint_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 全部改到 tmp_path，绝不碰真实数据
    monkeypatch.setattr(module, "DATA", str(tmp_path))
    monkeypatch.setattr(module, "LEDGER_ARCHIVE", str(tmp_path / "chanlun_paper_archive"))
    monkeypatch.setattr(module, "BACKFILL_DIR", str(tmp_path / "chanlun_paper_backfill"))
    (tmp_path / "chanlun_paper_archive").mkdir()
    return module


def ledger_row(code="600000.SH", typ="2买", sig="2026-06-01", ed="2026-06-03",
               exit_d="2026-06-10"):
    """台账溢出风格：有 sig_dt / entry_px，没有池字段 dt/entry_i。"""
    return {"code": code, "type": typ, "sig_dt": sig, "entry_date": ed,
            "exit_date": exit_d, "entry_px": 10.0, "last_px": 10.5, "ret": 0.05}


def backfill_row(code="000001.SZ", typ="1买", dt="2026-06-01", exit_d="2026-06-10"):
    """回填重算集风格：带 dt/entry_i/source=backfill。"""
    return {"code": code, "type": typ, "dt": dt, "entry_i": 100,
            "entry_date": dt, "exit_date": exit_d, "entry_px": 9.0,
            "source": "backfill", "_bars_len": 500}


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(s) for s in f if s.strip()]


def test_two_dirs_must_differ(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    assert m.BACKFILL_DIR != m.LEDGER_ARCHIVE
    assert m.BACKFILL_DIR.endswith("chanlun_paper_backfill")
    assert m.LEDGER_ARCHIVE.endswith("chanlun_paper_archive")


@pytest.mark.parametrize("row,expected", [
    (backfill_row(), True),
    ({"source": "backfill", "sig_dt": "2026-06-01"}, True),      # 显式标注
    ({"sig_dt": "2026-06-01", "entry_px": 10.0}, False),         # 台账风格
    ({"dt": "2026-06-01", "entry_px": 10.0}, True),              # 带池字段
    ({"code": "x", "entry_px": 10.0}, True),                     # 无 sig_dt
])
def test_is_backfill_row(monkeypatch, tmp_path, row, expected):
    m = _load(monkeypatch, tmp_path)
    assert m.is_backfill_row(row) is expected


def test_split_separates_and_backs_up(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    src = Path(m.LEDGER_ARCHIVE) / "closed_CN_202606.jsonl"
    _write(src, [ledger_row("600000.SH"), backfill_row("000001.SZ"),
                 ledger_row("600001.SH", sig="2026-06-05"),
                 backfill_row("000002.SZ"), backfill_row("000003.SZ")])

    m.split()

    kept = _read(src)
    assert len(kept) == 2
    assert all("sig_dt" in r for r in kept)
    moved = _read(Path(m.BACKFILL_DIR) / "backfill_CN_202606.jsonl")
    assert len(moved) == 3
    assert all(r["source"] == "backfill" for r in moved)
    # 备份必须留原始 5 行，不删任何字节
    baks = list(Path(m.DATA).glob("_bak_archive_split_*/closed_CN_202606.jsonl"))
    assert len(baks) == 1 and len(_read(baks[0])) == 5


def test_split_is_idempotent(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    src = Path(m.LEDGER_ARCHIVE) / "closed_CN_202607.jsonl"
    _write(src, [ledger_row(exit_d="2026-07-02"), backfill_row(exit_d="2026-07-02")])
    m.split()
    before = len(_read(Path(m.BACKFILL_DIR) / "backfill_CN_202607.jsonl"))
    m.split()                                  # 再跑一次不应产生新行
    after = len(_read(Path(m.BACKFILL_DIR) / "backfill_CN_202607.jsonl"))
    assert before == after == 1


def test_split_no_empty_backup_dir_when_nothing_to_move(monkeypatch, tmp_path):
    """无事可搬时不该留下空备份目录。

    2026-11-08 实测踩到：`split()` 原先在开头就 makedirs，幂等重跑时一行都不搬，
    却留下 0 字节的 `_bak_archive_split_20261108-091946`。目录若先建后弃，
    下次清理时就得靠人工判断"这个空目录是什么"，成本远高于按需建。
    """
    m = _load(monkeypatch, tmp_path)
    src = Path(m.LEDGER_ARCHIVE) / "closed_CN_202607.jsonl"
    _write(src, [ledger_row(exit_d="2026-07-02")])      # 干净：只有台账行
    m.split()
    assert list(Path(m.DATA).glob("_bak_archive_split_*")) == []
    assert not Path(m.BACKFILL_DIR).exists()            # 回填目录同样不该建
    assert len(_read(src)) == 1                          # 台账行原样保留


def test_archive_writes_backfill_dir_and_dedups(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    rows = [dict(backfill_row("000004.SZ"), status="closed", ret=0.01),
            dict(backfill_row("000005.SZ"), status="closed", ret=-0.02),
            dict(backfill_row("000006.SZ"), status="open")]      # open 不入归档
    full = {"rows": rows}
    m.archive(full)
    tgt = Path(m.BACKFILL_DIR) / "backfill_CN_202606.jsonl"
    assert len(_read(tgt)) == 2
    # 台账归档目录必须一无所获
    assert list(Path(m.LEDGER_ARCHIVE).glob("*.jsonl")) == []
    # 重复跑不重复写
    m.archive(full)
    assert len(_read(tgt)) == 2
