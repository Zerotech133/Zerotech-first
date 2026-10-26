# -*- coding: utf-8 -*-
"""前端入口统一治理（K-STOCK-100）· 第二件：静态页注入器 + 同步守卫。

inject：普通静态页幂等注入 nav.js（</head> 前），定型自导航页面不注入。
check：守卫——①普通页含 nav.js、定型页自身导航与返回入口有效且无旧顶栏
②nav.js 与 AST 重导出零 diff。

用法：
  python tools/unify_nav.py            # 注入 + 守卫
  python tools/unify_nav.py --check    # 仅守卫
"""
import ast
import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(REPO, "portal")
TAG = '<script src="/static/nav.js" defer></script>'
ANCHOR = "</head>"
# 2026-10-24 导航裁定 A：仅这五页自带浅色导航；不注入深色全局顶栏。
# 每项须同时具备自身导航及返回入口，缺任一项即失败，不得泛化到目录。
SELF_NAV_PAGES = {
    "zero_proto.html": (r'<nav\s+class="seg"[^>]*aria-label="板块"',
                        r'id="back-screen"'),
    "os_council.html": (r'<div\s+class="nav"',
                         r'<a\s+href="/zero_proto\.html#council"[^>]*>\s*← 返回会议指挥'),
    "os_issues.html": (r'<div\s+class="links"',
                        r'id="issues-back"\s+href="/zero_proto\.html#system"'),
    "os_map.html": (r'<nav\s+class="seg"',
                     r'<a\s+href="/zero_proto\.html#system"[^>]*>\s*← 返回系统'),
    "zero_home.html": (r'<nav\s+class="nav"',
                       r'<a\s+href="/zero_proto\.html#product"[^>]*>\s*← 返回产品建设'),
}


def _self_nav_valid(name, src):
    nav, back = SELF_NAV_PAGES[name]
    # 校验真实标记，不接受注释里写了导航字符串就绕过守卫。
    active = re.sub(r"<!--[\s\S]*?-->", "", src)
    return (bool(re.search(nav, active)) and bool(re.search(back, active))
            and TAG not in active)


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
    # 仅普通静态页注入；定型页绝不能被注入旧深色顶栏。
    ordinary = [p for p in pages if os.path.basename(p) not in SELF_NAV_PAGES]
    missing = []
    for p in ordinary:
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
    # 守卫 ①：普通页必须有全局导航；列名例外必须有自身导航与返回入口，且无旧顶栏。
    still = [os.path.basename(p) for p in ordinary
             if TAG not in open(p, encoding="utf-8").read()]
    bad_self = [os.path.basename(p) for p in pages
                if os.path.basename(p) in SELF_NAV_PAGES and
                not _self_nav_valid(os.path.basename(p), open(p, encoding="utf-8").read())]
    if still or bad_self:
        if still:
            print("FAIL 缺 nav.js：%s" % still)
        if bad_self:
            print("FAIL 定型页自身导航/返回入口缺失或叠加旧顶栏：%s" % bad_self)
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
        print("FAIL nav.js 与 api_common.py 导航数据漂移——"
              "先跑 python tools/export_nav.py")
        return 1
    print("守卫 PASS：%d 普通页带统一顶栏，%d 定型页有自身导航及返回入口，nav.js 与源零漂移"
          % (len(ordinary), len(pages) - len(ordinary)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
