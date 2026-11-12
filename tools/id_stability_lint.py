#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-01 id 稳定性静态检查：拦截裸 Python 内置 hash() 调用。

为什么需要它
------------
内置 `hash()` 按进程加盐（PEP 456 之前的行为，CPython 默认 PYTHONHASHSEED
随机），**跨进程不稳定**。任何用它生成 id / 快照号 / 去重键的代码，都会
出现「同一输入、两次运行、两个 id」——判例 R22（sse 采集器 1774 行仅 672
唯一，62% 重复膨胀）与本次 R-01（obs_bridge snapshot_id）都是这个病。

正确做法：`hashlib.sha256(raw.encode()).hexdigest()[:16]`（确定性、跨进程
恒定、十六进制形态不触发手机号类误报）。详见 docs/id_stability_audit.md。

判定规则（精确到 token，不靠 grep）
----------------------------------
- 用 `tokenize` 解析，只认 **NAME token 恰为 `hash`** 且 **下一个有效 token
  是 `(`** 的调用；
- 天然排除：注释、字符串（含文档字符串）、`hashlib.*`、`__hash__`、`xx.hash()`
  这类属性调用（前一个有效 token 是 `.`）；
- 行级豁免：该行含 `# noqa: unstable-hash`（须带具体理由，评审用）。

用法
----
    python tools/id_stability_lint.py                # audit：只报，exit 0
    python tools/id_stability_lint.py --enforce      # 有命中则 exit 1
    python tools/id_stability_lint.py --all          # 扫全仓 .py（默认三目录）
    python tools/id_stability_lint.py --paths a.py b.py

退出码
------
    0  audit 完成 / enforce 且无命中
    1  enforce 且有命中
    2  环境异常（git 不可用且未显式给 --paths）
"""
import argparse
import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tokenize

DEFAULT_DIRS = ("news_collector", "portal", "tools")
SKIP_DIR_PARTS = {"__pycache__", ".git", "node_modules", ".worktrees", "venv", ".venv"}
NOQA_MARK = "noqa: unstable-hash"

# 第三方 / 生成物目录：其内部 hash() 是库自身实现，与本仓 id 治理无关。
# 2026-11-13 实测 --all 全仓 40 处命中中 28 处集中于 `video/pylibs/*`——
# 注意 pylibs 在第二级，只排顶层拦不住，故按「任一路径段命中」判定。
EXCLUDE_ANY_PART = {
    "pylibs", "site-packages", "node_modules", "third_party", "vendor",
    "__pycache__", "venv", ".venv", ".worktrees", ".git",
}
# 仅顶层排除（这些词在业务子目录里可能是正常命名，不能全路径匹配）
EXCLUDE_TOP_DIRS = {"dist", "build", "PIL"}
BASELINE_F = "tools/id_stability_baseline.json"


def _is_excluded(rel):
    parts = rel.replace("\\", "/").split("/")
    if parts[0] in EXCLUDE_TOP_DIRS:
        return True
    return any(p in EXCLUDE_ANY_PART for p in parts[:-1])
# 结构性 token：不参与「前一个有效 token」判定
_SKIP_TYPES = {
    tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
    tokenize.COMMENT, tokenize.ENCODING, tokenize.ENDMARKER,
}


def _repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git_py_files(root):
    """git 跟踪的 .py 文件（相对路径）。git 不可用时返回 None。"""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True)
        if out.returncode != 0:
            return None
        return [p for p in out.stdout.splitlines() if p.endswith(".py")]
    except Exception:
        return None


def _iter_py(root, dirs=None):
    """无 git 时的兜底：目录递归扫 .py。"""
    for d in (dirs or DEFAULT_DIRS):
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIR_PARTS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.relpath(os.path.join(dirpath, fn), root)


def _next_sig(tokens, i):
    for j in range(i, len(tokens)):
        if tokens[j].type not in _SKIP_TYPES:
            return tokens[j]
    return None


def _hits_tokenize(path):
    """tokenize 兜底（AST 解析失败时用）。返回行号列表。

    注意版本差异：Python ≤3.11 把 f-string 整体当一个 STRING token，
    `f"{hash(x)}"` 内的调用**检不出**；3.12+ 才拆开。所以它只能兜底，
    主判据必须是 AST（见 _hits_ast）。
    """
    lns = []
    try:
        with io.open(path, "rb") as f:
            tokens = list(tokenize.tokenize(f.readline))
    except (tokenize.TokenError, IndentationError, SyntaxError,
            UnicodeDecodeError, OSError):
        return lns
    prev_sig = None
    for i, tk in enumerate(tokens):
        if tk.type == tokenize.NAME and tk.string == "hash":
            is_attr = (prev_sig is not None and prev_sig.type == tokenize.OP
                       and prev_sig.string == ".")
            nxt = _next_sig(tokens, i + 1)
            is_call = (nxt is not None and nxt.type == tokenize.OP
                       and nxt.string == "(")
            if is_call and not is_attr:
                lns.append(tk.start[0])
        if tk.type not in _SKIP_TYPES:
            prev_sig = tk
    return lns


def _hits_ast(text):
    """AST 主判据：跨解释器版本一致，且能覆盖 f-string 内的表达式。

    `ast.Name(id='hash')` 只匹配裸调用；`xx.hash()` / `hashlib.sha256()` 是
    `ast.Attribute`，天然排除。返回行号列表；语法错返回 None（交 tokenize 兜底）。
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "hash"):
            out.append(node.lineno)
    return out


