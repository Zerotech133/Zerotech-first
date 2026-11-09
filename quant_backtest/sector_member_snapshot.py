# -*- coding: utf-8 -*-
"""同花顺板块成分快照留存 —— 上游只给你「今天」，明天就没了。

ts_ths_member 是这套系统里唯一拿不到历史的东西。它有 38 万行、覆盖 1,666
个板块的成分，但接口只返回**当前**成分，没有日期列；上游翻新一次，上一版
就永久消失。没有历史成分，就没有板块共振的 PIT 回放——而共振恰恰是
「板块 + 个股」这一层最值钱的部分。

代价很小：上游这张表是月度级更新（本地这份 _fetched_at 是 2026-10-13，
11-05 复查远端仍是它），一年最多十几份、每份约 2MB。不存，半年后想回放
就得从那时重新等半年。

同时记一份变更：哪只票新进了哪个板块、哪只被剔除。这本身就是有用的数据
——「某只票被调进某个概念」经常发生在概念启动之前，而这层信息现在完全
没被利用。

输出：
  data/sector_member_snap/member_<YYYYMMDD>.parquet   全量快照（内容与上一份相同则跳过）
  data/sector_member_snap/changes.jsonl               每次变更明细（追加，不覆盖）

用法：
  python sector_member_snapshot.py            # 有变化才落盘
  python sector_member_snapshot.py --check     # 只报差异，不写任何东西
  python sector_member_snapsho.py --list      # 看已存了哪些快照
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "tdx", "ts_ths_member")
OUT = os.path.join(HERE, "data", "sector_member_snap")
CHANGES = os.path.join(OUT, "changes.jsonl")
COLS = ["ts_code", "con_code"]


def load_now():
    """当前成分表 → (DataFrame, 指纹)。

    指纹只取 (板块, 成分) 两列排序后算 md5：_fetched_at 每次拉取都会变，
    带进指纹会让「内容没变」被判成「变了」，天天存一份一模一样的 2MB。
    """
    fs = sorted(glob.glob(os.path.join(SRC, "part_*.parquet")))
    if not fs:
        raise SystemExit("[fail] 没有 %s/part_*.parquet，先跑 tdx 同步" % SRC)
    df = pd.concat([pd.read_parquet(f, columns=COLS) for f in fs],
                   ignore_index=True)
    df = df.astype(str).drop_duplicates()
    df = df.sort_values(COLS).reset_index(drop=True)
    h = hashlib.md5(
        ("\n".join(df["ts_code"] + "|" + df["con_code"])).encode()).hexdigest()[:12]
    return df, h


def snapshots():
    return sorted(glob.glob(os.path.join(OUT, "member_*.parquet")))


def latest():
    fs = snapshots()
    return fs[-1] if fs else None


def show_diff(old: pd.DataFrame, new: pd.DataFrame, top=12):
    """按板块分组报差异：哪个板块加了谁、删了谁。"""
    ok = set(zip(old["ts_code"], old["con_code"]))
    nk = set(zip(new["ts_code"], new["con_code"]))
    add, dele = nk - ok, ok - nk
    if not add and not dele:
        return 0, 0

    print("  新增 %d 条 / 剔除 %d 条" % (len(add), len(dele)))
    for lab, s in (("+", add), ("-", dele)):
        if not s:
            continue
        by = {}
        for sec, con in s:
            by.setdefault(sec, []).append(con)
        rank = sorted(by.items(), key=lambda kv: -len(kv[1]))[:top]
        for sec, cons in rank:
            print("   %s %-12s %3d 只  %s" % (
                lab, sec, len(cons), " ".join(sorted(cons)[:6])
                + (" …" if len(cons) > 6 else "")))
        if len(by) > top:
            print("    （另有 %d 个板块有%s）" % (len(by) - top,
                                              "新增" if lab == "+" else "剔除"))
    return len(add), len(dele)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报差异，不落盘")
    ap.add_argument("--list", action="store_true", help="列出已存快照")
    ap.add_argument("--force", action="store_true", help="内容没变也存一份")
    a = ap.parse_args()

    if a.list:
        fs = snapshots()
        if not fs:
            print("还没有任何快照")
            return 0
        print("已存 %d 份：" % len(fs))
        for f in fs:
            n = len(pd.read_parquet(f, columns=["ts_code"]))
            print("  %s  %.1f KB  %d 行" % (os.path.basename(f),
                                            os.path.getsize(f) / 1024, n))
        return 0

    now, h = load_now()
    print("当前成分表：%d 行 / %d 个板块 / 指纹 %s"
          % (len(now), now["ts_code"].nunique(), h))

    prev_f = latest()
    if not prev_f:
        print("此前没有任何快照——这是第一份，往后的每次变更都能被记住。")
        if a.check:
            print("[check] 不落盘")
            return 0
        os.makedirs(OUT, exist_ok=True)
        dst = os.path.join(OUT, "member_%s.parquet" % datetime.now().strftime("%Y%m%d"))
        now.to_parquet(dst, index=False)
        print("[ok] 存下 %s（%.1f KB）" % (os.path.basename(dst),
                                           os.path.getsize(dst) / 1024))
        return 0

    prev = pd.read_parquet(prev_f).astype(str)
    same = (len(prev) == len(now)) and not (
        set(zip(prev["ts_code"], prev["con_code"])) ^
        set(zip(now["ts_code"], now["con_code"])))
    print("上一份：%s（%d 行）" % (os.path.basename(prev_f), len(prev)))

    if same and not a.force:
        print("内容与上一份完全相同，跳过（上游这张表按月度更新，天天存只是浪费空间）。")
        return 0

    print("内容有变化：")
    n_add, n_del = show_diff(prev, now)
    if a.check:
        print("[check] 不落盘")
        return 0

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "member_%s.parquet" % datetime.now().strftime("%Y%m%d"))
    if os.path.exists(dst):
        dst = dst.replace(".parquet", "_%s.parquet" % datetime.now().strftime("%H%M%S"))
    now.to_parquet(dst, index=False)

    rec = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file": os.path.basename(dst),
        "n_rows": len(now), "n_sectors": int(now["ts_code"].nunique()),
        "prev": os.path.basename(prev_f),
        "add": n_add, "del": n_del,
        "fp": h,
    }
    with open(CHANGES, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("[ok] 存下 %s（%.1f KB），变更已追加 changes.jsonl"
          % (os.path.basename(dst), os.path.getsize(dst) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
