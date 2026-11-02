# -*- coding: utf-8 -*-
"""初始化 v2_strategy_ownership 并回填 12 个策略 owner。

本脚本先建运行态表（CREATE TABLE IF NOT EXISTS），不执行 alembic 0046；
后续可把同一 DDL 收敛为 0047 migration。
"""
from __future__ import annotations

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

ROWS = [
    # strategy_id, name, market, research, validation, data, ops, product, reviewer, lifecycle, next_review, note
    ("v1-cn-strat-2", "[V1] 均值回归v1", "CN", "STRAT1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "平台 CN 均值回归；研究 owner STRAT1"),
    ("v1-cn-strat-4", "[V1] 缠论v1", "CN", "ST1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "shadow", "2026-11-08", "缠论专项；研究 owner ST1（缠论首席）"),
    ("v1-cn-strat-4-v7", "[V7] 缠论增强版", "CN", "ST1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "shadow", "2026-11-08", "缠论增强；研究 owner ST1"),
    ("v1-hk-strat-7", "[V1] 港股趋势动量v1", "HK", "STRAT1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "港股趋势；研究 owner STRAT1"),
    ("v1-hk-strat-8", "[V1] 港股vibeRSI反转", "HK", "STRAT1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "港股 RSI；研究 owner STRAT1"),
    ("v1-us-strat-6", "[V1] 美股趋势动量v1", "US", "STRAT2", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "美股趋势；研究 owner STRAT2"),
    ("v1-us-strat-7", "[V1] 美股vibe均线交叉", "US", "STRAT2", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "美股均线；研究 owner STRAT2"),
    ("v1-crypto-strat-9", "[V1] 虚拟币趋势动量v1", "CRYPTO", "STRAT2", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "加密趋势；研究 owner STRAT2"),
    ("v1-crypto-strat-11", "[V1] 虚拟币vibe均值回归5分钟", "CRYPTO", "STRAT2", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "active", "2026-11-08", "加密 RSI；研究 owner STRAT2"),
    ("qpzm", "青萍之末", "CN", "STRAT1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "live", "2026-11-08", "sidecar；研究 owner STRAT1；ST1 领域支持"),
    ("chanlun", "缠论三类买卖点", "CN", "ST1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "shadow", "2026-11-08", "sidecar；研究 owner ST1"),
    ("dk", "量子双刀", "CN", "STRAT1", "BACK1", "DATA1", "AI1", "PROD1", "ACAD1", "research", "2026-11-08", "研究态：不产每日 picks，不计产数"),
]

DDL = """
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
"""

UPSERT = """
INSERT INTO v2_strategy_ownership
  (strategy_id, display_name, market, research_owner, validation_owner, data_owner,
   ops_owner, product_owner, reviewer, lifecycle_status, next_review, note)
VALUES
  (:sid, :name, :market, :research, :validation, :data, :ops, :product, :reviewer,
   :lifecycle, :next_review, :note)
ON CONFLICT (strategy_id) DO UPDATE SET
  display_name=EXCLUDED.display_name, market=EXCLUDED.market,
  research_owner=EXCLUDED.research_owner, validation_owner=EXCLUDED.validation_owner,
  data_owner=EXCLUDED.data_owner, ops_owner=EXCLUDED.ops_owner,
  product_owner=EXCLUDED.product_owner, reviewer=EXCLUDED.reviewer,
  lifecycle_status=EXCLUDED.lifecycle_status, next_review=EXCLUDED.next_review,
  note=EXCLUDED.note, updated_at=now()
"""


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.begin() as c:
        c.execute(sa.text(DDL))
        for sid, name, market, research, validation, data, ops, product, reviewer, lifecycle, next_review, note in ROWS:
            c.execute(sa.text(UPSERT), {
                "sid": sid, "name": name, "market": market,
                "research": research, "validation": validation, "data": data,
                "ops": ops, "product": product, "reviewer": reviewer,
                "lifecycle": lifecycle, "next_review": next_review, "note": note,
            })
            # 同步到现有策略表，避免 UI 旧字段仍为空
            c.execute(sa.text("UPDATE v2_strategy SET owner_user_id=:o WHERE strategy_id=:s"),
                      {"o": research, "s": sid})
            c.execute(sa.text("UPDATE v2_strategy_sidecar SET owner=:o WHERE strategy_id=:s"),
                      {"o": research, "s": sid})
        n = c.execute(sa.text("SELECT count(*) FROM v2_strategy_ownership")).scalar()
        print("ownership rows", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
