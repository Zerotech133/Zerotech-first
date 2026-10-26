# -*- coding: utf-8 -*-
"""利率新鲜度检查单测（K-DATA-20261026-SHIBOR）。

覆盖：
  · _rate_level 纯函数（shibor 日频阈值 / lpr 历史阈值 / 边界）
  · collect_shibor_lpr_freshness 的读取优先级（整理层 → 原始层 → LPR 回退）
    —— 用 tmp_path 造最小 parquet 夹具，不碰真实数据根。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gen_health_card as hc  # noqa: E402

NOW = datetime(2026, 10, 26, 9, 0, 0)


def test_rate_level_shibor_thresholds():
    assert hc._rate_level(1, "shibor-curated")[0] == "green"
    assert hc._rate_level(4, "shibor-raw")[0] == "green"
    assert hc._rate_level(5, "shibor-raw")[0] == "yellow"
    assert hc._rate_level(9, "shibor-curated")[0] == "red"


def test_rate_level_lpr_thresholds_unchanged():
    assert hc._rate_level(10, "lpr")[0] == "green"
    assert hc._rate_level(20, "lpr")[0] == "yellow"
    assert hc._rate_level(67, "lpr")[0] == "red"


def _write_parquet(path: Path, dates: list[str]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.table({"date": dates, "1y": [3.0] * len(dates)})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path))


def test_collect_prefers_curated_shibor(tmp_path):
    curated = tmp_path / "tushare_curated"
    _write_parquet(curated / "shibor.parquet", ["20261020", "20261023"])
    _write_parquet(curated / "shibor_lpr.parquet", ["20260820"])
    block = hc.collect_shibor_lpr_freshness(curated, NOW)
    assert block["available"] is True
    assert block["source"] == "shibor-curated"
    assert block["max_date"] == "2026-10-23"
    assert block["level"] == "green"


def test_collect_falls_back_to_raw_shibor(tmp_path):
    curated = tmp_path / "tushare_curated"
    curated.mkdir(parents=True)
    raw = tmp_path / "tushare" / "shibor"
    _write_parquet(raw / "part_0001.parquet", ["20261015"])
    _write_parquet(raw / "part_0002.parquet", ["20261023"])
    block = hc.collect_shibor_lpr_freshness(curated, NOW)
    assert block["source"] == "shibor-raw"
    assert block["max_date"] == "2026-10-23"
    assert block["level"] == "green"


def test_collect_falls_back_to_lpr_with_old_thresholds(tmp_path):
    curated = tmp_path / "tushare_curated"
    _write_parquet(curated / "shibor_lpr.parquet", ["20260820"])
    block = hc.collect_shibor_lpr_freshness(curated, NOW)
    assert block["source"] == "lpr"
    # 67 天：按 lpr 阈值 >60 → 红（保留 fail-visible 语义）
    assert block["level"] == "red"


def test_collect_missing_everything_is_yellow(tmp_path):
    curated = tmp_path / "tushare_curated"
    curated.mkdir(parents=True)
    block = hc.collect_shibor_lpr_freshness(curated, NOW)
    assert block["available"] is False
    assert block["level"] == "yellow"
    assert block["status"] == "missing"
