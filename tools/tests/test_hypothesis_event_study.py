# -*- coding: utf-8 -*-
"""只读事件研究 harness 契约测试（WO-STRAT-EVENTSTUDY-20261101 / task-29）。

全离线：合成交易日历/价格/事件表（不读真实数据仓、不联网）。
覆盖：
  · 正/负 CAR、多窗口、多事件/多标的聚类、重叠提示；
  · 样本不足 → insufficient（warn/verdict，不伪造显著）；min_events 可覆盖；
  · 缺列/空表/坏行 fail-visible；事件晚于数据/标的不存在/窗口不足被明确排除；
  · 非交易日事件顺延 T0；
  · PIT：窗口之后数据与 T0-1 之前数据不被使用（改数后 CAR 不变）；
  · 基准三种模式（全市场等权/return 列/close 列）；CLI CSV→JSON。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "research" / "hypothesis_event_study.py"


def _load():
    spec = importlib.util.spec_from_file_location("hypothesis_event_study_under_test",
                                                  MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hes = _load()


# ---------------------------------------------------------------- 合成数据
def _biz_dates(n=60):
    out, d = [], date(2026, 1, 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DATES = _biz_dates(60)


def _prices(dates, specs):
    """specs: {symbol: fn(i)->当日收益}；价格=100 累积。"""
    rows = []
    for symbol, fn in specs.items():
        close = 100.0
        for i, d in enumerate(dates):
            if i > 0:
                close *= 1.0 + float(fn(i))
            rows.append({"date": d, "symbol": symbol, "close": round(close, 8)})
    return pd.DataFrame(rows)


def _events(*triples):
    return pd.DataFrame([{"date": d, "anchor_symbol": s, "event_id": e}
                         for d, s, e in triples])


def _bench_zero(dates):
    return pd.DataFrame({"date": dates, "return": [0.0] * len(dates)})


def _step(r, start):
    """i>=start 起每日固定收益 r。"""
    return lambda i: (r if i >= start else 0.0)


# ---------------------------------------------------------------- 正/负 CAR
def test_positive_car_and_stats():
    ev_idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, ev_idx)})
    ev = _events((DATES[ev_idx], "600519", "H37-e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES), horizons=(20,))
    assert rep["schema"] == "hypothesis_event_study/v1"
    item = rep["events"][0]
    assert item["status"] == "ok" and item["t0"] == DATES[ev_idx]
    assert item["cars"]["20"] == pytest.approx(0.21, abs=1e-9)   # 21 个 1%
    assert item["path"] and len(item["path"]) == 21
    s = rep["stats"]["20"]
    assert s["n"] == 1 and s["mean"] == pytest.approx(0.21, abs=1e-9)
    assert s["median"] == pytest.approx(0.21, abs=1e-9)
    assert s["win_rate"] == 1.0 and s["t"] is None               # n=1 → 无 t
    assert s["insufficient_sample"] is True
    assert s["verdict"] == "insufficient"
    assert any("样本不足" in w for w in rep["warnings"])
    assert rep["params"]["car_definition"].startswith("CAR[0,+N]")


def test_negative_car_and_win_rate():
    ev_idx = 10
    prices = _prices(DATES, {"600519": _step(-0.01, ev_idx)})
    ev = _events((DATES[ev_idx], "600519", "H38-e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES), horizons=(20,))
    item = rep["events"][0]
    assert item["cars"]["20"] == pytest.approx(-0.21, abs=1e-9)
    assert item["max_drawdown"] < 0
    s = rep["stats"]["20"]
    assert s["mean"] < 0 and s["win_rate"] == 0.0


def test_multiple_horizons_and_insufficient_window():
    ev_idx = 50
    prices = _prices(DATES, {"600519": _step(0.01, ev_idx)})
    ev = _events((DATES[ev_idx], "600519", "e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES),
                    horizons=(5, 20))
    item = rep["events"][0]
    assert item["cars"]["5"] == pytest.approx(0.06, abs=1e-9)     # 6 个 1%
    assert item["cars"]["20"] is None
    assert 20 in item["missing_horizons"]
    assert item["status"] == "ok" or item["status"] == "insufficient_window"
    # 只有 5 日窗口有效 → status ok（至少一个窗口可算）
    assert item["status"] == "ok"
    assert rep["stats"]["20"]["n"] == 0 and rep["stats"]["20"]["mean"] is None


# ---------------------------------------------------------------- 多事件/聚类
def test_multi_event_multi_anchor_cluster_and_stats():
    # 同一事件 e1 两个标的：A +1%/日，B +2%/日 → CAR 0.21 / 0.42，事件级均值 0.315
    idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, idx),
                             "000858": _step(0.02, idx)})
    events = _events((DATES[idx], "600519", "e1"),
                     (DATES[idx], "000858", "e1"))
    rep = hes.study(events, prices, benchmark=_bench_zero(DATES),
                    horizons=(20,), min_events=1)
    by_id = {x["event_id"]: x for x in rep["by_event_id"]}
    assert by_id["e1"]["n_rows"] == 2 and len(by_id["e1"]["anchors"]) == 2
    assert by_id["e1"]["cars"]["20"] == pytest.approx(0.315, abs=1e-9)
    s = rep["stats"]["20"]
    assert s["n"] == 1 and s["mean"] == pytest.approx(0.315, abs=1e-9)
    assert s["row_stats"]["n"] == 2                               # 行级也给出
    assert rep["overlaps"]["event_clusters"]


def test_insufficient_sample_warn_vs_min_events_override():
    # 3 个不重叠事件，每个 +1%/日 × 5 窗口
    idxs = [5, 20, 35]
    # 每个事件日之后 +1%/日：用分段函数
    def ret(i):
        for start in idxs:
            if i >= start:
                return 0.01
        return 0.0
    prices = _prices(DATES, {"600519": ret})
    events = _events(*[(DATES[i], "600519", "e%d" % k)
                       for k, i in enumerate(idxs)])
    rep = hes.study(events, prices, benchmark=_bench_zero(DATES),
                    horizons=(5,), min_events=30)
    s = rep["stats"]["5"]
    assert s["n"] == 3 and s["insufficient_sample"] is True
    assert s["verdict"] == "insufficient"
    rep2 = hes.study(events, prices, benchmark=_bench_zero(DATES),
                     horizons=(5,), min_events=2)
    s2 = rep2["stats"]["5"]
    assert s2["insufficient_sample"] is False
    assert s2["verdict"] == "observation"
    assert s2["t"] is None            # 三个 CAR 全相同 → std=0 → 不伪造 t


def test_overlap_detection_pairs():
    idxs = [10, 12, 40]               # 前两个窗口重叠（间距 2 ≤ 20），第三个不重叠
    def ret(i):
        return 0.01 if any(i >= s for s in idxs) else 0.0
    prices = _prices(DATES, {"600519": ret})
    events = _events(*[(DATES[i], "600519", "e%d" % k)
                       for k, i in enumerate(idxs)])
    rep = hes.study(events, prices, benchmark=_bench_zero(DATES),
                    horizons=(20,), min_events=1)
    ov = rep["overlaps"]
    assert ov["pair_count"] == 1
    pair = ov["pairs"][0]
    assert {pair["a"], pair["b"]} == {"e0", "e1"}
    assert pair["same_anchor"] is True and pair["t0_gap_days"] == 2
    by_id = {x["event_id"]: x for x in rep["events"]}
    assert by_id["e0"]["overlaps_with"] == ["e1"]
    assert by_id["e2"]["overlaps_with"] == []


# ---------------------------------------------------------------- 非法输入
def test_missing_columns_raise():
    prices = _prices(DATES, {"600519": _step(0.01, 10)})
    good_ev = _events((DATES[10], "600519", "e1"))
    with pytest.raises(hes.EventStudyError, match="缺列"):
        hes.study(good_ev.drop(columns=["anchor_symbol"]), prices)
    with pytest.raises(hes.EventStudyError, match="缺列"):
        hes.study(good_ev, prices.drop(columns=["close"]))
    with pytest.raises(hes.EventStudyError, match="基准"):
        hes.study(good_ev, prices, benchmark=pd.DataFrame({"date": DATES}))
    with pytest.raises(hes.EventStudyError):
        hes.study(good_ev, prices, horizons=(0,))


def test_empty_tables_raise():
    with pytest.raises(hes.EventStudyError):
        hes.study(pd.DataFrame(columns=["date", "anchor_symbol", "event_id"]),
                  _prices(DATES, {"A": lambda i: 0.0}))
    with pytest.raises(hes.EventStudyError):
        hes.study(_events((DATES[10], "A", "e1")),
                  pd.DataFrame(columns=["date", "symbol", "close"]))


def test_bad_rows_fail_visible():
    prices = _prices(DATES, {"600519": _step(0.01, 10)})
    prices = pd.concat([
        prices,
        pd.DataFrame([{"date": "bad-date", "symbol": "600519", "close": 100},
                      {"date": DATES[5], "symbol": "", "close": 100},
                      {"date": DATES[5], "symbol": "000858", "close": 0},
                      {"date": DATES[6], "symbol": "000858", "close": "NaN"}])],
        ignore_index=True)
    events = pd.concat([
        _events((DATES[10], "600519", "ok:1")),
        pd.DataFrame([{"date": "bad", "anchor_symbol": "600519", "event_id": "bad:1"},
                      {"date": DATES[11], "anchor_symbol": "", "event_id": "bad:2"}])],
        ignore_index=True)
    rep = hes.study(events, prices, benchmark=_bench_zero(DATES),
                    horizons=(5,), min_events=1)
    assert rep["inputs"]["event_bad_rows"] == 2
    assert rep["inputs"]["price_bad_rows"] == 4
    assert len(rep["event_bad_rows"]) == 2
    assert any("坏行" in w for w in rep["warnings"])
    assert rep["events"][0]["status"] == "ok"


def test_excluded_statuses_event_after_data_anchor_missing():
    prices = _prices(DATES, {"600519": _step(0.01, 10)})
    events = _events(
        ("2027-01-04", "600519", "late"),
        (DATES[10], "999999", "no-anchor"),
        (DATES[10], "600519", "ok:1"),
    )
    rep = hes.study(events, prices, benchmark=_bench_zero(DATES),
                    horizons=(20,), min_events=1)
    by_id = {x["event_id"]: x for x in rep["events"]}
    assert by_id["late"]["status"] == "event_after_data"
    assert by_id["no-anchor"]["status"] == "anchor_missing"
    assert by_id["ok:1"]["status"] == "ok"
    assert rep["excluded"]["counts"].get("event_after_data") == 1
    assert rep["excluded"]["counts"].get("anchor_missing") == 1


def test_non_trading_event_day_shifted():
    ev_idx = 10
    # 取周五（DATES[9]）之后的周六作为事件日 → T0 顺延到下周一 DATES[10]
    from datetime import datetime as _dt
    gap_day = (_dt.fromisoformat(DATES[9]) + timedelta(days=1)).isoformat()
    prices = _prices(DATES, {"600519": _step(0.01, ev_idx)})
    ev = _events((gap_day, "600519", "e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES),
                    horizons=(5,), min_events=1)
    item = rep["events"][0]
    assert item["status"] == "ok"
    assert item["t0"] == DATES[ev_idx]
    assert item["t0_shifted"] is True


# ---------------------------------------------------------------- PIT
def test_future_data_after_window_not_used():
    idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, idx)})
    ev = _events((DATES[idx], "600519", "e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES),
                    horizons=(20,), min_events=1)
    car = rep["events"][0]["cars"]["20"]
    # 篡改窗口之后（T0+21 起）的价格：CAR 必须不变
    mutated = prices.copy()
    cutoff = DATES[idx + 20]
    mutated.loc[mutated["date"] > cutoff, "close"] *= 5.0
    rep2 = hes.study(ev, mutated, benchmark=_bench_zero(DATES),
                     horizons=(20,), min_events=1)
    assert rep2["events"][0]["cars"]["20"] == pytest.approx(car, abs=1e-12)


def test_data_before_t0_prior_close_not_used():
    idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, idx)})
    ev = _events((DATES[idx], "600519", "e1"))
    rep = hes.study(ev, prices, benchmark=_bench_zero(DATES),
                    horizons=(20,), min_events=1)
    car = rep["events"][0]["cars"]["20"]
    # 篡改 T0-1 之前（≤ DATES[idx-2]）的价格：不影响窗口内收益比率
    mutated = prices.copy()
    mutated.loc[mutated["date"] <= DATES[idx - 2], "close"] *= 3.0
    rep2 = hes.study(ev, mutated, benchmark=_bench_zero(DATES),
                     horizons=(20,), min_events=1)
    assert rep2["events"][0]["cars"]["20"] == pytest.approx(car, abs=1e-12)


# ---------------------------------------------------------------- 基准模式
def test_default_equal_weight_benchmark():
    idx = 10
    # A +1%/日、B +3%/日 → 等权基准 +2%/日 → A 的超额 -1%/日 → CAR=-0.21
    prices = _prices(DATES, {"600519": _step(0.01, idx),
                             "000858": _step(0.03, idx)})
    rep = hes.study(_events((DATES[idx], "600519", "e1")), prices,
                    horizons=(20,), min_events=1)
    assert rep["inputs"]["benchmark"] == "equal_weight_all_symbols"
    assert rep["events"][0]["cars"]["20"] == pytest.approx(-0.21, abs=1e-9)


def test_benchmark_close_mode():
    idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, idx)})
    bench = pd.DataFrame({
        "date": DATES,
        "close": [round(100.0 * (1.005 ** i), 8) for i in range(len(DATES))],
    })
    rep = hes.study(_events((DATES[idx], "600519", "e1")), prices,
                    benchmark=bench, horizons=(20,), min_events=1)
    assert rep["inputs"]["benchmark"] == "benchmark_close_column"
    assert rep["events"][0]["cars"]["20"] == pytest.approx(0.105, abs=1e-9)


# ---------------------------------------------------------------- CLI
def _write_inputs(tmp_path):
    idx = 10
    prices = _prices(DATES, {"600519": _step(0.01, idx)})
    events = _events((DATES[idx], "600519", "e1"))
    bench = _bench_zero(DATES)
    pe = tmp_path / "events.csv"
    pp = tmp_path / "prices.csv"
    pb = tmp_path / "bench.csv"
    events.to_csv(pe, index=False)
    prices.to_csv(pp, index=False)
    bench.to_csv(pb, index=False)
    return pe, pp, pb


def test_cli_csv_json_out(tmp_path, capsys):
    pe, pp, pb = _write_inputs(tmp_path)
    out = tmp_path / "report.json"
    rc = hes.main(["--events", str(pe), "--prices", str(pp),
                   "--benchmark", str(pb), "--horizons", "5,20",
                   "--json", "--out", str(out)])
    captured = capsys.readouterr().out
    report = json.loads(captured[captured.index("{"):])
    assert rc == 0
    assert report["schema"] == "hypothesis_event_study/v1"
    assert report["events"][0]["cars"]["20"] == pytest.approx(0.21, abs=1e-9)
    assert out.is_file()


def test_cli_missing_file_exit_3(tmp_path, capsys):
    pe, pp, _pb = _write_inputs(tmp_path)
    rc = hes.main(["--events", str(pe), "--prices", str(tmp_path / "nope.csv")])
    assert rc == 3 and "不存在" in capsys.readouterr().err


def test_cli_missing_column_exit_3(tmp_path, capsys):
    pe, pp, _pb = _write_inputs(tmp_path)
    bad = tmp_path / "bad_prices.csv"
    pd.read_csv(pp).drop(columns=["close"]).to_csv(bad, index=False)
    rc = hes.main(["--events", str(pe), "--prices", str(bad)])
    err = capsys.readouterr().err
    assert rc == 3 and "缺列" in err

