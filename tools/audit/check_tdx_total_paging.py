# -*- coding: utf-8 -*-
"""TDX `total` 分页守卫：禁止按下发接口的 `total` 算页数/容量。

为什么需要它
------------
2026-11-16 判例（`quant_backtest/tdx_api_client.py` 复核）：
  TDX 服务 `GET /api/data/table/{tbl}` 返回体里同时有 `total` 与 `total_exact`，
  文档称「`total_exact=false` 时 `total` 是估算下限」。实测方向相反 —— `total` 偏高估：

    ts_daily   total=19,200,353  真实 ≈8,250,000  (+132.8%)
    ts_income  total=17,971,605  真实   347,031   (×51.8)

  判据不是「哪个数更顺眼」，而是 **offset 越过声称行数后是否为空**：
    offset=8,247,000 → 1 行；offset=8,300,000 → 0 行  ⇒ 真实约 8.25M
  同一进程里 `/api/data/status` 的 `rows` 与越界实测一致（可信），
  `/api/backtest/data-sources` 的 `rows` 与 `total` 同源、同错。

  危害形态：不出错，只是多打 1～50 倍空请求、容量估高一个量级，或把「已拉齐」
  判成「分页契约不符」。与「读不到当没有」「md5 锁没人校验」同族 ——
  它不报错，只有主动去量才知道。

  正确的终止条件只有一条：**`offset` 递增直到 `rows` 为空**
  （`tdx_api_client.py` 用 `len(got) < PAGE_LIMIT`，等价且正确）。

怎么判（两遍，靠数据来源而不是靠字面量）
--------------------------------------
只扫**与 TDX 接口通话的文件**（源码出现 `/api/data/table`、`rookie.xtcloud`、
`48088`、`xtcloud` 之一）。全域扫描会把「选股总分」「total_seconds()」这类同形
词全部误报 —— 与 `check_link_relpath.py` 第一版拿字符串前缀判 junction 同类错误。

第一遍建立「污染集」：被赋值为 **接口返回体里的 total** 的变量
（`d.get("total")` / `d["total"]` / `obj.total`），可传递（`t = d.get("total")`；
`n = t`）。
第二遍只看污染变量与上述取值形式的**用法**：

  BAD  分页/容量算术：参与 `/`、`//`、`%`（字符串格式化除外）、`range()`、
       `math.ceil()`，或被赋给含 page 的名字。→ 一定是缺陷，算出来就是错数。
  WARN `while` 条件里用 total 当上界。→ 若有「空页即 break」保护则只是多打请求；
       若无保护，total 高估会让它多跑 1～50 倍；`sector_minute_gap_backfill.py`
       的写法更糟：空页会被判成 `pagination_contract_invalid`，把「已拉齐」变成
       「blocked」。需人工判读，故不直接判 BAD。

另有一类独立问题：

  BAD  文案失真：字符串常量里同时出现 `total` 与 `下限`。曾经真有这句
       ——「估算下限」与实测相反，下游会读成「保守估计」而放心使用。

用法
----
    python tools/audit/check_tdx_total_paging.py           # 打印明细
    python tools/audit/check_tdx_total_paging.py --quiet   # 只打印 TDXTOTAL: 汇总行
    python tools/audit/check_tdx_total_paging.py --json    # 机读

退出码
------
    0 = 扫完了；无 BAD（可能有 WARN，看功能行）
    1 = 扫完了；发现 BAD，需处置
    2 = 崩溃 / 根目录不存在 / 有文件读不了（本次未验全）

  ★ 与项目铁律一致：退出码只答「跑完了吗、有没有发现」，明细看 `TDXTOTAL:` 行。
    调度侧认功能行，不认 rc；两者都没有 ⇒ 按未验证处理。

只读：全程不写任何文件。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_ROOT = os.path.dirname(os.path.dirname(HERE))

SCAN_DIRS = ["quant_backtest", "tools", "portal", "news_collector", "common",
             "ops", "strategy_layer", "AI"]
SKIP_DIRS = {".git", "__pycache__", "data", "var", "logs", "log", "node_modules",
             "reports", "snapshots", "backups", "dist", "build", "tmp",
             "ffmpeg", "ffmpeg-9.0.2-essentials_build", "finetune",
             # 缺陷样本区：单测故意放的坏代码，扫进去等于自己报自己
             "tests", "fixtures"}

TDX_MARKERS = ("/api/data/table", "rookie.xtcloud", "48088", "xtcloud")
DIV_OPS = (ast.Div, ast.FloorDiv, ast.Mod)
CEIL_CALLS = {"ceil", "range"}
PAGEISH = ("page",)


# ── 取值来源判定 ───────────────────────────────────────────────────────────
def _is_total_extract(node):
    """node 是否为「接口返回体里的 total」：d["total"] / d.get("total") / obj.total。"""
    if isinstance(node, ast.Subscript):
        sl = node.slice
        return isinstance(sl, ast.Constant) and sl.value == "total"
    if isinstance(node, ast.Attribute):
        return node.attr == "total"
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "get" and node.args:
            a0 = node.args[0]
            return isinstance(a0, ast.Constant) and a0.value == "total"
    return False


def _refs(node, names):
    """子树里是否引用了 names 里的变量。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in names:
            return True
    return False


