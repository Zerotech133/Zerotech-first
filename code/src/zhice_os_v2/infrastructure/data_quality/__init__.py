# -*- coding: utf-8 -*-
"""数据仓数据质量（data quality）：freshness gate 可复用模块。

对外 API::

    from zhice_os_v2.infrastructure.data_quality import (
        GateReport, StaleDataError, evaluate, gate_mode_from_env,
        preflight_cache_build, quality_block,
    )
"""
from __future__ import annotations

from zhice_os_v2.infrastructure.data_quality.age_gate import (
    CONSUMED_DATASETS,
    DEFAULT_MODE,
    ENV_MODE,
    EXPECTED_STALE_CONSUMED,
    GateMode,
    GateReport,
    SourceVerdict,
    StaleDataError,
    attach_quality,
    evaluate,
    gate_mode_from_env,
    load_inventory,
    preflight_cache_build,
)

__all__ = [
    "CONSUMED_DATASETS",
    "DEFAULT_MODE",
    "ENV_MODE",
    "EXPECTED_STALE_CONSUMED",
    "GateMode",
    "GateReport",
    "SourceVerdict",
    "StaleDataError",
    "attach_quality",
    "evaluate",
    "gate_mode_from_env",
    "load_inventory",
    "preflight_cache_build",
]