def scan_file(path):
    """返回 [(行号, 源码行, 原因)]。解析失败不致命（跳过）。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    if not text:
        return []
    lines = text.splitlines()

    lns = _hits_ast(text)
    if lns is None:                       # 语法错 → tokenize 兜底
        lns = _hits_tokenize(path)

    hits = []
    for lineno in sorted(set(lns)):
        srcline = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        if NOQA_MARK in srcline:
            continue
        hits.append((lineno, srcline, "裸 hash() 跨进程不稳定"))
    return hits


def _base_key(rel, srcline):
    """基线键 = 相对路径 + 归一化源码行指纹。

    用源码行（而非行号）做指纹：代码上下移动不会让基线失效，
    但该行被改写就会失配 → 重新报出来（这正是我们要的：修了要复核，改了要重审）。
    """
    h = hashlib.sha1(srcline.strip().encode("utf-8", "replace")).hexdigest()[:16]
    return "%s|%s" % (rel.replace("\\", "/"), h)


def load_baseline(root):
    """读基线；文件不存在返回 None（=未启用基线，全部当新增）。"""
    p = os.path.join(root, BASELINE_F)
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("keys") or [])
    except Exception:
        return None


def write_baseline(root, entries):
    p = os.path.join(root, BASELINE_F)
    keys = sorted({_base_key(rel, src) for rel, _ln, src, _rs in entries})
    doc = {
        "_note": "裸 hash() 存量基线：仅用于让守卫先上线（存量不阻塞、增量必拦）。"
                 "待归属线清理后应逐项删除，删除即恢复严格拦截。",
        "_howto": "重生成：python tools/id_stability_lint.py --write-baseline",
        "count": len(keys),
        "keys": keys,
    }
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def main():
    ap = argparse.ArgumentParser(description="裸 hash() 静态检查（id 稳定性守卫）")
    ap.add_argument("--enforce", action="store_true", help="有新增命中则 exit 1（默认 audit）")
    ap.add_argument("--all", action="store_true", help="扫全仓 .py（默认只扫三目录）")
    ap.add_argument("--paths", nargs="*", default=None, help="显式指定文件列表")
    ap.add_argument("--quiet", action="store_true", help="无命中时不打印")
    ap.add_argument("--write-baseline", action="store_true",
                    help="把当前命中写成基线（tools/id_stability_baseline.json）")
    ap.add_argument("--no-baseline", action="store_true", help="忽略基线，全部当新增")
    ap.add_argument("--show-known", action="store_true", help="连基线内命中一起打印")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = _repo_root()
    os.chdir(root)

    if a.paths:
        targets = list(a.paths)
        source = "显式路径"
    else:
        git_files = _git_py_files(root)
        if git_files is None:
            if a.enforce:
                print("[id-lint] 环境异常：git 不可用且未给 --paths，无法判定", flush=True)
                return 2
            print("[id-lint] WARN: git 不可用，回退目录扫描", flush=True)
            targets = list(_iter_py(root))
        else:
            targets = git_files
        if not a.all:
            targets = [p for p in targets
                       if any(p.startswith(d + "/") or p.startswith(d + os.sep)
                              for d in DEFAULT_DIRS)]
        # 排除第三方 / 生成物目录（其内部 hash() 与本仓治理无关）
        targets = [p for p in targets if not _is_excluded(p)]
        source = "git 跟踪" + ("" if a.all else "（限 %s）" % "/".join(DEFAULT_DIRS))

    entries = []
    scanned = 0
    for rel in targets:
        if not os.path.isfile(rel):
            continue
        scanned += 1
        for lineno, srcline, reason in scan_file(rel):
            entries.append((rel, lineno, srcline, reason))

    if a.write_baseline:
        p = write_baseline(root, entries)
        print("[id-lint] 基线已写入 %s（%d 条，扫描 %d 个文件）"
              % (os.path.relpath(p, root).replace("\\", "/"), len(entries), scanned),
              flush=True)
        return 0

    base = None if (a.no_baseline or not entries) else load_baseline(root)
    new_hits, known_hits = [], []
    for e in entries:
        if base is not None and _base_key(e[0], e[2]) in base:
            known_hits.append(e)
        else:
            new_hits.append(e)

    for rel, lineno, srcline, reason in new_hits:
        print("[HIT] %s:%d  %s" % (rel, lineno, reason), flush=True)
        print("      %s" % srcline[:160], flush=True)
        print("      修法：hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]", flush=True)

    if a.show_known:
        for rel, lineno, srcline, _r in known_hits:
            print("[known] %s:%d  %s" % (rel, lineno, srcline[:120]), flush=True)

    # 基线失效提示：仅当该文件本次确被扫描、但基线键已扫不到时才算失效
    # （否则「本次没扫这个目录」会被误报成「已修复」，误导维护者）
    if base:
        scanned_files = set()
        for rel in targets:
            if os.path.isfile(rel):
                scanned_files.add(rel.replace("\\", "/"))
        alive = {_base_key(e[0], e[2]) for e in entries}
        stale = [k for k in base
                 if k.split("|")[0] in scanned_files and k not in alive]
        if stale:
            print("[id-lint] 注：基线有 %d 条已失效（该文件已扫描且该行已修复/改动），"
                  "可 --write-baseline 收缩" % len(stale), flush=True)

    if not new_hits:
        if not a.quiet:
            extra = "（另有 %d 条基线内已知命中，未阻断）" % len(known_hits) if known_hits else ""
            print("[id-lint] OK：%d 个文件（%s）无新增裸 hash()%s"
                  % (scanned, source, extra), flush=True)
        return 0

    print("[id-lint] 新增命中 %d 处（扫描 %d 个文件，%s）"
          % (len(new_hits), scanned, source), flush=True)
    if known_hits:
        print("[id-lint] 另有 %d 条基线内已知命中（存量，不阻断）" % len(known_hits), flush=True)
    if a.enforce:
        print("[id-lint] enforce 模式 → 阻断。修法见上，或该行加 `# noqa: unstable-hash` 并给理由。",
              flush=True)
        return 1
    print("[id-lint] audit 模式 → 不阻断（如需拦截：--enforce 或 WB_ID_GUARD_ENFORCE=1）",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
