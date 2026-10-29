# -*- coding: utf-8 -*-
"""k_stock 测试共享夹具（P0 #3 步骤2，2026-10-29）。

目标：让"mark 快照"类测试不再依赖真实行情湖与真实日期。
- frozen_lake：生成合成 parquet（日历基准票 + 若干标的），日期固定到 FROZEN_DAY；
- 自动注入 KSTOCK_PAPER_NOW=FROZEN_DAY（与合成湖一致），测试因此与真实日期/真实湖解耦。
"""
import pytest

FROZEN_DAY = "20261027"
FROZEN_DAYS = ["20261022", "20261023", "20261026", "20261027"]
CAL_CODE = "000001.SZ"


def _write_parquet(path, code, days):
    import pandas as pd

    base = 10.0
    rows = []
    for i, d in enumerate(days):
        close = base + i * 0.1
        rows.append({
            "ts_code": code,
            "trade_date": d,
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "pre_close": close - 0.1,
            "change": 0.1,
            "pct_chg": 1.0,
            "volume": 10000.0,
            "amount": 100000.0,
        })
    pd.DataFrame(rows).to_parquet(path, index=False)


@pytest.fixture
def frozen_lake(tmp_path, monkeypatch):
    """合成行情湖（仅日历基准票+1 备选），返回 ashare 目录路径。"""
    ashare = tmp_path / "ashare"
    ashare.mkdir(parents=True, exist_ok=True)
    _write_parquet(ashare / (CAL_CODE.replace(".", "_") + ".parquet"), CAL_CODE, FROZEN_DAYS)
    frozen_iso = "%s-%s-%sT18:00:00+08:00" % (FROZEN_DAY[:4], FROZEN_DAY[4:6], FROZEN_DAY[6:8])
    monkeypatch.setenv("KSTOCK_PAPER_NOW", frozen_iso)
    # _mark_selected 内部 `from market import ParquetMarket` 并自建实例 → patch 模块默认目录
    try:
        import sys as _sys
        from pathlib import Path as _Path
        paper_dir = _Path(__file__).resolve().parents[1] / "paper"
        if str(paper_dir) not in _sys.path:
            _sys.path.insert(0, str(paper_dir))
        import market as _market
        monkeypatch.setattr(_market, "DEFAULT_ASHARE_DIR", ashare)
    except Exception as exc:  # 夹具不得静默失败
        raise AssertionError("frozen_lake 无法 patch market.DEFAULT_ASHARE_DIR: %r" % exc)
    return ashare
