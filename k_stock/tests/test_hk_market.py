# -*- coding: utf-8 -*-
"""HkMarket 测试（HK 灰位期，镜像 test_us_market.py）：代码规范 / bar 契约 /
全市场聚合日历 / 09:30 HKT 时界 / HK 费率常量 / T+0 挂账 / LOT 与涨跌停运行时豁免 /
直读湖 _read 清洗。pandas 仅在 I/O 边界——逻辑层测试纯 stdlib（_read 注入），
pandas 路径以 fake 模块注入离线测。"""
import csv
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(HERE))

import engine as paper_engine  # noqa: E402
import hk_market as hm  # noqa: E402
from engine import PaperEngine  # noqa: E402
from market import FakeMarket  # noqa: E402

# ---------------- 代码规范（5 位 00700.HK 形态，与 A 股 6 位区分） ----------------


def test_normalize_hk_code():
    assert hm.normalize_hk_code("700") == "00700.HK"        # 3 位 zfill(5)
    assert hm.normalize_hk_code("0700") == "00700.HK"       # 4 位 zfill(5)
    assert hm.normalize_hk_code("00700") == "00700.HK"      # 5 位补后缀
    assert hm.normalize_hk_code("00700.HK") == "00700.HK"   # 规范形态原样
    assert hm.normalize_hk_code("9988") == "09988.HK"       # 阿里旧写法
    assert hm.normalize_hk_code("2800") == "02800.HK"       # 盈富基金
    assert hm.normalize_hk_code("600519.SH") == "600519.SH"  # A 股形态不误收（原样返回）
    assert hm.normalize_hk_code("AAPL") == "AAPL"           # 非 HK 原样


def test_is_hk_code_boundaries():
    assert hm.is_hk_code("00700.HK")
    assert hm.is_hk_code("00700")
    assert hm.is_hk_code("700")
    assert not hm.is_hk_code("600519.SH")   # A 股 6 位带后缀
    assert not hm.is_hk_code("gb_AAPL")     # US 前缀
    assert not hm.is_hk_code("AAPL")


def test_file_code():
    assert hm.file_code("00700.HK") == "00700"
    assert hm.file_code("700") == "00700"


# ---------------- HK 口径常量与费率 config ----------------


def test_stamp_duty_constant():
    # HK 现行印花税双边 0.1%（2023-11-17 起由 0.13% 下调，2026-10 复核仍 0.1%）
    assert hm.STAMP_DUTY == 0.001
    cfg = hm.hk_default_config()
    assert cfg["stamp_tax"] == hm.STAMP_DUTY
    assert cfg["commission_rate"] == hm.COMMISSION_RATE
    assert cfg["min_commission"] == hm.MIN_COMMISSION
    assert cfg["initial_cash"] == 1_000_000.0   # HKD 原币（P-5）


def test_hk_fee_sell_includes_stamp_tax(tmp_path):
    m = FakeMarket({"00700.HK": [{"trade_date": "20261015", "open": 400.0, "high": 410.0,
                                  "low": 395.0, "close": 405.0, "pre_close": 400.0}]})
    e = PaperEngine(tmp_path / "t.db", m, config=hm.hk_default_config(), account="hk_fee")
    # 卖 500 股 @400 = 200,000 → 佣金 max(3, 200000*0.0003)=60 + 印花税 200000*0.001=200
    assert e._fee_sell(200_000.0) == 260.0
    # 买入无印花税参数（引擎 _fee_buy 无 stamp_tax_buy，灰位期挂账低估）
    assert e._fee_buy(200_000.0) == 60.0


# ---------------- HkMarket 六方法契约（_read 注入 rows，逻辑层纯 stdlib） ----------------


def _rows_00700():
    return [{"trade_date": "20261015", "open": 410.0, "high": 420.0, "low": 405.0,
             "close": 415.0, "pre_close": 408.0},
            {"trade_date": "20261016", "open": 420.0, "high": 428.0, "low": 414.0,
             "close": 417.8, "pre_close": 415.0}]


