# -*- coding: utf-8 -*-
"""[兼容 shim] 雪球标的词形归一化 —— 真源已合并至 quant_backtest/xq_ticker_norm.py（D① 合一）。

本文件保留仅为兼容既有引用方（xq_classify / xq_daily_report / tools 迁移脚本 / 旧测试）
的 `import xq_ticker_normalize` 路径；全部实现 re-export 自 xq_ticker_norm。
勿在此新增逻辑；新代码请直接 import xq_ticker_norm。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent  # quant_backtest/
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import xq_ticker_norm as _src  # noqa: E402

# 公共 API 显式导出
KIND_DIST_SQL = _src.KIND_DIST_SQL
KINDS = _src.KINDS
backfill = _src.backfill
classify_token = _src.classify_token
ensure_norm_column = _src.ensure_norm_column
normalize_tickers = _src.normalize_tickers

# 全命名空间透传（含 _MAP/_TABLE/_SEGMENTS/_with_retry 等私有名）：
# 既有测试与 tools/xq_norm_migrate.py 直接访问模块私有属性，shim 必须完整复刻。
for _k, _v in vars(_src).items():
    if not _k.startswith("__") and _k not in globals():
        globals()[_k] = _v

__all__ = [
    "KIND_DIST_SQL",
    "KINDS",
    "backfill",
    "classify_token",
    "ensure_norm_column",
    "normalize_tickers",
]
