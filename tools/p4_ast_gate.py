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
import builtins
import json
import re
import sys

# 内建白名单：builtins 模块全部名字 + 少量模块级魔法名（运行时解释器保证存在）
BUILTIN_NAMES = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__debug__"}


# ---------------- resolve 模式（v3）：跨模块名字静态解析 ----------------

def collect_bound_names_in_scope(node):
    """收集某作用域节点（函数/模块）内的局部绑定名：
    函数参数、赋值左端、for/with/except 目标、walrus、推导式目标、局部 import、
    嵌套 def/class 名、global/nonlocal 声明。"""
    bound = set()

    def add_target(t):
        if isinstance(t, ast.Name):
            bound.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                add_target(e)
        elif isinstance(t, ast.Starred):
            add_target(t.value)
        # ast.Attribute / ast.Subscript：绑定的是属性/容器元素，不产生名字

    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                add_target(t)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            if n.target:
                add_target(n.target)
        elif isinstance(n, ast.NamedExpr):
            add_target(n.target)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            add_target(n.target)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars:
                    add_target(item.optional_vars)
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                bound.add(n.name)
        elif isinstance(n, ast.comprehension):
            add_target(n.target)
        elif isinstance(n, ast.Global):
            bound.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            bound.update(n.names)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name == "*":
                    continue
                bound.add(a.asname if a.asname else a.name.split(".")[0])
    return bound


