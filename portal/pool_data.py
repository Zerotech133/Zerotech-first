# -*- coding: utf-8 -*-
"""ACAD1（司马徽）推荐池只读数据口（IFS-POOL，2026-11-03）。

历史：2026-10-30 WO-INV-POOL-UI 直接聚合 ``agent_platform/data/recommendation_pool``
+ ``pool_review`` 的 JSON 池供 ``/acad1_pool.html`` 展示。

IFS-POOL（WO-IFS-POOL-20261103）起口径变更：**PG ``v2_acad1_pool`` 是唯一真源**。
本模块保持 ``pool_snapshot(day)`` 旧签名（``portal_api.py`` 不改），内部委托
``portal/api_acad1_pool.py`` 按「pg_official → projection → shadow」优先级取数：

- ``pg_official`` / ``pg_official_aggregated``：直连 PG（jev_common DSN，只读）；
- ``projection``：只读 PG→JSON 投影（tools/datawarehouse/acad1_pool_projection.py）；
- ``shadow``：旧 JSON 池兜底（**不是官方**：official=false / review_status=shadow_only）。

视图默认（ACAD1-FULLDEFAULT，2026-11-04）：``/api/acad1/pool`` 默认 ``view=full``
——每市场最新 ``pool_date`` 聚合（当前 30，source_mode=``pg_official_aggregated``）；
显式 ``?view=single`` 仍返回历史单日视图（当前 12）。缺 PG 时 full 降级为单日并
显式 ``degraded_reason``，**不伪造 30**。

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

# 视图口径（ACAD1-FULLVIEW 2026-11-04 → ACAD1-FULLDEFAULT 2026-11-04）：
#   full   = 默认（对外）：每市场最新 pool_date 聚合只读视图（当前 30）；
#   single = 显式 ?view=single：历史单日视图（当前 12）。
VIEWS = ("single", "full")
DEFAULT_VIEW = "full"
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
    """从 query string / dict 解析 ``view`` 参数（默认 ``full``，2026-11-04 起）。

    - 缺省/空/解析失败/非法值 → ``DEFAULT_VIEW="full"``（对外默认=每市场最新聚合 30；
      页面不因坏 query 白屏；非法值不会退回单日，避免默认行为被意外降级）；
    - ``view=full`` → ``full``（显式，聚合只读视图）；
    - ``view=single`` → ``single``（显式，历史单日视图）。
    """
    if not query:
        return DEFAULT_VIEW
    if isinstance(query, dict):
        values = query.get(VIEW_QUERY_KEY) or []
        if isinstance(values, (str, bytes)):
            values = [values]
        elif not isinstance(values, (list, tuple)):
            values = [values]
    else:
        try:
            parsed = parse_qs(str(query), keep_blank_values=False)
        except (ValueError, UnicodeDecodeError):
            return DEFAULT_VIEW
        values = parsed.get(VIEW_QUERY_KEY) or []
    if not values:
        return DEFAULT_VIEW
    value = values[0]
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in VIEWS else DEFAULT_VIEW


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
        HTTP query string（如 ``"view=single"``）。**缺省默认 ``view=full``**：
        每市场最新池日聚合视图（source_mode=pg_official_aggregated，当前 30 只）；
        显式 ``?view=single`` 返回历史单日视图（当前 12）。缺 PG 时 full 降级为单日并
        显式标注，不伪造 30。

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
    "DEFAULT_VIEW",
    "POOL_DIR",
    "REVIEW_DIR",
    "VIEWS",
    "VIEW_QUERY_KEY",
    "available_dates",
    "pool_snapshot",
    "view_from_query",
]

