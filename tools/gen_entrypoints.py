#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本入口登记表生成器（2026-10-28）。

扫描自研主线内的"可执行入口"（main() 或 if __name__ == "__main__"），
输出 TSV：路径、入口类型（main/guard/both）。

用法：python tools/gen_entrypoints.py [--out docs/ops/entrypoints_v1.tsv]
口径：排除 venv/vendor/archive/外部子系统（Hermes/AI/zerobound-kg 等）。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TOP = ["agent_platform", "portal", "quant_backtest", "k_stock", "news_collector",
       "jev-eval", "osint_video", "common", "analysis", "tools", "ops", "ollama_service",
       "data_sources"]
SKIP_SEG = ("/data/", "/backups/", "/archive/", "/__pycache__/", "/.venvs/", "/.venv",
            "/pylibs/", "/vendor/", "/node_modules/", "/deliverables/", "/work/", "/tmp/",  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
            "/outputs/", "/video/", "/tests_ai_operator/")
DEFAULT_OUT = REPO / "docs" / "ops" / "entrypoints_v1.tsv"


def collect() -> list[tuple[str, str]]:
    rows = []
    for top in TOP:
        base = REPO / top
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            rel = "/" + str(p.relative_to(REPO)).replace("\\", "/")
            if any(s in rel for s in SKIP_SEG):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            has_main = any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
                for n in tree.body
            )
            has_guard = any(
                isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) for n in tree.body
            )
            if has_main and has_guard:
                rows.append((str(p.relative_to(REPO)).replace("\\", "/"), "both"))
            elif has_main:
                rows.append((str(p.relative_to(REPO)).replace("\\", "/"), "main"))
            elif has_guard:
                rows.append((str(p.relative_to(REPO)).replace("\\", "/"), "guard"))
    return sorted(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成脚本入口登记表")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    rows = collect()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "path\tentry_type\n" + "\n".join("%s\t%s" % r for r in rows) + "\n",
        encoding="utf-8", newline="\n",
    )
    print("ENTRIES", len(rows), "->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
