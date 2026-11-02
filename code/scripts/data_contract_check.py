# -*- coding: utf-8 -*-
"""按 ODCS 契约检查智策选股中台表（只读）。"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import sqlalchemy as sa
import yaml

from zhice_os_v2.config.settings import get_settings

CONTRACT = Path(__file__).resolve().parents[1] / "config" / "contracts" / "zhice_selection.odcs.yaml"
MARKETS = {"CN", "HK", "US", "CRYPTO"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--json", default=r"C:\Temp\data_contract_report.json")
    args = ap.parse_args()
    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    results = []
    with e.connect() as c:
        for table in spec.get("schema", []):
            name = table["physicalName"]
            issues = []
            cols = {r[0] for r in c.execute(sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name=:t"), {"t": name}).all()}
            required = [p["name"] for p in table.get("properties", []) if p.get("required")]
            missing = [x for x in required if x not in cols]
            if missing:
                issues.append("缺列: " + ",".join(missing))
            pk = [p["name"] for p in table.get("properties", []) if p.get("primaryKey")]
            if pk and not missing:
                cols_sql = ",".join(pk)
                dup = c.execute(sa.text(
                    "SELECT count(*) FROM (SELECT %s, count(*) n FROM %s GROUP BY %s HAVING count(*)>1) x"
                    % (cols_sql, name, cols_sql))).scalar()
                if dup:
                    issues.append("主键重复 %d 组: %s" % (dup, ",".join(pk)))
                nulls = c.execute(sa.text(
                    "SELECT count(*) FROM %s WHERE %s" % (name, " OR ".join("%s IS NULL" % p for p in pk)))).scalar()
                if nulls:
                    issues.append("主键空值 %d" % nulls)
            # 表级规则
            if name == "v2_selection_report" and not missing:
                bad = c.execute(sa.text("SELECT count(*) FROM %s WHERE market IS NULL OR market NOT IN ('CN','HK','US','CRYPTO')" % name)).scalar()
                if bad:
                    issues.append("market 非法/MULTI %d" % bad)
                latest = c.execute(sa.text("SELECT max(report_date) FROM %s" % name)).scalar()
                if latest is None:
                    issues.append("无日报")
                elif (date.today() - latest).days > 7:
                    issues.append("日报过期 %s 天" % (date.today() - latest).days)
            if name == "v2_selection_pool" and not missing:
                latest = c.execute(sa.text("SELECT max(pool_date) FROM %s" % name)).scalar()
                n = c.execute(sa.text("SELECT count(*) FROM %s WHERE pool_date=:d" % name), {"d": latest}).scalar()
                if not n:
                    issues.append("最新池为空")
            if name == "v2_acad1_pool" and not missing:
                latest = c.execute(sa.text("SELECT max(pool_date) FROM %s" % name)).scalar()
                n = c.execute(sa.text("SELECT count(*) FROM %s WHERE pool_date=:d" % name), {"d": latest}).scalar()
                if n > 30:
                    issues.append("ACAD1 超 30: %d" % n)
                bad = c.execute(sa.text("SELECT count(*) FROM %s WHERE market NOT IN ('CN','HK','US','CRYPTO')" % name)).scalar()
                if bad:
                    issues.append("ACAD1 market 非法 %d" % bad)
            if name == "v2_stock_research" and not missing:
                bad = c.execute(sa.text(
                    "SELECT count(*) FROM %s WHERE status='draft' AND "
                    "(target_price IS NOT NULL OR stop_price IS NOT NULL OR buy_zone_low IS NOT NULL OR buy_zone_high IS NOT NULL)"
                    % name)).scalar()
                if bad:
                    issues.append("draft 卡含目标价/止损/买区 %d" % bad)
            results.append({"table": name, "ok": not issues, "issues": issues,
                            "required": required, "primary_key": pk})
    report = {"contract": str(CONTRACT), "checked_at": date.today().isoformat(),
              "tables": results, "pass": all(r["ok"] for r in results)}
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print("%-22s %s %s" % (r["table"], "PASS" if r["ok"] else "FAIL", "; ".join(r["issues"])))
    print("saved", args.json)
    return 0 if report["pass"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
