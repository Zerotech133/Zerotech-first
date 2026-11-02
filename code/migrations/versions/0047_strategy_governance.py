"""策略治理：v2_strategy_ownership + v2_strategy_change（0047）。

注意：本迁移尚未执行；当前生产使用 scripts/seed_strategy_ownership.py 的
运行态 CREATE TABLE IF NOT EXISTS。待 0046 评审/执行后，再在线 upgrade。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS v2_strategy_ownership (
      strategy_id VARCHAR(64) PRIMARY KEY,
      display_name VARCHAR(128) NOT NULL,
      market VARCHAR(16) NOT NULL,
      research_owner VARCHAR(32) NOT NULL,
      validation_owner VARCHAR(32) NOT NULL,
      data_owner VARCHAR(32) NOT NULL,
      ops_owner VARCHAR(32) NOT NULL,
      product_owner VARCHAR(32) NOT NULL,
      reviewer VARCHAR(32) NOT NULL,
      lifecycle_status VARCHAR(16) NOT NULL,
      next_review DATE,
      note TEXT NOT NULL DEFAULT '',
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS v2_strategy_change (
      id BIGSERIAL PRIMARY KEY,
      strategy_id VARCHAR(64) NOT NULL,
      from_version VARCHAR(64) NOT NULL,
      to_version VARCHAR(64) NOT NULL,
      stage VARCHAR(24) NOT NULL,
      requested_by VARCHAR(32) NOT NULL,
      backtest_by VARCHAR(32),
      acad1_by VARCHAR(32),
      published_by VARCHAR(32),
      summary TEXT NOT NULL DEFAULT '',
      evidence TEXT NOT NULL DEFAULT '',
      note TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS v2_strategy_change")
    op.execute("DROP TABLE IF EXISTS v2_strategy_ownership")
