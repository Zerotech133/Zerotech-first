# -*- coding: utf-8 -*-
"""TR1「事件—价格影响跟踪复盘」包。

模块：

- :mod:`analysis.track_review.events`    事件读取/归一/去重/置信度
- :mod:`analysis.track_review.benchmark` AR/CAR、行业基准（PIT + 指数/流通市值加权）
- :mod:`analysis.track_review.store`     SQLite（track.db）幂等落库
- :mod:`analysis.track_review.toplist`   每日盘后 Top 列表 / build-db
- :mod:`analysis.track_review.card`      单股复盘卡（对齐 TR0 口径）
- :mod:`analysis.track_review.cli`       命令行入口

运行方式::

    python -X utf8 -m analysis.track_review toplist --date 20261106 --days 10 --limit 20
    python -X utf8 -m analysis.track_review card 600337.SH --end 20261106
    python -X utf8 -m analysis.track_review build-db --end 20261106 --days 10

全部数据访问为只读；产物只写 ``C:\\项目\\analysis_work\\track_review``。
"""
from __future__ import annotations

from .benchmark import (
    BenchmarkProvider,
    divergence_flag,
    evaluate_event,
)
from .card import build_card
from .config import Paths, default_paths
from .events import (
    Event,
    assign_d0,
    classify_ann,
    count_by_source,
    load_events_for_code,
    load_events_for_range,
)
from .store import TrackStore
from .toplist import build_db, build_toplist

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "BenchmarkProvider",
    "Event",
    "Paths",
    "TrackStore",
    "assign_d0",
    "build_card",
    "build_db",
    "build_toplist",
    "classify_ann",
    "count_by_source",
    "default_paths",
    "divergence_flag",
    "evaluate_event",
    "load_events_for_code",
    "load_events_for_range",
]
