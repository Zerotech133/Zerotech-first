# -*- coding: utf-8 -*-
"""K-STOCK-349 · S1 封存脚本干跑预检（临时假结构端到端·零污染）。"""
import hashlib
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = r"E:\老田的AI助手"
T = os.path.join(os.environ.get("TEMP", "/tmp"), "s1seal_dryrun")
shutil.rmtree(T, ignore_errors=True)

for sub in (
    ("agent_platform", "data", "shadow"),
    ("agent_platform", "data", "archive"),
    ("docs", "ops"),
    ("k_stock", "data"),
    ("quant_backtest"),
):
    os.makedirs(os.path.join(T, *sub) if isinstance(sub, tuple) else os.path.join(T, sub), exist_ok=True)

# 假文件
open(os.path.join(T, "agent_platform", "data", "shadow", "shadow_ledger.jsonl"), "w", encoding="utf-8").write('{"date":"2026-10-13"}\n')
shutil.copy(os.path.join(ROOT, "agent_platform", "data", "checkpoints.db"), os.path.join(T, "agent_platform", "data", "checkpoints.db"))
REP = os.path.join(T, "agent_platform", "data", "shadow", "eval_shadow_2026-10-14_official.md")
open(REP, "w", encoding="utf-8").write("# 门禁正式报告（假）\nJ2 rho=+0.707\n")
open(os.path.join(T, "docs", "ops", "2026-10-13_合流就绪度检查_1015预检_K-STOCK.md"), "w", encoding="utf-8").write("预检\n")
open(os.path.join(T, "docs", "ops", "2026-10-13_K-ORG-043_双通道产出横向对比.md"), "w", encoding="utf-8").write("对比\n")
for i in range(3):
    open(os.path.join(T, "k_stock", "data", "poc_dry_%d.json" % i), "w", encoding="utf-8").write("{}")
shutil.copy(os.path.join(ROOT, "quant_backtest", "_s1_seal.py"), os.path.join(T, "quant_backtest", "_s1_seal.py"))

SCRIPT = os.path.join(T, "quant_backtest", "_s1_seal.py")

# 1) ABORT 分支：删假报告再跑
os.remove(REP)
r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=T)
tail = (r.stderr or r.stdout).strip().splitlines()[-1][:70]
print("ABORT 分支 rc=%d · %s" % (r.returncode, tail))
assert r.returncode != 0 and "ABORT" in (r.stderr or "") + (r.stdout or "")

# 2) PASS 分支：放回假报告
open(REP, "w", encoding="utf-8").write("# 门禁正式报告（假）\n")
r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=T)
print("PASS 分支 rc=%d" % r.returncode)
print("\n".join(r.stdout.strip().splitlines()[-4:]))
assert r.returncode == 0

# 3) MANIFEST 完整性抽验
zp = glob.glob(os.path.join(T, "agent_platform", "data", "archive", "s1_seal_*.zip"))[0]
z = zipfile.ZipFile(zp)
names = z.namelist()
man = z.read("MANIFEST.txt").decode("utf-8")
ok = all(hashlib.sha256(z.read(n)).hexdigest() in man for n in names if n != "MANIFEST.txt")
print("zip 条目: %d · MANIFEST sha256 全对: %s" % (len(names), ok))
assert ok and len(names) == 3 + 1 + 3 + 1  # 3 文档+db+ledger? 见清单: ledger+db+rep+2doc+3poc+MANIFEST=9
print("=== 干跑全过 · 清理 ===")
shutil.rmtree(T, ignore_errors=True)
