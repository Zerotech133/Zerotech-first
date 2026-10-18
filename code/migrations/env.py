"""Alembic 环境。"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow DB_MAIN_URL env var to override the hardcoded sqlalchemy.url in alembic.ini.
# This enables CI/CD and containerised deployments to inject the correct DSN without
# modifying alembic.ini.
_db_url_override = os.environ.get("DB_MAIN_URL")
if _db_url_override:
    config.set_main_option("sqlalchemy.url", _db_url_override)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = os.environ.get("DB_MAIN_URL") or config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
