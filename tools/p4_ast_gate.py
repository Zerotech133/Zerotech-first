#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p4_ast_gate.py —— P4 拆分的 AST 断言门（K-STOCK-089 第四道门落地工具）

用途：
    portal/portal_api.py（约 2.4 万行）P4 计划将其拆分为多个模块（api_common、
    data_sync 等）。拆分是"搬法"——方法从此文件搬到彼文件，但对外：
      1) API 路由面（route surface）必须不变；
      2) 方法总集（所有 top-level 函数 + 类方法的并集）必须不变。
    本工具通过 ast 模块静态解析，断言上述两点，防止拆分时误删/漏搬。

用法示例：
    # 1) 做基线快照（拆分前）
    python tools/p4_ast_gate.py snapshot --files portal/portal_api.py --out tools/p4_baseline.json

    # 2) 拆分后校验（对比文件集合与基线的并集）
    python tools/p4_ast_gate.py check --baseline tools/p4_baseline.json --files portal/api_common.py portal/data_sync.py ...

判定规则：
    - 路由：收集装饰器参数中所有以 "/" 开头的字符串常量（@app.route("/x"...)、
      @router.get("/x") 等形式通吃）。
      基线有现在无 → FAIL(rc=1)；现在有基线无 → WARN 不挡道。
    - 函数/方法：top-level 函数名 + 类名.方法名，按**并集**对比。
      基线有现在无 → FAIL(rc=1)；新增 → WARN。

退出码：0=PASS；1=有缺失（路由或函数/方法）；2=文件解析失败。

仅依赖标准库（ast/json/argparse/re/sys）。严禁修改生产文件，本工具只读。
"""

import argparse
import ast
import json
import re
import sys


def extract_strings_from_decorator(dec):
    """从一个装饰器节点里收集所有以 '/' 开头的字符串常量（即路由路径）。"""
    routes = []
    targets = []

    if isinstance(dec, ast.Call):
        # @app.route("/x", methods=[...]) / @router.get("/x") 等调用形式
        targets.extend(dec.args)
        for kw in dec.keywords:
            # methods=["GET"] 之类也可能含字符串，但以 "/" 开头的才算路由
            targets.append(kw.value)
    elif isinstance(dec, (ast.Attribute, ast.Name)):
        # 无参装饰器形式（罕见），无字符串可收集
        return routes
    else:
        targets.append(dec)

    def walk(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if s.startswith("/"):
                routes.append(s)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.elts:
                walk(elt)

    for t in targets:
        walk(t)
    return routes


def parse_file(path):
    """
    解析单个文件，返回 (routes:set, funcs:set)。
    routes: 路由路径字符串集合
    funcs:  top-level 函数名 + '类名.方法名' 集合
    解析失败抛异常，由调用方统一报错退出 rc=2。
    """
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=path)

    routes = set()
    funcs = set()

    for node in tree.body:
        # a) top-level 函数：收集函数本体上的装饰器路由
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
            for dec in node.decorator_list:
                routes.update(extract_strings_from_decorator(dec))
        # b) top-level 类：类本身装饰器 + 类下方法
        elif isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                routes.update(extract_strings_from_decorator(dec))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.add("%s.%s" % (node.name, sub.name))
                    for dec in sub.decorator_list:
                        routes.update(extract_strings_from_decorator(dec))

    return routes, funcs


def load_files(paths):
    """解析文件集合，按并集聚合。任一文件解析失败 → 报错退出 rc=2。"""
    all_routes = set()
    all_funcs = set()
    for p in paths:
        try:
            r, f = parse_file(p)
        except (SyntaxError, OSError, UnicodeDecodeError) as e:
            print("[GATE-ERROR] 文件解析失败: %s -> %s" % (p, e), file=sys.stderr)
            sys.exit(2)
        all_routes |= r
        all_funcs |= f
    return all_routes, all_funcs


def cmd_snapshot(args):
    """子命令 snapshot：生成 JSON 基线。"""
    all_routes, all_funcs = load_files(args.files)
    baseline = {}
    # 每个文件单独记录（便于看分布），整体并集由 check 端重算
    for p in args.files:
        r, f = parse_file(p)
        baseline[p] = {"routes": sorted(r), "funcs": sorted(f)}
    # 顶层附一份并集，check 实际以此为准（多文件并集视角）
    out = {"__union__": {"routes": sorted(all_routes), "funcs": sorted(all_funcs)}, "files": baseline}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("[snapshot] 文件数=%d 路由=%d 条 函数/方法=%d 个" % (
        len(args.files), len(all_routes), len(all_funcs)))
    for p in args.files:
        r, f = parse_file(p)
        print("  - %s: 路由 %d / 函数+方法 %d" % (p, len(r), len(f)))
    print("[snapshot] 基线已写出: %s" % args.out)


def cmd_check(args):
    """子命令 check：拆分后文件集合 vs 基线并集对比。"""
    with open(args.baseline, "r", encoding="utf-8") as f:
        base = json.load(f)
    base_routes = set(base["__union__"]["routes"])
    base_funcs = set(base["__union__"]["funcs"])

    cur_routes, cur_funcs = load_files(args.files)

    missing_routes = sorted(base_routes - cur_routes)
    new_routes = sorted(cur_routes - base_routes)
    missing_funcs = sorted(base_funcs - cur_funcs)
    new_funcs = sorted(cur_funcs - base_funcs)

    fail = False
    if missing_routes:
        fail = True
        print("[FAIL] 路由缺失 %d 条:" % len(missing_routes))
        for r in missing_routes:
            print("  - %s" % r)
    if missing_funcs:
        fail = True
        print("[FAIL] 函数/方法缺失 %d 个:" % len(missing_funcs))
        for fn in missing_funcs:
            print("  - %s" % fn)
    if new_routes:
        print("[WARN] 路由新增 %d 条（不挡道）:" % len(new_routes))
        for r in new_routes:
            print("  + %s" % r)
    if new_funcs:
        print("[WARN] 函数/方法新增 %d 个（不挡道）:" % len(new_funcs))
        for fn in new_funcs:
            print("  + %s" % fn)

    if fail:
        print("AST GATE FAIL: %d missing routes, %d missing funcs" % (
            len(missing_routes), len(missing_funcs)))
        sys.exit(1)
    else:
        print("AST GATE PASS (routes=%d funcs=%d)" % (len(cur_routes), len(cur_funcs)))
        sys.exit(0)


def main():
    ap = argparse.ArgumentParser(description="P4 拆分 AST 断言门：路由面 + 方法总集基线/校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_snap = sub.add_parser("snapshot", help="生成基线快照 JSON")
    ap_snap.add_argument("--files", nargs="+", required=True, help="待快照的源文件列表")
    ap_snap.add_argument("--out", required=True, help="基线输出路径")
    ap_snap.set_defaults(func=cmd_snapshot)

    ap_chk = sub.add_parser("check", help="校验拆分后文件集合 vs 基线（并集视角）")
    ap_chk.add_argument("--baseline", required=True, help="基线 JSON 路径")
    ap_chk.add_argument("--files", nargs="+", required=True, help="拆分后的文件列表")
    ap_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
