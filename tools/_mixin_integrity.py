# -*- coding: utf-8 -*-
"""P4 拆分第四道门槛：Handler 方法集 AST 断言（K-STOCK-089）。

用法：
  python tools/_mixin_integrity.py --baseline   # 首跑/重建基线（写 portal/mixin_baseline.json）
  python tools/_mixin_integrity.py --check      # 校验：基座+各 api_*.py Mixin 方法并集 == 基线

原理：搬代码不改逻辑 ⇒ Handler 方法名集合（含模块级函数可选）搬前搬后必须零 diff。
探针盲区（--probe-all 52 条打不全 112 方法）由此补上：多一个少一个都拒收。
"""
import ast
import glob
import json
import os
import sys

PORTAL = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "portal")
MAIN = os.path.join(PORTAL, "portal_api.py")
BASELINE = os.path.join(PORTAL, "mixin_baseline.json")


def _extract(path):
    """返回 (handler 方法名集合, 模块级函数名集合)。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    handlers, funcs = set(), set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    handlers.add(m.name)
        elif isinstance(node, ast.FunctionDef):
            funcs.add(node.name)
    return handlers, funcs


def collect():
    """基座 + 全部 api_*.py Mixin 的并集。"""
    hs, fs = _extract(MAIN)
    for f in sorted(glob.glob(os.path.join(PORTAL, "api_*.py"))):
        h2, f2 = _extract(f)
        hs |= h2
        fs |= f2
    return hs, fs


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    hs, fs = collect()
    if mode == "--baseline":
        json.dump({"handlers": sorted(hs), "funcs": sorted(fs)},
                  open(BASELINE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("baseline written: %d handler methods, %d module funcs"
              % (len(hs), len(fs)))
        return 0
    base = json.load(open(BASELINE, encoding="utf-8"))
    bh, bf = set(base["handlers"]), set(base["funcs"])
    lost, added = bh - hs, hs - bh
    lostf, addedf = bf - fs, fs - bf
    ok = True
    if lost:
        print("❌ 丢失 Handler 方法 %d:" % len(lost), sorted(lost)[:10])
        ok = False
    if added:
        print("⚠️ 新增 Handler 方法 %d:" % len(added), sorted(added)[:10])
    if lostf:
        print("❌ 丢失模块级函数 %d:" % len(lostf), sorted(lostf)[:10])
        ok = False
    if addedf:
        print("ℹ️ 新增模块级函数 %d:" % len(addedf), sorted(addedf)[:10])
    if ok:
        print("✅ INTEGRITY OK: %d methods / %d funcs 与基线零 diff"
              % (len(hs), len(fs)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
