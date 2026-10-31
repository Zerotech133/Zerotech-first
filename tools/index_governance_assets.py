# -*- coding: utf-8 -*-
"""全量计划 + 仓外交付物索引（2026-10-31 治理收口）。

输出：
  docs/audit/2026-10-31_仓外交付物索引_v1.json/md
  docs/plans/PLAN_REGISTER_ALL_20261031.json/md
只读扫描，不修改原文件。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = Path(r"C:\项目\deliverables")
PLANS = ROOT / "docs" / "plans"
OUT_EXT = ROOT / "docs" / "audit"
OUT_PLANS = PLANS


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
        mstat = re.search(r"(?im)^\s*>\s*STATUS\s*[:：]\s*([^\n]{0,120})", text[:2000])
        declared = (mstat.group(1).strip().lower() if mstat else "")
        if declared.startswith("superseded"):
            state = "superseded"
        elif declared.startswith("pending"):
            state = "pending_approval"
        elif declared.startswith("frozen"):
            state = "frozen"
        elif declared.startswith("unspecified"):
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


def write_md(path: Path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    plans = plan_rows()
    pcounts = {}
    for r in plans:
        pcounts[r["state"]] = pcounts.get(r["state"], 0) + 1
    pdata = {"schema": "plan-register-all/v1",
             "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "total": len(plans), "counts": pcounts, "plans": plans}
    pj = OUT_PLANS / "PLAN_REGISTER_ALL_20261031.json"
    pj.write_text(json.dumps(pdata, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 计划台账（全量，含子目录）2026-10-31", "",
             "> 只读扫描；原计划不改。unspecified 需 owner 在 2026-11-07 前补状态。", "",
             "总数：%d" % len(plans), "", "| 状态 | 数量 |", "|---|---|"]
    for k in sorted(pcounts):
        lines.append("| %s | %d |" % (k, pcounts[k]))
    lines += ["", "## 计划清单", "", "| 日期 | 状态 | 标题 | 文件 |", "|---|---|---|---|"]
    for r in plans[:300]:
        lines.append("| %s | %s | %s | `%s` |" % (
            r["date"] or "-", r["state"], (r["title"] or "-").replace("|", "/"), r["path"]))
    write_md(OUT_PLANS / "PLAN_REGISTER_ALL_20261031.md", lines)

    ext = ext_rows()
    ecounts = {}
    for r in ext:
        ecounts[r["ext"] or "(none)"] = ecounts.get(r["ext"] or "(none)", 0) + 1
    edata = {"schema": "external-deliverables-index/v1",
             "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "root": str(EXT), "total": len(ext), "by_ext": ecounts, "files": ext}
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
    print(json.dumps({"ok": True, "plans": len(plans), "plan_counts": pcounts,
                      "external": len(ext), "external_counts": ecounts,
                      "plan_register": str(pj), "external_index": str(ej)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
