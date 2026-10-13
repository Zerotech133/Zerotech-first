# -*- coding: utf-8 -*-
"""
sync_publish.py — X5 快照刷新一键脚本
将 portal/ 源头文件同步到 portal/picks_public/ 公网发布快照目录。
只做本地快照刷新；公网发布（deploy 到 WorkBuddy 站点托管）是第二步动作，另行触发。

路径映射表（2026-10-08 实际核对，源/目标以实际目录结构为准）：
    portal/picks.html            -> portal/picks_public/picks.html
    portal/static/tokens.css     -> portal/picks_public/static/tokens.css
    portal/data/todays_picks.json -> portal/picks_public/data/todays_picks.json
    portal/data/pick_details/*.json -> portal/picks_public/data/pick_details/*.json

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
    ("ledger.html", "ledger.html"),  # K-STOCK-353 交易台账
    (os.path.join("static", "tokens.css"), os.path.join("static", "tokens.css")),
    (os.path.join("data", "todays_picks.json"), os.path.join("data", "todays_picks.json")),
    (os.path.join("data", "ledger.json"), os.path.join("data", "ledger.json")),  # K-STOCK-353
]
DETAILS_SUBDIR = os.path.join("data", "pick_details")


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


def main():
    parser = argparse.ArgumentParser(description="刷新 picks_public 快照目录")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划动作，不写文件")
    args = parser.parse_args()

    t0 = time.time()
    stats = {"add": 0, "update": 0, "skip": 0, "kept": 0}
    errors = []

    # --- 单文件清单 ---
    for rel_src, rel_dst in SINGLE_FILES:
        src_path = os.path.join(SRC, rel_src)
        dst_path = os.path.join(DST, rel_dst)
        if not os.path.exists(src_path):
            errors.append("源缺失: %s" % rel_src)
            continue
        action, _ = classify(src_path, dst_path)
        if action == "skip":
            stats["skip"] += 1
            print("[跳过] %s (hash 相同)" % rel_dst)
        else:
            stats[action] += 1
            print("[%s] %s" % ("新增" if action == "add" else "更新", rel_dst))
            atomic_copy(src_path, dst_path, args.dry_run)

    # --- pick_details 差异同步 ---
    src_dir = os.path.join(SRC, DETAILS_SUBDIR)
    dst_dir = os.path.join(DST, DETAILS_SUBDIR)
    src_files = sorted(f for f in os.listdir(src_dir) if f.endswith(".json")) if os.path.isdir(src_dir) else []
    dst_files = set()
    if os.path.isdir(dst_dir):
        dst_files = {f for f in os.listdir(dst_dir) if f.endswith(".json")}

    for name in src_files:
        src_path = os.path.join(src_dir, name)
        dst_path = os.path.join(dst_dir, name)
        action, _ = classify(src_path, dst_path)
        if action == "skip":
            stats["skip"] += 1
        else:
            stats[action] += 1
            atomic_copy(src_path, dst_path, args.dry_run)

    # 源没有而目标有的历史文件：保留不删，只计数
    src_set = set(src_files)
    kept = sorted(dst_files - src_set)
    stats["kept"] = len(kept)

    # --- 汇总 ---
    elapsed = time.time() - t0
    print("")
    print("==== 快照刷新统计%s ====" % ("（dry-run，未写盘）" if args.dry_run else ""))
    print("新增: %d  更新: %d  跳过(hash相同): %d  保留(仅目标有,未删): %d" % (
        stats["add"], stats["update"], stats["skip"], stats["kept"]))
    print("pick_details 源: %d 个, 目标: %d 个, 保留名单示例: %s" % (
        len(src_files), len(dst_files), ", ".join(kept[:5]) if kept else "无"))
    print("总耗时: %.2fs" % elapsed)

    if errors:
        for e in errors:
            print("[错误] %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
