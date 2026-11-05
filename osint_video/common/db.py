"""PostgreSQL 连接与执行封装（psycopg3 + SQLAlchemy Core，架构 §2.1）。

约定：
- 惰性单例 Engine：首次真实使用时才建连（``pool_pre_ping`` 自动剔除死连接）；
- PG 不可用（未装驱动 / 无法连通 / 认证失败）统一抛
  :class:`DatabaseUnavailableError`，异常信息自带排障指引；
- 业务层（API / worker）捕获该异常做**降级**（返回 503 / 跳过本轮），
  绝不让 PG 故障 crash 主流程（架构 §7.7）；
- 查询统一走 :func:`fetch_all` / :func:`execute` / :func:`execute_many` /
  :func:`transaction`，SQL 一律用 ``sqlalchemy.text`` 命名参数绑定。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, URL

from config.settings import get_settings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """PG 不可用（驱动缺失 / 连接失败 / 认证失败）。调用方应降级而非崩溃。"""


_TROUBLESHOOT_HINT = (
    "排障指引：1) 确认 PG 容器已启动（docker ps | grep 5432）；"
    "2) pip install 'psycopg[binary]>=3.1'；"
    "3) 检查项目根 .env 的 PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB 配置"
)

#: 进程级 Engine 单例
_engine: Engine | None = None


def get_engine() -> Engine:
    """获取（并按需创建）全局 Engine 单例；不可用时抛 DatabaseUnavailableError。"""
    global _engine
    if _engine is not None:
        return _engine
    settings = get_settings()
    url = URL.create(
        "postgresql+psycopg",
        username=settings.pg_user,
        password=settings.pg_password or None,
        host=settings.pg_host,
        port=settings.pg_port,
        database=settings.pg_db,
    )
    try:
        engine = create_engine(
            url,
            pool_size=5,
            max_overflow=5,
            pool_timeout=10,
            pool_pre_ping=True,  # 取连接前 ping，自动剔除断连
            connect_args={"connect_timeout": 5},
        )
        with engine.connect() as conn:  # 试连：尽早暴露配置/驱动问题
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(
            f"PostgreSQL 不可用（{settings.pg_host}:{settings.pg_port}/{settings.pg_db}）：{exc}\n{_TROUBLESHOOT_HINT}"
        ) from exc
    _engine = engine
    logger.info(
        "PG 连接池就绪：%s:%s/%s", settings.pg_host, settings.pg_port, settings.pg_db
    )
    return _engine


def reset_engine() -> None:
    """丢弃当前 Engine（配置变更/测试时用）。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def health() -> bool:
    """PG 健康探测（绝不抛异常）。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """执行查询，返回 ``[{列名: 值}, ...]``。

    **只读专用。** 内部用 ``engine.connect()``，退出时事务被 ROLLBACK——
    拿它执行 INSERT/UPDATE/DELETE 会静默丢写：语句看似成功，行却没落库。
    带 ``RETURNING`` 时更危险：序列的 ``nextval`` 不受事务回滚影响，于是能拿到
    一个"看起来正常"的自增 ID，而对应的行根本不存在，故障会推迟到后续外键
    校验时才炸，现场与真因相距很远。

    写操作请用 :func:`execute` / :func:`execute_many`；写入同时要取回值时用
    :func:`insert_returning`。
    """
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), params or {}).mappings().all()
            return [dict(row) for row in rows]
    except DatabaseUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(f"PG 查询失败：{exc}\nSQL={sql}") from exc


def insert_returning(
    sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """执行**写入型**语句并取回 ``RETURNING`` 结果，成功后提交。

    存在理由：``INSERT ... RETURNING id`` 既要落库又要拿回生成值，
    :func:`fetch_all` 会回滚、:func:`execute` 只给 rowcount，两者都做不到。
    本函数在显式事务里执行并 COMMIT，是这类场景的唯一正确入口。

    Returns:
        返回行列表，结构与 ``fetch_all`` 一致（无 ``RETURNING`` 时为空列表）。
    """
    try:
        with transaction() as conn:
            rows = conn.execute(text(sql), params or {}).mappings().all()
            return [dict(row) for row in rows]
    except DatabaseUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(f"PG 写入失败：{exc}\nSQL={sql}") from exc


def execute(sql: str, params: dict[str, Any] | None = None) -> int:
    """执行单条 DML（INSERT/UPDATE/DELETE），自动提交，返回受影响行数。"""
    try:
        with get_engine().begin() as conn:
            return int(conn.execute(text(sql), params or {}).rowcount)
    except DatabaseUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(f"PG 执行失败：{exc}\nSQL={sql}") from exc


def execute_many(sql: str, seq_of_params: Sequence[dict[str, Any]]) -> int:
    """批量执行同构 DML（一个事务内），返回累计受影响行数。"""
    if not seq_of_params:
        return 0
    try:
        with get_engine().begin() as conn:
            total = 0
            for params in seq_of_params:
                total += int(conn.execute(text(sql), params).rowcount)
            return total
    except DatabaseUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseUnavailableError(f"PG 批量执行失败：{exc}\nSQL={sql}") from exc


@contextmanager
def transaction() -> Iterator[Connection]:
    """事务上下文：``with db.transaction() as conn: conn.execute(text(sql), params)``。

    正常退出自动 COMMIT，异常自动 ROLLBACK；PG 不可用时抛 DatabaseUnavailableError。
    """
    conn = get_engine().connect()
    try:
        conn.begin()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(
    conn: Connection, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """在既有事务连接上执行查询，返回 dict 行列表（配合 transaction 使用）。"""
    rows = conn.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in rows]
