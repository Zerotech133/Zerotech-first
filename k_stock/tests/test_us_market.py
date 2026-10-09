# -*- coding: utf-8 -*-
"""UsMarket 测试（K-STOCK-044 M1）：拆股复权 / 缓存读写 / bar 契约 / 缺数据降级。纯 stdlib，离线跑。"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(HERE))

import us_market as um


def _row(d, o, c, h=None, l=None, v=1000.0):
    return {"date": d, "open": o, "high": h if h is not None else max(o, c) * 1.01,
            "low": l if l is not None else min(o, c) * 0.99, "close": c, "volume": v}


def test_split_ratio_normal():
    assert um._split_ratio(100.0, 102.0) == 1   # +2% 普通日
    # 口径显性化（K-STOCK-044）：单日 -45% 暴跌（100→55）会被判为 2:1 拆股（修正后 +10% 落 ±20% 内）。
    # 这是接受的不对称取舍：误判成拆股 → 历史价温和失真；漏判拆股 → 持仓止损假触发 -45% 灾难。
    # 且模拟盘持仓都在近期（青萍动量股），历史旧断裂修错不影响持仓期止损；近期真拆股比例精确整齐。
    assert um._split_ratio(100.0, 55.0) == 2
    assert um._split_ratio(100.0, 100.0) == 1
    assert um._split_ratio(0.0, 5.0) == 1       # 脏数据兜底


def test_split_ratio_splits():
    assert um._split_ratio(499.23, 129.04) == 4   # AAPL 2020 4:1
    assert um._split_ratio(645.57, 93.70) == 7    # AAPL 2014 7:1
    assert um._split_ratio(100.0, 51.0) == 2      # 2:1
    assert um._split_ratio(100.0, 34.0) == 3      # 3:1


def test_split_adjust_two_splits():
    rows = [_row("2020-08-28", 480.0, 499.23),
            _row("2020-08-31", 129.04, 129.04),   # 4:1 拆股
            _row("2020-09-01", 130.0, 134.18)]
    out = um.split_adjust(rows)
    assert abs(out[0]["close"] - 499.23 / 4) < 1e-9
    assert abs(out[1]["close"] - 129.04) < 1e-9   # 拆股日之后不动
    # pct 连续性：拆股日真实涨跌幅 ≈ 129.04*4/499.23-1 ≈ +3.4%
    pct = out[1]["close"] / out[0]["close"] - 1
    assert abs(pct - (129.04 * 4 / 499.23 - 1)) < 1e-9


def test_us_market_bar_and_days(tmpdir):
    us_dir = Path(tmpdir)
    (us_dir / "SPY.csv").write_text(
        "date,open,high,low,close,volume,pre_close\n"
        "2026-10-07,670,675,669,672,50000000,668\n"
        "2026-10-08,673,678,671,677,51000000,672\n", encoding="utf-8")
    (us_dir / "AAPL.csv").write_text(
        "date,open,high,low,close,volume,pre_close\n"
        "2026-10-07,335,341,334,338,35000000,333\n"
        "2026-10-08,336.81,341.57,335.90,340.42,35332449,338\n", encoding="utf-8")
    m = um.UsMarket(us_dir=us_dir)
    assert m.trading_days() == ["20261007", "20261008"]
    b = m.bar("gb_AAPL", "20261008")           # 引擎原码（gb_ 前缀）直达
    assert b == {"trade_date": "20261008", "open": 336.81, "high": 341.57,
                 "low": 335.90, "close": 340.42, "pre_close": 338.0}
    assert m.bar("gb_AAPL", "20261009") is None  # 非交易日 None（幂等跳过语义）
    assert m.bar("gb_NOPE", "20261008") is None  # 无缓存票 None


def test_trading_days_missing_cal(tmpdir):
    try:
        um.UsMarket(us_dir=Path(tmpdir)).trading_days()
        raise AssertionError("应抛 FileNotFoundError")
    except FileNotFoundError:
        pass


def test_refresh_writes_csv(tmpdir, monkeypatch):
    monkeypatch.setattr(um, "_fetch_sina", lambda sym: [
        _row("2026-10-07", 100.0, 101.0),
        _row("2026-10-08", 50.5, 51.5)])       # mock 在线拉取（含 2:1 拆股断裂）
    p = um.refresh("gb_test", Path(tmpdir))
    assert p.name == "TEST.csv" and p.exists()
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].split(",")[:2] == ["date", "open"]
    assert len(lines) == 3                      # 表头 + 2 行
    m = um.UsMarket(us_dir=Path(tmpdir))
    b = m.bar("gb_test", "20261008")
    assert b["close"] == 51.5 and b["pre_close"] == 50.5
    assert abs(m.bar("gb_test", "20261007")["close"] - 101.0 / 2) < 1e-9  # 前复权生效


def test_cli_refresh_mocked(tmpdir, monkeypatch, capsys):
    monkeypatch.setattr(um, "_fetch_sina", lambda sym: [_row("2026-10-08", 9, 9.5)])
    rc = um.main(["refresh", "spy"], us_dir=Path(tmpdir))
    assert rc == 0
    out = capsys.readouterr().out
    assert '"symbol": "spy"' in out and (Path(tmpdir) / "SPY.csv").exists()


def test_engine_contract_methods(tmpdir):
    """K-STOCK-044 M2：引擎契约其余四方法（last_bar/last_bar_before/first_bar_on_or_after/next_trading_day）。"""
    us_dir = Path(tmpdir)
    (us_dir / "AAPL.csv").write_text(
        "date,open,high,low,close,volume,pre_close\n"
        "2026-10-06,330,336,329,333,34000000,331\n"
        "2026-10-07,335,341,334,338,35000000,333\n"
        "2026-10-08,336.81,341.57,335.90,340.42,35332449,338\n", encoding="utf-8")
    m = um.UsMarket(us_dir=us_dir)
    assert m.last_bar("gb_AAPL")["trade_date"] == "20261008"
    assert m.last_bar_before("gb_AAPL", "20261007")["trade_date"] == "20261007"
    assert m.last_bar_before("gb_AAPL", "20261005") is None       # 全在窗口后
    assert m.first_bar_on_or_after("gb_AAPL", "20261007")["trade_date"] == "20261007"
    assert m.first_bar_on_or_after("gb_AAPL", "20261009") is None  # 无未来数据
    # next_trading_day 美东口径（北京→ET -12h 夏令时）：盘前信号 → 当日开盘成交
    m2 = um.UsMarket(us_dir=us_dir, cal_symbol="AAPL")   # 日历基准指向 AAPL（测试环境无 SPY）
    assert m2.next_trading_day("2026-10-07T16:00") == "20261007"   # 北京16点=ET04点盘前 → 当日
    assert m2.next_trading_day("2026-10-08T16:00") == "20261008"   # 同上（ET10-08盘前）
    assert m2.next_trading_day("2026-10-09T21:00") == "20261009"   # 北京21点=ET09点盘前，湖尾外推当日


def test_extrapolate_weekend_boundary(tmpdir):
    """外推边界（美东口径）：北京周六晨=ET周五盘后 → 下周一；盘前信号含当日。"""
    us_dir = Path(tmpdir)
    (us_dir / "SPY.csv").write_text(
        "date,open,high,low,close,volume,pre_close\n"
        "2026-10-09,670,675,669,672,50000000,668\n", encoding="utf-8")
    m = um.UsMarket(us_dir=us_dir)
    # 北京 10-10 周六 09:00 = ET 10-09 周五 21:00 已收盘 → 湖外顺延跳周末 → 10-12 周一
    assert m.next_trading_day("2026-10-10T09:00") == "20261012"
    # 北京 10-09 09:00 = ET 10-08 21:00 → day=20261008 非交易日(湖无) → 湖内首个更大日 10-09
    assert m.next_trading_day("2026-10-09T09:00") == "20261009"


if __name__ == "__main__":
    import contextlib
    import io

    class _MP:  # mini monkeypatch
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    class _Capsys:  # mini capsys
        def __init__(self):
            self.buf = io.StringIO()
        def readouterr(self):
            return type("R", (), {"out": self.buf.getvalue()})()

    fails = []
    tests = {k: v for k, v in globals().items()
             if k.startswith("test_") and callable(v)}
    for name, fn in sorted(tests.items()):
        sig = fn.__code__.co_varnames[:fn.__code__.co_argcount]
        mp = _MP()
        with tempfile.TemporaryDirectory() as td:
            kwargs = {"tmpdir": td, "monkeypatch": mp, "capsys": _Capsys()}
            kwargs = {k: v for k, v in kwargs.items() if k in sig}
            try:
                with contextlib.redirect_stdout(kwargs["capsys"].buf if "capsys" in kwargs else io.StringIO()):
                    fn(**kwargs)
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                import traceback; traceback.print_exc()
                print(f"FAIL {name}: {e}")
                fails.append(name)
            finally:
                mp.undo()
    print(f"{len(fails)} failed" if fails else "ALL PASS")
    sys.exit(1 if fails else 0)