def _has_total(node, tainted):
    """子树里是否出现 total 的取值（直接取值 或 污染变量）。"""
    for n in ast.walk(node):
        if _is_total_extract(n):
            return True
        if isinstance(n, ast.Name) and n.id in tainted:
            return True
    return False


def _build_taint(tree):
    """只取**直接**来源：被赋值为「接口 total 取值」的变量（1 跳，不做传递闭包）。

    为什么不做闭包：`refresh_snapshot.py` 是正确示范 —— 它先把 `claim` 当初始上界，
    再用 `bisect_boundary()` 实测真实边界，之后所有页数/循环都用**实测值**。
    闭包会把实测值也算成受污染，从而把参考实现判成缺陷。
    守卫宁可漏掉间接改写，也不能对正确代码误报 —— 误报一次，闸门就会被关掉。
    """
    tainted = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        val = getattr(node, "value", None)
        if val is None:
            continue
        direct = any(_is_total_extract(n) for n in ast.walk(val))
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        # 元组解包：`rows, cols, total = (..., response.get("total"))`
        if isinstance(val, (ast.Tuple, ast.List)) and len(tgts) == 1 \
                and isinstance(tgts[0], ast.Tuple) and len(tgts[0].elts) == len(val.elts):
            for t, v in zip(tgts[0].elts, val.elts):
                if isinstance(t, ast.Name) and any(_is_total_extract(n) for n in ast.walk(v)):
                    tainted.add(t.id)
            continue
        if not direct:
            continue
        for t in tgts:
            if isinstance(t, ast.Name):
                tainted.add(t.id)
    return tainted


PAGEISH_NAMES = ("page", "pages", "limit", "size", "batch", "per_page", "pagesize")


def _pageish(node):
    """子树里是否出现页长类名字（分页算术一定除以页长）。"""
    for n in ast.walk(node):
        nm = None
        if isinstance(n, ast.Name):
            nm = n.id
        elif isinstance(n, ast.Attribute):
            nm = n.attr
        if nm and any(k in nm.lower() for k in PAGEISH_NAMES):
            return True
    return False


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _line(lines, lineno):
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:120]
    return ""