def collect_module_names(tree):
    """收集模块级名字集 + import* 标记。
    返回 (names:set, has_star_import:bool, star_modules:list)。"""
    names = set()
    has_star = False
    star_modules = []

    def add_target(t):
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                add_target(e)
        elif isinstance(t, ast.Starred):
            add_target(t.value)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            # 装饰器名字同样要参与解析（如 @app.route 里的 app）
            for dec in node.decorator_list:
                for n in ast.walk(dec):
                    if isinstance(n, ast.Name):
                        names.add(n.id)  # 装饰器里引用的名字先并入已知集，稍后单独校验
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                add_target(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            add_target(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            add_target(node.target)
        elif isinstance(node, ast.If):  # 模块级 if 分支里的赋值/导入也算
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        add_target(t)
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        if a.name == "*":
                            has_star = True
                            star_modules.append(sub.module or "?")
                        else:
                            names.add(a.asname if a.asname else a.name.split(".")[0])
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    add_target(item.optional_vars)
        elif isinstance(node, ast.Try):
            for sub in node.body + [h for h in node.handlers] + node.orelse + node.finalbody:
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        add_target(t)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        names.add(a.asname if a.asname else a.name.split(".")[0])
            for h in node.handlers:
                if h.name:
                    names.add(h.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname if a.asname else a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    has_star = True
                    star_modules.append(node.module or "?")
                else:
                    names.add(a.asname if a.asname else a.name)

    return names, has_star, star_modules


def resolve_module(path, module_names_pool):
    """对单个模块做名字解析。
    module_names_pool: 所有参与组合的模块的名字并集（定义 + 导入），
                       拆分后跨模块引用（新模块用基座名、基座用新模块名）都算可解析。
    返回 (failures, warns, checked_count, has_star, star_modules)
      failures: (file, lineno, func, name) 列表 —— 非 star 模块中无法解析的名字
      warns:    同结构 —— star import 模块中无法解析的名字（降级 WARN）
    """
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=path)

    mod_names, has_star, star_modules = collect_module_names(tree)
    # 判定只用「本模块定义/导入集」：star import 不展开具体名，跨模块名字
    # （尤其下划线名）不算可解析 —— 这正是 import * 的运行时陷阱所在
    known = mod_names
    failures = []
    warns = []
    checked = 0

    def check_func(func_node, func_label):
        nonlocal checked
        # 函数内局部绑定（参数/赋值/推导式/except 等）
        local = collect_bound_names_in_scope(func_node)
        # 装饰器里的 Name 也参与解析（@app / @cache 之类）
        dec_names = [n.id for n in ast.walk(func_node)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
        for name in dec_names:
            checked += 1
            if name not in known and name not in local and name not in BUILTIN_NAMES:
                rec = (path, func_node.lineno, func_label, name)
                if has_star:
                    warns.append(rec)
                else:
                    failures.append(rec)
        # 函数体内所有 Name Load
        for n in ast.walk(func_node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                checked += 1
                if (n.id not in known and n.id not in local
                        and n.id not in BUILTIN_NAMES):
                    rec = (path, n.lineno, func_label, n.id)
                    if has_star:
                        warns.append(rec)
                    else:
                        failures.append(rec)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_func(node, node.name)
        elif isinstance(node, ast.ClassDef):
            # 类体内方法
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check_func(sub, "%s.%s" % (node.name, sub.name))

    return failures, warns, checked, has_star, star_modules


def cmd_resolve(args):
    """子命令 resolve：跨模块名字静态解析（定义/导入/内建白名单），star import 降级 WARN。"""
    all_paths = list(args.modules) + [args.base]
    # 1) 先收集组合内全部模块的名字并集（定义 + 导入带入）
    pool = set()
    trees = {}
    try:
        for p in all_paths:
            with open(p, "r", encoding="utf-8") as f:
                trees[p] = ast.parse(f.read(), filename=p)
    except (SyntaxError, OSError, UnicodeDecodeError) as e:
        print("[GATE-ERROR] 文件解析失败: %s -> %s" % (p, e), file=sys.stderr)
        sys.exit(2)
    for p, t in trees.items():
        names, _, _ = collect_module_names(t)
        pool |= names

    # 2) 逐模块解析函数体内 Name Load
    total_checked = 0
    all_fail = []
    all_warn = []
    for p in all_paths:
        fails, warns, checked, has_star, star_mods = resolve_module(p, pool)
        total_checked += checked
        all_fail.extend(fails)
        all_warn.extend(warns)
        if has_star:
            print("[WARN] %s 含 import * (%s) —— 其函数体内无法静态确认的名字降级为 WARN"
                  % (p, ",".join(star_mods)))

    if all_warn:
        uniq_warn = sorted(set(all_warn))
        print("[WARN] star import 模块未解析名 %d 个（运行时 NameError 陷阱，施工时必须显式导入）:" % len(uniq_warn))
        for (f, ln, fn, nm) in uniq_warn:
            print("  [WARN] %s:%d %s -> %s" % (f, ln, fn, nm))
    if all_fail:
        print("[FAIL] 未解析名字 %d 个:" % len(all_fail))
        for (f, ln, fn, nm) in sorted(all_fail):
            print("  模块 %s:%d %s -> %s" % (f, ln, fn, nm))
        print("RESOLVE FAIL: %d unresolved (checked %d names)" % (len(all_fail), total_checked))
        sys.exit(1)
    else:
        print("RESOLVE PASS (%d names checked, %d star-import warns)" % (total_checked, len(all_warn)))
        sys.exit(0)


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

    def collect_body_path_literals(func_node):
        """收集函数体内所有以 '/' 开头的字符串字面量（自建 HTTP Server 的
        do_GET/do_POST/do_PUT/do_DELETE 等 do_* 方法没有装饰器，路由是
        `if path == "/xxx"` / `path.startswith("/yyy")` 这类分支字面量）。"""
        found = set()
        for n in ast.walk(func_node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                s = n.value.strip()
                if s.startswith("/"):
                    found.add(s)
        return found

    for node in tree.body:
        # a) top-level 函数：收集函数本体上的装饰器路由
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
            for dec in node.decorator_list:
                routes.update(extract_strings_from_decorator(dec))
            # do_* 一族方法体：路径字面量也算路由面
            if re.fullmatch(r"do_[A-Za-z]+", node.name):
                routes.update(collect_body_path_literals(node))
        # b) top-level 类：类本身装饰器 + 类下方法
        elif isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                routes.update(extract_strings_from_decorator(dec))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.add("%s.%s" % (node.name, sub.name))
                    for dec in sub.decorator_list:
                        routes.update(extract_strings_from_decorator(dec))
                    # 类内 do_* 方法（如 handler 类的 do_GET/do_POST）同样纳入
                    if re.fullmatch(r"do_[A-Za-z]+", sub.name):
                        routes.update(collect_body_path_literals(sub))

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
    # 向后兼容：旧基线无路由字段时视为空集（.get 默认 []）
    base_routes = set(base.get("__union__", {}).get("routes", []))
    base_funcs = set(base.get("__union__", {}).get("funcs", []))

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

    ap_res = sub.add_parser("resolve", help="跨模块名字静态解析（定义/导入/内建白名单）")
    ap_res.add_argument("--modules", nargs="+", required=True, help="拆分出的新模块文件列表")
    ap_res.add_argument("--base", required=True, help="基座文件（如 portal_api.py 副本）")
    ap_res.set_defaults(func=cmd_resolve)

    ap_chk = sub.add_parser("check", help="校验拆分后文件集合 vs 基线（并集视角）")
    ap_chk.add_argument("--baseline", required=True, help="基线 JSON 路径")
    ap_chk.add_argument("--files", nargs="+", required=True, help="拆分后的文件列表")
    ap_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
