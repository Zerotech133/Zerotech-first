# -*- coding: utf-8 -*-
"""治理一致性只读检查：manifest / ownership / 角色 / 迁移 / 变更表。

用法：
  python scripts/governance_check.py --warn-only
  python scripts/governance_check.py --strict

不写 DB、不改文件；输出 C:\\Temp\\governance_check_report.json。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import sqlalchemy as sa
import yaml

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "src"))

from zhice_os_v2.config.settings import get_settings  # noqa: E402

MANIFEST = CODE / "config" / "strategy_manifests.yaml"
OUT = Path(r"C:\Temp\governance_check_report.json")
EXPECTED_OWNERS = {"STRAT1": 5, "ST1": 3, "STRAT2": 4}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true")
    p.add_argument("--warn-only", action="store_true")
    args = p.parse_args()

    checks = []
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    items = doc.get("strategies") or []
    ids = [x.get("id") for x in items]

    def add(name: str, status: str, detail: str):
        checks.append({"id": name, "status": status, "detail": detail})

    # G1 manifest
    if len(items) == 12 and len(ids) == len(set(ids)):
        add("G1-manifest", "PASS", "12 条，ID 唯一")
    else:
        add("G1-manifest", "FAIL", f"count={len(items)} unique={len(set(ids))}")

    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        own = {r["strategy_id"]: dict(r) for r in c.execute(sa.text("SELECT * FROM v2_strategy_ownership")).mappings()}
        plat = {r[0] for r in c.execute(sa.text("SELECT strategy_id FROM v2_strategy")).all()}
        side = {r[0] for r in c.execute(sa.text("SELECT strategy_id FROM v2_strategy_sidecar")).all()}
        known = plat | side
        change = c.execute(sa.text("SELECT count(*) FROM v2_strategy_change")).scalar()
        version = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        owners = {}
        for r in own.values():
            owners[r["research_owner"]] = owners.get(r["research_owner"], 0) + 1

    # G2 ownership / G3 strategy tables
    missing_manifest = sorted(set(own) - set(ids))
    missing_own = sorted(set(ids) - set(own))
    if not missing_manifest and not missing_own and len(own) == 12:
        add("G2-ownership", "PASS", "12 条，双向一致")
    else:
        add("G2-ownership", "FAIL", f"manifest_only={missing_own} own_only={missing_manifest}")
    if set(ids) <= known:
        add("G3-strategy-tables", "PASS", "12 个 ID 均在 v2_strategy+sidecar")
    else:
        add("G3-strategy-tables", "FAIL", f"缺 {sorted(set(ids)-known)}")

    # G4 research owner distribution
    if owners == EXPECTED_OWNERS:
        add("G4-owner-distribution", "PASS", "STRAT1=5 ST1=3 STRAT2=4")
    else:
        add("G4-owner-distribution", "FAIL", str(owners))

    # G5 dk research constraint
    dk = next((x for x in items if x.get("id") == "dk"), None)
    if dk and dk.get("lifecycle") == "research" and dk.get("version_status") == "none":
        add("G5-dk-research", "PASS", "dk research/none")
    else:
        add("G5-dk-research", "FAIL", str(dk))

    # G6 next_review parse
    bad_review = [x.get("id") for x in items if not x.get("next_review")]
    add("G6-next-review", "PASS" if not bad_review else "WARN", f"missing={bad_review}")

    # G7 change table clean
    add("G7-change-clean", "PASS" if int(change or 0) == 0 else "WARN", f"rows={change}")

    # G8 alembic version vs repo head
    head = "0047"
    if str(version) == head:
        add("G8-migration", "PASS", f"db={version} head={head}")
    else:
        add("G8-migration", "WARN", f"db={version} head={head}（运行态表已建）")

    # G9 change table schema hash (stable col names)
    with e.connect() as c:
        cols = [r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='v2_strategy_change' ORDER BY ordinal_position"
        )).all()]
    expected = ["id", "strategy_id", "from_version", "to_version", "stage", "requested_by",
                "backtest_by", "acad1_by", "published_by", "summary", "evidence", "note",
                "created_at", "updated_at"]
    if cols == expected:
        add("G9-change-schema", "PASS", "14 列一致")
    else:
        add("G9-change-schema", "FAIL", f"cols={cols}")

    # G10 role gate static check
    change_src = (CODE / "scripts" / "strategy_change.py").read_text(encoding="utf-8")
    checks_txt = [
        ('BACK1', 'advance(a, "submitted", "backtest_approved"'),
        ('ACAD1', 'advance(x, "backtest_approved", "acad1_approved"'),
        ('AI1', 'advance(x, "acad1_approved", "published"'),
        ('research_owner', 'submit requires research_owner'),
        ('reject', 'reject requires ACAD1 or BACK1'),
    ]
    missing = [name for name, needle in checks_txt if needle not in change_src]
    add("G10-role-gates", "PASS" if not missing else "WARN", f"missing={missing}")

    fails = [x for x in checks if x["status"] == "FAIL"]
    warns = [x for x in checks if x["status"] == "WARN"]
    report = {
        "schema": "zhice.governance_check/v1",
        "read_only": True,
        "db_version": str(version),
        "repo_head": head,
        "owner_distribution": owners,
        "checks": checks,
        "summary": {"pass": len([x for x in checks if x["status"] == "PASS"]),
                    "warn": len(warns), "fail": len(fails)},
        "ok": not fails,
        "strict_ok": not fails and not warns,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for x in checks:
        print(x["status"], x["id"], x["detail"])
    if args.strict:
        return 0 if report["strict_ok"] else 1
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
