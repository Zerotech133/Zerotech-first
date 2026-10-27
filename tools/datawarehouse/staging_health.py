# -*- coding: utf-8 -*-
"""staging 生命周期周检：tushare_staging 体积/沉淀/可回收候选（只读）。

背景（2026-10-28）：staging 曾沉淀 6.5GB（history_3y 4.97GiB）未被发现；
本工具纳入 DW 周检，防再次无声膨胀：
- 体积：>2GB 告警（WARN），>5GB 严重（FAIL）；
- 明细：按子目录列体积/文件数/最新 mtime，标出「历史批次目录」（history_3y 类）
  与「上一代回滚目录」（*_prev_*、*.prev）；
- 可回收候选：调用 `quant_backtest/plan_staging_cleanup.py`（只读规划器）若可用，
  汇总 CANDIDATE 体积（不自动删除，只报告）。

退出码：0=健康 / 1=告警 / 2=严重/依赖缺失。
"""
from __future__ import annotations

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


def collect(staging: Path | None = None) -> dict:
    """staging 为 None 时用真实数据根；测试可传临时目录。"""
    root = Path(staging) if staging is not None else STAGING
    issues: list[str] = []
    rows: list[dict] = []
    total = files = 0
    newest = 0.0
    root_files = 0
    root_bytes = 0
    if root.exists():
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
            newest = max(newest, mt)
            rows.append({"dir": p.name, "bytes": size, "files": n,
                         "latest": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else "",
                         "history_like": any(m in p.name for m in HISTORY_MARKS),
                         "prev_gen": any(m in p.name for m in PREV_MARKS)})
        rows.sort(key=lambda x: -x["bytes"])
    else:
        issues.append("staging_missing")
    total += root_bytes
    files += root_files
    code = 0
    if total > FAIL_BYTES:
        issues.append("staging_size_%.1fGB_gt_5GB" % (total / 1024 ** 3))
        code = 2
    elif total > WARN_BYTES:
        issues.append("staging_size_%.1fGB_gt_2GB" % (total / 1024 ** 3))
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
            "dirs": rows, "candidates": candidates}


def main(argv: list[str] | None = None) -> int:
    import argparse
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
            print("  %-28s %9.2fMB %6d files %s %s" % (
                r["dir"], r["bytes"] / 1024 / 1024, r["files"], r["latest"], ",".join(tags)))
        if res["issues"]:
            print("issues:", res["issues"])
        print("result:", "OK" if res["code"] == 0 else ("WARN" if res["code"] == 1 else "FAIL"))
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
