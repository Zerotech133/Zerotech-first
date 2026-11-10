# -*- coding: utf-8 -*-
"""扫 gate 覆盖的测试/被测代码实际 import 的第三方顶层包（供 CI 装依赖）。

做法：解析 test_gate.ps1 的套件文件清单 → 取每个文件 + 其同目录/同仓的
被测模块的一级 import 名 → 与本机已安装发行版对账，只留第三方（排除标准库、
本仓自有模块），输出排序结果。
"""
import io
import os
import re
import sys
import sysconfig

REPO = r"C:\项目\老田的AI助手"

# 标准库名单（3.11）
STDLIB = set(sys.stdlib_module_names)

# 本仓自有顶层模块名（避免当成第三方）
LOCAL = set()
for d in ("portal", "quant_backtest", "tools", "agent_platform", "k_stock"):
    p = os.path.join(REPO, d)
    if os.path.isdir(p):
        for fn in os.listdir(p):
            if fn.endswith(".py"):
                LOCAL.add(fn[:-3])
            elif os.path.isdir(os.path.join(p, fn)):
                LOCAL.add(fn)

IMP = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def imports_of(path):
    try:
        s = io.open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return set()
    return {m.lower() for m in IMP.findall(s)}


def gate_files():
    gate = io.open(os.path.join(REPO, "tools", "test_gate.ps1"),
                   encoding="utf-8-sig").read()
    out = set()
    for m in re.finditer(r"'([^']+\.py)'", gate):
        rel = m.group(1)
        if rel.startswith(("portal", "quant_backtest", "agent_platform", "tools", "k_stock")):
            out.add(rel)
    # 目录套件
    for d in ("portal/tests", "k_stock/tests"):
        full = os.path.join(REPO, d.replace("/", os.sep))
        if os.path.isdir(full):
            for fn in os.listdir(full):
                if fn.startswith("test_") and fn.endswith(".py"):
                    out.add(d + "/" + fn)
    return sorted(out)


files = gate_files()
names = set()
for rel in files:
    names |= imports_of(os.path.join(REPO, rel.replace("/", os.sep)))

third = sorted(n for n in names
               if n not in STDLIB and n not in LOCAL and not n.startswith("_"))

print("覆盖文件数:", len(files))
print("第三方顶层包（%d）：" % len(third))
print(", ".join(third))

# 对账：本机是否真的装了（按 import 名猜发行版名）
try:
    from importlib.metadata import distributions
    dist_roots = {}
    for d in distributions():
        n = (d.metadata["Name"] or "").lower()
        top = set()
        for f in (d.files or []):
            p = str(f)
            if p.endswith(".py") or ".dist-info" in p:
                top.add(p.split("/")[0].split("\\")[0].lower())
        dist_roots[n] = top
    print()
    print("疑似未安装的 import 名：")
    miss = []
    for n in third:
        if not any(n in v or n.replace("_", "-") == k or n == k
                   for k, v in dist_roots.items()):
            miss.append(n)
    print(", ".join(miss) if miss else "（无）")
except Exception as exc:  # noqa: BLE001
    print("对账失败:", exc)
