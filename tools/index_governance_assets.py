# -*- coding: utf-8 -*-
"""全量计划 + 仓外交付物索引（2026-10-31 治理收口；K-ORG-20261031-PLANREG 修生成物分类）。

输出：
  docs/audit/2026-10-31_仓外交付物索引_v1.json/md
  docs/plans/PLAN_REGISTER_ALL_20261031.json/md
只读扫描，不修改原文件。

2026-10-31 PLANREG：`PLAN_REGISTER*` 是本工具自己的**生成物**，不再被当成"计划状态"
参与 declared/superseded/pending/frozen/unspecified 统计，单列 `generated`；
`--plans-only` 只重生成计划台账（不触碰仓外交付物索引）。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = Path(r"C:\项目\deliverables")
PLANS = ROOT / "docs" / "plans"
OUT_EXT = ROOT / "docs" / "audit"
OUT_PLANS = PLANS

# 生成物：本工具输出 / 计划台账（不参与计划状态统计；单列 generated）
GENERATED_PLAN_PREFIXES = ("PLAN_REGISTER",)
GENERATED_STATE = "generated"


def is_generated_plan(p: Path) -> bool:
    return p.name.upper().startswith(GENERATED_PLAN_PREFIXES)


def title_of(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:160]
    return ""


def plan_rows():
    rows = []
    for p in sorted(PLANS.rglob("*.md")):
        try:
            st = p.stat()
        except OSError:
            continue
        text = ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:5000]
        except OSError:
            pass
        low = text.lower() + "\n" + p.name.lower()
        # 2026-10-31 PLANREG：STATUS 行兼容中文「状态：」（部分新计划用中文标签）
        mstat = re.search(r"(?im)^\s*>\s*(?:STATUS|状态)\s*[:：]\s*([^\n]{0,120})",
                          text[:2000])
        declared = (mstat.group(1).strip().lower() if mstat else "")
        if is_generated_plan(p):
            # 生成物（PLAN_REGISTER_ALL / PLAN_REGISTER 台账）不冒充计划状态
            state = GENERATED_STATE
        elif declared.startswith("superseded") or "被取代" in declared:
            state = "superseded"
        elif declared.startswith("pending") or declared.startswith("待"):
            state = "pending_approval"
        elif declared.startswith("frozen") or "冻结" in declared or declared.startswith("只读"):
            state = "frozen"
        elif declared.startswith("unspecified") or "未标注" in declared:
            state = "unspecified"
        elif declared:
            state = "declared"
        elif "status" in low[:1500]:
            state = "declared"
        elif "superseded" in p.name.lower() or "_deprecated" in p.name.lower():
            state = "superseded"
        elif "待批" in text or "待拍板" in text:
            state = "pending_approval"
        elif "冻结" in text:
            state = "frozen"
        else:
            state = "unspecified"
        m = re.match(r"^(20\d\d-\d\d-\d\d)", p.name)
        rows.append({"path": str(p.relative_to(ROOT)), "date": m.group(1) if m else "",
                     "state": state, "title": title_of(p), "bytes": st.st_size,
                     "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return rows


def plan_payload() -> dict:
    plans = plan_rows()
    pcounts = {}
    for r in plans:
        pcounts[r["state"]] = pcounts.get(r["state"], 0) + 1
    return {"schema": "plan-register-all/v1",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(plans), "counts": pcounts, "plans": plans}


def write_plan_register() -> dict:
    pdata = plan_payload()
    OUT_PLANS.mkdir(parents=True, exist_ok=True)
    pj = OUT_PLANS / "PLAN_REGISTER_ALL_20261031.json"
    pj.write_text(json.dumps(pdata, ensure_ascii=False, indent=1), encoding="utf-8")
    pcounts = pdata["counts"]
    generated = pcounts.get(GENERATED_STATE, 0)
    lines = ["# 计划台账（全量，含子目录）2026-10-31", "",
             "> 只读扫描；原计划不改。生成物（`PLAN_REGISTER*`，本台账自身）单列 "
             "`generated`，不参与计划状态统计。",
             "> 非生成计划 unspecified 需 owner 在 2026-11-07 前补状态。", "",
             "总数：%d（其中生成物 generated %d，不计入计划状态统计）"
             % (pdata["total"], generated), "", "| 状态 | 数量 |", "|---|---|"]
    for k in sorted(pcounts):
        lines.append("| %s | %d |" % (k, pcounts[k]))
    lines += ["", "## 计划清单", "", "| 日期 | 状态 | 标题 | 文件 |", "|---|---|---|---|"]
    for r in pdata["plans"][:300]:
        lines.append("| %s | %s | %s | `%s` |" % (
            r["date"] or "-", r["state"], (r["title"] or "-").replace("|", "/"), r["path"]))
    write_md(OUT_PLANS / "PLAN_REGISTER_ALL_20261031.md", lines)
    return {"json": str(pj), "md": str(OUT_PLANS / "PLAN_REGISTER_ALL_20261031.md"),
            "data": pdata}


def ext_rows():
    rows = []
    if not EXT.is_dir():
        return rows
    for p in sorted(EXT.rglob("*")):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        rows.append({"path": str(p), "rel": str(p.relative_to(EXT)),
                     "ext": p.suffix.lower(), "bytes": st.st_size,
                     "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "title": title_of(p) if p.suffix.lower() == ".md" else ""})
    return rows


def write_external_index() -> dict:
    ext = ext_rows()
    ecounts = {}
    for r in ext:
        ecounts[r["ext"] or "(none)"] = ecounts.get(r["ext"] or "(none)", 0) + 1
    edata = {"schema": "external-deliverables-index/v1",
             "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "root": str(EXT), "total": len(ext), "by_ext": ecounts, "files": ext}
    OUT_EXT.mkdir(parents=True, exist_ok=True)
    ej = OUT_EXT / "2026-10-31_仓外交付物索引_v1.json"
    ej.write_text(json.dumps(edata, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 仓外交付物索引 2026-10-31", "", "根：`%s`" % EXT,
             "", "总数：%d" % len(ext), "", "| 类型 | 数量 |", "|---|---|"]
    for k in sorted(ecounts, key=lambda x: -ecounts[x]):
        lines.append("| %s | %d |" % (k, ecounts[k]))
    lines += ["", "## 最近交付物", "", "| 时间 | 类型 | 标题/文件 | 路径 |", "|---|---|---|---|"]
    for r in sorted(ext, key=lambda x: x["mtime"], reverse=True)[:120]:
        lines.append("| %s | %s | %s | `%s` |" % (
            r["mtime"], r["ext"] or "-", (r["title"] or r["rel"]).replace("|", "/")[:100],
            r["path"].replace("|", "/")))
    write_md(OUT_EXT / "2026-10-31_仓外交付物索引_v1.md", lines)
    return {"json": str(ej), "md": str(OUT_EXT / "2026-10-31_仓外交付物索引_v1.md"),
            "data": edata}


def write_md(path: Path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全量计划 + 仓外交付物索引（只读扫描）")
    ap.add_argument("--plans-only", action="store_true",
                    help="只重生成 docs/plans/PLAN_REGISTER_ALL_20261031.json/md")
    ap.add_argument("--external-only", action="store_true",
                    help="只重生成 docs/audit/仓外交付物索引")
    args = ap.parse_args(argv)
    do_plans = not args.external_only
    do_ext = not args.plans_only
    out = {"ok": True}
    if do_plans:
        r = write_plan_register()
        out["plans"] = r["data"]["total"]
        out["plan_counts"] = r["data"]["counts"]
        out["plan_register"] = r["json"]
    if do_ext:
        r = write_external_index()
        out["external"] = r["data"]["total"]
        out["external_counts"] = r["data"]["by_ext"]
        out["external_index"] = r["json"]
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
