# -*- coding: utf-8 -*-
"""P4 批 0 预演（K-STOCK-096）：在 git archive 副本里演练 api_common.py
helper 下沉，验证搬迁流程——工作区零接触。

流程：git archive HEAD → 副本 → AST 闭包解析切出目标定义 → 生成
api_common.py → portal_api.py 切除并插 import → py_compile + import
冒烟 + 方法集 AST 断言。

用法：python tools/p4_batch0_rehearsal.py   （产物全在 %TEMP%，可随时删）
"""
import ast
import os
import subprocess
import sys
import tarfile
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_FUNCS = ["_log_rotate", "_portal_token", "_load_cainiao_env", "log_exc",
              "pill_nav", "_back_link_html", "_tabs_html", "_read_json",
              "_env_market_key"]
# 注：_grp 为嵌套函数（随宿主 pill_nav 整体搬迁），不列 seed。
COMMON_IMPORTS = ("import json\nimport os\nimport secrets\nimport sys\n"
                  "import time\nimport traceback\n\n"
                  "# 副本内与 portal_api.py 的 ROOT 同构（junction 双路径一致）\n"
                  "ROOT = os.path.dirname(os.path.abspath(__file__))\n\n")


def _top_defs(tree):
    """顶层可搬定义：name -> ast node（FunctionDef/Assign）。"""
    defs = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = node
    return defs


def _free_names(node):
    import builtins
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            names.add(n.id)
    return {n for n in names if not hasattr(builtins, n)}


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="p4_batch0_")
    tar_path = os.path.join(tmp, "a.tar")
    # 只 archive portal/（目标文件全 ASCII 路径，绕开 docs/gzh 中文文件名
    # 在 Windows tar 的 "Invalid empty pathname" 坑）；解包用 Python tarfile
    # 指定 utf-8，双保险。
    subprocess.run(["git", "archive", "HEAD", "portal/", "-o", tar_path],
                   cwd=REPO, check=True)
    with tarfile.open(tar_path, encoding="utf-8",
                      errors="surrogateescape") as tf:
        tf.extractall(tmp, filter="data")
    rep = os.path.join(tmp, "portal")
    print("副本：", tmp)

    main_path = os.path.join(rep, "portal_api.py")
    src = open(main_path, encoding="utf-8").read()
    tree = ast.parse(src)
    defs = _top_defs(tree)

    # 闭包解析：seed → 自由名命中已定义符号 → 递增
    needed = set(SEED_FUNCS)
    changed = True
    while changed:
        changed = False
        for name in list(needed):
            node = defs.get(name)
            if node is None:
                continue
            for free in _free_names(node):
                if free in defs and free not in needed:
                    needed.add(free)
                    changed = True
    missing = [n for n in SEED_FUNCS if n not in defs]
    assert not missing, "seed 缺失: %s" % missing
    print("闭包解析：搬迁 %d 个顶层定义" % len(needed))

    # 切片（按行区间，源串拼接保持原文本）
    segs, cuts = [], []
    for name in sorted(needed):
        node = defs[name]
        segs.append((node.lineno, node.end_lineno,
                     ast.get_source_segment(src, node)))
        cuts.append((node.lineno, node.end_lineno))
    segs.sort()
    cuts.sort()

    common = COMMON_IMPORTS + "\n\n".join(s for _, _, s in segs) + "\n"
    open(os.path.join(rep, "api_common.py"), "w", encoding="utf-8").write(
        common)

    # 主文件切除 + 插 import（倒序删行）
    lines = src.splitlines(keepends=True)
    for a, b in reversed(cuts):
        del lines[a - 1:b]
    out = "".join(lines)
    anchor = "import json"
    out = out.replace(anchor,
                      "from api_common import (  # P4 批0：helper 下沉\n    " +
                      ",\n    ".join(sorted(needed)) + ",\n)", 1)
    open(main_path, "w", encoding="utf-8").write(out)

    # 验证：双文件编译 + 副本 import + AST 方法集不变
    import py_compile
    py_compile.compile(main_path, doraise=True)
    py_compile.compile(os.path.join(rep, "api_common.py"), doraise=True)
    print("py_compile：双文件 OK")

    sys.path.insert(0, rep)
    import importlib
    mod = importlib.import_module("portal_api")
    from portal_api import Handler  # noqa
    print("副本 import portal_api OK（PORT=%s）" % mod.PORT)

    new_tree = ast.parse(out)
    before = {n.name for n in tree.body if isinstance(n, ast.ClassDef)
              and n.name == "Handler"
              for n in n.body if isinstance(n, ast.FunctionDef)}
    # 上面的 compreh 写法不易读，展开重取
    before = set()
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            before = {m.name for m in n.body
                      if isinstance(m, ast.FunctionDef)}
    after = set()
    for n in new_tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            after = {m.name for m in n.body if isinstance(m, ast.FunctionDef)}
    assert before == after, "Handler 方法集漂移！"
    for name in sorted(needed):
        assert hasattr(mod, name), "模块级符号丢失: " + name
    print("AST 断言：Handler %d 方法零漂移，模块级符号 %d 个全在"
          % (len(after), len(needed)))
    print("批 0 预演 PASS · 耗时 %.1fs · 副本可查 %s" % (time.time() - t0, tmp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
