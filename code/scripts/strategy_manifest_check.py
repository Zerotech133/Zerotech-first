# -*- coding: utf-8 -*-
"""校验策略 manifest 与 ownership/策略表是否一致。只读。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sqlalchemy as sa
import yaml

from zhice_os_v2.config.settings import get_settings

MANIFEST = Path(__file__).resolve().parents[1] / "config" / "strategy_manifests.yaml"
REQUIRED = ["id", "name", "market", "lifecycle", "research_owner", "validation_owner",
            "data_owner", "ops_owner", "product_owner", "reviewer", "version",
            "version_status", "next_review"]


def main() -> int:
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    items = doc.get("strategies") or []
    errors = []
    ids = [x.get("id") for x in items]
    if len(ids) != len(set(ids)):
        errors.append("manifest strategy_id 重复")
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        own = {r[0]: dict(r._mapping) for r in c.execute(sa.text("SELECT * FROM v2_strategy_ownership")).all()}
        plat = {r[0] for r in c.execute(sa.text("SELECT strategy_id FROM v2_strategy")).all()}
        side = {r[0] for r in c.execute(sa.text("SELECT strategy_id FROM v2_strategy_sidecar")).all()}
        known = plat | side
    for x in items:
        sid = x.get("id")
        for f in REQUIRED:
            if not x.get(f):
                errors.append("%s 缺字段 %s" % (sid, f))
        if sid not in known:
            errors.append("%s 不在 v2_strategy/v2_strategy_sidecar" % sid)
        if sid not in own:
            errors.append("%s 不在 v2_strategy_ownership" % sid)
        if sid == "dk" and x.get("lifecycle") != "research":
            errors.append("dk 必须 research")
        if sid == "dk" and x.get("version_status") == "active":
            errors.append("dk 不能 active 发布")
    missing = sorted(set(known) - set(ids))
    if missing:
        errors.append("有策略无 manifest: " + ",".join(missing))
    out = {"manifest": str(MANIFEST), "count": len(items), "known": len(known),
           "pass": not errors, "errors": errors,
           "lifecycle": {x["id"]: x.get("lifecycle") for x in items}}
    p = Path(r"C:\Temp\strategy_manifest_report.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
