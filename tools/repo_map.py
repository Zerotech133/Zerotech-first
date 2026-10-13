# -*- coding: utf-8 -*-
"""
K-ORG-035/036 F2: 仓库地图（借 Aider RepoMap 范式，自研实现）。
AST 扫描指定目录 → 模块 → 类/函数签名压缩地图（字符预算可控）；
按工单关键词择邻：文件路径/符号名命中关键词的模块排前，预算紧张时丢弃零相关。
供 agent_coder 生成前注入 prompt（解决「Agent 不知道代码库里有什么」缺口）。

用法：
  python tools/repo_map.py [关键词...]          # 打印地图
  from repo_map import build_map               # 编码通道调用
"""
import ast
import os
import sys

SKIP_DIRS = {".git", "__pycache__", ".worktrees", "node_modules",
             ".workbuddy", "shadow", "docs", "portal", "protos"}
DEFAULT_ROOTS = ("tools", "agent_platform")
DEFAULT_BUDGET_CHARS = 8000  # ~2K token


def scan_tree(root, exts=(".py",)):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(exts):
                out.append(os.path.join(dirpath, fn))
    return out


def module_signatures(path):
    """返回签名行列表；语法坏文件返回 None（跳过不炸）。"""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:
        return None
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            lines.append("class %s" % node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                args = ast.unparse(node.args)[:90]
            except Exception:
                args = "..."
            lines.append("def %s(%s)" % (node.name, args))
    return lines


def build_map(roots=DEFAULT_ROOTS, keywords=(), budget_chars=DEFAULT_BUDGET_CHARS,
              base_dir="."):
    """生成压缩仓库地图：命中关键词的模块优先，零相关模块预算过半即丢弃。"""
    os.chdir(base_dir)
    scored = []
    for root in roots:
        for p in scan_tree(root):
            sigs = module_signatures(p)
            if not sigs:
                continue
            rel = p.replace("\\", "/")
            blob = rel + "\n" + "\n".join(sigs)
            score = sum(1 for kw in keywords
                        if kw and kw.lower() in blob.lower())
            scored.append((-score, rel, sigs))
    scored.sort()
    out, used = [], 0
    for neg, rel, sigs in scored:
        score = -neg
        block = rel + ":\n" + "\n".join("  " + s for s in sigs[:16]) + "\n"
        if score == 0 and used > budget_chars * 0.6:
            continue
        if used + len(block) > budget_chars:
            block = rel + ": (%d symbols, truncated)\n" % len(sigs)
        out.append(block)
        used += len(block)
        if used >= budget_chars:
            break
    return "".join(out)


if __name__ == "__main__":
    m = build_map(keywords=sys.argv[1:])
    sys.stdout.reconfigure(encoding="utf-8")
    print(m)
    print("# files=%d chars=%d ~tokens~%d" % (m.count(":\n"), len(m), len(m) // 3),
          file=sys.stderr)
