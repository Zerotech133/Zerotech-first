# -*- coding: utf-8 -*-
"""语料库血缘写入 quant-pg lineage（job_run + lineage_edge），幂等。

用法：python lineage_write.py --write
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:/项目/老田的AI助手")
DSN = "host=127.0.0.1 port=5442 user=quant_user dbname=quant_system"
JOB = "kb_corpus_pipeline"

EDGES = [
    ("corpus:kb_normalized", "corpus:kb_raw_zip", "tools/kb_corpus/export.py"),
    ("corpus:kb_cleaned", "corpus:kb_normalized", "tools/kb_corpus/p2_clean.py"),
    ("corpus:kb_index", "corpus:kb_cleaned", "tools/kb_corpus/build_full_index.py"),
    ("corpus:kb_rag", "corpus:kb_index", "tools/kb_corpus/merge_full_index.py+build_rag_index.py"),
]


def password() -> str:
    p = os.environ.get("LINEAGE_PASSWORD")
    if p:
        return p
    for line in (REPO / "quant_backtest" / ".env.local").read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("LINEAGE_PASSWORD="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("LINEAGE_PASSWORD not found")


def write() -> int:
    import psycopg2
    con = psycopg2.connect(DSN + " password=" + password())
    con.autocommit = False
    cur = con.cursor()
    # 幂等：清掉旧 job/edges
    cur.execute("SELECT id FROM lineage.job_run WHERE job=%s", (JOB,))
    old = [r[0] for r in cur.fetchall()]
    if old:
        cur.execute("DELETE FROM lineage.lineage_edge WHERE job_run_id = ANY(%s)", (old,))
        cur.execute("DELETE FROM lineage.job_run WHERE id = ANY(%s)", (old,))
    now = datetime.now(timezone.utc)
    cur.execute(
        "INSERT INTO lineage.job_run (job,step,started_at,ended_at,status,rows_written,detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING id",
        (JOB, "build", now, now, "ok", 62401,
         '{"host":"127.0.0.1","corpus":"youtube_quant_corpus","docs":62401,"videos":45827,"comments":1321892}'))
    jid = cur.fetchone()[0]
    for out_t, in_t, transform in EDGES:
        cur.execute(
            "INSERT INTO lineage.lineage_edge (job_run_id, output_table, input_table, transform, created_at) "
            "VALUES (%s,%s,%s,%s,%s)", (jid, out_t, in_t, transform, now))
    con.commit()
    cur.execute("SELECT job_run_id,output_table,input_table,transform FROM lineage.lineage_edge "
                "WHERE job_run_id=%s ORDER BY id", (jid,))
    rows = cur.fetchall()
    con.close()
    print(f"job_run_id={jid}; edges={len(rows)}", flush=True)
    for r in rows:
        print("  ", r, flush=True)
    return 0


if __name__ == "__main__":
    if "--write" in sys.argv:
        sys.exit(write())
    print("use --write", flush=True)
