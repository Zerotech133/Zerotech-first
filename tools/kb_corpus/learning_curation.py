# -*- coding: utf-8 -*-
"""学习流水线后置治理：去重 + 指标说明书排除（供每日 pipeline 任务调用）。

背景 BUG：learning_pipeline 每日 20:00 重建 units 会覆盖人工治理结果
（800→1018、1116→1293、286→288），且指标说明书 871 垃圾单元回流。
本工具在 pipeline 之后执行：
  1) knowledge_dedupe --apply（精确+近重复去重，自动备份）
  2) 生成 <pid>_units_effective.jsonl（剔除 指标说明书 整文件）
  3) 报告 research/reports/learning_curation_<date>.json/md

CLI:
  python tools/kb_corpus/learning_curation.py            # dry-run 预览
  python tools/kb_corpus/learning_curation.py --apply    # 执行
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
R = REPO / "research/reports"
PY = sys.executable
EXCLUDE_PAT = "指标说明书"


def projects() -> list[str]:
    reg = json.loads((REPO / "agent_platform/registry/learning_projects.json").read_text(encoding="utf-8"))
    return [p["project_id"] for p in reg.get("projects") or [] if p.get("status") != "archived"]


def lines(p: Path) -> int:
    return sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="执行去重与 effective 落盘")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()

    rows = []
    for pid in projects():
        up = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
        if not up.exists():
            rows.append({"project_id": pid, "units_before": 0, "units_after": 0,
                         "effective": 0, "excluded": 0, "dedupe_rc": None, "skipped": "no units"})
            continue
        before = lines(up)
        rc = None
        if args.apply and before:
            cp = subprocess.run([PY, "-X", "utf8", str(REPO / "tools/kb_corpus/knowledge_dedupe.py"),
                                 "--input", str(up), "--apply"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            rc = cp.returncode
        after = lines(up)
        # effective：剔除指标说明书
        eff_path = up.with_name(f"{pid}_units_effective.jsonl")
        kept = excluded = 0
        if args.apply and after:
            with eff_path.open("w", encoding="utf-8") as fo:
                for line in up.open(encoding="utf-8"):
                    if not line.strip():
                        continue
                    u = json.loads(line)
                    if EXCLUDE_PAT in str((u.get("evidence") or {}).get("file") or ""):
                        excluded += 1
                        continue
                    fo.write(line)
                    kept += 1
        else:
            for line in up.open(encoding="utf-8"):
                if not line.strip():
                    continue
                u = json.loads(line)
                if EXCLUDE_PAT in str((u.get("evidence") or {}).get("file") or ""):
                    excluded += 1
                else:
                    kept += 1
        rows.append({"project_id": pid, "units_before": before, "units_after": after,
                     "effective": kept, "excluded": excluded, "dedupe_rc": rc})
        print(f"{pid}: {before} -> {after} | effective {kept}（排除 {excluded}）rc={rc}", flush=True)

    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "mode": "apply" if args.apply else "dry-run", "exclude_pattern": EXCLUDE_PAT,
              "projects": rows}
    (R / f"learning_curation_{args.date}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 学习单元后置治理（{args.date}）", "",
         f"> mode={'apply' if args.apply else 'dry-run'}｜去重 + 排除「{EXCLUDE_PAT}」", "",
         "| 项目 | 去重前 | 去重后 | 有效单元 | 排除 | dedupe rc |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['project_id']} | {r['units_before']} | {r['units_after']} | {r['effective']} | {r['excluded']} | {r['dedupe_rc']} |")
    (R / f"learning_curation_{args.date}.md").write_text("\n".join(L), encoding="utf-8")
    return 0 if all((r.get("dedupe_rc") in (0, None)) for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
