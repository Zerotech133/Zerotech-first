#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal_api do_GET/do_POST 路由表草案生成器（A5 B4/B5，2026-10-31 关羽）。

只读分析，**不改运行行为**：把 Handler.do_GET/do_POST 的 if/elif 分派解析为
「路由 → 处理目标（self._xxx/函数名）」草案，供 B4/B5 路由表化评审与迁移使用。

输出（默认）：
  --json        打印草案 JSON
  --json-out P  写草案 JSON 到指定路径
  --md          打印 markdown 摘要
统计会给出：exact/prefix 路由数、处理目标识别率、无法识别条目示例。

用法：
  python tools/portal_route_table_draft.py --json-out tools/portal_route_table_draft.json
  python tools/portal_route_table_draft.py --md
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "portal" / "portal_api.py"

_ROUTE_FIELDS = {"path", "self.path", "parsed.path", "u.path"}
_METHOD_PREFIX = "do_"


def _is_route(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "path"
    if isinstance(node, ast.Attribute):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts)) in _ROUTE_FIELDS
    return False


def _target_text(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name) and f.value.id == "self":
            return "self." + f.attr
        parts = []
        cur = f
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(f, ast.Name):
        return f.id
    return "<expr>"


def _extract_predicates(cond: ast.AST, out: list) -> None:
    """递归拆条件：== / in / startswith / endswith / or。"""
    if isinstance(cond, ast.BoolOp):
        for v in cond.values:
            _extract_predicates(v, out)
    elif isinstance(cond, ast.Compare) and len(cond.ops) == 1 and len(cond.comparators) == 1:
        left, op, right = cond.left, cond.ops[0], cond.comparators[0]
        if _is_route(left) and isinstance(right, ast.Constant) and isinstance(right.value, str):
            if isinstance(op, ast.Eq):
                out.append({"route": right.value, "kind": "exact"})
            elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.List, ast.Tuple, ast.Set)):
                for elt in right.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        out.append({"route": elt.value, "kind": "exact"})
        elif _is_route(right) and isinstance(left, ast.Constant) and isinstance(left.value, str) \
                and isinstance(op, ast.Eq):
            out.append({"route": left.value, "kind": "exact"})
    elif isinstance(cond, ast.Call) and isinstance(cond.func, ast.Attribute):
        if cond.func.attr in ("startswith", "endswith") and _is_route(cond.func.value) \
                and cond.args and isinstance(cond.args[0], ast.Constant) \
                and isinstance(cond.args[0].value, str):
            out.append({"route": cond.args[0].value,
                        "kind": "prefix" if cond.func.attr == "startswith" else "suffix"})


def _find_handler(stmts: list) -> str:
    """从分支体找第一个 self._xxx(...) 调用作为处理目标。"""
    for st in stmts:
        for node in ast.walk(st):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "self" and node.func.attr.startswith("_"):
                return _target_text(node)
    # 退而求其次：任意第一个调用
    for st in stmts:
        for node in ast.walk(st):
            if isinstance(node, ast.Call):
                return _target_text(node)
    return ""


def _parse_if_chain(node: ast.If, method: str, source: str, entries: list) -> None:
    preds: list = []
    _extract_predicates(node.test, preds)
    handler = _find_handler(node.body)
    entries.append({"method": method, "line": node.lineno,
                    "predicates": preds, "handler": handler,
                    "n_route": len([p for p in preds if p["kind"] in ("exact", "prefix", "suffix")])})
    if not preds:
        entries.append({"method": method, "line": node.lineno, "predicates": [],
                        "handler": handler, "kind": "unparsed", "n_route": 0})
    orelse = node.orelse
    if len(orelse) == 1 and isinstance(orelse[0], ast.If):
        _parse_if_chain(orelse[0], method, source, entries)


