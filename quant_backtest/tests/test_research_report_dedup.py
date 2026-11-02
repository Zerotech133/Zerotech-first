# -*- coding: utf-8 -*-
"""G4 修复单测：research_report 采集侧按自然键去重追加（tushare_sync.save_full_dedup）。

覆盖（6 用例）：
  1) 自然键常量与要求一致；
  2) 只追加新键行（已有键不重复写，既有 part 不改）；
  3) 批次内重复键去重保留最后一行（keep="last"）；
  4) 无新增/空输入 → 不产生新 part，manifest rows=0（避免滚动快照重复堆积）；
  5) 键列缺失 → fail-open 回退 save_full（不丢数据）；
  6) NaN 键归一（nan!=nan 陷阱）：ts_code 为 NaN 的行可正确判重。

隔离：monkeypatch module.OUT/MANIFEST 到 tmp_path；**不连网、不读写真实数据根**。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
QUANT = REPO / "quant_backtest"

COLUMNS = ["trade_date", "title", "report_type", "author", "name",
           "ts_code", "inst_csname", "ind_name", "url"]


def _row(date, title, rtype, ts_code, author="某证券"):
    return {"trade_date": date, "title": title, "report_type": rtype,
            "author": author, "name": "某公司", "ts_code": ts_code,
            "inst_csname": "某机构", "ind_name": "某行业", "url": "http://x"}


def _df(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


@pytest.fixture()
def ts(tmp_path, monkeypatch):
    """加载 tushare_sync（data 路径全部指向 tmp_path，避免触碰真实数据）。"""
    if str(QUANT) not in sys.path:
        sys.path.insert(0, str(QUANT))
    try:
        import tushare_sync
    except SystemExit as exc:  # 干净环境无 .env.local 时跳过（本机/CI 有 token）
        pytest.skip("tushare_sync 需 quant_backtest/.env.local XT_DATA_KEY 才能 import：%s" % exc)
    monkeypatch.setattr(tushare_sync, "OUT", str(tmp_path / "data"))
    monkeypatch.setattr(tushare_sync, "MANIFEST", str(tmp_path / "data" / "_manifest.json"))
    return tushare_sync


def _parts(ts, tbl="research_report"):
    return sorted(Path(ts.OUT, tbl).glob("part_*.parquet"))


def test_key_constant_matches_spec(ts):
    assert ts.RESEARCH_REPORT_KEY == ("trade_date", "ts_code", "title", "report_type")


def test_appends_only_new_rows_and_keeps_old_parts(ts):
    old = _row("20260713", "旧研报_20260713.pdf", "个股研报", "000001.SZ")
    ts.save_full("research_report", _df([old]), {})
    assert len(_parts(ts)) == 1
    old_rows_before = len(pd.read_parquet(_parts(ts)[0]))

    new_dup = _row("20260713", "旧研报_20260713.pdf", "个股研报", "000001.SZ")
    new_row = _row("20260714", "新研报_20260714.pdf", "行业研报", "600000.SH")
    m = {}
    added = ts.save_full_dedup("research_report", _df([new_dup, new_row]), m,
                               ts.RESEARCH_REPORT_KEY, "测试")
    parts = _parts(ts)
    assert added == 1 and len(parts) == 2                     # 只追加 1 行新 part
    assert len(pd.read_parquet(parts[0])) == old_rows_before   # 旧 part 未被改写
    appended = pd.read_parquet(parts[1])
    assert len(appended) == 1 and appended.iloc[0]["title"] == "新研报_20260714.pdf"
    assert m["research_report"]["rows"] == 1
    # 合并后自然键唯一
    allrows = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    assert len(allrows) == 2
    assert len(allrows.drop_duplicates(subset=list(ts.RESEARCH_REPORT_KEY))) == 2


def test_batch_duplicates_keep_last(ts):
    a = _row("20260715", "同一篇_20260715.pdf", "个股研报", "000002.SZ", author="旧作者")
    b = _row("20260715", "同一篇_20260715.pdf", "个股研报", "000002.SZ", author="新作者")
    added = ts.save_full_dedup("research_report", _df([a, b]), {},
                               ts.RESEARCH_REPORT_KEY, "测试")
    assert added == 1 and len(_parts(ts)) == 1
    got = pd.read_parquet(_parts(ts)[0])
    assert len(got) == 1
    assert got.iloc[0]["author"] == "新作者"    # keep="last"


def test_no_new_rows_or_empty_writes_no_part(ts):
    ts.save_full("research_report",
                 _df([_row("20260716", "已有_20260716.pdf", "个股研报", "000003.SZ")]), {})
    m = {}
    added = ts.save_full_dedup("research_report",
                               _df([_row("20260716", "已有_20260716.pdf", "个股研报", "000003.SZ")]),
                               m, ts.RESEARCH_REPORT_KEY, "测试")
    assert added == 0
    assert len(_parts(ts)) == 1                 # 无重复 part 产生
    assert m["research_report"]["rows"] == 0
    assert "dedup 0 new" in m["research_report"]["note"]

    m2 = {}
    added2 = ts.save_full_dedup("research_report", _df([]), m2,
                                ts.RESEARCH_REPORT_KEY, "测试空")
    assert added2 == 0 and len(_parts(ts)) == 1
    assert m2["research_report"]["rows"] == 0


def test_missing_key_column_falls_back_to_save_full(ts):
    df = _df([_row("20260717", "缺列_20260717.pdf", "个股研报", "000004.SZ"),
              _row("20260717", "缺列_20260717.pdf", "个股研报", "000004.SZ")])
    df = df.drop(columns=["report_type"])       # 自然键缺一列 → fail-open
    m = {}
    added = ts.save_full_dedup("research_report", df, m,
                               ts.RESEARCH_REPORT_KEY, "测试缺列")
    assert added == 2                           # 回退 save_full：按原逻辑写入
    assert len(_parts(ts)) == 1
    assert "dedup_fallback" in m["research_report"].get("note", "")


def test_nan_key_rows_deduped(ts):
    nan = float("nan")
    row = _row("20260718", "行业研报_20260718.pdf", "行业研报", nan)
    ts.save_full("research_report", _df([row]), {})
    m = {}
    added = ts.save_full_dedup("research_report", _df([row]), m,
                               ts.RESEARCH_REPORT_KEY, "测试NaN")
    assert added == 0                           # NaN 键归一为 None → 判重成功
    assert len(_parts(ts)) == 1
    assert m["research_report"]["rows"] == 0
