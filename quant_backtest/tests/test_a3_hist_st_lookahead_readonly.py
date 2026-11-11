"""只读函数边界反例：隔离执行生产函数，不导入重型回测模块。"""

import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


QBT = Path(__file__).resolve().parents[1]


def _function_from_source(filename, name, namespace):
    tree = ast.parse((QBT / filename).read_text(encoding="utf-8"), filename=filename)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


class _Series:
    def __init__(self, values):
        self.values = list(values)

    def __iter__(self):
        return iter(self.values)

    def astype(self, _):
        return _Series(map(str, self.values))

    @property
    def str(self):
        return self

    def upper(self):
        return _Series(v.upper() for v in self.values)

    def contains(self, text):
        return _Series(text in v for v in self.values)

    def fillna(self, value):
        return _Series(value if v is None else v for v in self.values)

    def __le__(self, other):
        return _Series(v <= other for v in self.values)

    def __ge__(self, other):
        return _Series(v >= other for v in self.values)

    def __and__(self, other):
        return _Series(a and b for a, b in zip(self.values, other.values))


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, key):
        if isinstance(key, str):
            return _Series(row[key] for row in self.rows)
        return _Frame([row for row, include in zip(self.rows, key) if include])


def test_future_st_in_window_removes_earlier_signal(tmp_path, monkeypatch):
    # 只在 pytest 临时目录放路径哨兵和合成 JSON；不读取真实 parquet/事件缓存。
    namechange_path = tmp_path / "synthetic_namechange.parquet"
    namechange_path.touch()
    history = _Frame([
        {"ts_code": "600001.SH", "name": "*ST 测试", "start_date": "20260110", "end_date": "20260112"},
        {"ts_code": "600002.SH", "name": "普通测试", "start_date": "20260101", "end_date": None},
    ])

    def read_parquet(path):
        assert Path(path) == namechange_path
        return history

    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(read_parquet=read_parquet))
    hist_st_set = _function_from_source("market_scan.py", "hist_st_set", {
        "os": os, "NC_FP": str(namechange_path),
    })
    assert hist_st_set("20260105", "20260105") == set()
    assert hist_st_set("20260105", "20260115") == {"600001.SH"}

    monkeypatch.setitem(sys.modules, "market_scan", SimpleNamespace(hist_st_set=hist_st_set))
    calc = _function_from_source("backtest_engine_v2.py", "calc_chanlun_signals_market", {
        "os": os,
        "json": json,
        "BASE": str(tmp_path),
        "cal_idx_all": ["20260105", "20260113"],
        "BUY_FILTERS": {"3": {"3买"}},
        "_resolve_buy_filter": {"3": {"3买"}}.get,
        # 2026-11-11：A4 收口后 buy_filter 走 _resolve_buy_filter，夹具缺它 → NameError（hist_st 分支从未被测到）
        "BUY_FILTER_LABEL": {"3": "仅三买"},
        "SELL_TYPES": {"1卖", "2卖", "3卖"},
    })
    events = [
        {"stock": "600001.SH", "type": "3买", "day": "20260105", "score": 80},
        {"stock": "600002.SH", "type": "3买", "day": "20260105", "score": 70},
        {"stock": "600001.SH", "type": "3买", "day": "20260113", "score": 60},
    ]
    cache_dir = tmp_path / "quant_backtest" / "data" / "sig_events"
    cache_dir.mkdir(parents=True)
    for end in ("20260105", "20260115"):
        (cache_dir / f"sig_events_market_v3_20260105_{end}.json").write_text(
            json.dumps({"meta": {"universe": 2}, "events": events}), encoding="utf-8"
        )

    short_buy, _, short_meta, short_error = calc("20260105", "20260105", hist_st=True)
    long_buy, _, long_meta, long_error = calc("20260105", "20260115", hist_st=True)

    assert short_error is None and long_error is None
    assert short_buy["20260105"] == {"600001.SH", "600002.SH"}
    assert short_meta["hist_st"]["n_events_drop"] == 0
    assert long_buy["20260105"] == {"600002.SH"}
    assert long_buy["20260113"] == set()
    assert long_meta["hist_st"] == {"on": True, "n_codes": 1, "n_events_drop": 2}
    assert long_meta["pool_daily"]["20260105"] == {"pool": 1, "trade": 1}