def scan_text(src):
    """返回 (bad, warn, err)；bad/warn 元素为 (lineno, kind, src_line)。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        return [], [], "SyntaxError: %s" % ex
    lines = src.splitlines()
    tainted = _build_taint(tree)
    bad, warn = [], []

    for node in ast.walk(tree):
        # BAD-1 分页算术：total 参与除法/取模，且另一侧是页长类名字
        #       （只判「除以页长」这一种：一般的比值除法不是分页，报了就是噪音）
        if isinstance(node, ast.BinOp) and isinstance(node.op, DIV_OPS):
            is_strfmt = (isinstance(node.op, ast.Mod)
                         and isinstance(node.left, ast.Constant)
                         and isinstance(node.left.value, str))
            if not is_strfmt:
                l_tot, r_tot = _has_total(node.left, tainted), _has_total(node.right, tainted)
                if l_tot and _pageish(node.right) or r_tot and _pageish(node.left):
                    bad.append((node.lineno, "arith", _line(lines, node.lineno)))
                    continue
        # BAD-2 range()/ceil() 上界
        if isinstance(node, ast.Call) and _call_name(node) in CEIL_CALLS:
            if any(_has_total(a, tainted) for a in node.args):
                bad.append((node.lineno, "range", _line(lines, node.lineno)))
                continue
        # BAD-3 赋给含 page 的名字
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            val = getattr(node, "value", None)
            if val is not None and _has_total(val, tainted):
                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in tgts:
                    if isinstance(t, ast.Name) and "page" in t.id.lower():
                        bad.append((node.lineno, "page_var", _line(lines, node.lineno)))
                        break
        # WARN while 上界
        if isinstance(node, ast.While) and _has_total(node.test, tainted):
            warn.append((node.lineno, "while_bound", _line(lines, node.lineno)))
        # BAD-4 文案失真
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if "total" in s.lower() and "下限" in s:
                bad.append((node.lineno, "copy", _line(lines, node.lineno)))
    return bad, warn, None


def iter_py(root):
    for d in SCAN_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames
                           if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def main(argv=None):
    ap = argparse.ArgumentParser(description="禁止按接口 total 分页的静态守卫（只读）")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--quiet", action="store_true", help="只打印 TDXTOTAL: 行")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    root = os.path.realpath(a.root)
    if not os.path.isdir(root):
        print("TDXTOTAL: ERR root_not_found %s" % root)
        return 2
    self_path = os.path.realpath(__file__)

    t0 = time.time()
    checked = skipped = 0
    bad, warn, errors = [], [], []
    for p in iter_py(root):
        if os.path.realpath(p) == self_path:
            continue
        try:
            src = open(p, encoding="utf-8", errors="replace").read()
        except OSError as ex:
            errors.append((p, "read: %s" % ex))
            continue
        if not any(m in src for m in TDX_MARKERS):
            skipped += 1
            continue
        checked += 1
        b, w, err = scan_text(src)
        if err:
            errors.append((p, err))
            continue
        rel = os.path.relpath(p, root)
        for ln, kind, txt in b:
            bad.append({"file": rel, "line": ln, "kind": kind, "src": txt})
        for ln, kind, txt in w:
            warn.append({"file": rel, "line": ln, "kind": kind, "src": txt})

    elapsed = time.time() - t0
    if not a.quiet:
        for x in bad:
            print("TDXTOTAL: BAD %s:%s kind=%s | %s" % (x["file"], x["line"], x["kind"], x["src"]))
        for x in warn:
            print("TDXTOTAL: WARN %s:%s kind=%s | %s" % (x["file"], x["line"], x["kind"], x["src"]))
        for p, e in errors:
            print("TDXTOTAL: ERR %s | %s" % (os.path.relpath(p, root), e))

    # 功能行：无论有无 BAD 必打；调度侧认这一行
    print("TDXTOTAL: checked=%d skipped=%d bad=%d warn=%d err=%d elapsed=%.1fs"
          % (checked, skipped, len(bad), len(warn), len(errors), elapsed))

    if a.json:
        print(json.dumps({"checked": checked, "skipped": skipped, "bad": bad,
                          "warn": warn,
                          "errors": [{"file": f, "err": e} for f, e in errors]},
                         ensure_ascii=False, indent=2))

    if errors:
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
