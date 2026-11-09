"""TdxSync 长尾兜底批（2026-11-05，data_hub 对账后补位）。

背景：11-02 TdxSync 拆四批（A_高频/B_财务/C_低频/D_缺口，17:35-18:05）后，
四批共 73 张表，远端共 183 键——约 110 张长尾表不在任何批次，
11-01 起再无每日增量（govern 对账 84 张 xtcloud 滞后 5-7 天实证）。

设计：不复制四批表单（会与另一位 owner 的任务定义漂移），改按**漂移检测**：
  data/tdx/<table>/ 内最新文件 mtime 落后超过 --max-age-hours（默认 36h）
  且 manifest done=true 且不在 SKIP_DEFAULT 且目录结构可识别
  → 收入本轮 --tables 清单调 tdx_sync.py 增量。
四批覆盖的表每天 17:35 后都是新的，天然不入选；四批漏掉的表第二天早晨被兜住。
用法：
  python tdx_tail_sync.py --dry-run   # 只打印入选表清单
  python tdx_tail_sync.py             # 实跑增量（内部仍幂等，无新数据即 0 行跳过）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "tdx"
MANIFEST = OUT / "_manifest.json"
SYNC_SCRIPT = HERE / "tdx_sync.py"

# 与 tdx_sync.py 同口径：深表单独开关，兜底批永不碰
SKIP_DEFAULT = {
    "ts_rt_min",        # 分钟深表 7167 万行
    "tdx_sector_min",   # 分钟深表 3625 万行
    # 2026-11-05：无业务日期列（只有 ts_code/con_code/con_name/_fetched_at），
    # 1559 万行"当前成分快照"，增量无从判定 → 每天被选进来只会 SKIP 刷日志。
    # 正确做法是月级全量快照刷新（3119 页/次），见 reports/data_hub/xtcloud_tail_20261105.md
    "ts_ths_member",
}

LOG = Path(r"C:\Temp\tdx_tail_sync.log")


def latest_mtime(table: str) -> float:
    """表目录里最新文件的 mtime；目录不存在或为空返回 0。"""
    d = OUT / table
    if not d.is_dir():
        return 0.0
    newest = 0.0
    try:
        for dirpath, _, files in os.walk(d):
            for name in files:
                try:
                    m = os.path.getmtime(os.path.join(dirpath, name))
                except OSError:
                    continue
                if m > newest:
                    newest = m
    except OSError:
        return 0.0
    return newest


def select_stale(manifest: dict, *, max_age_hours: float, now: float | None = None) -> tuple[list[str], list[str]]:
    """→ (入选表, 跳过原因表)。纯函数（mtime 由 latest_mtime 注入前先收集）。"""
    now = now if now is not None else time.time()
    cutoff = now - max_age_hours * 3600
    picked: list[str] = []
    skipped: list[tuple[str, str]] = []
    for table, meta in manifest.items():
        if not isinstance(meta, dict):
            continue
        if table in SKIP_DEFAULT:
            skipped.append((table, "SKIP_DEFAULT"))
            continue
        if not meta.get("done"):
            skipped.append((table, "done=false（未完成初拉，兜底批不接）"))
            continue
        rows = meta.get("rows")
        if isinstance(rows, int) and rows == 0:
            skipped.append((table, "rows=0"))
            continue
        m = latest_mtime(table)
        if m == 0.0:
            skipped.append((table, "无落盘目录（sqlite 外置表或未拉）"))
            continue
        if m < cutoff:
            picked.append(table)
    picked.sort()
    return picked, [f"{t} ({r})" for t, r in skipped]


def run(picked: list[str]) -> int:
    """调 tdx_sync.py 增量（沿用其分批/重试/锁语义）。"""
    if not picked:
        return 0
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(SYNC_SCRIPT),
        "--tables",
        ",".join(picked),
    ]
    return subprocess.call(cmd, cwd=str(HERE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只打印入选清单，不取数")
    ap.add_argument("--max-age-hours", type=float, default=36.0)
    args = ap.parse_args()

    if not MANIFEST.exists():
        print("manifest 缺失：%s" % MANIFEST)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    picked, skipped = select_stale(manifest, max_age_hours=args.max_age_hours)

    print("兜底批入选 %d 张（mtime 落后 >%.0fh）：" % (len(picked), args.max_age_hours))
    for t in picked:
        print("  +", t)
    if args.dry_run:
        print("dry-run，共 %d 张，未取数。跳过 %d 张（SKIP_DEFAULT/未完成/无目录）。" % (len(picked), len(skipped)))
        return 0

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[tail] %s start, %d tables" % (started, len(picked)))
    rc = run(picked)
    line = "tail %s tables=%d rc=%d" % (started, len(picked), rc)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
