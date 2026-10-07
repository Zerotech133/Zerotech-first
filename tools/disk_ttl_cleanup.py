# -*- coding: utf-8 -*-
"""
disk_ttl_cleanup.py — 运行产物磁盘 TTL 清理（默认 dry-run）
============================================================
背景：monorepo 各 Agent 产线持续落盘运行产物（parquet 分区/日志/备份），
git 快照库已被 .gitignore + 膨胀守卫挡住，但磁盘本身会越来越重
（实测 72h 内 quant_backtest 重写 3.4 万个 parquet/2.4GB，backups 已 34.8GB）。
本工具按"保留窗口"清理过期产物，与快照入库体系解耦。

用法：
  预览（默认，不删任何东西）：
    python tools/disk_ttl_cleanup.py --target quant_backtest/data --days 30
  真删（需交互输入 EXECUTE 二次确认，防自动化误跑）：
    python tools/disk_ttl_cleanup.py --target quant_backtest/data --days 30 --execute

规则：
  - 只删 mtime 早于 --days 的文件；删完后顺带清掉变空的子目录
  - --target 可重复传多个；路径必须是仓库内相对路径
  - 拒绝目标是仓库根、.git、或含 .git 的路径（保底防误伤源码）
  - 每次执行追加日志 portal/logs/disk_cleanup.log
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"E:/老田的AI助手")
LOG_FILE = REPO / "portal" / "logs" / "disk_cleanup.log"


def collect_expired(target: Path, cutoff: float):
    expired, freed = [], 0
    for dp, _dn, fn in target.walk():
        for f in fn:
            p = dp / f
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                expired.append(p)
                freed += st.st_size
    return expired, freed


def remove_empty_dirs(target: Path) -> int:
    removed = 0
    for dp, _dn, _fn in target.walk(top_down=False):
        try:
            next(dp.iterdir())
        except StopIteration:
            try:
                dp.rmdir()
                removed += 1
            except OSError:
                pass
        except OSError:
            continue
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="运行产物 TTL 清理（默认 dry-run）")
    ap.add_argument("--target", action="append", required=True,
                    help="仓库内相对路径，可重复")
    ap.add_argument("--days", type=int, required=True, help="保留天数")
    ap.add_argument("--execute", action="store_true", help="真删（默认仅预览）")
    args = ap.parse_args()

    cutoff = time.time() - args.days * 86400
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    targets = []
    for t in args.target:
        p = (REPO / t).resolve()
        if p == REPO.resolve() or REPO.resolve() not in p.parents or ".git" in p.parts:
            print(f"[拒绝] 目标不合法或指向仓库根/.git: {t}")
            return 2
        targets.append((t, p))

    plan = []
    for name, p in targets:
        if not p.exists():
            print(f"[跳过] 不存在: {name}")
            continue
        expired, freed = collect_expired(p, cutoff)
        plan.append((name, p, expired, freed))
        print(f"[预览] {name}: 将删 {len(expired)} 个文件, "
              f"释放 {freed / 1048576:.1f} MB (mtime < {args.days}天)")

    if not args.execute:
        print("dry-run 结束：未删除任何文件。确认后加 --execute。")
        return 0

    if not plan:
        print("无可清理目标。")
        return 0

    ans = input(f"将对 {sum(len(e) for _, _, e, _ in plan)} 个文件执行删除，"
                f"输入 EXECUTE 确认: ").strip()
    if ans != "EXECUTE":
        print("已取消。")
        return 1

    log_lines = [f"{ts} | EXECUTE days={args.days}"]
    for name, p, expired, freed in plan:
        n_ok = 0
        for f in expired:
            try:
                f.unlink()
                n_ok += 1
            except OSError as e:
                log_lines.append(f"  FAIL {f}: {e}")
        n_dirs = remove_empty_dirs(p)
        log_lines.append(f"  {name}: 删{n_ok}/{len(expired)}文件, "
                         f"清{n_dirs}空目录, 释放{freed / 1048576:.1f}MB")
        print(f"[完成] {name}: 删 {n_ok}/{len(expired)} 个文件, 清 {n_dirs} 个空目录")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write("\n".join(log_lines) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
