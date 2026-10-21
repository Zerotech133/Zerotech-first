# -*- coding: utf-8 -*-
"""[WO-QT-20261021-092] export_us_hk 增量比对「只补尾、不补头」缺陷修复测试。

背景判例：原 _topup_symbol 仅判 old_max < db_max 便从 old_max+1 续拉，
从不检查 old_min。首导时库里只有近端数据的标的，后续库补灌更早历史也
永远补不回本地 → 头部空洞永不复原（实测抽样 400 只美股标的中 181 只
（45.3%）库内行数多于本地，合计多 48.8%）。

修复口径：增量比对改为**两端**比对库内 (min,max)——
  · 库内起点更早（db_min < old_min）→ 头部空洞 → 整只重导
  · 库内终点更新（db_max > old_max）→ 尾部缺口 → 追加增量
  · 两端齐 → skip
  · 重导后行数反而变少（库内残片/回档）→ 保留旧文件不覆盖（kept）

纪律：全部注入合成数据与临时目录，不触网、不读真实库、不碰 data/ 真实文件。
运行：仓根 pytest quant_backtest/tests/test_export_us_hk_head_gap.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))        # quant_backtest/tests
QBT = os.path.dirname(HERE)                              # quant_backtest
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import export_us_hk as ex                                # noqa: E402

COLS = ["open", "high", "low", "close", "volume", "turnover", "pre_close"]


def _bars(d0, d1, base=100.0):
    """生成 [d0, d1] 自然日区间日线（含两端），index=DatetimeIndex。"""
    idx = pd.date_range(start=d0, end=d1, freq="D")
    n = len(idx)
    return pd.DataFrame({c: [base] * n for c in COLS}, index=idx)


def _write_local(path, d0, d1):
    """按导出格式写本地 parquet（trade_date 为 YYYYMMDD 字符串）。"""
    idx = pd.date_range(start=d0, end=d1, freq="D")
    n = len(idx)
    pd.DataFrame({
        "ts_code": ["AAA"] * n,
        "trade_date": [x.strftime("%Y%m%d") for x in idx],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [1000.0] * n,
        "amount": [1e6] * n, "pre_close": [100.0] * n,
        "pct_chg": [0.0] * n,
    }).to_parquet(path, index=False)


def _local_rows(path):
    d = pd.read_parquet(path, columns=["trade_date"])
    return len(d), str(d["trade_date"].min()), str(d["trade_date"].max())


class _Base(unittest.TestCase):
    """统一注入：available_symbols / symbol_ranges / load_bars 全假。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_here = ex.HERE
        ex.HERE = self.tmp.name                    # 输出落临时目录
        self.addCleanup(self._restore)
        self.out = Path(self.tmp.name) / "data" / "us"
        self.out.mkdir(parents=True, exist_ok=True)

        self.old_avail = ex.dl.available_symbols
        self.old_ranges = ex.dl.symbol_ranges
        self.old_load = ex.dl.load_bars
        self.addCleanup(self._restore_dl)
        self.calls = []

    def _restore(self):
        ex.HERE = self.old_here

    def _restore_dl(self):
        ex.dl.available_symbols = self.old_avail
        ex.dl.symbol_ranges = self.old_ranges
        ex.dl.load_bars = self.old_load

    def inject(self, symbols, ranges, full, inc=None):
        """ranges={sym:(dmin,dmax)}；full={sym:df 全量}；inc={sym:df 增量}。

        注意：真实 dl.symbol_ranges 返回值已归一为 8 位 YYYYMMDD（内部
        replace('-','')[:8]），这里同样归一，保证与生产契约一致——
        否则带横线的字面量会因 '-'<'0' 被误判成头部缺口（本测试首轮即踩中）。
        """
        norm = {s: tuple(str(x).replace("-", "")[:8] for x in v)
                for s, v in ranges.items()}
        ex.dl.available_symbols = lambda market, limit=None: list(symbols)
        ex.dl.symbol_ranges = lambda market, syms: {
            s: norm[s] for s in syms if s in norm}

        def _load(market, syms, start=None, end=None):
            self.calls.append({"syms": list(syms), "start": start})
            src = inc if start else full
            src = src or {}
            return {s: src[s] for s in syms if s in src}

        ex.dl.load_bars = _load


