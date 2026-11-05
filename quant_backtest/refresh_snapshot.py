"""快照表（无业务日期列）全量刷新：先探真实边界，再整目录原子替换。

为什么单独写：xtcloud 的 `total` 会严重虚报。ts_ths_member 报
15,593,536 行，实测 offset=382,731 起就返回 0 行，真实行数 382,731
（与本地完全相等）。按 total 排期会得出「3119 页 / 1 小时」的错误
结论，实际只需 77 页 / 约 7 分钟。凡是"没有业务日期列、只能整表刷新"
的表，都必须先探边界，不能信 total。

与 tdx_sync.sync_parquet_table 的分工：
- tdx_sync 管**增量**（有日期列，按水位续拉，原地追加分片）；
- 本脚本管**全量覆盖**（无日期列的快照表，整目录替换 + 备份回滚）。

用法：
    python refresh_snapshot.py --table ts_ths_member --dry-run   # 只探边界
    python refresh_snapshot.py --table ts_ths_member             # 全量刷新
    python refresh_snapshot.py --table ts_ths_member --keep-days 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time

import pandas as pd

import tdx_sync as ts

OUT = ts.OUT
PAGE = ts.PAGE
PAGES_PER_PART = 10  # 每 10 页（5 万行）一片，与既有 ts_ths_member 布局一致


# ---------------------------------------------------------------- 纯函数
def bisect_boundary(probe, claim_total: int, *, step: int = PAGE,
                    max_grow: int = 8) -> int:
    """返回远端真实可拉行数。probe(offset) -> 该 offset 起返回的行数。

    真实行数 R 的定义：probe(R-1) >= 1 且 probe(R) == 0，即「最小的返回 0
    的 offset」。纯函数（probe 注入），便于离线测试。

    两个坑（2026-11-05 各踩一次）：
    1. probe 的 limit 可以远小于 step（判空只需 1 行，用 limit=100 更快），
       所以不能把 probe(0) 的返回值当总行数——首版据此把 1559 万行探成
       100 行。
    2. claim_total 也可能**低估**（表被别人追加过）。所以不能"尾页有数就
       采信 claim"，要指数扩张到真正的空 offset，再二分。
    """
    if claim_total <= 0:
        return 0
    if probe(0) == 0:
        return 0
    hi = claim_total
    grow = 0
    while probe(hi) > 0 and grow < max_grow:
        hi *= 2
        grow += 1
    if probe(hi) > 0:
        return hi  # 扩张上限仍非空：取保守下界，宁少拉不错覆盖
    lo = 0  # 不变式：probe(lo) > 0，probe(hi) == 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if probe(mid) > 0:
            lo = mid
        else:
            hi = mid
    return hi


def verify_pull(total_rows: int, boundary: int, parts: list[int],
                *, tol: float = 0.999) -> tuple[bool, str]:
    """拉全量后的验收：行数不得少于边界（允许 tol 的对端抖动），且无空分片。"""
    if boundary <= 0:
        return False, "边界探测为 0（远端返回空表），拒绝覆盖"
    if not parts:
        return False, "未落任何分片"
    if any(n <= 0 for n in parts):
        return False, "存在空分片：%s" % [n for n in parts if n <= 0][:5]
    floor = int(boundary * tol)
    if total_rows < floor:
        return False, "行数不足：拉到 %d，边界 %d（下限 %d）" % (total_rows, boundary, floor)
    return True, "ok：%d 行 / %d 片（边界 %d）" % (total_rows, len(parts), boundary)


def plan_pages(boundary: int, *, page: int = PAGE) -> int:
    return -(-boundary // page) if boundary > 0 else 0


# ---------------------------------------------------------------- 取数
def _probe(tbl: str, offset: int, limit: int = 100) -> int:
    d = ts._get("/api/data/table/" + tbl, {"limit": limit, "offset": offset})
    return len(d.get("rows") or [])


def pull_all(tbl: str, boundary: int, tmp_dir: str, *,
             page: int = PAGE) -> tuple[int, list[int]]:
    """把 [0, boundary) 全量拉到 tmp_dir，返回 (总行数, 每片行数)。

    分页推进一律按远端原始返回行数，不用过滤后行数（tdx_sync 的老 bug
    就在这里，见 TAIL-SCHED-20261105 提交说明）。
    """
    os.makedirs(tmp_dir, exist_ok=True)
    total = 0
    parts: list[int] = []
    buf: list[dict] = []
    cols: list[str] = []
    part_idx = 0
    offset = 0
    pages = plan_pages(boundary, page=page)
    t0 = time.time()

    def flush() -> None:
        nonlocal part_idx
        if not buf:
            return
        df = pd.DataFrame(buf, columns=cols or None)
        path = os.path.join(tmp_dir, "part_%04d.parquet" % part_idx)
        df.to_parquet(path, index=False)
        parts.append(len(df))
        part_idx += 1
        buf.clear()

    while offset < boundary:
        d = ts._get("/api/data/table/" + tbl, {"limit": page, "offset": offset})
        rows = d.get("rows") or []
        if not cols:
            cols = d.get("columns") or []
        if not rows:
            print("  offset=%d 返回空页，提前结束（已拉 %d 行）" % (offset, total),
                  flush=True)
            break
        buf.extend(dict(zip(cols, r)) for r in rows)
        offset += len(rows)
        total += len(rows)
        if len(buf) >= PAGES_PER_PART * page:
            flush()
        done = offset // page
        if done % 10 == 0:
            print("  进度 %d/%d 页  %d 行  %.0fs"
                  % (done, pages, total, time.time() - t0), flush=True)
    flush()
    return total, parts


# ---------------------------------------------------------------- 主流程
def _swap(table: str, tmp_dir: str, *, keep_days: int = 30) -> str:
    """整目录原子替换，旧目录改名保留（不删除）。返回备份目录路径。"""
    target = os.path.join(OUT, table)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(OUT, "%s.bak-%s" % (table, stamp))
    if os.path.isdir(target):
        os.rename(target, bak)
        try:
            os.rename(tmp_dir, target)
        except Exception:
            os.rename(bak, target)  # 回滚：旧数据原样放回
            raise
    else:
        os.rename(tmp_dir, target)
        bak = ""
    return bak


def _local_part_rows(target: str) -> tuple[int, int]:
    """读 parquet footer 取行数（不加载数据，38 万行表秒回）。"""
    if not os.path.isdir(target):
        return 0, 0
    rows = 0
    n = 0
    for name in sorted(os.listdir(target)):
        if not (name.startswith("part_") and name.endswith(".parquet")):
            continue
        try:
            import pyarrow.parquet as pq

            rows += pq.ParquetFile(os.path.join(target, name)).metadata.num_rows
            n += 1
        except Exception:
            continue
    return rows, n


def _prune_backups(table: str, keep_days: int) -> list[str]:
    """只清理本脚本产生的 .bak-*（老化保留），不碰任何正式目录。"""
    removed: list[str] = []
    if keep_days <= 0:
        return removed
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    for name in sorted(os.listdir(OUT)):
        if not name.startswith(table + ".bak-"):
            continue
        p = os.path.join(OUT, name)
        try:
            mtime = dt.datetime.fromtimestamp(os.path.getmtime(p))
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="快照表全量刷新（探边界 + 原子替换）")
    ap.add_argument("--table", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只探边界，不落盘")
    ap.add_argument("--keep-days", type=int, default=30, help="备份保留天数，0=不清理")
    a = ap.parse_args()

    tbl = a.table
    t0 = time.time()
    print("[%s] 探测远端真实边界（total 不可信）..." % tbl, flush=True)
    claim = ts._get("/api/data/table/" + tbl, {"limit": 1, "offset": 0}).get("total") or 0
    boundary = bisect_boundary(lambda off: _probe(tbl, off), claim)
    pages = plan_pages(boundary)
    print("[%s] total 声称 %d | 实测边界 %d 行 | 需 %d 页 | 约 %.0f 分钟"
          % (tbl, claim, boundary, pages, pages * 3.0 / 60), flush=True)
    if boundary <= 0:
        print("[%s] 远端空表，终止（不做覆盖）" % tbl, flush=True)
        return 2

    target = os.path.join(OUT, tbl)
    local_rows, local_parts = _local_part_rows(target)
    print("[%s] 本地现有 %d 行（%d 片）" % (tbl, local_rows, local_parts), flush=True)

    if a.dry_run:
        print("[%s] dry-run：边界与本地差异 %+d 行，未落盘" % (tbl, boundary - local_rows),
              flush=True)
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir = os.path.join(OUT, "_refresh_%s_%s" % (tbl, stamp))
    print("[%s] 拉全量 → %s" % (tbl, os.path.basename(tmp_dir)), flush=True)
    total, parts = pull_all(tbl, boundary, tmp_dir)

    ok, msg = verify_pull(total, boundary, parts)
    print("[%s] 验收：%s" % (tbl, msg), flush=True)
    if not ok:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[%s] 验收未过，临时目录已清，正式数据未动" % tbl, flush=True)
        return 3

    bak = _swap(tbl, tmp_dir)
    m = ts._load_manifest()
    m[tbl] = {"offset": total, "part": len(parts), "rows": total, "done": True,
              "refreshed_at": stamp}
    ts._save_manifest(m)
    print("[%s] 已替换：%d 行 / %d 片；备份 %s"
          % (tbl, total, len(parts), os.path.basename(bak) or "(无旧目录)"), flush=True)
    for name in _prune_backups(tbl, a.keep_days):
        print("[%s] 清理过期备份 %s" % (tbl, name), flush=True)
    print("[%s] 完成，用时 %.1f 分钟" % (tbl, (time.time() - t0) / 60), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
