"""数据库初始化脚本：执行 store/schema.sql 建全部表 + 打印确认。

用法::

    python scripts/init_db.py            # 建表（幂等，IF NOT EXISTS）
    python scripts/init_db.py --drop     # 危险：先 DROP 再建（仅开发环境清库）

依赖 psycopg3（psycopg[binary]）直连执行 DDL，不依赖 SQLAlchemy。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 让脚本在任意 cwd 下都能以包内绝对导入运行（python scripts/init_db.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from common.logging_setup import setup_logging  # noqa: E402
from common.paths import SCHEMA_SQL_PATH  # noqa: E402
from config.settings import get_settings  # noqa: E402

#: 库中关注的表清单（打印确认用，与 schema.sql 对齐）
_TABLES = [
    "osint_link_task",
    "osint_import_task",
    "osint_retry_idempotency",
    "osint_credential",
    "osint_target",
    "osint_creator_run",
    "osint_creator_summary",
    "osint_comment",
    "osint_video",
    "osint_knowledge_unit",
    "osint_blogger_history",
    "osint_strategy_evidence",
    "osint_processing_run",
    "osint_knowledge_resource",
    "osint_media_asset",
    "osint_index_task",
    "osint_story",
    "osint_story_member",
    "osint_entity_alias",
]

_DROP_SQL = "DROP TABLE IF EXISTS {name} CASCADE"

# Keep the creator scheduler lease migration explicit.  This is intentionally
# idempotent and runs after schema.sql so older QA/development databases cannot
# silently drift from the worker SQL contract.
_CREATOR_LEASE_MIGRATION = (
    "ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS claim_token TEXT",
    "ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_target_scan_lease ON osint_target(lease_until, claim_token)",
)

_STATEMENT_SPLIT_RE = re.compile(r";\s*(?:\n|$)")


def _split_statements(sql_text: str) -> list[str]:
    """把 schema.sql 按分号切分为可执行语句（去除注释与空段）。"""
    statements: list[str] = []
    for raw in _STATEMENT_SPLIT_RE.split(sql_text):
        stmt = re.sub(r"--[^\n]*", "", raw).strip()
        if stmt:
            statements.append(stmt)
    return statements


def _drop_tables(cur: psycopg.Cursor) -> None:
    """开发环境清库（--drop 显式触发）。"""
    for name in _TABLES:
        cur.execute(_DROP_SQL.format(name=name))
        print(f"[drop] {name}")


def init_db(drop: bool = False) -> int:
    """建库主流程；返回退出码（0 成功 / 1 失败）。"""
    setup_logging()
    settings = get_settings()
    if not SCHEMA_SQL_PATH.exists():
        print(f"[error] 找不到 DDL 文件：{SCHEMA_SQL_PATH}")
        return 1

    sql_text = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    statements = _split_statements(sql_text)
    try:
        with psycopg.connect(settings.pg_dsn_plain) as conn:
            with conn.cursor() as cur:
                if drop:
                    _drop_tables(cur)
                for stmt in statements:
                    cur.execute(stmt)
                # Explicitly re-apply the lease migration for installations
                # whose schema was initialized before creator leases existed.
                for stmt in _CREATOR_LEASE_MIGRATION:
                    cur.execute(stmt)
                conn.commit()
                # 打印确认：逐表存在性 + 行数
                for table in _TABLES:
                    cur.execute(f"SELECT count(*) FROM {table}")
                    (cnt,) = cur.fetchone()
                    print(f"[ok] {table:<20} 行数={cnt}")
    except psycopg.OperationalError as exc:
        print(
            f"[error] PG 连接失败（{settings.pg_host}:{settings.pg_port}/{settings.pg_db}）：{exc}\n"
            "排障：1) docker ps 确认容器；2) 检查 .env 的 PG_* 配置；3) pip install 'psycopg[binary]'"
        )
        return 1
    print(
        f"[done] schema 初始化完成：{len(statements)} 条语句，{len(_TABLES)} 张表就绪"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="osint_video 建库脚本（幂等）")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="危险：先 DROP 全部 osint_* 表再重建（仅开发环境）",
    )
    args = parser.parse_args()
    if args.drop:
        confirm = input("确认删除全部 osint_* 表？输入 yes 执行：").strip().lower()
        if confirm != "yes":
            print("已取消")
            return
    raise SystemExit(init_db(drop=args.drop))


if __name__ == "__main__":
    main()
