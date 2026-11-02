"""从 V1 导入概念板块数据。"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

V1_URL = os.getenv("V1_DB_URL")
V2_URL = os.getenv("V2_DB_URL") or __import__("zhice_os_v2.config.settings", fromlist=["get_settings"]).get_settings().db_main_url


def main() -> None:
    v1 = create_engine(V1_URL)
    v2 = create_engine(V2_URL)
    now = datetime.now(timezone.utc)

    with v1.connect() as conn:
        concepts = conn.execute(
            text("SELECT id, concept_code, concept_name FROM md_concept_master WHERE status='active'")
        ).mappings().all()
        memberships = conn.execute(
            text("SELECT concept_id, ts_code FROM rel_concept_membership WHERE is_active=true")
        ).mappings().all()

    concept_map = {}
    with v2.begin() as conn:
        for row in concepts:
            res = conn.execute(
                text("""
                    INSERT INTO v2_concept (concept_code, concept_name, source, created_at)
                    VALUES (:code, :name, 'v1_import', :now)
                    ON CONFLICT (concept_code) DO NOTHING
                    RETURNING id
                """),
                {"code": row["concept_code"], "name": row["concept_name"], "now": now},
            )
            cid = res.scalar()
            if cid is None:
                cid = conn.execute(text("SELECT id FROM v2_concept WHERE concept_code=:code"), {"code": row["concept_code"]}).scalar()
            concept_map[row["id"]] = cid

    inserted = 0
    with v2.begin() as conn:
        for m in memberships:
            cid = concept_map.get(m["concept_id"])
            if cid is None:
                continue
            res = conn.execute(
                text("""
                    INSERT INTO v2_concept_membership (concept_id, symbol, created_at)
                    VALUES (:cid, :symbol, :now)
                    ON CONFLICT DO NOTHING
                """),
                {"cid": cid, "symbol": m["ts_code"], "now": now},
            )
            inserted += res.rowcount or 0

    print(f"concepts={len(concepts)}, memberships inserted={inserted}")


if __name__ == "__main__":
    main()
