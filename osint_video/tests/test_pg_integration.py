"""真库集成测试（默认跳过，需显式开启）。

## 为什么要有这一层

2026-11-04 在真实 PostgreSQL 上首次跑通事件层时，暴露了两个假库测试**结构性
无法发现**的缺陷：

1. ``_create_story`` 用 ``fetch_all`` 执行 ``INSERT ... RETURNING``。真实
   ``fetch_all`` 用 ``engine.connect()``，退出即回滚——写入静默丢失，而
   ``RETURNING`` 仍返回一个来自序列的自增 ID，故障推迟到插成员边时才以外键
   冲突爆出。假库只比对 SQL 片段，天然测不到事务语义。
2. ``story_query._LIST_SQL`` 的 ``:param::int`` 写法让 SQLAlchemy 的
   ``text()`` 无法识别绑定参数，一执行就是**语法错误**，整条消费出口（
   ``/api/v1/stories``、``/api/v1/brief/daily``）全部 500。假库只按片段返回
   构造好的行，SQL 根本没被数据库解析过。

两者的共同点是：**SQL 文本从未交给真实的 PostgreSQL 解析过**。本文件补的就是
这一环。

## 开启方式

默认 ``pytest.skip``，因为大部分开发和 CI 环境没有 PG。需要真库验证时：

    OSINT_TEST_PG=1 python -m pytest tests/test_pg_integration.py -q \\
        -p no:cacheprovider --basetemp=".pytest_tmp"

前置：``.env`` 指向的库已执行过 ``scripts/init_db.py``。测试自己插入的行都以
``pgtest-`` 前缀标记并在结束时清理，不会污染既有数据。
"""

from __future__ import annotations

import os

import pytest

from common import db

pytestmark = pytest.mark.skipif(
    os.environ.get("OSINT_TEST_PG") != "1",
    reason="真库集成测试：设置 OSINT_TEST_PG=1 开启（需可连通的 PG 且已 init_db）",
)

PREFIX = "pgtest-"

_INSERT_STORY = """
INSERT INTO osint_story (story_key, title, first_seen_at, last_seen_at,
                         independent_sources, fact_count, heat)
VALUES (:key, :title, now(), now(), 2, 2, 1.0)
RETURNING story_id
"""


def _cleanup() -> None:
    db.execute("DELETE FROM osint_story WHERE story_key LIKE :p", {"p": f"{PREFIX}%"})


@pytest.fixture()
def clean():
    _cleanup()
    yield
    _cleanup()


class TestWritePathCommits:
    def test_insert_returning_actually_persists(self, clean):
        """insert_returning 必须真的落库，而不是只返回一个 ID。"""
        rows = db.insert_returning(_INSERT_STORY, {"key": f"{PREFIX}w", "title": "写入落库"})
        story_id = int(rows[0]["story_id"])

        found = db.fetch_all(
            "SELECT story_id FROM osint_story WHERE story_key = :k", {"k": f"{PREFIX}w"}
        )
        assert found, "insert_returning 返回了 ID 但行不存在——事务被回滚了"
        assert int(found[0]["story_id"]) == story_id

    def test_fetch_all_rolls_back_writes(self, clean):
        """反向护栏：fetch_all 写库会回滚。

        这不是"应该这样用"，而是把这个反直觉行为钉成可执行的文档——正因为
        它看起来能拿到 ID，才有人误用。
        """
        rows = db.fetch_all(_INSERT_STORY, {"key": f"{PREFIX}r", "title": "回滚验证"})
        assert rows and rows[0]["story_id"], "fetch_all 仍会返回 RETURNING 的值"
        left = db.fetch_all(
            "SELECT 1 AS one FROM osint_story WHERE story_key = :k", {"k": f"{PREFIX}r"}
        )
        assert not left, "fetch_all 竟然提交了写入，本测试的前提已变，需重新评估"


class TestStoryQuerySqlIsValid:
    """消费出口的 SQL 必须能被 PostgreSQL 真正解析。"""

    def test_list_stories_runs(self, clean):
        db.insert_returning(_INSERT_STORY, {"key": f"{PREFIX}q", "title": "可被查询到"})
        result = db.fetch_all("SELECT count(*) AS n FROM osint_story WHERE story_key LIKE :p",
                              {"p": f"{PREFIX}%"})
        assert int(result[0]["n"]) == 1

        from knowledge import story_query

        payload = story_query.list_stories(hours=1, min_sources=1, limit=5)
        assert payload["total"] >= 1
        assert any(item["story_key"].startswith(PREFIX) for item in payload["items"])

    def test_list_stories_accepts_null_filters(self):
        """三个可空筛选条件传 None 时不得触发类型推断错误。"""
        from knowledge import story_query

        assert story_query.list_stories(stage=None, review_status=None, min_sources=None)

    def test_daily_brief_runs(self):
        from knowledge import story_query

        payload = story_query.daily_brief(limit=5)
        # 返回的是 {items, total, window_hours} 字典，不是裸列表——
        # 别用 len() 判断条数，那会数成键的个数。
        assert isinstance(payload, dict)
        assert set(payload) >= {"items", "total"}
        assert isinstance(payload["items"], list)

    def test_brief_view_runs(self, clean):
        """对外视图 v_story_brief 必须可查且能返回行（kg_brief 依赖它）。

        只断言"不报错"太弱——视图条件写错时返回 0 行同样不报错。这里插入一条
        满足全部条件的行（近 24h、independent_sources>=2、未 rejected），
        要求视图必须把它查出来。
        """
        rows = db.insert_returning(
            _INSERT_STORY, {"key": f"{PREFIX}v", "title": "应出现在简报里"}
        )
        assert rows

        found = db.fetch_all(
            "SELECT story_key FROM v_story_brief WHERE story_key = :k",
            {"k": f"{PREFIX}v"},
        )
        assert found, "v_story_brief 没返回满足条件的行，视图口径有问题"
