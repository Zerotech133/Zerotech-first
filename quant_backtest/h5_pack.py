# -*- coding: utf-8 -*-
"""H5 打包：把 reports/ 里两个页面搬成「同目录、能互相相对跳转」的一套。

为什么需要这一步：
  两个页面是**双向深链**的（板块页点成分股 → 个股结构页，个股页点「所属板块」→ 板块页），
  跳转写的是相对文件名。线上站点只服务发布目录，`reports/` 不在里面 —— 只发布个股页的话，
  点「所属板块」就是 404，而且这种 404 在本地测不出来（本地 file:// 打两个文件都在）。

发布目录长这样（三个文件缺一不可）：

    h5_daily/
      index.html            个股页（根入口，`/?sharecode=...` 落在这，链接不用改）
      pool_report_h5.html   个股页（板块页跳过来用这个名字）
      sector_platform.html  板块页（个股页跳过去用这个名字）

  index.html 和 pool_report_h5.html 是同一份内容的两个名字：根路径必须是 index.html，
  而板块页里的 STOCK_H5 写的是 pool_report_h5.html，两边都要能命中。
  多一份 ~1MB 的副本，换掉的是「改名就得改代码」这种更贵的耦合。

自检（任一不过就非 0 退出，不允许把半套东西发布出去）：
  · 两个源文件存在且非空
  · 两个页面里所有 `xxx.html?...` 的引用，在发布目录里都真有这个文件
  · 发布目录里的副本不比源头旧

用法:
  python h5_pack.py            # 打包 + 自检
  python h5_pack.py --dry      # 只自检，不落盘
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
DEST = os.path.join(HERE, "h5_daily")

STOCK_SRC = os.path.join(REPORTS, "pool_report_h5.html")
SECTOR_SRC = os.path.join(REPORTS, "sector_platform.html")
PC_SRC = os.path.join(REPORTS, "pool_pc.html")

# (源文件, 发布目录里的名字)
PLAN = [
    (STOCK_SRC, "index.html"),
    (STOCK_SRC, "pool_report_h5.html"),
    (SECTOR_SRC, "sector_platform.html"),
    (PC_SRC, "pc.html"),
]

# 页面内部引用的本地 html 文件名：形如 'sector_platform.html?sector=...'
REF_RE = re.compile(r"""['"]([A-Za-z0-9_\-]+\.html)(?:\?[^'"]*)?['"]""")


def log(msg):
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg))


def local_refs(path):
    """页面里出现的本地 html 引用（不含 http/file 绝对地址）。"""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    refs = set()
    for m in REF_RE.finditer(txt):
        name = m.group(1)
        # 排除外链写法 "https://x/y.html" —— 正则只抓裸文件名，天然隔离；
        # 这里再兜一层，防止路径里带目录.
        if "/" in name or "\\" in name:
            continue
        refs.add(name)
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只自检，不写文件")
    a = ap.parse_args()

    # 1) 源文件
    for src in (STOCK_SRC, SECTOR_SRC, PC_SRC):
        if not os.path.exists(src):
            log("[fail] 缺源文件 %s —— 先跑对应的页面脚本" % src)
            return 1
        if os.path.getsize(src) < 10000:
            log("[fail] %s 只有 %d 字节，像是空壳"
                % (os.path.basename(src), os.path.getsize(src)))
            return 1
        log("%-24s %7.1f KB  %s"
            % (os.path.basename(src), os.path.getsize(src) / 1024.0,
               datetime.fromtimestamp(os.path.getmtime(src)).strftime("%m-%d %H:%M")))

    names = set(n for _, n in PLAN)

    # 2) 引用闭环：页面里点了的，发布目录里必须有
    bad = []
    for src in (STOCK_SRC, SECTOR_SRC, PC_SRC):
        for ref in local_refs(src):
            if ref not in names:
                bad.append((os.path.basename(src), ref))
    if bad:
        for who, ref in bad:
            log("[fail] %s 里引用了 %s，但打包清单里没有这个名字" % (who, ref))
        log("       要么把 %s 加进 PLAN，要么改页面里的跳转常量。" % bad[0][1])
        return 1
    log("引用闭环 OK：%d 个相对跳转目标，打包清单全覆盖" % len(names))

    if a.dry:
        log("--dry：不写文件")
        return 0

    # 3) 落盘
    os.makedirs(DEST, exist_ok=True)
    for src, name in PLAN:
        dst = os.path.join(DEST, name)
        shutil.copyfile(src, dst)
        # copyfile 保留不了 mtime，显式对齐，方便下一步判断「线上是不是旧的」
        os.utime(dst, (os.path.getatime(src), os.path.getmtime(src)))

    # 4) 复检：三个文件都在、都不比源头旧
    for src, name in PLAN:
        dst = os.path.join(DEST, name)
        if not os.path.exists(dst):
            log("[fail] 打包后 %s 不见了" % name)
            return 1
        if os.path.getsize(dst) != os.path.getsize(src):
            log("[fail] %s 大小和源头不一致" % name)
            return 1

    total = sum(os.path.getsize(os.path.join(DEST, n)) for n in names)
    log("打包完成 → %s" % DEST)
    for n in sorted(names):
        p = os.path.join(DEST, n)
        log("   %-22s %7.1f KB  %s"
            % (n, os.path.getsize(p) / 1024.0,
               datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")))
    log("合计 %.1f MB（整套发布，四个文件一起上）" % (total / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
