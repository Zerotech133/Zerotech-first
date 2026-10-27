# -*- coding: utf-8 -*-
"""docs 本地链接检查（WO-DRILL5-SOPLINK）：防 SOP/ADR 文档断链。

做两件事：
1. 提取 Markdown 链接 ``[text](target)``（跳过 http/https/mailto 等外链与
   ``#`` 纯锚点），以及反引号代码段内形如 ``tools/...`` / ``docs/...`` /
   ``agent_platform/...`` 的本地路径；
2. 逐条解析：先按**文档所在目录**拼相对路径，再按**仓库根**拼相对路径，
   任一存在即通过，否则记为断链。

用法：
  python -X utf8 tools/sop_link_check.py [--check] [--json]
  python -X utf8 tools/sop_link_check.py --root <仓库根> --docs <文档...>

默认文档（均已实测零断链，2026-10-27）：
  docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md
  docs/adr/ADR-003-dev-routing-tool-matrix.md

参数：
  --root   仓库根目录，默认本脚本上一级（worktree 根），测试可传 tmp 根；
  --docs   要检查的文档（相对 root 或绝对路径），可一次多个/多次传，默认见上；
  --check  有断链时退出码 1，否则 0（CI/验收用）；不带则仅报告，恒 0；
  --json   输出机器可读 JSON（doc/summary/broken 等字段），供自动化消费。

只做静态检查：不联网、不读 git、不改文档。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote

# 仓库根：tools/sop_link_check.py → parents[1]
REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DOCS: tuple[str, ...] = (
    "docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md",
    "docs/adr/ADR-003-dev-routing-tool-matrix.md",
)

# 反引号内只认这三类本地路径前缀（与工单口径一致）
LOCAL_PATH_PREFIXES: tuple[str, ...] = ("tools/", "docs/", "agent_platform/")

# Markdown 链接（排除图片 ![](...)）；target 至少一个非空字符
_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*([^)\n]+?)\s*\)")
# 反引号代码段（单行，避免跨段误吞）
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
# 代码段里的本地路径；前界防 mytools/xtools 误匹配，后界在清理阶段处理标点
_CODE_PATH_RE = re.compile(
    r"(?<![\w/.-])((?:%s)[^\s`'\"<>|()\[\]{}]+)"
    % "|".join(re.escape(p.rstrip("/")) for p in LOCAL_PATH_PREFIXES)
)

# 链接目标尾部的标点/中文标点（代码段路径常被标点包围）
_TRAILING_PUNCT = ".,;:!?，。；：、）)]}》」』\"'"

# 任意 URL scheme（http/https/mailto/tel/ftp/data…）；Windows 盘符路径不在此列
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass
class Target:
    """文档里提取到的一条本地链接/路径。"""

    kind: str  # markdown / backtick
    target: str
    line: int


@dataclass
class DocResult:
    """单个文档的检查结果。"""

    doc: str  # 相对 root 的展示路径
    path: Path
    exists: bool = True
    items: list[dict] = field(default_factory=list)
    broken: list[dict] = field(default_factory=list)

    @property
    def target_count(self) -> int:
        return len(self.items)


class SopLinkResult:
    """整次检查的汇总（便于 JSON 序列化）。"""

    def __init__(self, root: Path, docs: list[DocResult]) -> None:
        self.root = root
        self.docs = docs
        self.broken = [b for d in docs for b in d.broken]

    @property
    def target_count(self) -> int:
        return sum(d.target_count for d in self.docs)

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "summary": {
                "docs": len(self.docs),
                "targets": self.target_count,
                "broken": len(self.broken),
                "missing_docs": sum(1 for d in self.docs if not d.exists),
            },
            "docs": [
                {
                    "doc": d.doc,
                    "exists": d.exists,
                    "targets": d.target_count,
                    "broken": len(d.broken),
                    "items": d.items,
                }
                for d in self.docs
            ],
            "broken": self.broken,
        }


def _clean_target(raw: str) -> str:
    """归一链接/代码段路径：去 <>、去 title、去锚点/查询串、去尾部标点。"""
    target = raw.strip()
    if target.startswith("<"):
        # 尖括号目标允许含空格，取到第一个 > 为止
        end = target.find(">")
        if end != -1:
            target = target[1:end].strip()
    elif " " in target or "\t" in target:
        # Markdown 标题写法：path "title" / path 'title'
        target = target.split()[0]
    # 锚点与查询串不参与文件存在性判断
    target = re.split(r"[#?]", target, 1)[0]
    target = target.rstrip(_TRAILING_PUNCT)
    return unquote(target).strip()


def _is_external(target: str) -> bool:
    """外链/纯锚点/协议相对链接/空目标 → 不检查。"""
    if not target:
        return True
    if target.startswith("#"):  # 纯锚点
        return True
    if target.startswith("//"):  # 协议相对，非本地文件
        return True
    if _URL_SCHEME_RE.match(target):
        return True
    return False


def _looks_like_local_path(target: str) -> bool:
    """反引号内容里只保留 tools/ docs/ agent_platform/ 前缀的路径。"""
    normalized = target.replace("\\", "/")
    return normalized.startswith(LOCAL_PATH_PREFIXES)


def extract_targets(text: str) -> list[Target]:
    """从 Markdown 文本提取待检查的本地链接与反引号路径（含行号）。"""
    targets: list[Target] = []

    for match in _MD_LINK_RE.finditer(text):
        raw = match.group(1)
        target = _clean_target(raw)
        if _is_external(target):
            continue
        line = text.count("\n", 0, match.start()) + 1
        targets.append(Target(kind="markdown", target=target, line=line))

    for match in _CODE_SPAN_RE.finditer(text):
        code = match.group(1)
        for path_match in _CODE_PATH_RE.finditer(code):
            target = _clean_target(path_match.group(1))
            if not _looks_like_local_path(target):
                continue
            line = text.count("\n", 0, match.start()) + 1
            targets.append(Target(kind="backtick", target=target, line=line))

    # 同一行同一路径在代码段里重复出现时去重，保持文档顺序
    seen: set[tuple[str, str, int]] = set()
    unique: list[Target] = []
    for item in targets:
        key = (item.kind, item.target, item.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def resolve_target(root: Path, doc: Path, target: str) -> Path | None:
    """解析顺序：文档所在目录 → 仓库根；存在即返回绝对路径。"""
    candidate = Path(target)
    candidates: Iterable[Path]
    if candidate.is_absolute():
        candidates = (candidate,)
    else:
        candidates = (doc.parent / candidate, root / candidate)
    for path in candidates:
        try:
            if path.exists():
                return path
        except OSError:  # 路径过长/非法字符等，按不存在处理
            continue
    return None


def _display_path(root: Path, path: Path) -> str:
    """尽量展示相对仓库根的路径，失败则展示绝对路径。"""
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def check_doc(root: Path, doc_arg: str) -> DocResult:
    """检查单个文档；文档缺失或读取失败视为断链（reason=missing_doc）。"""
    doc_path = Path(doc_arg)
    if not doc_path.is_absolute():
        doc_path = root / doc_path
    doc_name = _display_path(root, doc_path)

    result = DocResult(doc=doc_name, path=doc_path)
    if not doc_path.is_file():
        result.exists = False
        result.broken.append(
            {
                "doc": doc_name,
                "kind": "doc",
                "line": 0,
                "target": doc_arg,
                "reason": "missing_doc",
                "resolved": None,
            }
        )
        return result

    try:
        text = doc_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # 极端情况：权限/占用
        result.exists = False
        result.broken.append(
            {
                "doc": doc_name,
                "kind": "doc",
                "line": 0,
                "target": doc_arg,
                "reason": f"read_error: {exc}",
                "resolved": None,
            }
        )
        return result

    for item in extract_targets(text):
        resolved = resolve_target(root, doc_path, item.target)
        entry = {
            "kind": item.kind,
            "line": item.line,
            "target": item.target,
            "resolved": _display_path(root, resolved) if resolved else None,
        }
        result.items.append(entry)
        if resolved is None:
            broken = dict(entry)
            broken["doc"] = doc_name
            broken["reason"] = "not_found"
            result.broken.append(broken)

    return result


def check(root: Path, docs: Sequence[str]) -> SopLinkResult:
    """批量检查文档，返回汇总结果。"""
    return SopLinkResult(root, [check_doc(root, doc) for doc in docs])


def _print_human(result: SopLinkResult) -> None:
    print(f"docs 本地链接检查（root={result.root}）")
    for doc in result.docs:
        status = "OK" if doc.exists and not doc.broken else "FAIL"
        print(
            f"\n[{status}] {doc.doc}："
            f"{doc.target_count} 条链接/路径，断链 {len(doc.broken)} 条"
        )
        for item in doc.items:
            if item["resolved"]:
                print(
                    f"  [OK] L{item['line']} {item['target']} "
                    f"-> {item['resolved']}"
                )
            else:
                print(
                    f"  [断链] L{item['line']} {item['target']}"
                    "（文档目录与仓库根均不存在）"
                )
        for broken in doc.broken:
            if broken["kind"] == "doc":
                print(f"  [断链] 文档不存在或不可读：{broken['target']}"
                      f"（{broken['reason']}）")
    print(
        f"\n合计：文档 {len(result.docs)} 个，链接/路径 {result.target_count} 条，"
        f"断链 {len(result.broken)} 条"
    )
    print("结论：" + ("通过" if not result.broken else "存在断链，需修复"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查 docs 文档中的本地 Markdown 链接与反引号路径是否断链。"
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="仓库根目录（默认本脚本所在仓库/worktree 根）",
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        action="append",
        default=None,
        metavar="DOC",
        help="要检查的文档，可一次多个或多次传（默认内置两份 2026-10-27 路由文档）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="有断链时退出码 1（否则 0），CI/验收用",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 结果（含 docs/summary/broken）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口：返回进程退出码（0 通过 / 1 断链）。"""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()

    if not root.is_dir():
        print(f"错误：--root 不是目录：{root}", file=sys.stderr)
        return 2

    docs: list[str] = []
    if args.docs:
        for group in args.docs:
            docs.extend(group)
    else:
        docs = list(DEFAULT_DOCS)

    result = check(root, docs)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    if args.check and result.broken:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
