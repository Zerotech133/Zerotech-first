# -*- coding: utf-8 -*-
"""SEPA 每日扫描包装：跑严格+宽口径扫描 → 合并静态 JSON/台账/摘要。

定时：每日 17:40（行情同步 17:30 之后）；早报 08:20 读取本 JSON 展示。
产出：
  portal/static/sepa_scan_daily.json
  research/ledger/sepa_scan_daily.jsonl
用法：python tools/research/sepa_scan_daily.py [--date 20261030]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
import sepa_scan  # noqa: E402

STATIC = REPO / "portal" / "static"
LEDGER = REPO / "research" / "ledger"

sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))
from safe_json import backup_then_atomic_write  # noqa: E402


def export_payload(payload: dict, static_dir: Path | str | None = None) -> dict:
    """门户静态 JSON：写前备份 + 原子写 + 写后校验（safe_json）；返回报告。"""
    target = Path(static_dir or STATIC) / "sepa_scan_daily.json"
    return backup_then_atomic_write(target, payload)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args(argv)
    rc1 = sepa_scan.main(["--fund", "growth"] + (["--date", args.date] if args.date else []))
    rc2 = sepa_scan.main(["--fund", "none"] + (["--date", args.date] if args.date else []))
    if rc1 != 0 or rc2 != 0:
        print("scan failed", rc1, rc2, flush=True)
        return 1
    # sepa_scan 输出文件名：sepa_scan_<date>_growth.json / _none.json；静态分别写了 sepa_scan.json/sepa_scan_nofund.json
    strict = json.loads((STATIC / "sepa_scan.json").read_text(encoding="utf-8"))
    wide = json.loads((STATIC / "sepa_scan_nofund.json").read_text(encoding="utf-8"))
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scan_date": strict["scan_date"],
        "strict": {"universe": strict["universe"], "tt_pass": strict["tt_pass"],
                    "fund_pass": strict["fund_pass"], "vcp": strict["vcp"],
                    "funnel": strict["funnel"], "breakout": strict.get("breakout", [])[:20],
                    "near": strict.get("near", [])[:20], "setup": strict.get("setup", [])[:20],
                    "only_tt_fund": strict.get("only_tt_fund", [])[:20]},
        "wide": {"tt_pass": wide["tt_pass"], "vcp": wide["vcp"], "vcp_lite": wide.get("vcp_lite", [])[:20],
                  "tt_top": wide.get("tt_top", [])[:20]},
        "note": "严格=TT+基本面+VCP+突破；宽口径=仅TT技术面；研究扫描，不构成投资建议",
    }
    STATIC.mkdir(parents=True, exist_ok=True)
    export_payload(payload)          # 备份+原子写+写后校验（safe_json）
    LEDGER.mkdir(parents=True, exist_ok=True)
    with (LEDGER / "sepa_scan_daily.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": payload["scan_date"], "generated_at": payload["generated_at"],
                            "strict_funnel": payload["strict"]["funnel"],
                            "breakout": [x["code"] for x in payload["strict"]["breakout"]],
                            "near": [x["code"] for x in payload["strict"]["near"]],
                            "tt_fund": [x["code"] for x in payload["strict"]["only_tt_fund"]],
                            "wide_vcp_lite": [x["code"] for x in payload["wide"]["vcp_lite"]],
                            "wide_tt": [x["code"] for x in payload["wide"]["tt_top"]]}, ensure_ascii=False) + "\n")
    print(json.dumps({"date": payload["scan_date"], "strict": payload["strict"]["funnel"],
                      "tt_fund": [x["code"] for x in payload["strict"]["only_tt_fund"]],
                      "wide_vcp_lite": len(payload["wide"]["vcp_lite"]),
                      "out": str(STATIC / "sepa_scan_daily.json")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
