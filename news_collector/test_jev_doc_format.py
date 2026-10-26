# -*- coding: utf-8 -*-
"""JEV 文档 markdown 生成回归（防 % 格式化再炸）。"""
import os
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "portal"))

import news_jev_chart as C  # noqa: E402

NOW = datetime.now(timezone(timedelta(hours=8)))


def test_md_h5_empty_items():
    md = C._md_h5(NOW, [])
    assert "JEV 新闻判断" in md
    assert "低置信" in md


def test_md_h5_sample_items():
    items = [
        {"t": NOW, "bias": "bull", "conf": 0.82, "scope": "cn", "title": "测试利多"},
        {"t": NOW, "bias": "bear", "conf": 0.55, "scope": "us", "title": "测试利空"},
        {"t": NOW - timedelta(hours=2), "bias": "neutral", "conf": 0.9,
         "scope": "macro", "title": "测试中性"},
    ]
    md = C._md_h5(NOW, items)
    assert "近 24h 方向分布" in md
    assert "（<70%）" in md or "（<70%%）" in md
