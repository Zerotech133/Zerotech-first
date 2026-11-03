# -*- coding: utf-8 -*-
"""ACAD1（司马徽）推荐池只读数据口（IFS-POOL，2026-11-03）。

历史：2026-10-30 WO-INV-POOL-UI 直接聚合 ``agent_platform/data/recommendation_pool``
+ ``pool_review`` 的 JSON 池供 ``/acad1_pool.html`` 展示。

IFS-POOL（WO-IFS-POOL-20261103）起口径变更：**PG ``v2_acad1_pool`` 是唯一真源**。
本模块保持 ``pool_snapshot(day)`` 旧签名（``portal_api.py`` 不改），内部委托
``portal/api_acad1_pool.py`` 按「pg_official → projection → shadow」优先级取数：

- ``pg_official``：直连 PG（jev_common DSN，只读）→ 官方 30 selected；
- ``projection``：只读 PG→JSON 投影（tools/datawarehouse/acad1_pool_projection.py）；
- ``shadow``：旧 JSON 池兜底（**不是官方**：official=false / review_status=shadow_only）。

兼容性：既有单测会 monkeypatch 本模块 ``POOL_DIR`` / ``REVIEW_DIR`` 注入临时旧池；
检测到自定义目录时强制走 shadow 旧池路径（不访问 PG/投影），保持旧行为可测。
"""

from __future__ import annotations

import io
import json
import os
from urllib.parse import parse_qs

try:  # 包内导入（portal_api 以顶层模块导入 portal 目录文件）
    from . import api_acad1_pool as _api
except ImportError:  # 脚本/测试直载（portal 目录在 sys.path）
    import api_acad1_pool as _api

# 视图口径（ACAD1-FULLVIEW，2026-11-04）：
#   single = 默认单日视图（历史行为，当前 12；缺省）；
#   full   = 显式 ?view=full 的每市场最新聚合只读视图（当前 30）。
VIEWS = ("single", "full")
VIEW_QUERY_KEY = "view"

PORTAL = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PORTAL)
_DEFAULT_POOL_DIR = os.path.join(REPO, "agent_platform", "data", "recommendation_pool")
_DEFAULT_REVIEW_DIR = os.path.join(REPO, "agent_platform", "data", "pool_review")
POOL_DIR = _DEFAULT_POOL_DIR
REVIEW_DIR = _DEFAULT_REVIEW_DIR


def _read(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def available_dates():
    try:
        out = [f[:-5] for f in os.listdir(POOL_DIR)
               if f.startswith("20") and f.endswith(".json")]
        return sorted(out, reverse=True)[:14]
    except OSError:
        return []


def _legacy_snapshot(day=None):
    """旧 JSON 池快照（shadow 兜底；保持 2026-10-30 字段契约）。"""
    return _api.read_legacy_snapshot(day=day, pool_dir=POOL_DIR, review_dir=REVIEW_DIR)


def view_from_query(query=None):
    """从 query string / dict 解析 ``view`` 参数。

    - 缺省/空 → ``single``（保持历史默认，不改变行为）；
    - ``view=full`` → ``full``（每市场最新聚合只读视图）；
    - 非法值/解析失败 → ``single``（不抛异常，页面不因坏 query 白屏；
      未知值不会被当成 full，避免误显示聚合 30）。
    """
    if not query:
        return "single"
    if isinstance(query, dict):
        values = query.get(VIEW_QUERY_KEY) or []
        if isinstance(values, str):
            values = [values]
    else:
        try:
            parsed = parse_qs(str(query), keep_blank_values=False)
        except (ValueError, UnicodeDecodeError):
            return "single"
        values = parsed.get(VIEW_QUERY_KEY) or []
    if not values:
        return "single"
    value = values[0]
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in VIEWS else "single"


def _custom_storage_dirs():
    """是否被测试/调用方注入自定义旧池目录（注入时强制 shadow 旧路径）。"""
    return (
        os.path.abspath(POOL_DIR) != os.path.abspath(_DEFAULT_POOL_DIR)
        or os.path.abspath(REVIEW_DIR) != os.path.abspath(_DEFAULT_REVIEW_DIR)
    )


def pool_snapshot(day=None, query=None):
    """ACAD1 池快照（官方优先：PG→投影→shadow 兜底；只读）。

    Parameters
    ----------
    day : str | None
        池日 YYYY-MM-DD；None=最新。
    query : str | dict | None
        HTTP query string（如 ``"view=full"``）；缺省/非法 → 单日视图（默认行为不变）。
        ``view=full`` 时返回每市场最新池日聚合视图（source_mode=pg_official_aggregated，
        当前 30 只）；缺 PG 时降级为单日并显式标注，不伪造 30。

    返回字段兼容旧契约（ok/mode/trading_permitted/generated_at/date/pool_id/
    owner/data_date/freshness_days/stale/summary/plan/items/dates），并新增
    ``view/source_mode/pool_date/pool_dates/published_at/review_status/official/
    degraded_reason``（见 ``api_acad1_pool`` 模块 docstring）。
    """
    view = view_from_query(query)
    legacy_only = _custom_storage_dirs()
    return _api.load_pool_snapshot(
        day=day,
        legacy_loader=_legacy_snapshot,
        allow_pg=not legacy_only,
        allow_projection=not legacy_only,
        view=view,
    )


__all__ = [
    "POOL_DIR",
    "REVIEW_DIR",
    "VIEWS",
    "VIEW_QUERY_KEY",
    "available_dates",
    "pool_snapshot",
    "view_from_query",
]

