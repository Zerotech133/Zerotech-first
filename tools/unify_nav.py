# -*- coding: utf-8 -*-
"""前端入口统一治理（K-STOCK-100）· 第二件：静态页注入器 + 同步守卫。

inject：普通静态页幂等注入 nav.js（</head> 前），定型自导航页面不注入。
check：守卫——①普通页含 nav.js、定型页自身导航与返回入口有效且无旧顶栏
②nav.js 与 AST 重导出零 diff。

用法：
  python tools/unify_nav.py            # 注入 + 守卫
  python tools/unify_nav.py --check    # 仅守卫
  python tools/unify_nav.py --check --root <worktree>
                                       # 分支感知守卫（WO-NAVROOT-DEDUP-20261031）
"""
import ast
import glob
import json
import os
import re
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
    "home_v3.html": (r'<nav\s+class="nav"[^>]*aria-label="主导航"',
                     r'href="/home\.html"[^>]*>打开旧总览'),
}

def _self_nav_valid(name, src):
    nav, back = SELF_NAV_PAGES[name]
    # 校验真实标记，不接受注释里写了导航字符串就绕过守卫。
    active = re.sub(r"<!--[\s\S]*?-->", "", src)
    return (bool(re.search(nav, active)) and bool(re.search(back, active))
            and TAG not in active)


def _regen_js(root=None):
    """调 export_nav.py 的提取逻辑，返回应生成的 nav.js 内容。

    ``root`` 缺省为脚本自身所在仓库；分支感知门传 worktree 根时，加载的是该
    worktree 的 tools/export_nav.py，其 SRC 随之指向该 worktree 的
    portal/api_common.py（不是 master 的）。
    """
    root = root or REPO
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "export_nav", os.path.join(root, "tools", "export_nav.py"))
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


def _parse_root(argv):
    """解析 ``--root <path>``；缺省=脚本所在仓库（向后兼容）。"""
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    if i + 1 >= len(argv):
        raise SystemExit("--root 需要跟仓库/worktree 路径")
    root = os.path.abspath(argv[i + 1])
    if not os.path.isdir(os.path.join(root, "portal")):
        raise SystemExit("--root 不是仓库根（缺 portal/）：%s" % root)
    return root


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv
    root = _parse_root(argv)
    portal = os.path.join(root, "portal")
    pages = sorted(glob.glob(os.path.join(portal, "*.html")))
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
    navjs = open(os.path.join(portal, "static", "nav.js"),
                 encoding="utf-8").read()
    want = _regen_js(root)
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
