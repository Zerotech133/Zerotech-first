"""事件查询层 SQL 与真实库表的一致性检查。

## 为什么需要这个测试

2026-11-05 做看板时撞出：``story_query._MEMBERS_SQL`` 里写的是 ``u.published_at``，
而 ``published_at`` 在 ``osint_knowledge_resource`` 上，``osint_knowledge_unit`` 根本
没有这一列——真实库上必抛 ``UndefinedColumn``。此前该模块**没有任何测试**，这条
读路径（"采了没人吃"的修复点）实际上一次都没跑通过。

教训：mock 测不出列名拼错。所以这里分两层——

1. 静态层（默认执行）：把已知的错列钉死，防止后人改回去；
2. 真实层（``OSINT_TEST_PG=1`` 才跑）：把两条 SQL 拿到真库上执行一遍，
   列名拼错、类型转换错、join 写错都会当场抛。

真实层直接读项目根 ``.env``（conftest 会屏蔽 .env 对 settings 的影响），
并绕开 ``common.db`` 的进程级 Engine 单例，避免污染其他测试。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from knowledge import story_query

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PG_ENABLED = os.getenv("OSINT_TEST_PG") == "1"


def _dsn_from_env_file() -> dict[str, object]:
    """直接解析项目根 .env，不经过 settings（conftest 会清掉环境变量影响）。"""
    values: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    if not values.get("PG_HOST"):
        pytest.skip("项目根 .env 没有 PG_HOST，无法连真实库")
    return {
        "host": values.get("PG_HOST", "127.0.0.1"),
        "port": int(values.get("PG_PORT", "5432")),
        "user": values.get("PG_USER", "postgres"),
        "password": values.get("PG_PASSWORD") or None,
        "dbname": values.get("PG_DB", "osint"),
    }


def test_members_sql_does_not_select_unit_published_at() -> None:
    """``published_at`` 属于资源表，不属于单元表——这条错列曾让详情接口整段不可用。"""
    sql = story_query._MEMBERS_SQL
    assert "u.published_at" not in sql, (
        "osint_knowledge_unit 没有 published_at 列；发布时间要从 "
        "osint_knowledge_resource 取"
    )
    assert "osint_knowledge_resource" in sql, "成员查询要 join 资源表才能拿到发布时间"
    assert "ingested_at" in sql, "采集时间要单独返回，不能拿去冒充发布时间"


@pytest.mark.skipif(not PG_ENABLED, reason="需要真实 PG：设置 OSINT_TEST_PG=1")
def test_queries_execute_on_real_database() -> None:
    """按应用的真实执行方式（SQLAlchemy text）在真库上跑一遍。

    参数取不存在的值，只验证语句本身能被解析与规划——列名/join/类型错误都会抛。
    """
    sqlalchemy = pytest.importorskip("sqlalchemy")
    pytest.importorskip("psycopg")
    dsn = _dsn_from_env_file()
    url = sqlalchemy.URL.create(
        "postgresql+psycopg",
        username=str(dsn["user"]),
        password=dsn["password"],
        host=str(dsn["host"]),
        port=int(dsn["port"]),
        database=str(dsn["dbname"]),
    )
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text(story_query._LIST_SQL),
                {
                    "hours": 24,
                    "min_sources": None,
                    "stage": None,
                    "review_status": None,
                    "lim": 5,
                },
            ).fetchall()
            conn.execute(
                sqlalchemy.text(story_query._DETAIL_SQL), {"story_id": -1}
            ).fetchall()
            conn.execute(
                sqlalchemy.text(story_query._MEMBERS_SQL), {"story_id": -1}
            ).fetchall()
    finally:
        engine.dispose()
