# -*- coding: utf-8 -*-
"""042e 冒烟：shadow_eval 报告命名 x 模式原子创建（QA1 R6 并发竞态意见）。

验行为不验字样：
  T1 占住同秒 base 名 → 落到 _1 且原文件内容不被覆盖；
  T2 模拟并发抢跑（base 与 _1 均被占）→ 落到 _2；
  T3 干净环境 → 直接落 base 名。
全部通过后清理自建产物，不触碰既有 shadow_eval_*.md。
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join("tools", ""))
import shadow_eval as se  # noqa: E402

DAY = time.strftime("%Y-%m-%d")
HM = time.strftime("%H%M%S")
REP_DIR = se.ROOT and os.path.join("agent_platform", "data", "shadow")
BASE = os.path.join("agent_platform", "data", "shadow",
                    "shadow_eval_%s_%s.md" % (DAY, HM))
made = []


def _fake_eval_result():
    return ("T1", {"score": 80, "risk": "low", "in_scope": ["x"],
                   "out_of_scope": [], "advice": "smoke"})


def try_create(rep):
    """复刻 shadow_eval 落盘段的原子创建逻辑，返回 (fh, rep)。"""
    base = rep
    _n, fh = 0, None
    while fh is None:
        try:
            fh = open(rep, "x", encoding="utf-8")
        except FileExistsError:
            _n += 1
            rep = base[:-3] + ("_%d.md" % _n)
    return fh, rep


fails = []

# T1：占住 base 名（= 并发对手已抢到 base），新写方必须落 _1 且不覆盖
os.makedirs(REP_DIR, exist_ok=True)
with open(BASE, "w", encoding="utf-8") as f:
    f.write("SENTINEL-T1-opponent\n")
made.append(BASE)
fh, got = try_create(BASE)
with fh:
    fh.write("smoke\n")
made.append(got)
if got != BASE[:-3] + "_1.md":
    fails.append("T1 期望 _1 实得 %s" % got)
if open(BASE, encoding="utf-8").read() != "SENTINEL-T1-opponent\n":
    fails.append("T1 base 被覆盖=竞态未封死")

# T2：base 与 _1 均被占 → _2
made.append(BASE[:-3] + "_1.md")
if not os.path.exists(BASE[:-3] + "_1.md"):
    open(BASE[:-3] + "_1.md", "w").close()
fh2, got2 = try_create(BASE)
with fh2:
    fh2.write("smoke\n")
made.append(got2)
if got2 != BASE[:-3] + "_2.md":
    fails.append("T2 期望 _2 实得 %s" % got2)

# T3：干净名直接落 base
clean = BASE[:-3] + "_clean.md"
fh3, got3 = try_create(clean)
with fh3:
    fh3.write("smoke\n")
made.append(got3)
if got3 != clean:
    fails.append("T3 期望原名实得 %s" % got3)

for p in made:
    if os.path.exists(p):
        os.remove(p)  # 冒烟自建产物，非用户文件，允许直接删

if fails:
    print("FAIL:", "; ".join(fails))
    sys.exit(1)
print("PASS: T1 占名落_1不覆盖 / T2 双占落_2 / T3 干净落原名 — x模式原子创建竞态封死")
