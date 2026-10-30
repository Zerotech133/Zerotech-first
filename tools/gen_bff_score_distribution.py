# -*- coding: utf-8 -*-
"""生成菜鸟评分快照历史分布（只读）。

用途：8091 尚未重启到带 BFF 分布端点的版本时，/os-entry 下钻用
`portal/data/bff_score_distribution.json` 同源静态快照兜底。

只读：START TRANSACTION READ ONLY；只发 SELECT；不打印数据库密码。

用法：
  python tools/gen_bff_score_distribution.py
  python tools/gen_bff_score_distribution.py --out portal/data/bff_score_distribution.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "portal" / "data" / "bff_score_distribution.json"
PW_FILE = Path(
    os.environ.get(
        "CAINIAO_DB_PASSWORD_FILE",
        r"C:\项目\_vault\secrets\cainiao\cainiao_mysql_root.pw",
    )
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    import pymysql

    password = os.environ.get("CAINIAO_DB_PASSWORD", "").strip() or PW_FILE.read_text(
        encoding="utf-8"
    ).strip()
    conn = pymysql.connect(
        host=os.environ.get("CAINIAO_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CAINIAO_DB_PORT", "3307")),
        user=os.environ.get("CAINIAO_DB_USER", "root"),
        password=password,
        database=os.environ.get("CAINIAO_DB_NAME", "thunder_model"),
        connect_timeout=4,
        read_timeout=10,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute(
                "SELECT DATE(record_time) AS d, COUNT(*) AS n, ROUND(AVG(score), 2) AS avg_score "
                "FROM stock_realtime_monitor_record GROUP BY DATE(record_time) ORDER BY d"
            )
            daily = [
                {"date": str(r[0]), "count": int(r[1]), "avg_score": float(r[2]) if r[2] is not None else None}
                for r in cur.fetchall()
            ]
            cur.execute(
                "SELECT CASE "
                "WHEN score < 60 THEN '0-59' WHEN score < 70 THEN '60-69' "
                "WHEN score < 80 THEN '70-79' WHEN score < 90 THEN '80-89' "
                "ELSE '90-100' END AS bucket, COUNT(*) AS n "
                "FROM stock_realtime_monitor_record GROUP BY bucket ORDER BY bucket"
            )
            buckets = [{"range": str(r[0]), "count": int(r[1])} for r in cur.fetchall()]
            cur.execute("SELECT MAX(record_time) FROM stock_realtime_monitor_record")
            latest = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM stock_recommend")
            recommend_total = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT MAX(create_time) FROM stock_recommend")
            recommend_latest = cur.fetchone()[0]
            conn.rollback()
    finally:
        conn.close()

    payload = {
        "ok": True,
        "mode": "read_only",
        "source": "cainiao_mysql:thunder_model",
        "kind": "score_distribution",
        "generated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "latest_business_at": latest.isoformat(sep=" ") if hasattr(latest, "isoformat") else None,
        "stale": True,
        "note": "历史快照；非实时。仅统计评分快照，不含交易指令。",
        "daily": daily,
        "buckets": buckets,
        "recommendations": {
            "total": recommend_total,
            "latest_at": recommend_latest.isoformat(sep=" ") if hasattr(recommend_latest, "isoformat") else None,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("written", out, "days", len(daily), "buckets", len(buckets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
