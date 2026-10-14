# -*- coding: utf-8 -*-
"""格式串占位符 vs 参数数 AST 静态校验（W9 门禁，K-STOCK-393）。

背景：premarket.build_doc_md 政策新闻行"7 占位符对 6 参数"潜伏 bug
致盘前镜像文档连崩 3 天（K-STOCK-389 修复）。本工具全仓扫描
`"格式串" % (...)` 调用，静态比对占位符个数与实参个数，杜绝第三处潜伏。

只处理可静态判定的情况：
  left = 常量字符串、right = 元组字面量（元素定长、无 ** 解包）
其余（变量格式串、dict 参数、* 解包、.format、f-string）一律跳过不报。

用法：
  python tools/check_format_args.py [根目录...]     # 默认扫仓库根
退出码：0=无不匹配；1=发现不匹配；2=工具自身错误。
"""
import ast
import os
import re
import sys

# printf 风格转换。%% 必须整体优先消耗（否则 "90%% avg" 的第二个 %
# 会与后面的空格+单词首字母 a 组成 "% a" 假占位符——20261015 全仓
# 26 误报实证）；宽度/精度中的 * 各吃 1 个参数（%.*f 对 2 参实证）。
PLACEHOLDER_TOKEN_RE = re.compile(
    r"%%|(?:%(?:\([^)]*\))?[#0\- +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?"
    r"[diouxXeEfFgGcrsa])")


def count_placeholders(fmt):
    """占位符个数；每个 token 内的 * 追加计数（各吃 1 参）。"""
    n = 0
    for tok in PLACEHOLDER_TOKEN_RE.findall(fmt):
        if tok == "%%":
            continue
        n += 1 + tok.count("*")
    return n
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy",
             "projects-archive", "llamacpp_35b", "backups"}


def count_params(node):
    """right 节点 → 可判定的参数个数；不可判定返回 None。"""
    if isinstance(node, ast.IfExp):
        # 条件表达式（"a" if c else "b"）整体求值为一个对象；
        # 两分支同为定长元组才可判定，否则跳过（api_misc:1441 实证）
        body, orelse = count_params(node.body), count_params(node.orelse)
        return body if body is not None and body == orelse else None
    if isinstance(node, ast.Tuple):
        n = 0
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                return None                      # * 解包长度未知
            n += 1
        return n
    if isinstance(node, ast.Dict):
        return None                              # ** 映射解包
    if isinstance(node, (ast.Name, ast.Attribute, ast.Call,
                         ast.Subscript, ast.BinOp)):
        return None                              # 动态值不定长
    return 1                                     # 单字面量当 1 参


def check_file(fp):
    """单文件 → [(lineno, n_fmt, n_args, 片段), ...] 不匹配清单。"""
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=fp)
    except (SyntaxError, OSError):
        return []
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)):
            continue
        fmt = node.left.value
        n_args = count_params(node.right)
        if n_args is None:
            continue
        n_fmt = count_placeholders(fmt)
        if n_fmt != n_args:
            seg = fmt.strip().replace("\n", " ")[:60]
            bad.append((node.lineno, n_fmt, n_args, seg))
    return bad


def scan(roots):
    hits = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, name)
                for lineno, nf, na, seg in check_file(fp):
                    hits.append((fp, lineno, nf, na, seg))
    return hits


def main(argv):
    roots = argv[1:] or [os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))]
    hits = scan(roots)
    for fp, lineno, nf, na, seg in hits:
        print("[MISMATCH] %s:%d 占位符=%d 参数=%d | %s" %
              (fp, lineno, nf, na, seg))
    print("checked roots=%s mismatches=%d" % (roots, len(hits)))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
