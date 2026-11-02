"""评分 v0 分项落库表 v2_score_component（0046）。

背景
----
`scripts/score_candidate_pool.py` 的 v0 评分（40/25/15/10/10）目前只在内存中计算，
生产表没有保存任何分项（pct_rank / resonance / data_quality / reliability /
risk_penalty / total / 权重贡献），导致：无法回溯每日排序原因、无法做后续权重校准、
无法按分项做归因。设计稿见 C:\\Temp\\score_components_report.json（task-14 / P3-3）。

本迁移（upgrade，additive only）
--------------------------------
1. 新增表 v2_score_component：每个 (pool_date, market, code, score_version,
   component_quality) 一行，保存全部分项、权重贡献 JSONB、输入快照 JSONB；
2. 唯一约束 uq_v2_score_component 作为写入层幂等 upsert 键；
3. 排名索引 ix_v2_score_component_rank，供每日 TopN / 分项查询（普通 btree 支持
   ORDER BY total DESC 的 backward scan，故不额外声明 DESC 排序）。
不改任何现有表/列；不触碰 v2_selection_pool / ACAD1 池 / 研究卡。

本迁移不执行
------------
本任务（P3-4）只提交迁移文件并用 `alembic upgrade 0045:0046 --sql` 生成/验证 SQL，
**不在生产库执行 upgrade**，也不在本次任务创建/写入数据。执行时机由评审与试运行流程决定。

写入方
------
`scripts/persist_score_components.py`（默认 dry-run，`--apply` 才写入；
本次任务不执行 apply）。

回滚（downgrade）
-----------------
只 drop 索引与 v2_score_component 表，不影响任何现有表；已写入数据随之丢失（派生数据可重算）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_score_component",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("code_raw", sa.String(32), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("component_quality", sa.String(16), nullable=False),
        sa.Column("raw_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_rank", sa.Numeric(10, 6), nullable=False),
        sa.Column("resonance_sources", sa.Integer(), nullable=False),
        sa.Column("resonance", sa.Numeric(10, 6), nullable=False),
        sa.Column("data_quality", sa.Numeric(10, 6), nullable=False),
        sa.Column("reliability", sa.Numeric(10, 6), nullable=False),
        sa.Column("risk_penalty", sa.Numeric(10, 6), nullable=False),
        sa.Column("total", sa.Numeric(12, 6), nullable=False),
        sa.Column("components", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("inputs", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("pool_date", "market", "code", "score_version",
                            "component_quality", name="uq_v2_score_component"),
    )
    op.create_index(
        "ix_v2_score_component_rank",
        "v2_score_component",
        ["score_version", "pool_date", "market", "total"],
    )


def downgrade() -> None:
    op.drop_index("ix_v2_score_component_rank", table_name="v2_score_component")
    op.drop_table("v2_score_component")
