# -*- coding: utf-8 -*-
"""staging 生命周期检查增强（2026-10-28）：保留窗口 + 清单未知 + 陈旧锁。

在 base staging_health 的体积阈值之上增加：
- history/回填批次目录保留窗口（HISTORY_KEEP_DAYS=30）超期 → WARN
- 上一代回滚目录（*_prev_*/*.prev）超期仍占盘 → WARN（可回收候选）
- staging 根散落 `.warehouse-publication.lock` 超 7 天 → WARN（锁语义=OS 文件锁，文件本身是卫生问题）
- 目录内 `run_manifest.json`：production_write=False / global_activation=False
  （未发布候选）超 30 天 → WARN（防再次沉淀 6.5GB）
退出码：0=健康 / 1=告警 / 2=严重（体积>5GB）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STAGING = common.DATA / "tushare_staging"
WARN_BYTES = 2 * 1024 ** 3
FAIL_BYTES = 5 * 1024 ** 3
HISTORY_MARKS = ("history_3y", "history_")
PREV_MARKS = ("_prev_", ".prev")
HISTORY_KEEP_DAYS = 30
PREV_KEEP_DAYS = 30
LOCK_STALE_DAYS = 7


def dir_size(p: Path) -> tuple[int, int, float]:
    total = n = 0
    newest = 0.0
    try:
        for f in p.rglob("*"):
            if f.is_file():
                st = f.stat()
                total += st.st_size
                n += 1
                newest = max(newest, st.st_mtime)
    except OSError:
        pass
    return total, n, newest


def _age_days(ts: float) -> float:
    return (time.time() - ts) / 86400.0 if ts else 0.0


def _unpublished_in(p: Path) -> int:
    """目录内 run_manifest.json 中 production_write/global_activation 为 False 的批次数。"""
    n = 0
    try:
        for mf in p.rglob("run_manifest.json"):
            try:
                d = json.loads(mf.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                continue
            if d.get("production_write") is False or d.get("global_activation") is False:
                n += 1
    except OSError:
        pass
    return n


def collect(staging: Path | None = None) -> dict:
    root = Path(staging) if staging is not None else STAGING
    issues: list[str] = []
    rows: list[dict] = []
    total = files = 0
    newest = 0.0
    root_files = 0
    root_bytes = 0
    if not root.exists():
        issues.append("staging_missing")
    else:
        for f in root.iterdir():
            if f.is_file():
                try:
                    st = f.stat()
                    root_bytes += st.st_size
                    root_files += 1
                    newest = max(newest, st.st_mtime)
                except OSError:
                    pass
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            size, n, mt = dir_size(p)
            total += size
            files += n
            newest = max(newest, mt) if mt else newest
            history_like = any(m in p.name for m in HISTORY_MARKS)
            prev_gen = any(m in p.name for m in PREV_MARKS)
            row = {"dir": p.name, "bytes": size, "files": n,
                   "latest": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else "",
                   "age_days": round(_age_days(mt), 1),
                   "history_like": history_like, "prev_gen": prev_gen}
            if history_like or prev_gen:
                row["unpublished_manifests"] = _unpublished_in(p)
            rows.append(row)
            if history_like and row["age_days"] > HISTORY_KEEP_DAYS:
                issues.append("history_dir_older_than_%dd:%s(age=%.1fd)" % (
                    HISTORY_KEEP_DAYS, p.name, row["age_days"]))
            if prev_gen and row["age_days"] > PREV_KEEP_DAYS:
                issues.append("prev_gen_reclaim:%s(age=%.1fd)" % (p.name, row["age_days"]))
            if row.get("unpublished_manifests") and row["age_days"] > HISTORY_KEEP_DAYS:
                issues.append("unpublished_candidates_stale:%s" % p.name)
        rows.sort(key=lambda x: -x["bytes"])
        lock = root / ".warehouse-publication.lock"
        if lock.exists():
            age = _age_days(lock.stat().st_mtime)
            if age > LOCK_STALE_DAYS:
                issues.append("stale_publication_lock(age=%.1fd)" % age)
    total += root_bytes
    files += root_files
    code = 0
    if total > FAIL_BYTES:
        issues.append("staging_size_%.1fGB_gt_5GB" % (total / 1024 ** 3))
        code = 2
    elif total > WARN_BYTES:
        issues.append("staging_size_%.1fGB_gt_2GB" % (total / 1024 ** 3))
        code = 1
    if issues and code == 0:
        code = 1
    candidates = []
    planner = common.REPO / "quant_backtest" / "plan_staging_cleanup.py"
    if planner.exists():
        try:
            sys.path.insert(0, str(planner.parent))
            import plan_staging_cleanup as psc  # noqa: WPS433
            plan = psc.build_plan() if hasattr(psc, "build_plan") else None
            if plan:
                candidates = [{"name": it.get("name"), "bytes": it.get("bytes")}
                              for it in plan.get("items", []) if it.get("verdict") == "CANDIDATE"]
        except Exception:  # noqa: BLE001
            candidates = []
    if candidates:
        issues.append("reclaimable_candidate_%d" % len(candidates))
        if code == 0:
            code = 1
    return {"tool": "staging_health", "code": code, "issues": issues,
            "total_bytes": total, "total_gb": round(total / 1024 ** 3, 2),
            "files": files,
            "latest": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else "",
            "retention": {"history_keep_days": HISTORY_KEEP_DAYS,
                          "prev_gen_keep_days": PREV_KEEP_DAYS,
                          "lock_stale_days": LOCK_STALE_DAYS},
            "dirs": rows, "candidates": candidates}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tushare_staging 生命周期周检（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = collect()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("staging %.2fGB / %d files / latest=%s" % (res["total_gb"], res["files"], res["latest"]))
        for r in res["dirs"][:12]:
            tags = []
            if r["history_like"]:
                tags.append("history")
            if r["prev_gen"]:
                tags.append("prev-gen")
            print("  %-28s %9.2fMB %6d files %s age=%.1fd %s" % (
                r["dir"], r["bytes"] / 1024 / 1024, r["files"], r["latest"],
                r["age_days"], ",".join(tags)))
        if res["issues"]:
            print("issues:", res["issues"])
        print("result:", "OK" if res["code"] == 0 else ("WARN" if res["code"] == 1 else "FAIL"))
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
