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
import io
import os
import subprocess
import sys
import tokenize

DEFAULT_DIRS = ("news_collector", "portal", "tools")
SKIP_DIR_PARTS = {"__pycache__", ".git", "node_modules", ".worktrees", "venv", ".venv"}
NOQA_MARK = "noqa: unstable-hash"
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


def scan_file(path):
    """返回 [(行号, 源码行, 原因)]。解析失败不致命（语法错的文件跳过）。"""
    hits = []
    try:
        with io.open(path, "rb") as f:
            tokens = list(tokenize.tokenize(f.readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, UnicodeDecodeError):
        return hits
    except OSError:
        return hits

    lines = []
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        pass

    prev_sig = None
    for i, tk in enumerate(tokens):
        if tk.type == tokenize.NAME and tk.string == "hash":
            # 属性调用 xx.hash(...) 不算（可能是业务自定义方法）
            is_attr = (prev_sig is not None and prev_sig.type == tokenize.OP
                       and prev_sig.string == ".")
            nxt = _next_sig(tokens, i + 1)
            is_call = (nxt is not None and nxt.type == tokenize.OP
                       and nxt.string == "(")
            if is_call and not is_attr:
                lineno = tk.start[0]
                srcline = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
                if NOQA_MARK not in srcline:
                    hits.append((lineno, srcline, "裸 hash() 跨进程不稳定"))
        if tk.type not in _SKIP_TYPES:
            prev_sig = tk
    return hits


def main():
    ap = argparse.ArgumentParser(description="裸 hash() 静态检查（id 稳定性守卫）")
    ap.add_argument("--enforce", action="store_true", help="有命中则 exit 1（默认 audit）")
    ap.add_argument("--all", action="store_true", help="扫全仓 .py（默认只扫三目录）")
    ap.add_argument("--paths", nargs="*", default=None, help="显式指定文件列表")
    ap.add_argument("--quiet", action="store_true", help="无命中时不打印")
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
        source = "git 跟踪" + ("" if a.all else "（限 %s）" % "/".join(DEFAULT_DIRS))

    total_hits = 0
    scanned = 0
    for rel in targets:
        if not os.path.isfile(rel):
            continue
        scanned += 1
        for lineno, srcline, reason in scan_file(rel):
            total_hits += 1
            print("[HIT] %s:%d  %s" % (rel, lineno, reason), flush=True)
            print("      %s" % srcline[:160], flush=True)
            print("      修法：hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]", flush=True)

    if total_hits == 0:
        if not a.quiet:
            print("[id-lint] OK：%d 个文件（%s）未发现裸 hash() 调用" % (scanned, source),
                  flush=True)
        return 0

    print("[id-lint] 命中 %d 处（扫描 %d 个文件，%s）" % (total_hits, scanned, source),
          flush=True)
    if a.enforce:
        print("[id-lint] enforce 模式 → 阻断。修法见上，或该行加 `# noqa: unstable-hash` 并给理由。",
              flush=True)
        return 1
    print("[id-lint] audit 模式 → 不阻断（如需拦截：--enforce 或 WB_ID_GUARD_ENFORCE=1）",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
