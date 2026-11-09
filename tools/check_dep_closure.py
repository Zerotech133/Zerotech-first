#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""依赖闭包检查：已入库的模块，不得 import 未入库的本地模块。

## 为什么需要这个

2026-11-06 的真实事故：`portal/api_trace_drilldown.py` 已入库，但模块级
`import kg_cq` / `import cq_registry` 指向的两个文件从未入库。结果：

    git archive HEAD 导出的干净树上 import 直接 ModuleNotFoundError

更隐蔽的一条：`quant_backtest/qpzm_backtest.py:710` 在**函数体内**
`import tdx_api_client`，平时启动毫无异常，跑回测到特定函数才炸。

这类缺陷的共同点：**本机永远看不出来**（文件就在旁边），只有新 clone、
CI、或 `git clean` 之后才暴露，而那时通常已经在排障中途。

## 判定范围

只检查「已入库文件」引用的「本地模块」：
  · 本地模块 = 仓库内存在同名 .py 或同名包目录
  · 标准库与第三方包不检查（它们由 requirements 管）
  · 未入库文件引用的东西不检查（它自己都不在版本控制里，管不着）

## 用法

    python tools/check_dep_closure.py                  # 检查默认目录
    python tools/check_dep_closure.py portal quant_backtest
    python tools/check_dep_closure.py --json           # 机器可读

退出码：0 = 全部闭合；1 = 存在未入库依赖。
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = ("portal", "tools", "agent_platform", "quant_backtest")

# 这些目录里的 py 不参与检查：一次性探针/归档实验，本就不入版本控制
SKIP_PARTS = ("/.git/", "/__pycache__/", "/_qpzm_rd_", "/_qpzm_audit_",
              "/.worktrees/", "/node_modules/", "/site-packages/")


def _tracked_set() -> set[str]:
    """一次性取全部已入库路径（相对仓库根，正斜杠）。

    早期实现是每个文件跑一次 `git ls-files --error-unmatch`，1699 个文件要
    1699 次 subprocess，实测 170 秒。改成一次全量列举后降到毫秒级。
    """
    r = subprocess.run(["git", "ls-files"], cwd=REPO,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git ls-files 失败：%s" % (r.stderr or "")[:200])
    return {ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()}


def _tracked(path: Path, _cache: list = []) -> bool:
    """判断是否已入库。查询走一次性缓存的集合，避免 N 次 subprocess。"""
    if not _cache:
        _cache.append(_tracked_set())
    tracked = _cache[0]
    rel = str(path).replace("\\", "/")
    try:
        rel = str(Path(path).resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        pass
    if rel in tracked:
        return True
    # 包目录：目录下有任一 __init__.py 已入库即算
    return any(t.startswith(rel.rstrip("/") + "/") for t in tracked)


def _local_modules(src: Path, repo: Path) -> set[str]:
    """解析出「本地模块」名：仓库内存在同名 .py 或同名包目录。"""
    try:
        tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level>0 是相对导入，天然同包，不需要检查
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    out = set()
    for nm in names:
        if not nm or nm in sys.stdlib_module_names:
            continue
        # 本地模块判定：仓库任意位置有 <nm>.py 或 <nm>/__init__.py
        if (repo / (nm + ".py")).exists() or any(
                (d / (nm + ".py")).exists() or (d / nm / "__init__.py").exists()
                for d in (repo, *[repo / x for x in DEFAULT_DIRS])):
            out.add(nm)
    return out


def _resolve(nm: str, repo: Path) -> Path | None:
    for d in (repo, *[repo / x for x in DEFAULT_DIRS]):
        p = d / (nm + ".py")
        if p.exists():
            return p
        if (d / nm / "__init__.py").exists():
            return d / nm
    return None


def scan(dirs: tuple[str, ...]) -> dict:
    broken, checked = [], 0
    for d in dirs:
        base = REPO / d
        if not base.is_dir():
            continue
        for src in base.rglob("*.py"):
            s = str(src).replace("\\", "/")
            if any(k in s for k in SKIP_PARTS):
                continue
            if not _tracked(src):
                continue              # 未入库文件引用的东西不管
            checked += 1
            for nm in sorted(_local_modules(src, REPO)):
                target = _resolve(nm, REPO)
                if target is None:
                    continue
                if not _tracked(target if target.is_dir() else target):
                    broken.append({
                        "importer": str(src.relative_to(REPO)).replace("\\", "/"),
                        "module": nm,
                        "target": str(target.relative_to(REPO)).replace("\\", "/"),
                    })
    # 去重（同一对 importer→module 只报一次）
    uniq = {(b["importer"], b["module"]): b for b in broken}
    return {"checked_files": checked, "broken": sorted(uniq.values(),
                                                       key=lambda x: (x["module"], x["importer"]))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="检查已入库模块是否依赖未入库本地模块")
    ap.add_argument("dirs", nargs="*", default=None, help="检查目录，默认核心四目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)

    res = scan(tuple(a.dirs) if a.dirs else DEFAULT_DIRS)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("检查已入库 .py 文件 %d 个" % res["checked_files"])
        if not res["broken"]:
            print("依赖闭包完整：已入库模块没有引用未入库的本地模块。")
        else:
            print("\n发现 %d 处「已入库 → 未入库」引用：" % len(res["broken"]))
            for b in res["broken"]:
                print("  %s\n      import %s   ->  %s（未入库）"
                      % (b["importer"], b["module"], b["target"]))
            print("\n后果：新 clone / CI / git clean 后此处会 ModuleNotFoundError。")
            print("处置：把 target 一并入库，或给 import 加 try/except 兜底并明示降级。")
    return 1 if res["broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
