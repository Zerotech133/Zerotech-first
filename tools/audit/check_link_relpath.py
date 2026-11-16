# -*- coding: utf-8 -*-
"""退层路径守卫：检出「从 __file__/HERE 往上退层」的常量在 C:\\aios 别名入口下会算错的写法。

为什么需要它
------------
2026-11-16 判例（`quant_backtest/paper_trade.py`）：
  计划任务以 `C:\\aios\\quant_backtest` 启动（`C:\\aios` 是项目根的 ASCII 别名
  junction），而 `DELIVERABLES = abspath(os.path.join(HERE, "..", "..", "deliverables"))`。
  `os.path.abspath` 是**纯字符串运算、不解析 junction** ⇒ 退两层落到
  `C:\\deliverables`（不存在）⇒ 抑制令永远读不到，而「读不到」被当成
  「当日无抑制」，静默失效半个月。

  这类缺陷的共同点：**它不出错**。算错的路径与「本来就没有」返回值一样，
  所以没人会报错。只有主动去量，才能在发生当天知道。

判据
----
对每处退层常量，分别用两个 HERE 求值：
  · 实体 HERE = 文件真实所在目录
  · 别名 HERE = `C:/aios` + 该文件的相对项目根路径
若两者 **realpath 之后**仍不同 ⇒ 该常量在调度入口下指向别处。

  ★ 必须用 realpath 比较，不能用字符串前缀：`C:\\aios` 与
  `C:\\项目\\老田的AI助手` 字符串不同但是同一目录（junction）。
  第一版实现用前缀判定，把 112 处正常写法误报成缺陷 —— 见文末判例。

  字符串不同但 realpath 相同 ⇒ **正常**，只是写法不优雅，不报。
  realpath 不同且目标不存在 ⇒ **真失效**，计入 bad，退出码 1。

用法
----
    python tools/audit/check_link_relpath.py            # 默认扫代码目录，打印明细
    python tools/audit/check_link_relpath.py --quiet    # 只打印 LNK: 汇总行（调度用）
    python tools/audit/check_link_relpath.py --json     # 机读

退出码
------
    0 = 扫完了，无真失效
    1 = 扫完了，发现 N 处真失效（真缺陷，需处置）
    2 = 崩溃（没扫完，本次什么都没验到）

  ★ 与项目铁律一致：退出码只答「跑完了吗」；结论看 `LNK:` 功能行。
    调度侧不要只看 rc，要 grep `LNK:` 行。

只读：全程不写任何文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DEFAULT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.realpath(__file__))))
# ★ 这里必须 realpath，否则守卫会自己踩它要防的坑：
#   若从 C:\aios 启动、root 用 abspath 算出 C:\aios，就与 ALIAS_ROOT 相同，
#   于是每个常量都判成「两个入口一致」⇒ 永远检不出缺陷、永远 PASS。
#   判据：检查器自身必须站在实体路径上，才能与别名路径比对。
ALIAS_ROOT = r"C:\aios"

# 只扫代码目录：数据盘（data/var/logs）里没有 py，扫了纯浪费时间。
SCAN_DIRS = ["quant_backtest", "tools", "portal", "news_collector", "common",
             "ops", "strategy_layer", "AI"]
SKIP_DIRS = {".git", "__pycache__", "data", "var", "logs", "log", "node_modules",
             "reports", "snapshots", "backups", "dist", "build", "tmp",
             "ffmpeg", "ffmpeg-9.0.2-essentials_build", "finetune"}

# 匹配 os.path.abspath(os.path.join(<var>, "..", "..", ...))
# 只认紧邻的连续 ".." 段：中间夹别的段说明退的位置不同，口径不好统一。
PAT = re.compile(
    r'os\.path\.abspath\(\s*os\.path\.join\(\s*([A-Za-z_][A-Za-z0-9_]*)'
    r'((?:\s*,\s*"\.\.")+)')

MAX_DEPTH = 3


def same_realpath(a, b):
    """两个路径是否指向真实同一位置。

    ★ 这是本模块唯一正确的判定口径：`C:\\aios` 与 `C:\\项目\\老田的AI助手`
    字符串完全不同，却是同一目录（junction）。用字符串前缀判「是否在项目内」
    会把 112 处正常写法全报成缺陷（2026-11-16 第一版实现即犯此错）。
    """
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _iter_py(root):
    """遍历要扫的 py 文件。

    跳过 tests 目录与 test_*.py：测试里为了验证守卫而**故意写下的缺陷样本**，
    会被当成真实缺陷计数（实测 hits 因此 +1）。守卫只扫生产代码。
    """
    seen = set()
    for sub in SCAN_DIRS:
        top = os.path.join(root, sub)
        if not os.path.isdir(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and d != "tests"
                           and not d.startswith(".venv")]
            if os.path.relpath(dirpath, top).count(os.sep) >= MAX_DEPTH:
                dirnames[:] = []
            for fn in filenames:
                if not fn.endswith(".py") or fn.startswith("test_"):
                    continue
                p = os.path.join(dirpath, fn)
                if p in seen:
                    continue
                seen.add(p)
                yield p


def scan(root=DEFAULT_ROOT, alias=ALIAS_ROOT):
    """返回 (findings, stats)。

    findings 里每条：{file, line, ups, real, alias_path, exists, same_realpath}
    只收「字符串不同」的，realpath 相同的标 same_realpath=True（正常写法）。
    """
    findings = []
    n_files = 0
    n_hits = 0
    alias_ok = os.path.isdir(alias)

    for p in _iter_py(root):
        n_files += 1
        try:
            src = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if "os.path.abspath" not in src:
            continue
        dirpath = os.path.dirname(p)
        rel_dir = os.path.relpath(dirpath, root)
        base_alias = alias if rel_dir == "." else os.path.join(alias, rel_dir)

        for m in PAT.finditer(src):
            n_hits += 1
            ups = m.group(2).count('".."')
            line = src.count("\n", 0, m.start()) + 1
            real = os.path.normpath(os.path.join(dirpath, *([".."] * ups)))
            aliased = os.path.normpath(os.path.join(base_alias, *([".."] * ups)))
            if os.path.normcase(real) == os.path.normcase(aliased):
                continue  # 两个入口算得一样，无风险
            same = same_realpath(real, aliased)
            findings.append({
                "file": os.path.relpath(p, root),
                "line": line,
                "ups": ups,
                "real": real,
                "alias": aliased,
                "exists_via_alias": os.path.isdir(aliased),
                "same_realpath": same,
            })

    stats = {"root": root, "alias": alias, "alias_ok": alias_ok,
             "n_files": n_files, "n_hits": n_hits, "n_diff_string": len(findings),
             "n_bad": sum(1 for f in findings if not f["same_realpath"])}
    return findings, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="退层路径守卫：别名入口下算错的常量检出")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="项目根")
    ap.add_argument("--alias", default=ALIAS_ROOT, help="别名入口（默认 C:\\aios）")
    ap.add_argument("--quiet", action="store_true", help="只打印 LNK: 汇总行")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-show", type=int, default=20, help="明细最多显示条数")
    a = ap.parse_args(argv)

    t0 = time.time()
    findings, stats = scan(a.root, a.alias)
    elapsed = time.time() - t0
    bad = [f for f in findings if not f["same_realpath"]]
    benign = [f for f in findings if f["same_realpath"]]

    if a.json:
        print(json.dumps({"stats": stats, "bad": bad, "benign": benign},
                         ensure_ascii=False, indent=1))
    else:
        if not a.quiet:
            print("根    : %s" % stats["root"])
            print("别名  : %s  (存在=%s)" % (stats["alias"], stats["alias_ok"]))
            print("扫描  : %d 个 py 文件，命中退层常量 %d 处"
                  % (stats["n_files"], stats["n_hits"]))
            print()
            if bad:
                print("=== 真失效（realpath 也不同）===")
                print("%-52s %5s %3s %-26s %-22s %s"
                      % ("文件", "行", "退层", "实体入口", "别名入口", "目标存在"))
                for f in bad[:a.max_show]:
                    print("%-52s %5d %3d %-26s %-22s %s"
                          % (f["file"][:52], f["line"], f["ups"], f["real"][-26:],
                             f["alias"][-22:], "是" if f["exists_via_alias"] else "否"))
                if len(bad) > a.max_show:
                    print("  …… 另有 %d 处未显示" % (len(bad) - a.max_show))
                print()
            if benign:
                print("=== 字符串不同但 realpath 相同（正常，不报缺陷）%d 处 ===" % len(benign))
                for f in benign[:a.max_show]:
                    print("  %-50s :%-5d 退%d层 -> %s"
                          % (f["file"][:50], f["line"], f["ups"], f["alias"]))
                if len(benign) > a.max_show:
                    print("  …… 另有 %d 处未显示" % (len(benign) - a.max_show))
                print()

        # 功能行：调度侧靠这一行判定，不看退出码
        print("LNK: checked=%d hits=%d diff=%d bad=%d alias_ok=%s elapsed=%.1fs"
              % (stats["n_files"], stats["n_hits"], stats["n_diff_string"],
                 stats["n_bad"], "1" if stats["alias_ok"] else "0", elapsed))
        if not stats["alias_ok"]:
            print("LNK: WARN 别名入口 %s 不存在 —— 本次只验了实体路径，"
                  "别名侧未覆盖（结果不完整）" % stats["alias"])

    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print("LNK: CRASH %s: %s" % (type(e).__name__, e))
        sys.exit(2)
