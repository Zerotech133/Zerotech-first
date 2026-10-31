# -*- coding: utf-8 -*-
"""H1b 数据适配层测试（只读 CSV 合成夹具；不依赖 parquet/pandas）。

覆盖：单位换算（amount 千元→元、vol 手→股）、top_list 过滤可转债/B股
（审计实测 1,628 非 A 股 code-date 口径）、ST 开区间展开、OOS 判定段过滤、
字段缺失 fail-visible、无 parquet 依赖 import、合成端到端（panel/events →
harness load_* → 硬闸/回测）。
"""
import csv
import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
HARNESS_PATH = TOOLS / "lcs1_h1b_harness.py"
ADAPTER_PATH = TOOLS / "h1b_data_adapter.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HARNESS = _load("lcs1_h1b_harness_t38", HARNESS_PATH)
ADAPTER = _load("h1b_data_adapter_t38", ADAPTER_PATH)

CAL = [(date(2024, 1, 2) + timedelta(days=i)).strftime("%Y%m%d")
       for i in range(60)]
CAL_ISO = ["%s-%s-%s" % (d[:4], d[4:6], d[6:8]) for d in CAL]


def _write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _daily_rows():
    """600000.SH 在 CAL[30] 涨停（10→11）；其余平盘；amount 原始=千元。"""
    rows = []
    for code in ("600000.SH", "600001.SH", "600002.SH", "600003.SH"):
        for idx, day in enumerate(CAL):
            if code == "600000.SH":
                if idx < 30:
                    open_p = high = low = close = pre = 10.0
                elif idx == 30:
                    open_p, high, low, close, pre = 10.0, 11.0, 10.0, 11.0, 10.0
                else:
                    open_p, high, low, close, pre = 10.6, 11.0, 10.5, 11.0, 11.0
            else:
                open_p = high = low = close = pre = 10.0
            rows.append({"ts_code": code, "trade_date": day,
                         "open": open_p, "high": high, "low": low,
                         "close": close, "pre_close": pre,
                         "vol": 1000, "amount": 35000})  # 手 / 千元
    return rows


