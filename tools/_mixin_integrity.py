# -*- coding: utf-8 -*-
"""P4 拆分第四道门槛：Handler 方法集 AST 断言（K-STOCK-089）。

用法：
  python tools/_mixin_integrity.py --baseline   # 首跑/重建基线（写 portal/mixin_baseline.json）
  python tools/_mixin_integrity.py --check      # 校验：基座+各 api_*.py Mixin 方法并集 == 基线
  python tools/_mixin_integrity.py --check --root <worktree>
                                               # 分支感知：扫描指定 worktree（评审门用）

原理：搬代码不改逻辑 ⇒ Handler 方法名集合（含模块级函数可选）搬前搬后必须零 diff。
探针盲区（--probe-all 52 条打不全 112 方法）由此补上：多一个少一个都拒收。
"""
import ast
import glob
import json
import os
import sys
import logging

# 2026-10-25 B5/P1-07（审计 RC-04）：Windows 下 stdout 被管道重定向时默认按 GBK
# 编码，本文件输出的 ❌/⚠️/✅ 会 UnicodeEncodeError 崩溃 → 评审门在干净 shell
# 假红（rc=1 与真实违规不可区分）。强制 UTF-8，不可编码字符替换而非崩溃。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError) as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/_mixin_integrity.py", exc)

_DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(_DEFAULT_REPO, "portal")
MAIN = os.path.join(PORTAL, "portal_api.py")
BASELINE = os.path.join(PORTAL, "mixin_baseline.json")


def _apply_root(root):
    """2026-10-30 P103：把扫描根切到分支 worktree（评审门分支感知）。"""
    global PORTAL, MAIN, BASELINE
    PORTAL = os.path.join(root, "portal")
    MAIN = os.path.join(PORTAL, "portal_api.py")
    BASELINE = os.path.join(PORTAL, "mixin_baseline.json")

# 2026-10-25 B6/P1-08（审计 RC-06）：「只改不扩」拒收闸。此前 --check 只拦
# 「丢失」不拦「新增」→ 基线停在 10-12 的 121/98 而代码已 185/381 仍 rc=0。
# 基线已于 2026-10-25 刷新到现状（185 methods / 381 funcs），此后新增 Handler
# 方法预算为 0：要扩基座必须在同一提交显式重跑 --baseline（评审 diff 可见），
# 不允许静默增长。新 handler 应落新的 portal/api_*.py 文件（基线含并集，新增
# 同样计数），本门只保证「新增必须被看见」，不区分新增落在哪个文件。
MAX_ADDED_HANDLERS = 0
# portal_api.py 行数预算（同一 P1-08）：2026-10-25 现状 5,068 行（非空 4,618），
# P4 完工口径 3,370（docs/plans/2026-10-12_P4完工总结.md:4,34），12 天回涨 +37%。
# 阈值取 5,200（留 132 行余量）——超过即 FAIL，禁止巨石继续膨胀。
PORTAL_API_MAX_LINES = 5200


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
    argv = sys.argv[1:]
    root = ""
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 < len(argv):
            root = argv[i + 1]
            del argv[i:i + 2]
    if root:
        _apply_root(root)
    mode = argv[0] if argv else "--check"
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
        if len(added) > MAX_ADDED_HANDLERS:
            print("❌ 新增 Handler 方法 %d > 预算 %d（只改不扩：确需扩基座，"
                  "须在同一提交显式重跑 --baseline）:"
                  % (len(added), MAX_ADDED_HANDLERS), sorted(added)[:10])
            ok = False
        else:
            print("ℹ️ 新增 Handler 方法 %d（预算 %d 内）:"
                  % (len(added), MAX_ADDED_HANDLERS), sorted(added)[:10])
    if lostf:
        print("❌ 丢失模块级函数 %d:" % len(lostf), sorted(lostf)[:10])
        ok = False
    if addedf:
        print("ℹ️ 新增模块级函数 %d:" % len(addedf), sorted(addedf)[:10])
    # portal_api.py 行数预算门（B6/P1-08）：独立于方法集，直接卡「只改不扩」红线。
    with open(MAIN, encoding="utf-8") as f:
        main_lines = len(f.read().splitlines())
    if main_lines > PORTAL_API_MAX_LINES:
        print("❌ portal_api.py 行数 %d > 预算 %d（只改不扩红线，见 "
              "PORTAL_API_MAX_LINES 注释）" % (main_lines, PORTAL_API_MAX_LINES))
        ok = False
    else:
        print("ℹ️ portal_api.py 行数 %d / 预算 %d"
              % (main_lines, PORTAL_API_MAX_LINES))
    if ok:
        print("✅ INTEGRITY OK: %d methods / %d funcs 与基线零 diff"
              % (len(hs), len(fs)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
