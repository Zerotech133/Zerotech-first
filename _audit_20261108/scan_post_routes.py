# -*- coding: utf-8 -*-
"""枚举 portal 的 POST 路由，核对鉴权白名单覆盖率（审计用）。

背景：
  portal_api.do_POST 的鉴权是「列举式」——
      if path in _MUTATION_POSTS and not self._check_portal_token(): 403
      if path.startswith(_EARLY_TOKEN_PREFIXES) and not ...: 403
  即**默认放行**。新增一个写端点但忘记登记白名单，就是一个未鉴权写入口。
  正确形态应是「默认拒绝 + 白名单例外」。

本脚本用 AST 提取各 mixin 中 do_POST/handle_post 分支里出现的路径常量，
与两份白名单比对，输出未覆盖清单供人工确认。

用法：python _audit_20261108/scan_post_routes.py
"""

import ast
import json
import os
import sys

ROOT = r"C:\项目\老田的AI助手"
PORTAL = os.path.join(ROOT, "portal")
MAIN = os.path.join(PORTAL, "portal_api.py")


def _const_strs(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if v.startswith("/") and len(v) > 1 and " " not in v:
                out.append(v)
    return out


def _whitelist():
    tree = ast.parse(open(MAIN, encoding="utf-8").read())
    mut, early, action = [], [], []
    for n in tree.body:
        if isinstance(n, ast.Assign) and n.targets:
            t = n.targets[0]
            if not isinstance(t, ast.Name):
                continue
            if t.id in ("_MUTATION_POSTS", "_EARLY_TOKEN_PREFIXES", "_ACTION_GETS"):
                vals = [s.value for s in ast.walk(n.value)
                        if isinstance(s, ast.Constant) and isinstance(s.value, str)]
                {"_MUTATION_POSTS": mut, "_EARLY_TOKEN_PREFIXES": early,
                 "_ACTION_GETS": action}[t.id].extend(vals)
    return set(mut), tuple(early), set(action)


def _mixin_modules():
    """从 portal_api.Handler 的基类列表里提取真实 mixin 模块名。

    2026-11-08 审计自纠：首版扫了所有 api_*.py，把 api_model_admin.py（独立
    跑在 :8098，不在 Handler 继承链上）也算进来，产生 5 条误报。只有被
    Handler 多继承的模块，其 do_POST 才真正挂在 8091 的路由上。
    """
    src = open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            for base in n.bases:
                d = ast.unparse(base)
                if ".Handler" in d:
                    mods.add(d.split(".Handler")[0])
            break
    return mods


def main():
    mut, early, action = _whitelist()
    mixins = _mixin_modules()
    print("[white] _MUTATION_POSTS=%d  _EARLY_TOKEN_PREFIXES=%d  _ACTION_GETS=%d"
          % (len(mut), len(early), len(action)))
    print("[mixins] Handler 真实继承 %d 个模块 + portal_api 本体" % len(mixins))

    found = {}
    for fn in sorted(os.listdir(PORTAL)):
        if not fn.endswith(".py"):
            continue
        stem = fn[:-3]
        if stem != "portal_api" and stem not in mixins:
            continue
        path = os.path.join(PORTAL, fn)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as e:
            print("  SKIP(syntax) %s: %s" % (fn, e))
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for f in cls.body:
                if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not (f.name == "do_POST" or f.name.startswith(("handle_post",
                                                                   "_post", "do_PUT"))):
                    continue
                for s in _const_strs(f):
                    found.setdefault(s, set()).add("%s::%s.%s" % (fn, cls.name, f.name))

    def covered(p):
        if p in mut:
            return "mutation"
        for pref in early:
            if p.startswith(pref):
                return "early_prefix"
        return ""

    rows, uncovered = [], []
    for p in sorted(found):
        c = covered(p)
        rows.append({"path": p, "covered": c, "handlers": sorted(found[p])})
        if not c:
            uncovered.append(p)

    out = os.path.join(ROOT, "_audit_20261108", "post_routes.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"whitelist": {"mutation": sorted(mut), "early": list(early),
                                 "action_gets": sorted(action)},
                   "routes": rows}, fh, ensure_ascii=False, indent=1)

    print("[scan] POST/PUT 分支路径常量 %d 个，其中未命中白名单 %d 个"
          % (len(rows), len(uncovered)))
    print()
    print("未命中白名单的路径（需人工确认是否在 handler 内部自校验）：")
    for p in uncovered:
        print("   %-46s %s" % (p, ", ".join(sorted(found[p])[:2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
