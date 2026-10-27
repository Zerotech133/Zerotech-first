#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""湖序列特征供给器：读 reports/qd_breadth_series.json（W3-5b 全市场
宽度序列，口径同 cross 环境闸门：raw close、MA20 有效股为分母）。

锚位 T-1 语义（显式标注，方案 §五阶段 2 要消灭的锚位差）：
湖序列是日批收盘后落的 T 日值；T 日盘中做判定时，最新可用锚
语义上是 T-1。约定：
  date 给定   —— 严格取序列中该锚日之前最近一个有效值；
  date=None  —— 取序列最后有效值（即判定时点的 T-1 收盘锚）。
湖只有 breadth，med20 缺维 → None（EnvGate 落 safe=None 降权，
不误杀——跨项目数据源不齐的既定对策）。
"""

import json
import os

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
DEFAULT_SERIES_FP = os.path.join(PROJECT_ROOT, "reports", "qd_breadth_series.json")


def _norm_date(d):
    return str(d).replace("-", "").strip()


class LakeFeeder:
    """湖 breadth 供给器（feeder 契约同 qpzm_parquet）。"""

    name = "lake"
    anchor_semantics = "T-1"

    def __init__(self, series_fp=None):
        self.series_fp = series_fp or DEFAULT_SERIES_FP

    def _load(self):
        try:
            with open(self.series_fp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def get_features(self, market, date=None):
        """→ (anchor_date|None, features dict)。

        序列里的非数值位（null）跳过（同 voltarget_ab._lake_last 口径）。
        """
        s = self._load()
        dates = [str(d) for d in (s.get("dates") or [])]
        breadths = s.get("breadth") or []
        pairs = [(d, b) for d, b in zip(dates, breadths) if isinstance(b, (int, float))]
        feats = {
            "breadth": None,
            "med20": None,
            "anchor_semantics": self.anchor_semantics,
        }
        if not pairs:
            return None, feats
        if date is None:
            d, b = pairs[-1]
        else:
            key = _norm_date(date)
            prior = [(d, b) for d, b in pairs if d < key]
            if not prior:
                return key, dict(feats, anchor_missing=True)
            d, b = prior[-1]
        return d, dict(feats, breadth=float(b))
