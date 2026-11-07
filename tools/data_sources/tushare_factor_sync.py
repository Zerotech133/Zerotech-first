# -*- coding: utf-8 -*-
"""Tushare factor_list 快照 diff 与 ``TYPE_NAMES`` 补丁生成器。

用途
----
Tushare ``factor_list`` 是静态清单；新增/删除因子后，``factor_lib_tushare.py``
的 ``TYPE_NAMES`` 需要人工同步。本脚本把这一步做成离线、可审查的半自动流程：

1. 读取新旧两份快照（JSON，或 parquet 兜底）；
2. 按 ``factor_name`` 对比，输出 added / removed / moved 明细；
3. 以**当前源代码里的 TYPE_NAMES 为基线**，生成目标映射与 unified diff；
4. 默认只写补丁和 JSON 报告，不改源码；``--apply`` 才写回并留 ``.bak``。

不联网、不拉取快照、不实现因子。新增因子类型（新的 ``factor_type``）不会自动
猜测 batch，脚本会报错并要求人工先确定 B1/B2/B3。

用法
----
    python tools/data_sources/tushare_factor_sync.py diff
    python tools/data_sources/tushare_factor_sync.py diff --old <old.json> --new <new.json>
    python tools/data_sources/tushare_factor_sync.py diff --apply
    python tools/data_sources/tushare_factor_sync.py diff --check

``diff`` 默认：新快照 = ``latest.json``；旧快照 = snapshots 中内容不同的第二新
JSON；输出目录 = ``deliverables/2026-11-07_tushare_factor_sync``。
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SNAP_DIR = ROOT / "quant_backtest" / "data" / "factor_library" / "tushare_factor_list"
DEFAULT_SOURCE = ROOT / "tools" / "research" / "factor_lib_tushare.py"
DEFAULT_OUT = ROOT / "deliverables" / "2026-11-07_tushare_factor_sync"

# factor_type -> batch。新增类型必须先人工确认 batch，脚本不猜。
TYPE_BATCH = {
    "Alpha101": "B2_alpha101_expression",
    "Growth": "B3_financial_pit",
    "Quality": "B3_financial_pit",
    "Value": "B3_financial_pit",
    "Momentum": "B1_price_volume",
    "Liquidity": "B1_price_volume",
    "Risk": "B1_price_volume",
    "Reversal": "B1_price_volume",
    "Size": "B1_price_volume",
}
CHUNK = 4


class SyncError(RuntimeError):
    """输入/源码结构不满足同步前提。"""


# ------------------------------------------------------------------ 快照读取
def _read_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SyncError(f"快照不存在: {path}")
    if path.suffix.lower() == ".parquet":
        try:
            import pandas as pd  # 延迟导入：只读 JSON 时不依赖 pandas
        except Exception as exc:  # pragma: no cover
            raise SyncError(f"读取 parquet 需要 pandas: {exc}") from exc
        frame = pd.read_parquet(path)
        rows = frame.to_dict(orient="records")
        return {"rows": rows, "source_path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SyncError(f"快照 JSON 无法解析: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise SyncError(f"快照缺少 rows 列表: {path}")
    payload["source_path"] = str(path)
    return payload


def _normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    out: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        name = str(row.get("factor_name", "")).strip()
        ftype = str(row.get("factor_type", "")).strip()
        if not name:
            errors.append(f"row {i}: factor_name 为空")
            continue
        if not ftype:
            errors.append(f"row {i} ({name}): factor_type 为空")
            continue
        if name in seen:
            errors.append(f"row {i}: factor_name 重复: {name}")
            continue
        seen.add(name)
        out.append(
            {
                "factor_name": name,
                "factor_type": ftype,
                "factor_desc": str(row.get("factor_desc", "") or ""),
                "asset_type": str(row.get("asset_type", "") or ""),
            }
        )
    return out, errors


def _count_by_type(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["factor_type"]] = counts.get(row["factor_type"], 0) + 1
    return dict(sorted(counts.items()))


def _snapshot_meta(payload: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, Any]:
    path = Path(str(payload.get("source_path", "")))
    text = path.read_bytes() if path.is_file() else b""
    return {
        "path": str(path),
        "sha256": hashlib.sha256(text).hexdigest() if text else "",
        "fetched_at": payload.get("fetched_at", ""),
        "total": len(rows),
        "by_type": _count_by_type(rows),
    }


# ------------------------------------------------------------------ 差异
def diff_rows(
    old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]
) -> dict[str, Any]:
    old = {r["factor_name"]: r for r in old_rows}
    new = {r["factor_name"]: r for r in new_rows}
    added = [
        {"factor_name": n, "factor_type": new[n]["factor_type"]}
        for n in sorted(new.keys() - old.keys())
    ]
    removed = [
        {"factor_name": n, "factor_type": old[n]["factor_type"]}
        for n in sorted(old.keys() - new.keys())
    ]
    moved = [
        {
            "factor_name": n,
            "old_type": old[n]["factor_type"],
            "new_type": new[n]["factor_type"],
        }
        for n in sorted(old.keys() & new.keys())
        if old[n]["factor_type"] != new[n]["factor_type"]
    ]
    unchanged = len(old.keys() & new.keys()) - len(moved)
    return {
        "added": added,
        "removed": removed,
        "moved": moved,
        "unchanged": unchanged,
        "counts": {
            "old": len(old_rows),
            "new": len(new_rows),
            "added": len(added),
            "removed": len(removed),
            "moved": len(moved),
        },
        "by_type_old": _count_by_type(old_rows),
        "by_type_new": _count_by_type(new_rows),
    }


# ------------------------------------------------------------------ TYPE_NAMES 源码解析
def _type_names_node(tree: ast.Module) -> ast.AnnAssign | ast.Assign:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "TYPE_NAMES":
                return node
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TYPE_NAMES":
                    return node
    raise SyncError("源码中未找到 TYPE_NAMES 赋值")


def _names_from_value(node: ast.AST, source: str) -> list[str]:
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for elt in node.elts:
            try:
                value = ast.literal_eval(elt)
            except Exception as exc:
                raise SyncError(f"TYPE_NAMES 元组含非字面量元素: {ast.dump(elt)}") from exc
            names.append(str(value))
        return names
    # 兼容 Alpha101 当前的 tuple(f"alpha101_{i}" for i in range(1, 102))
    segment = ast.get_source_segment(source, node) or ""
    match = re.search(r"range\(\s*1\s*,\s*(\d+)\s*\)", segment)
    if match:
        limit = int(match.group(1))
        return [f"alpha101_{i}" for i in range(1, limit)]
    raise SyncError(f"无法解析 TYPE_NAMES 值: {segment[:120]}")


def parse_type_names(source: str) -> tuple[dict[str, list[str]], dict[str, tuple[int, int, str]], int]:
    """返回 (名称映射, 类型->(起始行,结束行,缩进), dict闭合行)。

    行号 1-based；起始行是 key 所在行，结束行是 value 结束行（含 `),`）。
    """
    tree = ast.parse(source)
    assign = _type_names_node(tree)
    value = assign.value
    if not isinstance(value, ast.Dict):
        raise SyncError("TYPE_NAMES 必须是字典字面量")
    mapping: dict[str, list[str]] = OrderedDict()
    spans: dict[str, tuple[int, int, str]] = {}
    source_lines = source.splitlines()
    for key_node, value_node in zip(value.keys, value.values):
        if not isinstance(key_node, ast.Constant):
            raise SyncError("TYPE_NAMES 的 key 必须是字符串字面量")
        key = str(key_node.value)
        names = _names_from_value(value_node, source)
        if key in mapping:
            raise SyncError(f"TYPE_NAMES 重复分类: {key}")
        mapping[key] = names
        start = key_node.lineno
        end = value_node.end_lineno or start
        indent = re.match(r"\s*", source_lines[start - 1]).group(0)
        spans[key] = (start, end, indent)
    return mapping, spans, value.end_lineno or assign.end_lineno or 0


def _format_names(names: list[str], indent: str) -> list[str]:
    if not names:
        return []
    lines: list[str] = []
    for i in range(0, len(names), CHUNK):
        chunk = names[i : i + CHUNK]
        lines.append(indent + ", ".join(f'"{n}"' for n in chunk) + ",")
    return lines


def _format_type_block(key: str, names: list[str], indent: str, one_line: bool) -> list[str]:
    if key == "Alpha101" and names == [f"alpha101_{i}" for i in range(1, len(names) + 1)]:
        return [
            f'{indent}"Alpha101": tuple(f"alpha101_{{i}}" for i in range(1, {len(names) + 1})),'
        ]
    if one_line and len(names) <= CHUNK:
        body = ", ".join(f'"{n}"' for n in names)
        return [f'{indent}"{key}": ({body}),']
    return [f'{indent}"{key}": ('] + _format_names(names, indent + "    ") + [f"{indent}),"]


def build_type_names_patch(
    source: str,
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    """生成 patch 后的源码文本与摘要。

    - 以源码中的 TYPE_NAMES 为基线；
    - 新快照是目标集合：源码有但新快照没有的因子被移除，反之加入；
    - moved 因子从旧分类移到新分类；
    - 未知 factor_type 直接报错，避免默认落到 B3。
    """
    mapping, spans, dict_end = parse_type_names(source)
    unknown = sorted({r["factor_type"] for r in new_rows} - set(TYPE_BATCH))
    if unknown:
        raise SyncError(
            "新快照出现未知 factor_type，脚本不猜 batch；请先人工登记："
            + ", ".join(unknown)
        )
    unknown_old = sorted(set(mapping) - set(TYPE_BATCH))
    if unknown_old:
        raise SyncError("源码 TYPE_NAMES 含未知分类，拒绝生成：" + ", ".join(unknown_old))

    target: dict[str, list[str]] = OrderedDict()
    for row in new_rows:
        target.setdefault(row["factor_type"], []).append(row["factor_name"])

    missing_categories = sorted(set(target) - set(mapping))
    if missing_categories:
        raise SyncError(
            "新快照出现 TYPE_NAMES 中没有的 factor_type，请先手工加分类："
            + ", ".join(missing_categories)
        )

    source_lines = source.splitlines()
    edits: list[tuple[int, int, list[str]]] = []
    sections: list[dict[str, Any]] = []

    for key, old_names in mapping.items():
        desired = [n for n in old_names if n in set(target.get(key, []))]
        desired += [n for n in target.get(key, []) if n not in old_names]
        if desired == old_names:
            continue
        start, end, indent = spans[key]
        if (start, end) in {(s, e) for s, e, _ in edits}:
            raise SyncError(f"TYPE_NAMES 块重叠: {key}")
        one_line = start == end
        block = _format_type_block(key, desired, indent, one_line)
        edits.append((start, end, block))
        sections.append(
            {
                "factor_type": key,
                "added": [n for n in desired if n not in old_names],
                "removed": [n for n in old_names if n not in desired],
                "before": len(old_names),
                "after": len(desired),
            }
        )

    if not edits:
        return source, {"changed": False, "sections": [], "target": target}

    for start, end, block in sorted(edits, reverse=True):
        source_lines[start - 1 : end] = block
    if not source.endswith("\n"):
        source += "\n"
    patched = "\n".join(source_lines) + "\n"
    return patched, {"changed": True, "sections": sections, "target": target}


# ------------------------------------------------------------------ 路径/输出
def _unique_snapshots() -> list[Path]:
    paths = sorted(
        [p for p in SNAP_DIR.glob("snapshots/factor_list_*.json") if p.is_file()],
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in reversed(paths):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(path)
    return unique


def _default_old() -> Path:
    snapshots = _unique_snapshots()
    if len(snapshots) < 2:
        raise SyncError(
            "快照库中没有两份内容不同的历史快照；请显式传 --old，或先 fetch 一份新快照"
        )
    return snapshots[1]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_diff(args: argparse.Namespace) -> int:
    if args.baseline and args.old:
        raise SyncError("--baseline 与 --old 只能二选一")
    new_path = Path(args.new).resolve() if args.new else (SNAP_DIR / "latest.json")
    if args.baseline:
        old_path = Path(args.baseline).resolve()
    else:
        old_path = Path(args.old).resolve() if args.old else _default_old()
    source_path = Path(args.source).resolve() if args.source else DEFAULT_SOURCE
    out_dir = Path(args.out_dir).resolve() if args.out_dir else DEFAULT_OUT

    old_payload = _read_payload(old_path)
    new_payload = _read_payload(new_path)
    old_rows, old_errors = _normalize_rows(old_payload["rows"])
    new_rows, new_errors = _normalize_rows(new_payload["rows"])
    if old_errors or new_errors:
        for message in old_errors + new_errors:
            print(f"[WARN] {message}", file=sys.stderr)
    if not old_rows or not new_rows:
        raise SyncError("快照 rows 为空，拒绝 diff")

    diff = diff_rows(old_rows, new_rows)
    source_text = source_path.read_text(encoding="utf-8")
    patched_text, patch_meta = build_type_names_patch(source_text, old_rows, new_rows)
    diff_path = "".join(
        difflib.unified_diff(
            source_text.splitlines(keepends=True),
            patched_text.splitlines(keepends=True),
            fromfile=_rel(source_path),
            tofile=_rel(source_path) + " (TYPE_NAMES patched)",
            n=3,
        )
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "old_snapshot": _snapshot_meta(old_payload, old_rows),
        "new_snapshot": _snapshot_meta(new_payload, new_rows),
        "baseline_mode": bool(args.baseline),
        "diff": diff,
        "type_names_patch": {
            "changed": patch_meta["changed"],
            "sections": patch_meta["sections"],
            "unknown_new_types": sorted(
                {r["factor_type"] for r in new_rows} - set(TYPE_BATCH)
            ),
        },
        "semantics": {
            "old": "旧快照只用于解释变更来源；patch 基线是当前源码 TYPE_NAMES。",
            "new": "新快照是目标集合；不在新快照中的源码名称会被移出 TYPE_NAMES。",
            "new_type": "出现未知 factor_type 时脚本拒绝生成补丁，不猜测 batch。",
        },
    }
    _write_json(out_dir / "diff.json", report)
    _write_json(
        out_dir / "type_names_target.json",
        {k: v for k, v in patch_meta["target"].items()},
    )
    (out_dir / "TYPE_NAMES.patch.diff").write_text(diff_path, encoding="utf-8")
    if patch_meta["changed"]:
        (out_dir / f"factor_lib_tushare.patched.{stamp}.py").write_text(
            patched_text, encoding="utf-8"
        )

    print(f"old: {_rel(old_path)}  total={diff['counts']['old']}")
    print(f"new: {_rel(new_path)}  total={diff['counts']['new']}")
    print(
        "change: added={added} removed={removed} moved={moved} unchanged={unchanged}".format(
            **diff["counts"], unchanged=diff["unchanged"]
        )
    )
    if diff["added"]:
        print("added:", ", ".join(f"{x['factor_name']}({x['factor_type']})" for x in diff["added"][:20]))
    if diff["removed"]:
        print("removed:", ", ".join(x["factor_name"] for x in diff["removed"][:20]))
    if diff["moved"]:
        print(
            "moved:",
            ", ".join(
                f"{x['factor_name']}:{x['old_type']}->{x['new_type']}"
                for x in diff["moved"][:20]
            ),
        )
    print(f"out: {_rel(out_dir)}")
    if patch_meta["changed"]:
        print(f"patch: {_rel(out_dir / 'TYPE_NAMES.patch.diff')}")
        for section in patch_meta["sections"]:
            print(
                "  {factor_type}: {before}->{after} (+{add} -{rem})".format(
                    factor_type=section["factor_type"],
                    before=section["before"],
                    after=section["after"],
                    add=len(section["added"]),
                    rem=len(section["removed"]),
                )
            )
    else:
        print("TYPE_NAMES 无变化，未生成补丁")

    if args.apply:
        if not patch_meta["changed"]:
            print("--apply: 无变化，跳过")
        else:
            backup = source_path.with_name(source_path.name + f".bak.{stamp}")
            backup.write_text(source_text, encoding="utf-8")
            source_path.write_text(patched_text, encoding="utf-8")
            print(f"applied: {_rel(source_path)}  backup={_rel(backup)}")
    if args.check and patch_meta["changed"]:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tushare factor_list 新旧快照 diff，并生成 factor_lib_tushare.py 的 TYPE_NAMES 补丁"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_diff = sub.add_parser("diff", help="diff 快照并生成 TYPE_NAMES.patch.diff")
    p_diff.add_argument("--old", help="旧快照 JSON/parquet；默认自动取第二新")
    p_diff.add_argument(
        "--baseline",
        help="CI 基线快照（通常从 main 导出）；显式指定时优先于 --old，二者互斥",
    )
    p_diff.add_argument("--new", help="新快照 JSON/parquet；默认 latest.json")
    p_diff.add_argument("--source", help="factor_lib_tushare.py 路径")
    p_diff.add_argument("--out-dir", help="输出目录")
    p_diff.add_argument("--apply", action="store_true", help="把补丁写回源码（会留 .bak）")
    p_diff.add_argument("--check", action="store_true", help="有补丁时退出码 1，供 CI 使用")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "diff":
            return run_diff(args)
        raise SyncError(f"未知命令: {args.command}")
    except SyncError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
