# -*- coding: utf-8 -*-
"""对 check_format_args 的 MISMATCH 逐个打印完整源码段，人工定真假。"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_format_args import scan  # noqa: E402

roots = sys.argv[1:] or ["quant_backtest", "portal", "news_collector",
                         "jev-eval", "agent_platform", "k_stock", "gzh",
                         "AI", "ops", "tools"]
BASE = os.path.normpath(os.path.join(os.path.dirname(
    os.path.abspath(__file__)), ".."))

for fp, lineno, nf, na, seg in scan(roots):
    full = os.path.join(BASE, fp)
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            src = f.read()
        tree = ast.parse(src)
        node = None
        for n in ast.walk(tree):
            if (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod)
                    and getattr(n, "lineno", None) == lineno):
                node = n
                break
        seg_txt = (ast.get_source_segment(src, node) or "")[:260]
    except Exception as exc:
        seg_txt = "<parse fail: %s>" % exc
    print("=== %s:%d fmt=%d args=%d" % (fp, lineno, nf, na))
    print(seg_txt.replace("\n", " ⏎ "))
    print()
