# -*- coding: utf-8 -*-
"""数据仓质量日报（DATA-06 / DATA3 陈群 07:00 巡检证据）。

聚合：catalog_snapshot / metadata_consistency / freshness_report / manifest_check / quality_test
+ warehouse_registry 资质分布，产出 research/reports/quality_daily_<YYYYMMDD>.json。

只读；不写数据仓。失败不抛异常，落 fail-visible 报告（供 agent_daily 证据判定）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
DW = REPO / "tools" / "datawarehouse"
OUT_DIR = REPO / "research" / "reports"

CHECKS = [
    ("catalog_snapshot", ["catalog_snapshot.py", "--json"]),
    ("metadata_consistency", ["metadata_consistency.py"]),
    ("freshness_report", ["freshness_report.py", "--json"]),
    ("manifest_check", ["manifest_check.py", "--json"]),
    ("quality_test", ["quality_test.py", "--json"]),
]


def _run(args: list[str]) -> dict:
    p = subprocess.run([PY, "-X", "utf8"] + [str(DW / args[0])] + args[1:],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    out = {"rc": p.returncode,
           "status": "ok" if p.returncode == 0 else ("warn" if p.returncode == 1 else "error")}
    text = (p.stdout or "").strip()
    try:
        data = json.loads(text)
        out["ok"] = out["status"] == "ok"
        out["issues"] = data.get("issues") or []
        if isinstance(out["issues"], list):
            out["issue_count"] = len(out["issues"])
        if "result" in data:
            out["result"] = data["result"]
    except Exception:  # noqa: BLE001
        out["ok"] = p.returncode == 0
        out["stdout_tail"] = text[-400:]
        out["stderr_tail"] = (p.stderr or "")[-300:]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args(argv)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "date": args.date, "checks": {}, "registry": {}}
    for name, cmd in CHECKS:
        rep["checks"][name] = _run(cmd)
    reg_p = REPO / "quant_backtest" / "data" / "warehouse_registry.json"
    if reg_p.exists():
        try:
            reg = json.loads(reg_p.read_text(encoding="utf-8"))
            tabs = reg.get("physical_tables") or []
            qual = {}
            for t in tabs:
                if isinstance(t, dict):
                    q = t.get("qualification_status") or "unknown"
                    qual[q] = qual.get(q, 0) + 1
            rep["registry"] = {"physical_count": reg.get("physical_count"), "display_count": reg.get("display_count"),
                               "qualification": qual, "catalog_sha256_match": reg.get("catalog_sha256_match", None)}
        except Exception as e:  # noqa: BLE001
            rep["registry"] = {"error": str(e)[:120]}
    codes = [c.get("status") for c in rep["checks"].values()]
    rep["summary"] = {"checks_total": len(codes),
                      "checks_ok": codes.count("ok"), "checks_warn": codes.count("warn"),
                      "checks_error": codes.count("error"),
                      "hard_ok": codes.count("error") == 0}
    out = Path(args.out) if args.out else OUT_DIR / f"quality_daily_{args.date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "summary": rep["summary"], "registry": rep["registry"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