def test_hk_market_contract(tmp_path, monkeypatch):
    m = hm.HkMarket(export_dir=tmp_path)
    monkeypatch.setattr(m, "_read",
                        lambda code: _rows_00700() if hm.file_code(code) == "00700" else None)
    assert m.trading_days() == ["20261015", "20261016"]   # 回退路径：基准票 00700
    b = m.bar("00700.HK", "20261016")                     # 引擎原码（.HK 后缀）直达
    assert b == {"trade_date": "20261016", "open": 420.0, "high": 428.0,
                 "low": 414.0, "close": 417.8, "pre_close": 415.0}
    assert m.bar("00700.HK", "20261017") is None          # 非交易日 None
    assert m.bar("09988.HK", "20261016") is None          # 无湖文件票 None
    assert m.last_bar("00700.HK")["trade_date"] == "20261016"
    assert m.last_bar_before("00700.HK", "20261015")["close"] == 415.0
    assert m.first_bar_on_or_after("00700.HK", "20261016")["trade_date"] == "20261016"
    assert m.first_bar_on_or_after("00700.HK", "20261010")["trade_date"] == "20261015"


def test_trading_days_market_days_csv_priority(tmp_path):
    """H2 实证采纳：聚合日历 market_days.csv 优先（不依赖单票长历史）。"""
    d = tmp_path / "hk_paper"
    d.mkdir()
    (d / hm.MARKET_DAYS_FILE).write_text(
        "trade_date\n20261014\n20261015\n20261016\n", encoding="utf-8")
    m = hm.HkMarket(export_dir=tmp_path, hk_dir=d)
    assert m.trading_days() == ["20261014", "20261015", "20261016"]


