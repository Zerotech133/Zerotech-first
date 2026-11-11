# -*- coding: utf-8 -*-
"""ops 备份：把 git 追踪不到、又不可再生的关键产物做日期快照。

备份范围（2026-11-10 审计定）：
  - data/shadow/*.json          影子台账：真实动作差记录，**不可再生**（F1 转正验收的原始证据）
  - reports/bt10m_*.json        bt10m 线全部实验报告：重跑要几十分钟且依赖当日引擎版本

不备份（可再生，scan/引擎重算即可）：
  - pool_state/sector_state/页面 html/日志
  - 源码与文档（git 已管，另有服务器 git bundle 03:40 每日备份）

保留：最近 KEEP 份日期快照，旧的自动删除。幂等：同一天重跑覆盖同目录。
用法：python ops_backup.py          # 备份 + 轮转
      python ops_backup.py --drill  # 备份后自动做一次恢复演练（md5 比对）
"""
import argparse
import hashlib
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP = 30
SOURCES = [
    (os.path.join(HERE, "data", "shadow"), "shadow"),
    (os.path.join(HERE, "reports"), "bt10m_*.json"),
]


def md5(p, bs=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(bs), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", action="store_true",
                    help="备份后抽 2 个文件做恢复演练（md5 比对）")
    a = ap.parse_args()

    from datetime import date
    day = date.today().strftime("%Y%m%d")
    dst_root = os.path.join(HERE, "backup", "ops", day)
    os.makedirs(dst_root, exist_ok=True)

    n, total = 0, 0
    for src_dir, pat in SOURCES:
        if pat.endswith(".json") and not pat.startswith("bt10m"):
            pass
        import glob as _g
        if os.path.isdir(src_dir) and pat == "shadow":
            fs = sorted(_g.glob(os.path.join(src_dir, "*.json")))
        else:
            fs = sorted(_g.glob(os.path.join(src_dir, pat)))
        for f in fs:
            rel = os.path.join("shadow" if pat == "shadow" else "reports",
                               os.path.basename(f))
            d = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(f, d)
            n += 1
            total += os.path.getsize(f)
    print("备份 %d 个文件，%.1f MB → %s" % (n, total / 1e6, dst_root))

    # 轮转：按目录名排序，删超过 KEEP 的旧快照
    root = os.path.join(HERE, "backup", "ops")
    days = sorted(os.listdir(root))
    for old in days[:-KEEP]:
        shutil.rmtree(os.path.join(root, old))
        print("轮转删除旧快照 %s" % old)

    if a.drill:
        import random
        picks = []
        for sub in ("shadow", "reports"):
            dd = os.path.join(dst_root, sub)
            if os.path.isdir(dd):
                fs = sorted(os.listdir(dd))
                if fs:
                    picks.append((sub, random.choice(fs)))
        assert len(picks) >= 2, "备份后文件不足，演练失败"
        for sub, name in picks:
            if sub == "shadow":
                orig = os.path.join(HERE, "data", "shadow", name)
            else:
                orig = os.path.join(HERE, "reports", name)
            back = os.path.join(dst_root, sub, name)
            m1, m2 = md5(orig), md5(back)
            assert m1 == m2, "恢复演练失败：md5 不一致 %s/%s" % (sub, name)
            print("演练通过：%s/%s md5=%s" % (sub, name, m1[:12]))
        print("恢复演练 PASS（%d 个抽样文件逐字节一致）" % len(picks))


if __name__ == "__main__":
    main()
