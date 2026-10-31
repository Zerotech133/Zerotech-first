# -*- coding: utf-8 -*-
"""知识全链路检查器：按标准 yaml 校验「入库产物」是否达标，并输出下一步 SOP 清单。

用法：
  python tools/kb_corpus/pipeline_check.py --ingest-dir C:/项目/PDF/_ingest
  python tools/kb_corpus/pipeline_check.py --standard docs/standards/2026-10-31_知识全链路标准_v1.yaml --check

退出码：0=全部达标 / 1=有未达标项（附 next_steps）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STANDARD = REPO / "docs" / "standards" / "2026-10-31_知识全链路标准_v1.yaml"


def load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        # 极简 fallback：仅提取关键阈值（不依赖 PyYAML）
        text = path.read_text(encoding="utf-8")
        return {"_raw": text}


def check_ingest(ingest_dir: Path) -> dict:
    report = {"checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "ingest_dir": str(ingest_dir), "books": {}, "ok": True, "next_steps": []}
    g = {"page_coverage_min": 0.95, "ocr_chars_per_page_min": 100,
         "garbled_ratio_max": 0.02, "chapters_min": 5,
         "units_min": 10, "citation_completeness": 1.0}
    agg = ingest_dir / "verify.json"
    candidates = [agg] if agg.exists() else sorted(ingest_dir.glob("*/verify.json"))
    for vf in candidates:
        v = json.loads(vf.read_text(encoding="utf-8"))
        books = v.get("books") or {}
        if not books and "books" in v and isinstance(v["books"], list):
            books = {str(b.get("book", "?")): b for b in v["books"]}
        for book, b in books.items():
            checks = {
                "page_coverage": b.get("page_coverage", 0) >= g["page_coverage_min"],
                "ocr_quality": b.get("ocr_chars_per_page", 0) >= g["ocr_chars_per_page_min"] and b.get("garbled_ratio", 1) < g["garbled_ratio_max"],
                "structure": b.get("chapters", 0) >= g["chapters_min"],
                "units": b.get("units", 0) >= g["units_min"] and b.get("unit_citation_rate", 0) >= g["citation_completeness"],
            }
            report["books"][book] = {"checks": checks, "metrics": b}
            if not all(checks.values()):
                report["ok"] = False
    if not report["books"]:
        report["ok"] = False
    if not report["ok"]:
        report["next_steps"] = ["修到 verify 全绿后再入库", "参见 docs/standards/2026-10-31_PDF知识入库标准_v1.md"]
    else:
        report["next_steps"] = [
            "golden 评测（Recall@5≥0.6、引用正确率≥0.9）",
            "知识单元→特征映射（写入报告）",
            "假设登记 + 四关回测（PIT/OOS/成本/容量 + 重叠/前视修正）",
            "例会复核清单 6 条逐项打勾",
            "备份 + 版本登记 + 驾驶舱展示",
        ]
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-dir", default=r"C:/项目/PDF/_ingest")
    ap.add_argument("--standard", default=str(STANDARD))
    ap.add_argument("--check", action="store_true", help="仅校验标准文件可读")
    args = ap.parse_args(argv)
    std = load_yaml(Path(args.standard))
    if args.check:
        ok = bool(std) and (std.get("version") == 1 or "_raw" in std)
        print(json.dumps({"standard": args.standard, "ok": ok,
                          "layers": list(std.keys())[:8]}, ensure_ascii=False))
        return 0 if ok else 1
    report = check_ingest(Path(args.ingest_dir))
    out = Path(args.ingest_dir) / "pipeline_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "books": {k: v["checks"] for k, v in report["books"].items()},
                      "next_steps": len(report["next_steps"]), "report": str(out)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