def build_draft(source: str) -> dict:
    tree = ast.parse(source)
    cls = None
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            cls = n
            break
    if cls is None:
        return {"ok": False, "error": "Handler class not found"}
    entries = []
    scanned = 0
    for m in cls.body:
        if not isinstance(m, ast.FunctionDef) or m.name not in ("do_GET", "do_POST"):
            continue
        method = m.name[3:]
        # 递归遍历方法体内所有 If（含 elif/嵌套分支），提取路由谓词
        for node in ast.walk(m):
            if not isinstance(node, ast.If):
                continue
            scanned += 1
            preds: list = []
            _extract_predicates(node.test, preds)
            if not preds:
                continue
            entries.append({"method": method, "line": node.lineno,
                            "predicates": preds,
                            "handler": _find_handler(node.body),
                            "n_route": len(preds)})
    exact = sum(1 for e in entries for p in e["predicates"] if p["kind"] == "exact")
    prefix = sum(1 for e in entries for p in e["predicates"] if p["kind"] == "prefix")
    has_handler = sum(1 for e in entries if e["handler"])
    routed = len(entries)
    unparsed = scanned - routed
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(DEFAULT_SOURCE),
        "stats": {
            "if_branches": routed,
            "scanned_ifs": scanned,
            "exact_routes": exact,
            "prefix_routes": prefix,
            "handler_identified": has_handler,
            "handler_identified_pct": round(has_handler * 100.0 / routed, 1) if routed else 0.0,
            "unparsed_branches": unparsed,
        },
        "unparsed_sample": [{"method": m.name, "line": None, "handler": ""}
                            for m in cls.body if isinstance(m, ast.FunctionDef)
                            and m.name in ("do_GET", "do_POST")][:0],
        "entries": entries,
    }


def render_md(rep: dict) -> str:
    st = rep.get("stats") or {}
    lines = ["# portal_api 路由表草案（A5 B4/B5）", "",
             "> 自动生成：`python tools/portal_route_table_draft.py --md`",
             "> 生成时间：%s  |  只读分析，未改运行行为" % rep.get("generated_at"), "",
             "## 统计", "",
             "| 项 | 值 |", "|---|---:|",
             "| if/elif 分支 | %d |" % st.get("if_branches", 0),
             "| exact 路由条件 | %d |" % st.get("exact_routes", 0),
             "| prefix 路由条件 | %d |" % st.get("prefix_routes", 0),
             "| 处理目标识别率 | %.1f%% |" % st.get("handler_identified_pct", 0.0),
             "| 未解析分支 | %d |" % st.get("unparsed_branches", 0), ""]
    if rep.get("unparsed_sample"):
        lines += ["## 未解析分支（需人工判定，保留原代码块）", ""]
        for e in rep["unparsed_sample"]:
            lines.append("- %s L%d → `%s`" % (e["method"], e["line"], e["handler"] or "?"))
        lines.append("")
    lines += ["## 迁移批次建议（草案）", "",
              "1. **B4a 精确路由（无动态段）**：按 handler 分组迁入表（页面/API 两类），保留原顺序语义；",
              "2. **B4b 前缀/OR 条件**：转成有序前缀规则列表（先长后短，保持原有优先级）；",
              "3. **B5 POST 表化**：与 B4a/B4b 同法，先做等价测试再切；",
              "4. 其余动态/含逻辑分支保持原 if/elif（表项值可为 `callable` 或 `None` 表示回退原分支）。", ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="portal_api 路由表草案生成器（只读）")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)
    src = Path(args.source)
    if not src.is_file():
        print("source 不存在: %s" % src, file=sys.stderr)
        return 1
    rep = build_draft(src.read_text(encoding="utf-8"))
    if not rep.get("ok"):
        print("ERROR:", rep.get("error"), file=sys.stderr)
        return 1
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("json -> %s" % out)
    if args.md:
        print(render_md(rep))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    if not (args.json or args.md or args.json_out):
        st = rep["stats"]
        print("portal_route_table_draft: branches=%d exact=%d prefix=%d handler_pct=%.1f%% unparsed=%d"
              % (st["if_branches"], st["exact_routes"], st["prefix_routes"],
                 st["handler_identified_pct"], st["unparsed_branches"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
