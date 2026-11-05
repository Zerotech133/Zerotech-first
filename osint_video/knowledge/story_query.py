"""事件（story）只读查询层。

## 定位

这是 P0 的**消费出口**。事件层建成后，``kg_brief`` / ``event_graph`` /
portal 不再需要自己去聚合散点事实，直接读这里即可拿到"今天哪几件事值得
看、几家独立来源在说"——即 2026-10-17 报告 F2「采了没人吃」的修复点。

## 脱敏约定

与 ``knowledge/api.py`` 的只读端点保持一致：**不返回正文**（``content``
可能含个人信息），只返回标题、来源、时间与判定元数据。正文要通过
``/api/v1/knowledge/units/{unit_key}`` 单独取，且受原有权限约束。
"""

from __future__ import annotations

from typing import Any

from common import db

#: 默认只回溯 7 天（日报关注窗口），可由调用方覆盖。
DEFAULT_WINDOW_HOURS: int = 168
#: 默认返回条数。
DEFAULT_LIMIT: int = 20
#: 单次查询上限，防止把整表拉出来。
MAX_LIMIT: int = 200

_LIST_SQL = """
SELECT s.story_id,
       s.story_key,
       s.title,
       s.summary,
       s.first_seen_at,
       s.last_seen_at,
       s.independent_sources,
       s.fact_count,
       s.heat,
       s.stage,
       s.topics,
       s.entities,
       s.review_status
  FROM osint_story s
 WHERE s.last_seen_at >= now() - make_interval(hours => :hours)
   AND (CAST(:min_sources AS integer) IS NULL
        OR s.independent_sources >= CAST(:min_sources AS integer))
   AND (CAST(:stage AS text) IS NULL OR s.stage = CAST(:stage AS text))
   AND (CAST(:review_status AS text) IS NULL
        OR s.review_status = CAST(:review_status AS text))
 ORDER BY s.heat DESC, s.last_seen_at DESC
 LIMIT :lim
"""

_DETAIL_SQL = """
SELECT s.story_id,
       s.story_key,
       s.title,
       s.summary,
       s.first_seen_at,
       s.last_seen_at,
       s.independent_sources,
       s.fact_count,
       s.heat,
       s.stage,
       s.topics,
       s.entities,
       s.confidence,
       s.review_status,
       s.model_version,
       s.created_at,
       s.updated_at
  FROM osint_story s
 WHERE s.story_id = :story_id
"""

_MEMBERS_SQL = """
SELECT m.unit_key,
       m.unit_version,
       m.relation,
       m.similarity,
       m.decided_by,
       m.rationale,
       m.source_id,
       m.created_at AS linked_at,
       u.title,
       u.source_url,
       u.source_channel,
       r.published_at,
       u.created_at AS ingested_at
  FROM osint_story_member m
  LEFT JOIN osint_knowledge_unit u
    ON u.unit_key = m.unit_key
   AND u.unit_version = m.unit_version
  LEFT JOIN osint_knowledge_resource r
    ON r.resource_key = u.resource_key
 WHERE m.story_id = :story_id
 ORDER BY m.created_at ASC, m.unit_key ASC
"""


def _clamp_limit(limit: int) -> int:
    """把条数约束在 [1, MAX_LIMIT]。"""
    return max(1, min(int(limit), MAX_LIMIT))


def list_stories(
    *,
    hours: int = DEFAULT_WINDOW_HOURS,
    min_sources: int | None = None,
    stage: str | None = None,
    review_status: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """按热度返回事件列表。

    Args:
        hours: 回溯窗口（小时），默认 168（7 天）。
        min_sources: 最小独立来源数。日报场景建议传 2 或 3，直接过滤掉
            只有单一来源的噪声。
        stage: 事件阶段过滤（emerging/developing/peak/fading/closed）。
        review_status: 复核状态过滤。
        limit: 条数上限。

    Returns:
        ``{"items": [...], "total": n, "window_hours": h}``
    """
    rows = db.fetch_all(
        _LIST_SQL,
        {
            "hours": max(1, int(hours)),
            "min_sources": min_sources,
            "stage": stage,
            "review_status": review_status,
            "lim": _clamp_limit(limit),
        },
    )
    return {
        "items": rows,
        "total": len(rows),
        "window_hours": max(1, int(hours)),
    }


def get_story_detail(story_id: int) -> dict[str, Any] | None:
    """返回单个事件及其归属事实（已脱敏）；不存在时返回 None。

    Note:
        成员的 ``published_at`` 取自 ``osint_knowledge_resource``（原始素材的发布
        时间），**可能为 NULL**——单元没关联资源时就是没有发布时间，不要用
        ``created_at`` 冒充。采集时间单独以 ``ingested_at`` 返回，两者语义不同：
        前者是"事什么时候发生"，后者是"我们什么时候拿到"。
        2026-11-05 修正：此前误写 ``u.published_at``，该列不存在于
        ``osint_knowledge_unit``，真实库上必抛 ``UndefinedColumn``（mock 测不出来）。
    """
    rows = db.fetch_all(_DETAIL_SQL, {"story_id": int(story_id)})
    if not rows:
        return None
    story = rows[0]
    members = db.fetch_all(_MEMBERS_SQL, {"story_id": int(story_id)})
    story["members"] = members
    story["member_count"] = len(members)
    return story


def find_story_by_key(story_key: str) -> dict[str, Any] | None:
    """按稳定业务键取事件详情（下游用 key 引用比用自增 id 更稳）。"""
    rows = db.fetch_all(
        "SELECT story_id FROM osint_story WHERE story_key = :story_key",
        {"story_key": str(story_key)},
    )
    if not rows:
        return None
    return get_story_detail(int(rows[0]["story_id"]))


def daily_brief(*, hours: int = 24, min_sources: int = 2, limit: int = 10) -> dict[str, Any]:
    """日报视图：只取"多家在说"的事件，按热度排序。

    这是 ``kg_brief`` 的最小替换口径——独立来源数是情报价值的第一序信号，
    单一来源的事件不进日报（进人工观察池）。
    """
    return list_stories(
        hours=hours,
        min_sources=min_sources,
        review_status=None,
        limit=limit,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_WINDOW_HOURS",
    "MAX_LIMIT",
    "daily_brief",
    "find_story_by_key",
    "get_story_detail",
    "list_stories",
]
