# -*- coding: utf-8 -*-
"""
sync_publish.py — X5 快照刷新一键脚本
将 portal/ 源头文件同步到 portal/picks_public/ 公网发布快照目录。
只做本地快照刷新；公网发布（deploy 到 WorkBuddy 站点托管）是第二步动作，另行触发。

路径映射表（K-STOCK-411 止血后，仅三件白名单）：
    portal/picks.html             -> portal/picks_public/picks.html
    portal/static/tokens.css      -> portal/picks_public/static/tokens.css
    portal/data/todays_picks.json -> portal/picks_public/data/todays_picks.json
（ledger/ledger.json/pick_details/server.py 已停发并移档，见 SENSITIVE_REMOVAL）

同步规则：
    - sha256 变更检测：相同跳过，只写变化的文件（避免无谓 IO 打到在跑的 server.py 网关）
    - 原子写入：先写 <target>.tmp 再 os.replace，防半文件被网关读到
    - pick_details 差异同步：源新增 -> 复制；两处都有且不同 -> 覆盖；
      源没有而目标有 -> 保留不删（历史详情可能仍有访问价值，仅报告数量）
    - 零第三方依赖，仅标准库

用法：
    python sync_publish.py             # 真同步
    python sync_publish.py --dry-run   # 只列计划动作，不写任何文件
退出码：0 成功（含 dry-run）；非 0 表示有文件级异常。
"""

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time

# 项目根 = 本脚本所在目录（portal/；v1.1 起脚本自 picks_public/ 移至 portal/ 根以便 git 跟踪）
PORTAL = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(PORTAL)
DST = os.path.join(PORTAL, "picks_public")

SINGLE_FILES = [
    ("picks.html", "picks.html"),
    (os.path.join("static", "tokens.css"), os.path.join("static", "tokens.css")),
    (os.path.join("data", "todays_picks.json"), os.path.join("data", "todays_picks.json")),
]
# K-STOCK-411 止血判例（2026-10-15 线上取证）：CloudStudio Gateway 为静态
# 直出、不执行 Python 进程——曾发布的 ledger/ledger.json/pick_details/
# server.py 全部无码 200 裸奔（server.py 更是口令明文裸下载）。
# 敏感件一律停发，快照残留移档（见 remove_sensitive），线上待 deploy 下架。
# 若未来网关支持边缘鉴权/进程托管，再评审恢复（届时 server_gate.py 源头仍在 git）。
SENSITIVE_REMOVAL = [
    "ledger.html",                 # K-STOCK-353 交易台账页（41 笔持仓/胜率/盈亏）
    os.path.join("data", "ledger.json"),
    os.path.join("data", "pick_details"),  # 个股明细目录（止损位/操作方案）
    "server.py",                   # 口令明文（v1.1 旧版滞留线上）
]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_copy(src_path, dst_path, dry_run):
    """先写 .tmp 再 os.replace，保证网关不会读到半文件。"""
    if dry_run:
        return
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(dst_path), prefix=os.path.basename(dst_path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as out, open(src_path, "rb") as src:
            shutil.copyfileobj(src, out, 65536)
        os.replace(tmp_path, dst_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def classify(src_path, dst_path):
    """返回 (动作, 是否同 hash)。动作: add/update/skip；dst 不存在时同 hash=False。"""
    if not os.path.exists(dst_path):
        return "add", False
    return ("skip", True) if sha256_of(src_path) == sha256_of(dst_path) else ("update", False)


def remove_sensitive(dry_run):
    """K-STOCK-411：敏感件从快照移档归档（portal/work/unpublished/<date>/）。

    移档≠删除（Windows 删除红线），同卷 os.replace 原子搬移；重复执行时
    目标已存在则追加时间戳后缀防覆盖。返回移档相对路径列表。
    """
    archive_root = os.path.join(PORTAL, "work", "unpublished",
                                time.strftime("%Y%m%d"))
    moved = []
    for rel in SENSITIVE_REMOVAL:
        dst = os.path.join(DST, rel)
        if not os.path.exists(dst):
            continue
        if dry_run:
            moved.append(rel)
            continue
        tgt = os.path.join(archive_root, rel)
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.exists(tgt):
            base, ext = os.path.splitext(tgt)
            tgt = "%s_%s%s" % (base, time.strftime("%H%M%S"), ext)
        os.replace(dst, tgt)
        moved.append(rel)
    return moved


def main():
    parser = argparse.ArgumentParser(description="刷新 picks_public 快照目录")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划动作，不写文件")
    args = parser.parse_args()

    t0 = time.time()
    stats = {"add": 0, "update": 0, "skip": 0, "removed": 0}
    errors = []

    # --- 敏感件移档（先于复制：防旧残留随 deploy 再次上线）---
    try:
        moved = remove_sensitive(args.dry_run)
        stats["removed"] = len(moved)
        for rel in moved:
            print("[移档] %s → work/unpublished/" % rel)
    except OSError as e:
        errors.append("敏感件移档失败: %s" % e)
        print("[错误] 敏感件移档失败: %s" % e)

    # --- 单文件清单 ---
    for rel_src, rel_dst in SINGLE_FILES:
        src_path = os.path.join(SRC, rel_src)
        dst_path = os.path.join(DST, rel_dst)
        if not os.path.exists(src_path):
            errors.append("源缺失: %s" % rel_src)
            continue
        try:
            action, _ = classify(src_path, dst_path)
            if action == "skip":
                stats["skip"] += 1
                print("[跳过] %s (hash 相同)" % rel_dst)
            else:
                stats[action] += 1
                print("[%s] %s" % ("新增" if action == "add" else "更新", rel_dst))
                atomic_copy(src_path, dst_path, args.dry_run)
        except OSError as e:
            # 审计 P2-4（2026-10-14）：单文件被占用/读失败不再炸全链，记错误继续
            errors.append("%s: %s" % (rel_dst, e))
            print("[错误] %s: %s" % (rel_dst, e))

    # --- 汇总 ---
    elapsed = time.time() - t0
    print("")
    print("==== 快照刷新统计%s ====" % ("（dry-run，未写盘）" if args.dry_run else ""))
    print("新增: %d  更新: %d  跳过(hash相同): %d  敏感移档: %d" % (
        stats["add"], stats["update"], stats["skip"], stats["removed"]))
    print("总耗时: %.2fs" % elapsed)

    if errors:
        for e in errors:
            print("[错误] %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
