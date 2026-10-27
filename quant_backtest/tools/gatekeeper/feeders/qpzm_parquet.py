#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qpzm parquet 特征供给器：读 data/ashare/*.parquet 算环境特征。

口径逐项对齐 qpzm_backtest.load_wide（447-489 行）+ env_gate（130-144
行），2026-10-15 联调修正（等价性黄金基线 reports/_gatekeeper_golden_
baseline.json 锁定，test_equivalence_golden.py 逐值 diff=0）：
  1) 复权链：ratio=(c.shift(1)/pre_close)，异常域 [0.05,20] 外置 1.0，
     fillna(1.0)，cumprod 后乘 close（load_wide :456-461 同式）；
  2) MA20 = rolling(20, min_periods=1)（load_wide :466，1 根即有值）；
  3) 计闸门槛：锚日累计 bar 数 n_bars >= MIN_BARS_ENV(21) 才计入
     分母（n_bars 更小 → above 置 NaN，同 env_gate 的 above.notna()
     掩码语义；MIN_BARS_ENV=21 对齐 qpzm_backtest.py:68 现值）；
  4) ret20 = c/c.shift(20)-1（复权后 c，load_wide :466 同式）；
  5) breadth = above 掩码后均值；med20 = ret20 按同一掩码取中位。

bar 不足的股票当日不计入分母——个股缺维只降样本，不误杀。
首跑全量读湖（5597 只 parquet，仅 trade_date/close/pre_close 三列）
并缓存；之后 get_features 为 O(锚日) 内存切片。

market 参数本 feeder 不过滤（湖目录即单市场 CN 股），仅透传记账。
"""

import glob
import os

import pandas as pd

MIN_BARS_ENV = 21  # 计闸门槛：锚日累计 bar 数下限（对齐 qpzm:68）

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
DEFAULT_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "ashare")


def _norm_date(d):
    """'2026-10-12' / '20261012' -> '20261012'。"""
    return str(d).replace("-", "").strip()


class QpzmParquetFeeder:
    """按锚日产出 {breadth, med20, n_stocks}；缺源一律 None（缺维降权）。"""

    name = "qpzm_parquet"

    def __init__(self, data_dir=None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self._cache = None  # (close_df, above_df, ret20_df)

    # ---- 装载与口径（对齐 qpzm load_wide） ------------------------------

    def _load(self):
        """全湖一次装载 -> (above_df, ret20_df)，按锚日切片复用。

        ⚠️ own-bar 语义（对齐 qpzm load_wide "逐股在自身 bar 序列上算
        指标再 scatter"）：rolling/shift 必须在该股自身日期序列上做，
        先算完再 reindex 到 union 索引。在 union DataFrame 上直接
        rolling 会让停牌断档票的窗口跨过缺日格，MA20 偏移（联调实证：
        600243.SH 等停牌票 above 翻转，2026-10-15 修复）。

        above_df：close>MA20（复权后 c、own-bar MA20=rolling(20,
        min_periods=1)），own 第 n 根 n<MIN_BARS_ENV(21) 置 NaN（计闸
        门槛）。ret20_df：c/c.shift(20)-1（复权后 c，own-bar）。
        """
        if self._cache is not None:
            return self._cache
        closes = {}
        for fp in sorted(glob.glob(os.path.join(self.data_dir, "*.parquet"))):
            code = os.path.splitext(os.path.basename(fp))[0]
            try:
                df = pd.read_parquet(fp, columns=["trade_date", "close", "pre_close"])
            except Exception:  # noqa: BLE001 缺列/损坏票跳过
                continue
            s = df.drop_duplicates("trade_date").set_index("trade_date").sort_index()
            if s.empty:
                continue
            c = pd.to_numeric(s["close"], errors="coerce")
            pc = pd.to_numeric(s["pre_close"], errors="coerce")
            # 复权链（qpzm load_wide :456-461 同式）
            ratio = (c.shift(1) / pc).astype(float)
            ratio = ratio.where(ratio.between(0.05, 20.0), 1.0).fillna(1.0)
            c = c * ratio.cumprod()
            closes[code] = c
        if not closes:
            self._cache = (pd.DataFrame(dtype=float), pd.DataFrame(dtype=float))
            return self._cache
        close = pd.DataFrame(closes).sort_index()
        above_cols, ret_cols = {}, {}
        for code, c in closes.items():  # own-bar 逐股算
            ma20 = c.rolling(20, min_periods=1).mean()
            nb = pd.Series(range(1, len(c) + 1), index=c.index, dtype=float)
            ab = (c > ma20).astype(float)
            above_cols[code] = ab.where(nb >= MIN_BARS_ENV)  # 门槛外 NaN
            ret_cols[code] = c / c.shift(20) - 1.0
        above = pd.DataFrame(above_cols).reindex(close.index)
        ret20 = pd.DataFrame(ret_cols).reindex(close.index)
        self._cache = (above, ret20)
        return self._cache

    # ---- 对外接口 -------------------------------------------------------

    def get_features(self, market, date=None):
        """-> (anchor_date|None, features dict)。

        date=None 取湖内最新交易日为锚；指定日期湖内无行情 -> 特征全
        None + anchor_missing=True（EnvGate 落 safe=None 缺维降权）。
        """
        above, ret20 = self._load()
        if above.empty:
            return None, {"breadth": None, "med20": None, "n_stocks": 0}
        if date is None:
            anchor = str(above.index.max())
        else:
            anchor = _norm_date(date)
            if anchor not in above.index:
                return anchor, {
                    "breadth": None,
                    "med20": None,
                    "n_stocks": 0,
                    "anchor_missing": True,
                }
        mask = above.loc[anchor].notna()  # n_bars>=MIN_BARS_ENV
        n = int(mask.sum())
        if n == 0:
            return anchor, {"breadth": None, "med20": None, "n_stocks": 0}
        b_raw = above.loc[anchor][mask].mean()
        m_raw = ret20.loc[anchor][mask].median()  # 中位跳 NaN（env_gate 同）
        return anchor, {
            "breadth": None if pd.isna(b_raw) else float(b_raw),
            "med20": None if pd.isna(m_raw) else float(m_raw),
            "n_stocks": n,
        }