def _make_fixture(root, daily_drop_column=None, skip=()):
    """生成 CSV 合成夹具（默认 8 个数据集齐；skip 用于缺文件用例）。"""
    root = Path(root)
    daily = _daily_rows()
    fields = ["ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "vol", "amount"]
    if daily_drop_column:
        fields = [f for f in fields if f != daily_drop_column]
        daily = [{k: row[k] for k in fields} for row in daily]
    _write_csv(root / "daily.csv", fields, daily)

    basic = []
    for code in ("600000.SH", "600001.SH", "600002.SH", "600003.SH"):
        for day in CAL:
            basic.append({"ts_code": code, "trade_date": day,
                          "circ_mv": 500000.0, "turnover_rate": 2.0})
    _write_csv(root / "daily_basic.csv",
               ["ts_code", "trade_date", "circ_mv", "turnover_rate"], basic)

    # ST：600001 开区间（空 end_date = 持续至今）；600002 闭区间 CAL[5]~CAL[8]
    _write_csv(root / "namechange.csv",
               ["ts_code", "name", "start_date", "end_date"],
               [{"ts_code": "600001.SH", "name": "ST测试",
                 "start_date": "20200101", "end_date": ""},
                {"ts_code": "600002.SH", "name": "ST区间",
                 "start_date": CAL[5], "end_date": CAL[8]}])

    # 600003 故意不登记 list_date（fail-visible，不伪造）
    _write_csv(root / "stock_basic.csv", ["ts_code", "list_date"],
               [{"ts_code": "600000.SH", "list_date": "20150101"},
                {"ts_code": "600001.SH", "list_date": "20150101"},
                {"ts_code": "600002.SH", "list_date": "20150101"}])

    # 龙虎榜：A 股 2 行聚合为正 / 可转债 / B 股 / 不在日线池 / 负值 / 空值
    _write_csv(root / "top_list.csv",
               ["ts_code", "trade_date", "net_amount"],
               [{"ts_code": "600000.SH", "trade_date": CAL_ISO[30],
                 "net_amount": 3000000},
                {"ts_code": "600000.SH", "trade_date": CAL_ISO[30],
                 "net_amount": 2000000},
                {"ts_code": "110044.SH", "trade_date": CAL_ISO[30],
                 "net_amount": 1000000},
                {"ts_code": "900901.SH", "trade_date": CAL_ISO[30],
                 "net_amount": 1000000},
                {"ts_code": "600999.SH", "trade_date": CAL_ISO[30],
                 "net_amount": 1000000},
                {"ts_code": "600002.SH", "trade_date": CAL_ISO[32],
                 "net_amount": -2000000},
                {"ts_code": "600001.SH", "trade_date": CAL_ISO[31],
                 "net_amount": ""}])

    _write_csv(root / "sw_classify.csv",
               ["index_code", "industry_name", "level"],
               [{"index_code": "801080.SI", "industry_name": "电子",
                 "level": "L1"}])
    _write_csv(root / "sw_member.csv",
               ["index_code", "con_code", "in_date", "out_date"],
               [{"index_code": "801080.SI", "con_code": code,
                 "in_date": "20150101", "out_date": ""}
                for code in ("600000.SH", "600001.SH", "600002.SH")])
    _write_csv(root / "trade_cal.csv", ["cal_date", "is_open"],
               [{"cal_date": day, "is_open": 1} for day in CAL])
    for name in skip:
        path = root / ("%s.csv" % name)
        if path.exists():
            path.unlink()
    return root


# ---------------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------------
def test_units_converted_on_load(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv")
    rows = adapter.load_daily(root / "daily.csv")
    first = rows[0]
    assert first["amount"] == 35000 * adapter.AMOUNT_MULTIPLIER  # 千元→元
    assert first["volume"] == 1000 * 100.0                       # 手→股
    meta = adapter.UNITS_META
    assert meta["amount"]["source"] == "千元" and meta["amount"]["target"] == "元"
    assert meta["amount"]["multiplier"] == 1000
    assert meta["vol"]["target"] == "股" and meta["vol"]["multiplier"] == 100


# ---------------------------------------------------------------------------
# ST 开区间展开
# ---------------------------------------------------------------------------
def test_st_open_and_closed_interval_expansion(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv")
    namechange = adapter.load_namechange(root / "namechange.csv")
    intervals = adapter.build_st_intervals(namechange)
    # 开区间：600001 从 2020 年起持续 ST
    assert adapter.is_st_on(intervals["600001.SH"], CAL[0]) is True
    assert adapter.is_st_on(intervals["600001.SH"], CAL[-1]) is True
    # 闭区间：600002 仅 CAL[5]~CAL[8]（含端点）
    assert adapter.is_st_on(intervals["600002.SH"], CAL[4]) is False
    assert adapter.is_st_on(intervals["600002.SH"], CAL[5]) is True
    assert adapter.is_st_on(intervals["600002.SH"], CAL[8]) is True
    assert adapter.is_st_on(intervals["600002.SH"], CAL[9]) is False

    report = adapter.build_all(root, fmt="csv", out_dir=tmp_path / "out",
                               write=False, check_only=True)
    assert report["ok"] is True
    panel = adapter.build_panel(
        adapter.load_daily(root / "daily.csv"),
        adapter.load_daily_basic(root / "daily_basic.csv"),
        namechange, adapter.load_stock_basic(root / "stock_basic.csv"),
        adapter.load_sw_classify(root / "sw_classify.csv"),
        adapter.load_sw_member(root / "sw_member.csv"),
        adapter.load_trade_cal(root / "trade_cal.csv"))
    bars_600001 = panel["codes"]["600001.SH"]["bars"]
    assert all(bar["is_st"] for bar in bars_600001), "开区间 ST 应展开到所有 bar"
    bars_600002 = panel["codes"]["600002.SH"]["bars"]
    assert [bar["is_st"] for bar in bars_600002[4:10]] == \
        [False, True, True, True, True, False]
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# top_list A 股过滤（可转债/B 股/池外/负值/空值）
# ---------------------------------------------------------------------------
def test_top_list_filter_a_share_only(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv")
    daily = adapter.load_daily(root / "daily.csv")
    universe = sorted({row["ts_code"] for row in daily})
    assert "600999.SH" not in universe

    kept, stats = adapter.filter_top_list(
        adapter.load_top_list(root / "top_list.csv"), universe)
    assert stats["rows"] == 7
    assert stats["non_a_share"] == 2          # 110044.SH 可转债 + 900901.SH B股
    assert stats["not_in_universe"] == 1      # 600999.SH 不在日线池
    assert stats["net_amount_missing"] == 1   # 600001.SH 空 net_amount
    assert stats["a_share_rows"] == 3         # 600000×2 + 600002×1
    assert stats["code_date"] == 2
    assert stats["e2_positive"] == 1
    codes = {row["ts_code"] for row in kept}
    assert codes == {"600000.SH", "600002.SH"}
    assert all(adapter.is_a_share_code(code) for code in codes)
    # 同日两行聚合：3000000+2000000
    row_600000 = [r for r in kept if r["ts_code"] == "600000.SH"][0]
    assert row_600000["net_amount"] == 5000000.0
    assert adapter.is_a_share_code("600000.SH") is True
    assert adapter.is_a_share_code("300001.SZ") is True
    assert adapter.is_a_share_code("830799.BJ") is True
    assert adapter.is_a_share_code("920162.BJ") is True
    assert adapter.is_a_share_code("110044.SH") is False   # 可转债
    assert adapter.is_a_share_code("900901.SH") is False   # B 股
    assert adapter.is_a_share_code("510300.SH") is False   # ETF


# ---------------------------------------------------------------------------
# 字段缺失 / 缺文件 fail-visible
# ---------------------------------------------------------------------------
def test_missing_column_fail_visible(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv", daily_drop_column="amount")
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.build_all(root, fmt="csv", write=False, check_only=True)
    assert excinfo.value.dataset == "daily"
    assert "amount" in excinfo.value.missing
    assert "amount" in str(excinfo.value)


def test_missing_dataset_fail_visible(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv", skip=("trade_cal",))
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.build_all(root, fmt="csv", write=False, check_only=True)
    assert excinfo.value.dataset == "datasets"
    assert any("trade_cal" in item for item in excinfo.value.missing)


def test_cli_json_missing_column_exit3(tmp_path, capsys):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv", daily_drop_column="amount")
    rc = adapter.main(["--root", str(root), "--format", "csv",
                       "--check", "--json"])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["dataset"] == "daily"
    assert "amount" in payload["missing"]


# ---------------------------------------------------------------------------
# 合成端到端：适配 → harness 契约 → 硬闸/回测 → OOS 过滤
# ---------------------------------------------------------------------------
def test_end_to_end_adapter_to_harness(tmp_path):
    adapter, harness = ADAPTER, HARNESS
    root = _make_fixture(tmp_path / "csv")
    out_dir = tmp_path / "out"
    report = adapter.build_all(root, fmt="csv", out_dir=out_dir, write=True)
    assert report["ok"] is True
    panel_path = out_dir / "panel.json"
    events_path = out_dir / "events.jsonl"
    assert panel_path.exists() and events_path.exists()
    assert (out_dir / "adapter_meta.json").exists()

    panel = harness.load_panel(str(panel_path))
    events = harness.load_events(str(events_path))
    raw_panel = json.loads(panel_path.read_text(encoding="utf-8"))
    # 面板契约：ISO 日期、单位已归一、ST/SW/list_date 就位
    first_code = panel["codes"]["600000.SH"]
    first_bar = first_code["bars"][0]
    assert first_bar["date"] == CAL_ISO[0]
    assert first_bar["amount"] == 35000 * 1000       # 元
    assert first_bar["volume"] == 1000 * 100        # 股
    assert first_bar["sw_l1"] == "电子"
    assert first_bar["circ_mv"] == 500000.0
    assert first_code["list_date"] == "2015-01-01"
    assert panel["codes"]["600003.SH"]["list_date"] is None  # 不伪造
    assert raw_panel["meta"]["missing"]["list_date"] >= 1
    assert raw_panel["meta"]["units"]["amount"]["multiplier"] == 1000
    # 事件：E1 1 个（600000 CAL[30] 涨停）；E2 1 个（A 股过滤后）
    e1 = [ev for ev in events if ev["event_type"] == "E1"]
    e2 = [ev for ev in events if ev["event_type"] == "E2"]
    assert len(e1) == 1 and e1[0]["date"] == CAL_ISO[30]
    assert len(e2) == 1 and e2[0]["date"] == CAL_ISO[30]
    assert e2[0]["ts_code"] == "600000.SH"
    assert all(ev["ts_code"] not in ("110044.SH", "900901.SH", "600999.SH")
               for ev in events)
    # 硬闸：amount 若未×1000（35,000 < 3,000 万）会失败；换算后 35,000,000 通过
    # 合成日历仅 60 天 → 用测试 spec 把「上市≥250 交易日」降到 10（口径不伪造，
    # 真实回测仍用 SPEC 的 250；此处置换是为了在 60 日夹具里验证单位换算生效）
    spec = dict(harness.SPEC)
    spec["min_listed_days"] = 10
    ok, reasons = harness.gate_t1(panel, e1[0], spec)
    assert ok is True, reasons
    assert panel["codes"]["600000.SH"]["bars"][0]["amount"] > 30_000_000
    # 回测 → 事件/中性化/判定段过滤
    backtest = harness.run_backtest(events, panel, spec)
    assert backtest["funnel"]["traded"] >= 1
    evaluation = harness.evaluate_all(backtest["trades"], spec,
                                      panel=panel, events=events,
                                      segment="is")
    assert evaluation["segment"] == "is"
    assert evaluation["n_trades_segment"] == len(backtest["trades"])
    assert evaluation["n_trades_total"] == len(backtest["trades"])


# ---------------------------------------------------------------------------
# OOS 判定段过滤（evaluate_all）
# ---------------------------------------------------------------------------
def _trade(day, net=0.01):
    return {"ts_code": "600000.SH", "event_type": "E1", "event_date": day,
            "entry_date": day, "exit_date": day, "raw_net": net,
            "stress_net": net - 0.005}


def test_oos_segment_filtering_in_evaluate_all():
    harness = HARNESS
    trades = [_trade("2024-01-10"), _trade("2025-06-30"),
              _trade("2025-07-01"), _trade("2026-03-10"), _trade("2026-03-11")]
    oos = harness.evaluate_all(trades, harness.SPEC, segment="oos")
    assert oos["segment"] == "oos"
    assert oos["segment_window"] == ["2025-07-01", "2026-10-16"]
    assert oos["n_trades_total"] == 5
    assert oos["n_trades_segment"] == 3          # 2025-07-01 / 2026-03-10 / 11
    assert oos["by_event"]["E1"]["n_indep"] == 3
    is_seg = harness.evaluate_all(trades, harness.SPEC, segment="is")
    assert is_seg["n_trades_segment"] == 2       # 2024-01-10 / 2025-06-30（边界含）
    default = harness.evaluate_all(trades, harness.SPEC)
    assert default["segment"] == "all" and default["n_trades_segment"] == 5
    with pytest.raises(ValueError):
        harness.evaluate_all(trades, harness.SPEC, segment="bad")
    # 数字日期（parquet 源格式）亦按预注册段过滤
    numeric = [_trade("20250630"), _trade("20250701")]
    assert harness.evaluate_all(numeric, harness.SPEC,
                                segment="is")["n_trades_segment"] == 1
    assert harness.evaluate_all(numeric, harness.SPEC,
                                segment="oos")["n_trades_segment"] == 1


# ---------------------------------------------------------------------------
# --check 零写盘 / 无 parquet 依赖 import
# ---------------------------------------------------------------------------
def test_check_only_does_not_write(tmp_path):
    adapter = ADAPTER
    root = _make_fixture(tmp_path / "csv")
    out_dir = tmp_path / "not_written"
    report = adapter.build_all(root, fmt="csv", out_dir=out_dir,
                               write=False, check_only=True)
    assert report["ok"] is True and report["check_only"] is True
    assert not out_dir.exists()
    rc = adapter.main(["--root", str(root), "--format", "csv",
                       "--check", "--out-dir", str(out_dir), "--json"])
    assert rc == 0
    assert not out_dir.exists()


def test_no_parquet_dependency_on_import():
    code = ("import sys; sys.path.insert(0, 'tools'); "
            "import h1b_data_adapter as a; "
            "print(int('pandas' in sys.modules), int('pyarrow' in sys.modules))")
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("0 0")
