#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史代码引用链扫描（P0-2，2026-10-30）：为 legacy_registry 提供证据。

用途：给定路径（文件或目录），扫描仓库内引用次数与出处，支撑 L1/L2/L3 定级：
  · 0 引用（文本文件引用为 0）→ L3 回收候选（仍需 L2 审批 + 回收站）；
  · 仅文档引用 → L2 归档候选（证据保留）；
  · 代码/脚本/配置引用 → 保留（L1 冻结或 L0 活跃）。

用法：
  python tools/legacy_scan.py --path quant_backtest/archive
  python tools/legacy_scan.py --path a.py --path b.ps1 --json
  python tools/legacy_scan.py --from-list docs/ops/legacy_targets.txt
退出码：0 正常；1 参数/路径错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REF_SUFFIXES = {
    ".py", ".ps1", ".bat", ".cmd", ".js", ".ts", ".json", ".yaml", ".yml",
    ".md", ".toml", ".ini", ".txt", ".cfg", ".sh",
}
SKIP_DIRS = {
    ".git", "__pycache__", ".worktrees", "node_modules", ".venv", "venv",
    ".venv_czsc", "site-packages", ".pytest_cache", "backups", ".mypy_cache",
}
SKIP_PREFIXES = (
    "quant_backtest/data/", "agent_platform/data/reports/metadata_daily/",
)


def iter_ref_files(root: Path = REPO):
    """仓库内可引用文本文件（os.walk + 容错：跳过 socket/无权限等特殊文件）。"""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir != "." and rel_dir.startswith(SKIP_PREFIXES):
            continue
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix not in REF_SUFFIXES:
                continue
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            yield p, p.relative_to(root).as_posix()


def _target_tokens(rel: str):
    """引用可能出现的写法：路径正斜杠 / 反斜杠 / 模块点分（.py）。"""
    rel = rel.replace("\\", "/")
    toks = {rel}
    if rel.endswith(".py"):
        mod = rel[:-3].replace("/", ".")
        toks.add(mod)
        toks.add("/".join(rel.split("/")[-2:]))  # 末尾两段，降低误配
    return {t for t in toks if t}


def scan_paths(targets, root: Path = REPO, max_hits=500):
    """targets: 相对路径列表（文件或目录）。返回每个目标的引用统计。"""
    root = Path(root)
    ref_files = list(iter_ref_files(root))
    out = {}
    for t in targets:
        t = t.replace("\\", "/").lstrip("./")
        full = root / t
        if not full.exists():
            out[t] = {"error": "path_not_found"}
            continue
        if full.is_dir():
            files = [p for p in full.rglob("*") if p.is_file()]
            kind = "dir"
        else:
            files = [full]
            kind = "file"
        file_rels = [p.relative_to(root).as_posix() for p in files]
        # 目标内文件互引不算外部引用
        target_set = set(file_rels)
        hits = []
        for p, rel in ref_files:
            if rel in target_set:
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matched = None
            for frel in file_rels:
                for tok in _target_tokens(frel):
                    if tok and tok in txt:
                        matched = tok
                        break
                if matched:
                    break
            if matched:
                hits.append({"file": rel, "token": matched, "suffix": p.suffix})
                if len(hits) >= max_hits:
                    break
        doc_hits = [h for h in hits if h["suffix"] in (".md", ".txt")]
        code_hits = [h for h in hits if h["suffix"] not in (".md", ".txt")]
        if not hits:
            level = "L3_candidate"
        elif not code_hits:
            level = "L2_candidate_doc_only"
        else:
            level = "referenced"
        out[t] = {
            "kind": kind,
            "files": len(file_rels),
            "hits": len(hits),
            "code_hits": len(code_hits),
            "doc_hits": len(doc_hits),
            "level_hint": level,
            "samples": hits[:8],
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="历史代码引用链扫描（legacy 定级证据）")
    ap.add_argument("--path", action="append", default=[], help="相对路径（可多次）")
    ap.add_argument("--from-list", help="从文件读目标列表（每行一个）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=str(REPO), help="仓库根（测试用）")
    args = ap.parse_args(argv)

    targets = list(args.path)
    if args.from_list:
        p = Path(args.from_list)
        if not p.exists():
            print("列表文件不存在：%s" % p, file=sys.stderr)
            return 1
        targets += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
    if not targets:
        print("用法：python tools/legacy_scan.py --path <相对路径> 或 --from-list <文件>",
              file=sys.stderr)
        return 1

    res = scan_paths(targets, root=Path(args.root))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("%-52s %-12s %6s %6s %6s %s" % ("目标", "类型", "文件", "引用", "代码引用", "定级提示"))
    for t, r in res.items():
        if r.get("error"):
            print("%-52s ERROR %s" % (t, r["error"]))
            continue
        print("%-52s %-12s %6d %6d %6d %s"
              % (t, r["kind"], r["files"], r["hits"], r["code_hits"], r["level_hint"]))
        for s in r["samples"][:3]:
            print("      ← %s (%s)" % (s["file"], s["token"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
