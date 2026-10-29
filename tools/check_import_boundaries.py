# -*- coding: utf-8 -*-
"""导入边界联动检查：业务模块的新增 import 必须仍落在对应单测白名单内。

动机（2026-10-28）：`osleg_consumer` 新增 `logging` 后，白名单测试
`test_source_imports_no_network_or_heavy_dependencies` 滞后失败（全量测试红）。

实现：
- BOUNDARIES 登记「模块 ↔ 白名单测试」，从测试源码 AST 中提取
  `assert roots <= { ... }` 的字面量集合；
- 解析模块所有顶层 import 根名，检查 ⊆ 白名单；
- 测试源码里 `roots <=` 出现但未登记边界 → WARN（提示补登记，不阻断）。
用法：python tools/check_import_boundaries.py [--json]
退出码：0=一致 / 1=不一致 / 2=环境异常。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BOUNDARIES = [
    {
        "module": "agent_platform/gateway/osleg_consumer.py",
        "test": "agent_platform/tests/test_osleg_consumer.py",
        "reason": "osleg 消费器隔离红线：仅允许 stdlib 轻量依赖（禁网络/重依赖）",
    },
]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _allowed_from_test(path: Path) -> set[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
            continue
        left = test.left
        lname = left.id if isinstance(left, ast.Name) else (
            left.attr if isinstance(left, ast.Attribute) else "")
        comp = test.comparators[0]
        if lname == "roots" and isinstance(comp, ast.Set):
            vals = {el.value for el in comp.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)}
            if vals:
                return vals
    return None


def check(boundaries: list[dict] | None = None, base: Path | None = None) -> dict:
    base = Path(base) if base is not None else REPO
    specs = boundaries if boundaries is not None else BOUNDARIES
    issues: list[str] = []
    warnings: list[str] = []
    checked = []
    for spec in specs:
        mod = base / spec["module"]
        tst = base / spec["test"]
        if not mod.exists() or not tst.exists():
            warnings.append("boundary_files_missing:%s" % spec["module"])
            continue
        try:
            roots = _import_roots(mod)
            allowed = _allowed_from_test(tst)
        except SyntaxError as e:
            issues.append("parse_error:%s:%s" % (spec["module"], e))
            continue
        if allowed is None:
            warnings.append("whitelist_not_found:%s" % spec["test"])
            continue
        extra = sorted(roots - allowed)
        checked.append({"module": spec["module"], "imports": sorted(roots),
                        "allowed": sorted(allowed), "extra": extra})
        if extra:
            issues.append("import_not_in_whitelist:%s:%s" % (spec["module"], ",".join(extra)))
    # 未登记的边界测试提示
    for t in base.glob("**/test_*.py"):
        rel = t.relative_to(base).as_posix()
        if any(x in rel for x in (".worktrees/", "__pycache__/", "Hermes/", "node_modules/")):
            continue
        if rel.endswith("test_import_boundaries.py"):
            continue  # 本工具自身的夹具字符串不算边界测试
        try:
            text = t.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "assert roots <=" in text and not any(s["test"] == rel for s in specs):
            warnings.append("unregistered_boundary_test:%s" % rel)
    return {"tool": "check_import_boundaries", "code": 1 if issues else 0,
            "checked": checked, "issues": issues, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导入边界联动检查")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo", default="", help="仓库/工作树根（默认本工具所在仓）")
    args = ap.parse_args(argv)
    res = check(base=Path(args.repo) if args.repo else None)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for c in res["checked"]:
            print("边界 %-42s imports=%s extra=%s" % (
                c["module"], ",".join(c["imports"]), c["extra"] or "-"))
        for i in res["issues"]:
            print("  FAIL", i)
        for w in res["warnings"][:10]:
            print("  WARN", w)
        print("result:", "PASS" if res["code"] == 0 else "FAIL")
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
