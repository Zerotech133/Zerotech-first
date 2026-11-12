# -*- coding: utf-8 -*-
"""平台打包：把 reports/ 里的分析页面整理成一套「有门面、能互跳、同目录」的独立平台。

这个脚本以前只搬 4 个文件（结构日报 / 板块 / PC），因为那时平台上只有缠论一条线。
现在它是**平台构建的唯一入口**：清单与修补逻辑在 platform_pages.py，本文件负责编排 +
自检，不再自己 copy 文件（否则会把门面覆盖成结构日报）。

发布目录长这样：

    h5_daily/
      index.html              平台门面（根入口，分享链接落点）
      pool_report_h5.html     结构日报（板块页跳回来用这个名字）
      sector_platform.html    板块平台
      pc.html                 结构工作台
      premarket.html ...      另外 20 个分析页（每个都带统一导航条）
      col_audit_summary.html  数据源列对账（取最新一份）

为什么必须同目录：
  页面之间是**相对文件名**跳转的。线上只服务发布目录，`reports/` 不在里面 ——
  缺一个文件，那条跳转就是 404，而本地 file:// 打两个文件都在，测不出来。

自检（任一不过就非 0 退出，不允许把半套东西发布出去）：
  · 核心源文件存在且非空
  · 发布目录里每个被页面引用的 xxx.html 都真实存在（引用闭环）
  · 修补后的页面里不再有指向门户服务的可点链接（避免点下去白屏）
  · 门面里的每张卡片都能在发布目录里找到目标文件

用法:
  python h5_pack.py            # 构建 + 自检
  python h5_pack.py --dry      # 只自检，不落盘
"""

import argparse
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPORTS = os.path.join(HERE, "reports")
DEST = os.path.join(HERE, "h5_daily")

import platform_pages as PP  # noqa: E402

# 平台的四根柱子：缺任何一个，平台就不是完整的（其余页缺了只是少一个入口）
# 模拟盘那页不进 CORE —— 它读 portfolio.json，账本可能不存在（当前就停更了），
# 缺账本不该把 18:30 的流水线整条打断。
CORE = [
    ("pool_report_h5.html", "结构日报"),
    ("sector_platform.html", "板块平台"),
    ("pool_pc.html", "结构工作台"),
    ("chanlun_tool.html", "缠论分析工具"),
]

REF_RE = re.compile(r"""['"]([A-Za-z0-9_\-]+\.html)(?:\?[^'"]*)?['"]""")


def log(msg):
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg))


def check_sources():
    bad = 0
    for name, label in CORE:
        p = os.path.join(REPORTS, name)
        if not os.path.exists(p):
            log("[fail] 缺源文件 %s（%s）—— 先跑对应页面脚本" % (name, label))
            bad += 1
            continue
        sz = os.path.getsize(p)
        if sz < 10000:
            log("[fail] %s 只有 %d 字节，像空壳" % (name, sz))
            bad += 1
            continue
        log("%-26s %7.1f KB  %s" % (name, sz / 1024.0,
                                    datetime.fromtimestamp(
                                        os.path.getmtime(p)).strftime("%m-%d %H:%M")))
    return bad == 0


def refs_of(path):
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return set()
    out = set()
    for m in REF_RE.finditer(txt):
        name = m.group(1)
        if "/" in name or "\\" in name:
            continue
        out.add(name)
    return out


def verify(strict=True):
    """发布目录自检：引用闭环 + 无残留门户链接 + 门面卡片可达。"""
    if not os.path.isdir(DEST):
        log("[fail] 发布目录不存在：%s" % DEST)
        return False

    have = {f for f in os.listdir(DEST) if f.endswith(".html")}
    ok = True

    # ① 引用闭环：页面点了的，目录里必须有
    bad = []
    for f in sorted(have):
        for r in refs_of(os.path.join(DEST, f)):
            if r not in have:
                bad.append((f, r))
    if bad and strict:
        for who, ref in bad[:10]:
            log("[fail] %s 引用了 %s，但发布目录里没有" % (who, ref))
        log("       共 %d 处。要么补进 CATALOG，要么改页面里的跳转。" % len(bad))
        ok = False
    elif bad:
        log("[warn] %d 处引用缺失（非严格模式）" % len(bad))

    # ② 不该还有指向门户服务的可点 <a>
    live_dead = []
    for f in sorted(have):
        txt = open(os.path.join(DEST, f), encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r'<a\b[^>]*href=["\'](%s)[^"\']*["\']'
                             % "|".join(re.escape(p) for p in PP.DEAD_ROUTES), txt):
            live_dead.append((f, m.group(1)))
    if live_dead:
        for who, href in live_dead[:6]:
            log("[fail] %s 里仍有可点的门户链接 %s" % (who, href))
        log("       共 %d 处没被标灰——点下去会白屏。" % len(live_dead))
        ok = False

    # ③ 门面卡片都得能点开
    home = os.path.join(DEST, "index.html")
    if not os.path.exists(home):
        log("[fail] 缺门面 index.html")
        return False
    htxt = open(home, encoding="utf-8", errors="ignore").read()
    # hero 大卡也是 class="card ..."（card hero），放宽匹配
    cards = re.findall(r'<a class="card[^"]*" href="([^"]+)"', htxt)
    # 去掉 query 与锚点再比文件：premarket.html#zen 指向的是 premarket.html
    miss = [c for c in cards if c.split("?")[0].split("#")[0] not in have]
    if miss:
        for c in miss[:6]:
            log("[fail] 门面卡片指向 %s，文件不存在" % c)
        ok = False
    log("门面 %d 张卡片 · 发布目录 %d 个页面 · 引用闭环 %s"
        % (len(cards), len(have), "OK" if not bad else "有缺口"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只自检，不写文件")
    a = ap.parse_args()

    log("=== 1/3 源文件 ===")
    if not check_sources():
        return 1

    if a.dry:
        log("=== 2/3 --dry：不落盘，只检查现有发布目录 ===")
        return 0 if verify() else 1

    log("=== 2/3 修补内页 + 生成门面 ===")
    rows = PP.collect()
    PP.build_home(rows)
    core_names = {n for n, _ in CORE}
    hard, soft = [], []
    for item in PP.CATALOG:
        try:
            PP.patch_one(item)
        except Exception as e:  # noqa: BLE001
            (hard if item["src"] in core_names else soft).append((item["src"], str(e)))
    # 分级：四根柱子缺了 = 平台不完整，必须停；其余页缺了只是少一个入口，
    # 不能让「某个历史页被清理」把整条 18:30 流水线打断。
    if soft:
        log("[warn] %d 个非核心页跳过（源文件不在或读取失败）：" % len(soft))
        for s, e in soft:
            log("       %s → %s" % (s, e))
    if hard:
        for s, e in hard:
            log("[fail] 核心页 %s → %s" % (s, e))
        return 1
    log("修补 %d 个页面（跳过 %d）" % (len(PP.CATALOG) - len(soft), len(soft)))

    log("=== 3/3 自检 ===")
    if not verify():
        return 1

    names = [f for f in os.listdir(DEST) if f.endswith(".html")]
    total = sum(os.path.getsize(os.path.join(DEST, n)) for n in names)
    log("打包完成 → %s" % DEST)
    for n in sorted(names):
        p = os.path.join(DEST, n)
        log("   %-30s %7.1f KB" % (n, os.path.getsize(p) / 1024.0))
    log("合计 %d 个页面 %.1f MB（整套发布，缺一不可）"
        % (len(names), total / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
