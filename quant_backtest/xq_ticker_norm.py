# -*- coding: utf-8 -*-
r"""雪球 canonical 双轨格式适配层（工单 WO-QT-20261020-079）。

背景（WO-075 审计实证）：
  xq_status_classified.norm_tickers（雪球库 quant_backtest/data/xueqiu/
  xueqiu_info.db）的 stock_a canonical 为前缀式（SZ300750/SH600519/BJ830799，
  雪球行情接口口径，见 tools/xq_ticker_normalize.py L24-28 canonical 口径）；
  下游量化体系股票代码为后缀式（300750.SZ/600519.SH）。按后缀式直查
  norm_tickers 命中 0。本模块做双轨互转，供消费点按需切换口径。

设计要点：
  - 纯函数：零 IO、零网络、无状态（不读 ticker_map.json / 不连库）；
  - 格式层转换，不区分 kind：只要形如 (SH|SZ|BJ)+6 位数字即互转，
    kind 过滤由调用方（norm_tickers JSON 里取 kind=='stock_a' 等）负责；
  - 港股裸 5 位码（00700）、美股字母码（AAPL）、指数专属后缀（.CSI/.HK）
    不支持互转 → 返回 None，如实标注不造数；
  - 幂等：已是目标形态的原样返回；
  - 容错：None/空/非字符串/畸形值不抛异常，标量返回 None；
    批量 *_many 返回 (结果列表, invalid 计数)，invalid 含畸形与不支持形态；
  - 大小写不敏感、容忍首尾空白（canonical 可能混入手工数据）。

形态判定（双向共用）：
  PREFIX_RE  ^(SH|SZ|BJ)\d{6}$   前缀式（雪球口径）
  SUFFIX_RE  ^\d{6}\.(SH|SZ|BJ)$ 后缀式（下游口径）

用法：
  norm_to_suffix("SZ300750")   -> "300750.SZ"
  norm_from_suffix("600519.SH") -> "SH600519"
  norm_to_suffix_many(["SZ300750", "00700"])  -> (["300750.SZ", None], 1)
"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple

__all__ = [
    "norm_to_suffix",
    "norm_from_suffix",
    "norm_to_suffix_many",
    "norm_from_suffix_many",
]

# A 股三交易所：沪 SH / 深 SZ / 北 BJ（北交所 6 位码 82xxxx/83xxxx/87xxxx 等）
_EXCH = r"(?:SH|SZ|BJ)"
_PREFIX_RE = re.compile(r"^(%s)(\d{6})$" % _EXCH)
_SUFFIX_RE = re.compile(r"^(\d{6})\.(%s)$" % _EXCH)


def _to_text(value) -> str:
    """任意输入 → 规整文本（None→''；非 str 先 str()；upper+去首尾空白）。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip().upper()


def norm_to_suffix(value) -> str | None:
    """前缀式 canonical → 后缀式代码；不支持/畸形 → None。

    SZ300750 -> 300750.SZ；SH600519 -> 600519.SH；BJ830799 -> 830799.BJ。
    已是后缀式原样返回（幂等）。港股 5 位码/美股/其余形态 None。
    """
    text = _to_text(value)
    if not text:
        return None
    m = _PREFIX_RE.match(text)
    if m:
        return "%s.%s" % (m.group(2), m.group(1))
    if _SUFFIX_RE.match(text):
        return text  # 已是后缀式，幂等
    return None


def norm_from_suffix(value) -> str | None:
    """后缀式代码 → 前缀式 canonical；不支持/畸形 → None。

    300750.SZ -> SZ300750；600519.SH -> SH600519；830799.BJ -> BJ830799。
    已是前缀式原样返回（幂等）。港股/美股/.CSI 等其余形态 None。
    """
    text = _to_text(value)
    if not text:
        return None
    m = _SUFFIX_RE.match(text)
    if m:
        return "%s%s" % (m.group(2), m.group(1))
    if _PREFIX_RE.match(text):
        return text  # 已是前缀式，幂等
    return None


def norm_to_suffix_many(values: Iterable) -> Tuple[List[str | None], int]:
    """批量前缀式→后缀式。返回 (逐条结果, invalid 计数)。

    结果与输入等长保序，不支持的条目占位 None；invalid 计入
    畸形值与不支持形态（含 None/空），不抛异常。
    """
    out: List[str | None] = []
    invalid = 0
    for v in values:
        r = norm_to_suffix(v)
        out.append(r)
        if r is None:
            invalid += 1
    return out, invalid


def norm_from_suffix_many(values: Iterable) -> Tuple[List[str | None], int]:
    """批量后缀式→前缀式。返回 (逐条结果, invalid 计数)，语义同上。"""
    out: List[str | None] = []
    invalid = 0
    for v in values:
        r = norm_from_suffix(v)
        out.append(r)
        if r is None:
            invalid += 1
    return out, invalid
