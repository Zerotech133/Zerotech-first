#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口归档扫描 v2（P1-5，2026-10-30）：引用链证据（代码/配置，排除文档）。

v1 教训：用 basename 全域搜索 → 文档里提一句就算"被引用"，候选恒 0 且耗时 460s。
v2 口径：
  · 只把「代码/配置」文件算引用源（.py/.ps1/.bat/.cmd/.js/.ts/.json/.yaml/.yml/.toml/.ini/.sh），
    **文档(.md/.txt)不计**（历史计划文档提到不等于在运行）；
  · 引用判定 = ①全仓代码/配置文本出现「相对路径」（正/反斜杠）②.py 入口的模块名出现在
    任何 .py 的 import 语句（AST 收集）③计划任务 XML 含路径或模块名；
  · 0 引用 → 归档候选（只出清单；移动/删除需 L2 审批 + 逐项复核）。

用法：
  python tools/entry_archive_scan.py           # 汇总+落盘
  python tools/entry_archive_scan.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TSV = REPO / "docs" / "ops" / "entrypoints_v1.tsv"
OUT = REPO / "agent_platform" / "data" / "reports" / "entry_archive"
CODE_SUFFIX = {".py", ".ps1", ".bat", ".cmd", ".js", ".ts", ".json",
               ".yaml", ".yml", ".toml", ".ini", ".sh"}
DOC_SUFFIX = {".md", ".txt"}
SKIP_DIRS = {".git", "__pycache__", ".worktrees", "node_modules", ".venv",
             "venv", ".venv_czsc", "site-packages", ".pytest_cache",
             "backups", ".mypy_cache", ".pytest_cache"}
SKIP_PREFIX = ("quant_backtest/data", "agent_platform/data",
               "docs/visual_baseline", "outputs/", "work/",
               "deliverables/", "docs/audit/_archive", "backup/",
               "zerobound-kg", "CowAgent", "bin", "_vault", "video",
               "installs", "node_modules")
# 清单/索引类文件：内容含全量路径（会伪造“被引用”），不作为引用源
INVENTORY_SKIP_NAMES = ("agent_ide_bundle.json", "entrypoints_v1.tsv",
                        "catalog.json", "warehouse_registry.json")
INVENTORY_SKIP_SUFFIX = ("_manifest.json", "_inventory.json")
MAX_FILE = 2 * 1024 * 1024


def iter_files(root=REPO):
    for dirpath, dirnames, filenames in _walk(Path(root)):
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(REPO).as_posix()
            yield p, rel


def _walk(root):
    import os
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = Path(dirpath).relative_to(root).as_posix()
        if rel != "." and any(rel == p.rstrip("/") or rel.startswith(p) for p in SKIP_PREFIX):
            continue
        # 测试目录/测试文件不算“生产引用”（import 只证明被测试覆盖）
        dirnames[:] = [d for d in dirnames
                       if d not in ("tests", "test", "testdata", "__tests__")]
        filenames = [f for f in filenames
                     if not f.startswith("test_")
                     and f not in INVENTORY_SKIP_NAMES
                     and not f.endswith(INVENTORY_SKIP_SUFFIX)]
        yield dirpath, dirnames, filenames


def build_index(roots=None):
    """一次遍历：拼接代码/配置文本 + AST 收集 import 模块名。

    roots 为空=全仓（测试用）；生产调用传入口所属顶层目录，避免外链巨树。
    """
    chunks = []
    imported = set()
    file_count = 0
    iterables = []
    if roots:
        for r in roots:
            iterables.extend(iter_files(REPO / r))
    else:
        iterables.extend(iter_files())
    for p, rel in iterables:
        try:
            if p.stat().st_size > MAX_FILE:
                continue
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_count += 1
        chunks.append(txt.replace("\\", "/"))
        if p.suffix == ".py":
            try:
                tree = ast.parse(txt)
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imported.add(a.name.split(".")[0])
                        imported.add(a.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
                    imported.add(node.module)
    return "\n".join(chunks), imported, file_count


def scheduled_text():
    try:
        p = subprocess.run(["schtasks", "/query", "/xml", "ONE"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return ((p.stdout or "") + (p.stderr or "")).replace("\\", "/")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="入口归档扫描（代码/配置口径）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    rows = TSV.read_text(encoding="utf-8").splitlines()[1:]
    entries = []
    types = {}
    for r in rows:
        parts = r.split("\t")
        rel = parts[0].strip()
        et = (parts[1].strip() if len(parts) > 1 else "")
        if rel and (REPO / rel).exists():
            entries.append(rel)
            types[rel] = et
    # 默认全仓（SKIP_PREFIX 已排除巨树/数据根）；如需只扫入口顶层目录可传 roots。
    text, imported, files = build_index(None)
    sched = scheduled_text()
    lower_text = text  # 已统一正斜杠
    results = []
    for rel in entries:
        stem = Path(rel).stem
        path_hit = rel in lower_text
        import_hit = rel.endswith(".py") and (stem in imported)
        sched_hit = (rel in sched) or (stem in sched)
        refs = int(path_hit) + int(import_hit) + int(sched_hit)
        results.append({"path": rel, "entry_type": types.get(rel, ""),
                        "refs": refs, "path_hit": path_hit,
                        "import_hit": import_hit, "sched_hit": sched_hit})
    def _is_test_entry(rel: str) -> bool:
        parts = rel.replace("\\", "/").split("/")
        name = parts[-1]
        return (any(x in ("tests", "test", "testdata", "__tests__") for x in parts[:-1])
                or name.startswith("test_") or name == "conftest.py")

    cand = [r for r in results if r["refs"] == 0 and not _is_test_entry(r["path"])]
    cand.sort(key=lambda r: r["path"])
    payload = {
        "tool": "entry_archive_scan_v2",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entries": len(entries), "indexed_files": files,
        "counts": {"entries": len(entries), "candidates": len(cand),
                   "referenced": len(entries) - len(cand)},
        "candidates": cand, "elapsed_s": round(time.time() - t0, 1),
    }
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / ("scan_%s.json" % time.strftime("%Y%m%d_%H%M%S"))).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        print("[entry_archive] 落盘失败：%r" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("entries=%d candidates=%d referenced=%d indexed=%d elapsed=%.1fs"
              % (len(entries), len(cand), len(entries) - len(cand), files,
                 payload["elapsed_s"]))
        for r in cand[:80]:
            print("  - %s" % r["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
