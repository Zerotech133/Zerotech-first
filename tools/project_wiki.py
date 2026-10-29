#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""project_wiki：生成仓库 Wiki（仓库地图 / 核心模块 / ADR 索引 / 约定入口）。

用法：
  python -X utf8 tools/project_wiki.py            # 生成 docs/project_wiki.md
  python -X utf8 tools/project_wiki.py --check    # 只校验，不写；0=最新，1=缺失/过期
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_OUT = REPO / "docs" / "project_wiki.md"
ADR_DIR = REPO / "docs" / "adr"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _adr_rows(adr_dir: Path = ADR_DIR) -> list[tuple[str, str]]:
    rows = []
    if not adr_dir.is_dir():
        return rows
    for p in sorted(adr_dir.glob("ADR-*.md")):
        title = ""
        for line in _read_text(p).splitlines():
            line = line.strip()
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break
        rows.append((p.name, title or p.stem))
    return rows


def _convention_rows() -> list[str]:
    rows = []
    agents = REPO / "AGENTS.md"
    for line in _read_text(agents).splitlines():
        line = line.strip()
        if line.startswith("## "):
            rows.append(line[3:].strip())
    return rows


def _load_map():
    sys.path.insert(0, str(HERE))
    try:
        import codebase_intel as ci

        return ci.build_map(REPO, use_cache=False)
    except Exception:
        return {"file_count": 0, "symbol_count": 0, "modules": []}


def render() -> str:
    data = _load_map()
    modules = data.get("modules") or []
    top = {}
    for m in modules:
        rel = str((m or {}).get("path") or "")
        if not rel:
            continue
        head = rel.split("/", 1)[0]
        top[head] = top.get(head, 0) + 1

    lines = [
        "# 零界项目 Wiki（自动生成）",
        "",
        "> 由 `tools/project_wiki.py` 生成；`--check` 校验是否与仓库现状一致。",
        "> 仓库地图/ADR 索引/约定入口的单一自动快照；人工正文请写 docs/ 对应专题。",
        "",
        "## 1. 仓库地图",
        "",
        "| 顶层目录 | Python 模块数（codebase_intel scope） |",
        "|---|---:|",
    ]
    for name in sorted(top):
        lines.append("| `%s/` | %d |" % (name, top[name]))
    lines += [
        "",
        "codebase_intel 统计：模块 **%d** / 符号 **%d**。" % (
            int(data.get("file_count") or 0), int(data.get("symbol_count") or 0)
        ),
        "",
        "## 2. 核心模块（按路径）",
        "",
        "| 模块 | 说明 |",
        "|---|---|",
    ]
    for m in sorted(modules, key=lambda x: str((x or {}).get("path") or "")):
        rel = str((m or {}).get("path") or "")
        if not rel:
            continue
        doc = str((m or {}).get("doc") or "").replace("|", "\\|")
        lines.append("| `%s` | %s |" % (rel, doc or "—"))
    lines += [
        "",
        "## 3. ADR 索引",
        "",
        "| ADR | 标题 |",
        "|---|---|",
    ]
    for name, title in _adr_rows():
        lines.append("| [%s](adr/%s) | %s |" % (name, name, title.replace("|", "\\|")))
    lines += [
        "",
        "## 4. 约定与入口",
        "",
        "- 开发宪法：[AGENTS.md](../AGENTS.md)",
        "- 文档总索引：[docs/README.md](README.md)",
        "- 交付物：[deliverables/](../deliverables/)",
        "- 代码库智能：`python -X utf8 tools/codebase_intel.py map|search|refs|impact`",
        "",
        "### AGENTS.md 章节",
        "",
    ]
    for row in _convention_rows():
        lines.append("- %s" % row)
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验项目 Wiki")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径（默认 docs/project_wiki.md）")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO / out
    text = render()
    if args.check:
        if not out.is_file():
            print("[project_wiki] MISSING %s" % out)
            return 1
        current = _read_text(out).replace("\r\n", "\n")
        if current != text:
            print("[project_wiki] STALE %s（重跑生成器）" % out)
            return 1
        print("[project_wiki] OK %s" % out)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print("[project_wiki] wrote %s (%d chars)" % (out, len(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
