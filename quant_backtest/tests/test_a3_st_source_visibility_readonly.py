"""只读合成反例：观察历史 ST 数据源缺失、公告时点与状态生效日的现有边界。"""

import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


QBT = Path(__file__).resolve().parents[1]
STOCK = "600001.SH"
OTHER = "600002.SH"


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
    def __init__(self, rows, accessed):
        self.rows = rows
        self.accessed = accessed

    def __getitem__(self, key):
        if isinstance(key, str):
            self.accessed.add(key)
            return _Series(row[key] for row in self.rows)
        return _Frame([row for row, include in zip(self.rows, key) if include], self.accessed)


def _isolated_functions(tmp_path, monkeypatch, rows=None):
    namechange_path = tmp_path / "synthetic_namechange.parquet"
    accessed = set()
    if rows is not None:
        namechange_path.touch()  # 仅创建临时路径哨兵；绝不读取真实 parquet。

    def read_parquet(path):
        assert Path(path) == namechange_path
        assert rows is not None, "缺失文件不应调用 read_parquet"
        return _Frame(rows, accessed)

    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(read_parquet=read_parquet))
    hist_st_set = _function_from_source("market_scan.py", "hist_st_set", {
        "os": os, "NC_FP": str(namechange_path),
    })
    monkeypatch.setitem(sys.modules, "market_scan", SimpleNamespace(hist_st_set=hist_st_set))
    buy_filters = {"3": {"3买"}}
    calc = _function_from_source("backtest_engine_v2.py", "calc_chanlun_signals_market", {
        "os": os,
        "json": json,
        "BASE": str(tmp_path),
        "cal_idx_all": ["20260105", "20260110", "20260113"],
        "BUY_FILTERS": buy_filters,
        "BUY_FILTER_LABEL": {"3": "仅三买"},
        "SELL_TYPES": {"1卖", "2卖", "3卖"},
        # 2026-11-10 A4 收口：buy_filter 改经 _resolve_buy_filter 强解析
        # （禁止静默回落）。夹具缺它 → NameError，hist_st 分支从未被测到
        # （2026-11-11 修复）。测试只用合法值 "3"，永不触发 ValueError。
        "_resolve_buy_filter": buy_filters.get,
    })
    return hist_st_set, calc, accessed


def _synthetic_events(tmp_path, end):
    cache_dir = tmp_path / "quant_backtest" / "data" / "sig_events"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"sig_events_market_v3_20260105_{end}.json").write_text(
        json.dumps({"meta": {"universe": 2}, "events": [
            {"stock": STOCK, "type": "3买", "day": "20260105", "score": 80},
            {"stock": STOCK, "type": "3买", "day": "20260113", "score": 70},
            {"stock": OTHER, "type": "3买", "day": "20260105", "score": 60},
        ]}), encoding="utf-8",
    )


def test_missing_namechange_source_returns_empty_not_evidence_of_no_st(tmp_path, monkeypatch):
    hist_st_set, calc, accessed = _isolated_functions(tmp_path, monkeypatch)
    _synthetic_events(tmp_path, "20260113")

    assert hist_st_set("20260105", "20260113") == set()
    buy, _, meta, error = calc("20260105", "20260113", hist_st=True)

    assert error is None
    assert buy["20260105"] == {STOCK, OTHER}
    assert buy["20260113"] == {STOCK}
    assert meta["hist_st"] == {"on": True, "n_codes": 0, "n_events_drop": 0}
    assert accessed == set()
    # 空集只说明当前函数在源文件缺失时未剔除，不能证明这两只股票不是历史 ST。


def test_existing_source_does_not_filter_on_ann_date(tmp_path, monkeypatch):
    # 状态在窗口内生效，合成公告日却晚于整个窗口；公告时点不代表已可交易获知。
    rows = [{"ts_code": STOCK, "name": "*ST 合成", "start_date": "20260110",
             "end_date": "20260112", "ann_date": "20260120"}]
    hist_st_set, calc, accessed = _isolated_functions(tmp_path, monkeypatch, rows)
    _synthetic_events(tmp_path, "20260113")

    assert hist_st_set("20260105", "20260113") == {STOCK}
    buy, _, meta, error = calc("20260105", "20260113", hist_st=True)

    assert error is None
    assert buy["20260105"] == {OTHER}  # 状态生效日、公告日之前的事件也被全窗剔除。
    assert buy["20260113"] == set()
    assert meta["hist_st"] == {"on": True, "n_codes": 1, "n_events_drop": 2}
    assert "ann_date" not in accessed


def test_effective_date_outside_window_even_if_announcement_earlier(tmp_path, monkeypatch):
    # 公告日早于窗口，但状态生效日迟于窗口；当前函数只看状态区间交集。
    rows = [{"ts_code": STOCK, "name": "ST 合成", "start_date": "20260120",
             "end_date": None, "ann_date": "20260104"}]
    hist_st_set, calc, accessed = _isolated_functions(tmp_path, monkeypatch, rows)
    _synthetic_events(tmp_path, "20260113")

    assert hist_st_set("20260105", "20260113") == set()
    buy, _, meta, error = calc("20260105", "20260113", hist_st=True)

    assert error is None
    assert buy["20260105"] == {STOCK, OTHER}
    assert buy["20260113"] == {STOCK}
    assert meta["hist_st"] == {"on": True, "n_codes": 0, "n_events_drop": 0}
    assert "ann_date" not in accessed
    # 这些断言没有验证公告字段的真实含义、发布时间或当时交易可知性。