class TestHeadGap(_Base):
    def test_head_gap_triggers_full_redo(self):
        """库内起点更早 → 整只重导（全量、不带 start），本地补回头部。"""
        fn = self.out / "AAA.parquet"
        _write_local(fn, "2026-04-06", "2026-10-08")      # 130 行，缺头部
        n0, lo0, _ = _local_rows(fn)
        self.assertEqual(n0, 186)                          # 04-06~10-08 自然日
        self.inject(["AAA"],
                    {"AAA": ("2025-09-24", "2026-10-08")},
                    {"AAA": _bars("2025-09-24", "2026-10-08")})
        stats = ex.export_market("US", batch=200)
        self.assertEqual(stats["redo"], 1, "头部空洞必须计 redo")
        self.assertEqual(stats["upd"], 0, "头部缺口不应走增量路径")
        n1, lo1, _ = _local_rows(fn)
        self.assertEqual(lo1, "20250924", "重导后本地起点须回到库内起点")
        self.assertGreater(n1, n0, "重导后行数必须增加")
        self.assertTrue(any(c["start"] is None for c in self.calls),
                        "重导必须是全量读取（start=None）")

    def test_both_ends_aligned_skips_without_reading(self):
        """两端齐 → skip，且完全不触发库读取（零无效 IO）。"""
        fn = self.out / "AAA.parquet"
        _write_local(fn, "2025-09-24", "2026-10-08")
        self.inject(["AAA"],
                    {"AAA": ("2025-09-24", "2026-10-08")},
                    {"AAA": _bars("2025-09-24", "2026-10-08")})
        stats = ex.export_market("US", batch=200)
        self.assertEqual(stats["skip"], 1)
        self.assertEqual(stats["redo"], 0)
        self.assertEqual(stats["new"], 0)
        self.assertEqual(self.calls, [], "两端齐时不得调用 load_bars")

    def test_tail_only_gap_uses_incremental(self):
        """仅尾部缺口 → 走增量（带 start），不整只重导。"""
        fn = self.out / "AAA.parquet"
        _write_local(fn, "2026-04-06", "2026-10-08")
        n0, _, hi0 = _local_rows(fn)
        self.inject(["AAA"],
                    {"AAA": ("2026-04-06", "2026-10-20")},
                    {"AAA": _bars("2026-04-06", "2026-10-20")},
                    inc={"AAA": _bars("2026-10-09", "2026-10-20")})
        stats = ex.export_market("US", batch=200)
        self.assertEqual(stats["upd"], 1)
        self.assertEqual(stats["redo"], 0, "无头缺不得走重导")
        n1, lo1, hi1 = _local_rows(fn)
        self.assertEqual(lo1, "20260406", "增量不得改动头部")
        self.assertEqual(hi1, "20261020")
        self.assertGreater(n1, n0)
        self.assertTrue(all(c["start"] is not None for c in self.calls),
                        "增量必须带 start")

    def test_redo_shrink_keeps_old_file(self):
        """触发重导但库返回量反而更少 → 保留旧文件，不静默减损。

        场景要点：必须让「头部缺口」成立（db_min < old_min）才会进重导分支，
        再由 load_bars 返回不全（模拟库内中段缺失/行被过滤）导致新数据
        比旧文件短。若只让库内整体更短，则两端齐 → skip，属正确行为
        （该情形由 test_both_ends_aligned_skips_without_reading 覆盖）。
        """
        fn = self.out / "AAA.parquet"
        _write_local(fn, "2025-09-24", "2026-10-08")
        n0, lo0, hi0 = _local_rows(fn)
        self.inject(["AAA"],
                    {"AAA": ("2025-09-01", "2026-10-08")},   # 库内起点更早 → 重导
                    {"AAA": _bars("2026-06-01", "2026-10-08")})  # 但返量偏少
        stats = ex.export_market("US", batch=200)
        self.assertEqual(stats["kept"], 1, "行数减少必须计 kept")
        self.assertEqual(stats["redo"], 0)
        n1, lo1, hi1 = _local_rows(fn)
        self.assertEqual((n1, lo1, hi1), (n0, lo0, hi0),
                         "旧文件必须原样保留")

    def test_missing_in_db_skips(self):
        """库内无该标的 → skip，不导不造数。"""
        _write_local(self.out / "AAA.parquet", "2026-04-06", "2026-10-08")
        self.inject(["AAA"], {}, {})
        stats = ex.export_market("US", batch=200)
        self.assertEqual(stats["skip"], 1)
        self.assertEqual(stats["new"] + stats["redo"] + stats["upd"], 0)
        self.assertEqual(self.calls, [])

    def test_force_reexports_all(self):
        """--force 全量重导，不比对库内起止。"""
        _write_local(self.out / "AAA.parquet", "2026-04-06", "2026-10-08")
        self.inject(["AAA"], {"AAA": ("2025-09-24", "2026-10-20")},
                    {"AAA": _bars("2025-09-24", "2026-10-20")})
        stats = ex.export_market("US", batch=200, force=True)
        self.assertEqual(stats["new"], 1)
        self.assertEqual(_local_rows(self.out / "AAA.parquet")[1], "20250924")


class TestLocalMeta(_Base):
    def test_local_meta_reads_both_ends(self):
        fn = self.out / "AAA.parquet"
        _write_local(fn, "2026-04-06", "2026-10-08")
        n, lo, hi = ex._local_meta(fn)
        self.assertEqual((lo, hi), ("20260406", "20261008"))
        self.assertGreater(n, 0)

    def test_local_meta_absent_returns_zeros(self):
        self.assertEqual(ex._local_meta(self.out / "NONE.parquet"),
                         (0, "", ""))


if __name__ == "__main__":
    unittest.main()
