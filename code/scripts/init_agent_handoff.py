# -*- coding: utf-8 -*-
"""运行态建表：v2_agent_handoff（前端→Hermes 交接队列）。"""
from __future__ import annotations

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

DDL = """
CREATE TABLE IF NOT EXISTS v2_agent_handoff (
  id BIGSERIAL PRIMARY KEY,
  question TEXT NOT NULL,
  context JSONB NOT NULL DEFAULT '{}'::jsonb,
  page VARCHAR(64) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_by VARCHAR(64) NOT NULL DEFAULT '',
  answer TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at TIMESTAMPTZ,
  answered_at TIMESTAMPTZ
)
"""


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.begin() as c:
        c.execute(sa.text(DDL))
        c.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_v2_agent_handoff_status ON v2_agent_handoff (status, id)"))
        print("v2_agent_handoff ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
