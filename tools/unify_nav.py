# -*- coding: utf-8 -*-
"""前端入口统一治理（K-STOCK-100）· 第二件：静态页注入器 + 同步守卫。

inject：把统一顶栏 nav.js 幂等注入 portal 下全部静态 .html（</head> 前）。
check：守卫——①全部静态页含 nav.js 引用 ②nav.js 与 AST 重导出零 diff。

用法：
  python tools/unify_nav.py            # 注入 + 守卫
  python tools/unify_nav.py --check    # 仅守卫
"""
import ast
import glob
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(REPO, "portal")
TAG = '<script src="/static/nav.js" defer></script>'
ANCHOR = "</head>"


def _regen_js():
    """调 export_nav.py 的提取逻辑，返回应生成的 nav.js 内容。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "export_nav", os.path.join(REPO, "tools", "export_nav.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tree = ast.parse(open(m.SRC, encoding="utf-8").read())
    data = {
        "sections": [[k, lb, h]
                     for k, lb, h in m._extract_literal(tree, "SECTIONS")],
        "groups": {k: [[glb, [[lb, href, key] for lb, href, key in its]]
                       for glb, its in g]
                   for k, g in m._extract_literal(
                       tree, "DOMAIN_NAV_GROUPS").items()},
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def main():
    check_only = "--check" in sys.argv
    pages = sorted(glob.glob(os.path.join(PORTAL, "*.html")))
    pages = [p for p in pages
             if ANCHOR in open(p, encoding="utf-8").read()]  # 片段页跳过
    # 注入
    missing = []
    for p in pages:
        src = open(p, encoding="utf-8").read()
        if TAG in src:
            continue
        missing.append(os.path.basename(p))
        if not check_only:
            assert ANCHOR in src, p + " 无 </head>"
            src = src.replace(ANCHOR, TAG + ANCHOR, 1)
            open(p, "w", encoding="utf-8").write(src)
    if missing and not check_only:
        print("已注入 %d 页：%s" % (len(missing), ", ".join(missing)))
    # 守卫 ①：全部含引用
    still = [os.path.basename(p) for p in pages
             if TAG not in open(p, encoding="utf-8").read()]
    if still:
        print("FAIL 缺 nav.js：%s" % still)
        return 1
    # 守卫 ②：nav.js 与源同步（结构化比对，键序无关）
    navjs = open(os.path.join(PORTAL, "static", "nav.js"),
                 encoding="utf-8").read()
    want = _regen_js()
    i = navjs.find("var NAV=")
    j = navjs.find(";\n", i)
    assert i >= 0 and j > i, "nav.js 里找不到 NAV 数据块"
    got = json.loads(navjs[i + len("var NAV="):j])
    want = json.loads(want)  # _regen_js 返回 JSON 串，解析后结构比对
    if got != want:
        print("FAIL nav.js 与 portal_api.py 导航数据漂移——"
              "先跑 python tools/export_nav.py")
        return 1
    print("守卫 PASS：%d 静态页全带统一顶栏，nav.js 与源零漂移"
          % len(pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