def test_trading_days_missing_everything(tmp_path):
    m = hm.HkMarket(export_dir=tmp_path, hk_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        m.trading_days()


# ---------------- next_trading_day：09:30 HKT 界（防偷价口径，同 US/A 股） ----------------


def test_next_trading_day_hkt_boundary(tmp_path):
    d = tmp_path / "hk_paper"
    d.mkdir()
    (d / hm.MARKET_DAYS_FILE).write_text(
        "trade_date\n20261015\n20261016\n", encoding="utf-8")
    m = hm.HkMarket(export_dir=tmp_path, hk_dir=d)
    # 盘前（北京 08:00 = HKT 08:00 < 09:30）→ 当日开盘价尚未产生，可成交
    assert m.next_trading_day("2026-10-16T08:00:00") == "20261016"
    # 盘中（10:00）→ 日历尾 1016 之后外推：1017 周六 → 1019 周一
    assert m.next_trading_day("2026-10-16T10:00:00") == "20261019"
    # 1015 盘后 → 严格后继 1016
    assert m.next_trading_day("2026-10-15T23:00:00") == "20261016"


# ---------------- _read 直读湖清洗（pre_close NaN 补齐；pandas fake 注入离线测） ----------------


class FakeSeries(list):
    def fillna(self, other):
        return FakeSeries(v if (v is not None and v == v) else o
                          for v, o in zip(self, other))

    def shift(self, n=1):
        return FakeSeries([None] * n + list(self[: len(self) - n]) if n > 0 else list(self))

    def astype(self, _t):
        return FakeSeries(str(v) for v in self)


class FakeDF:
    def __init__(self, records):
        self._records = [dict(r) for r in records]
        self._cols = {k: FakeSeries(r[k] for r in self._records)
                      for k in self._records[0]} if self._records else {}

    def sort_values(self, _col):
        return self

    def __getitem__(self, col):
        return self._cols[col]

    def assign(self, **kw):
        out = FakeDF(self._records)
        for k, v in kw.items():
            out._cols[k] = v
        return out

    def to_dict(self, _orient):
        return [{k: list(col)[i] for k, col in self._cols.items()}
                for i in range(len(next(iter(self._cols.values()))))]


def test_read_fills_pre_close_from_prev_close(tmp_path, monkeypatch):
    """湖 pre_close 实量 NaN：次行补前日 close、首行回填当日 open（bar 契约完整性）。"""
    records = [
        {"trade_date": 20261015, "open": 410.0, "high": 420.0, "low": 405.0,
         "close": 415.0, "pre_close": None},
        {"trade_date": 20261016, "open": 420.0, "high": 428.0, "low": 414.0,
         "close": 417.8, "pre_close": None},
    ]

    class _FakePD:
        @staticmethod
        def read_parquet(_path):
            return FakeDF(records)

    monkeypatch.setitem(sys.modules, "pandas", _FakePD)
    (tmp_path / "00700.parquet").write_bytes(b"fake")   # 仅需存在性
    rows = hm.HkMarket(export_dir=tmp_path)._read("00700.HK")
    assert rows[0]["pre_close"] == 410.0                # 首行 NaN → 当日 open
    assert rows[1]["pre_close"] == 415.0                # 次行 → 前日 close
    assert rows[0]["trade_date"] == "20261015"          # 统一 YYYYMMDD 字符串


def test_read_missing_file_returns_none(tmp_path):
    assert hm.HkMarket(export_dir=tmp_path)._read("00700.HK") is None


# ---------------- refresh_calendar（全市场聚合日历；pandas fake 注入离线测） ----------------


class _FakeCalPD:
    """read_parquet(f, columns=['trade_date']) → {'trade_date': 可迭代日期}。"""

    def __init__(self, by_code):
        self._by = by_code

    def read_parquet(self, path, columns=None):
        code = Path(path).stem
        if code not in self._by:
            raise FileNotFoundError(path)
        return {"trade_date": FakeSeries(self._by[code])}


def test_refresh_calendar_union_sorted(tmp_path, monkeypatch):
    fake = _FakeCalPD({"00700": [20261015, 20261016],
                       "02800": [20260827, 20261016],   # 02800 短历史（H2 实证 36 根）
                       "09988": [20261014, 20261016]})
    monkeypatch.setitem(sys.modules, "pandas", fake)
    export_dir = tmp_path / "hk"
    export_dir.mkdir()
    for code in fake._by:
        (export_dir / f"{code}.parquet").write_bytes(b"fake")
    p = hm.refresh_calendar(export_dir=export_dir, hk_dir=tmp_path / "hk_paper")
    days = [r["trade_date"] for r in csv.DictReader(open(p, encoding="utf-8"))]
    assert days == ["20260827", "20261014", "20261015", "20261016"]  # 并集升序去重


def test_refresh_calendar_all_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        hm.refresh_calendar(export_dir=tmp_path / "nope", hk_dir=tmp_path)


# ---------------- T+0 挂账（P-3：照 US 先例不豁免，T+1 冻结保守无害） ----------------


def test_t0_freeze_kept_as_debt(tmp_path):
    """HK T+0 不豁免是拍板挂账：当日买入 T+1 冻结语义对 HK 账本依然生效（保守无害）。
    TODO(引擎工单)：available_qty 市场开关拍板后本测试同步更新。"""
    m = FakeMarket({"00700.HK": [{"trade_date": "20261015", "open": 400.0, "high": 420.0,
                                  "low": 395.0, "close": 415.0, "pre_close": 400.0},
                                 {"trade_date": "20261016", "open": 416.0, "high": 430.0,
                                  "low": 410.0, "close": 420.0, "pre_close": 415.0}]})
    e = PaperEngine(tmp_path / "t.db", m, config=hm.hk_default_config(), account="hk_t0")
    r = e.buy("00700.HK", 400, 416.0)          # 指定价须落在最近 bar 区间内且 ≤20% 仓位
    assert r["fill_date"] == "20261016"        # 手动单 fill_date=湖内最后交易日
    assert e.available_qty("00700.HK") == 0    # T+1 冻结保持（未豁免=挂账行为）
    with pytest.raises(Exception):
        e.sell("00700.HK", 400)                # 当日买不可卖（挂账口径下被冻结）


# ---------------- 运行时豁免（灰位期落地，进程内生效，不改 engine.py） ----------------


@pytest.fixture
def hk_waivers():
    """豁免前保存共享 engine 模块状态，测试后恢复——不污染同进程既有引擎测试。"""
    saved_lot, saved_lr = paper_engine.LOT, paper_engine.limit_ratio
    hm.apply_hk_engine_waivers()
    yield paper_engine
    paper_engine.LOT, paper_engine.limit_ratio = saved_lot, saved_lr


def test_waiver_limit_ratio_hk(hk_waivers):
    assert hk_waivers.limit_ratio("00700.HK") == 1.0   # HK 无涨跌停
    assert hk_waivers.limit_ratio("02800.HK") == 1.0
    assert hk_waivers.limit_ratio("09988.HK") == 1.0
    assert hk_waivers.limit_ratio("600519.SH") == 0.10  # A 股主板不受影响
    assert hk_waivers.limit_ratio("300750.SZ") == 0.20  # 创业板不受影响
    assert hk_waivers.limit_ratio("gb_AAPL") == 1.0     # US 先例豁免保持


def test_waiver_lot_non_100_buy(tmp_path, hk_waivers):
    """LOT 放开（P-4 灰位期）：HK 每手股数各异（汇丰 400/腾讯 100），非 100 倍数放行。"""
    m = FakeMarket({"00005.HK": [{"trade_date": "20261015", "open": 88.0, "high": 90.0,
                                  "low": 87.0, "close": 89.0, "pre_close": 88.0}]})
    e = PaperEngine(tmp_path / "t.db", m, config=hm.hk_default_config(), account="hk_lot")
    r = e.buy("00005.HK", 400, 88.0)   # 汇丰每手 400 股——A 股 LOT=100 会拒，HK 放行
    assert r["qty"] == 400
    assert abs(r["fee"] - 400 * 88.0 * hm.COMMISSION_RATE) < 0.01


def test_no_waiver_lot_100_default(tmp_path):
    """未豁免时引擎保持 A 股 LOT=100 原语义（豁免只作用于显式调用的 HK 跑批进程）。"""
    m = FakeMarket({"600519.SH": [{"trade_date": "20261015", "open": 1250.0, "high": 1260.0,
                                   "low": 1240.0, "close": 1255.0, "pre_close": 1250.0}]})
    e = PaperEngine(tmp_path / "t.db", m, account="main")
    with pytest.raises(Exception):
        e.buy("600519.SH", 150, 1250.0)   # 150 非 100 倍数 → 拒（原语义保持）


# ---------------- 账本通道灰态（同 US 先例：无账本不算错） ----------------


def test_hk_accounts_gray_when_no_db(tmpdir):
    assert hm.hk_accounts(Path(tmpdir) / "none.db") == []
    assert hm.hk_pass(Path(tmpdir) / "none.db", do_refresh=False) is None


def test_hk_symbols_in_db_filters_hk(tmp_path):
    import sqlite3
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.executescript(paper_engine._SCHEMA)
    con.execute("INSERT INTO account(account_id,cash,initial_cash,created_at) "
                "VALUES('hk_qingping',1000000,1000000,'2026-10-19T00:00:00')")
    con.execute("INSERT INTO positions(account_id,stock,qty,avg_cost,updated_at) "
                "VALUES('hk_qingping','00700.HK',500,400.0,'2026-10-19T00:00:00')")
    con.execute("INSERT INTO positions(account_id,stock,qty,avg_cost,updated_at) "
                "VALUES('main','600519.SH',100,1250.0,'2026-10-19T00:00:00')")
    con.commit()
    con.close()
    assert hm.hk_accounts(db) == ["hk_qingping"]
    assert hm.hk_symbols_in_db(db) == ["00700"]
