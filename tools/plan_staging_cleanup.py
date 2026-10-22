# -*- coding: utf-8 -*-
"""生成 tushare_staging 清理清单（只读，不删任何东西）。

规则（按「批次状态」而不是「过期天数」）：
  KEEP     在跑管线的目录（最近 24h 有写入）
  CANDIDATE 批次已完成 / 重复中间产物 / 备份文件
  UNKNOWN  没有 summary、看不出状态的目录 —— 一律保留待人工判断

输出：C:\\项目\\_archive\\2026-10-22\\staging_cleanup_plan.json + 人读表格
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

STAGE = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_staging")
OUT_JSON = Path(r"C:\项目\_archive\2026-10-22\staging_cleanup_plan.json")

# 明确在跑 / 明确要保留的（有代码在读，或管线活跃）
ALWAYS_KEEP = {"tail_fill", "held_tail_fill", "history", "reconciliation", "explicit_gaps"}


def dir_size(p: Path) -> tuple[int, int, float]:
    total = 0
    n = 0
    newest = 0.0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                st = f.stat()
                total += st.st_size
                n += 1
                newest = max(newest, st.st_mtime)
        except OSError:
            continue
    return total, n, newest


def summary_status(path: Path) -> str | None:
    """判定批次完成度。

    这个 staging 的 annual 摘要**没有 status 字段**，只有每张表的
    entry_count / complete_count / source_error_count。所以真正的判据是：
        sum(complete_count) == sum(entry_count) 且 sum(source_error_count) == 0
    只看 status 会把 49 个已完成的批次全判成"状态不明"（2026-10-22 实测）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("status", "result", "state", "ok", "verdict"):
        if key in data:
            return f"{key}={data[key]}"
    tables = data.get("tables")
    if isinstance(tables, dict) and tables:
        try:
            entry = sum(int(t.get("entry_count") or 0) for t in tables.values())
            comp = sum(int(t.get("complete_count") or 0) for t in tables.values())
            err = sum(int(t.get("source_error_count") or 0) for t in tables.values())
        except (TypeError, ValueError):
            return None
        if entry == 0:
            return "empty"
        if comp == entry and err == 0:
            return f"complete({comp}/{entry},err=0)"
        return f"incomplete({comp}/{entry},err={err})"
    return "no-status-field"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    now = time.time()
    plan: list[dict] = []

    # ---- 目录类 ----
    for d in sorted(p for p in STAGE.iterdir() if p.is_dir()):
        name = d.name
        size, files, newest = dir_size(d)
        age_h = (now - newest) / 3600 if newest else 9999
        verdict = "KEEP"
        reason = ""
        if name in ALWAYS_KEEP:
            reason = "在跑管线目录（代码引用中）"
        elif name.startswith("annual_") and re.search(r"_batch_\d+$", name):
            sib = STAGE / f"{name}_summary.json"
            st = summary_status(sib) if sib.is_file() else None
            if st is not None and st.startswith("complete"):
                verdict, reason = "CANDIDATE", f"批次已完成（{sib.name}: {st}）"
            elif st is not None and st.startswith("incomplete"):
                verdict, reason = "UNKNOWN", f"批次未完成（{st}），保留待查"
            elif age_h > 48:
                verdict, reason = "UNKNOWN", f"批次摘要状态不明（{st or '无 summary'}），超 48h 未写"
            else:
                reason = "批次较新，保留"
        elif name == "history_3y":
            if age_h > 48:
                verdict, reason = "CANDIDATE", f"历史回填暂存批次，{age_h/24:.1f} 天未写"
            else:
                reason = "较新"
        else:
            verdict, reason = "UNKNOWN", f"未识别目录，{age_h/24:.1f} 天未写"
        plan.append({"kind": "dir", "name": name, "bytes": size, "files": files,
                     "age_hours": round(age_h, 1),
                     "newest": datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M") if newest else None,
                     "verdict": verdict, "reason": reason})

    # ---- 文件类 ----
    root_files = sorted(p for p in STAGE.iterdir() if p.is_file())
    payloads = [p for p in root_files if p.name.startswith("nine_dim_source_payload_full_")]
    keep_payload = payloads[-1].name if payloads else None
    for p in root_files:
        st = p.stat()
        age_h = (now - st.st_mtime) / 3600
        verdict, reason = "KEEP", ""
        if p.name.startswith("nine_dim_source_payload_full_"):
            if p.name == keep_payload:
                reason = "同一 payload 的最新版本，保留"
            else:
                verdict, reason = "CANDIDATE", "同一 payload 的重复版本（只保留最新一版）"
        elif p.name.endswith(".json") and "_summary.json" in p.name:
            reason = "批次摘要，用于判定批次状态，保留"
        elif p.suffix == ".bak" or ".bak_" in p.name:
            verdict, reason = "CANDIDATE", "备份文件（原名仍在）"
        elif p.suffix in (".sqlite3", ".sqlite3-wal", ".sqlite3-shm"):
            reason = "运行库，保留"
        elif p.suffix == ".lock":
            reason = "锁文件，保留"
        elif p.suffix == ".json" and age_h > 72:
            verdict, reason = "UNKNOWN", f"历史报表 JSON，{age_h/24:.1f} 天未更新"
        else:
            verdict, reason = "KEEP", "未识别但体积极小或近期更新"
        plan.append({"kind": "file", "name": p.name, "bytes": st.st_size, "files": 1,
                     "age_hours": round(age_h, 1),
                     "newest": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "verdict": verdict, "reason": reason})

    # ---- 汇总 ----
    def total(v: str) -> int:
        return sum(x["bytes"] for x in plan if x["verdict"] == v)

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage_root": str(STAGE),
        "rule": "按批次状态而非过期天数：完成即清 / 重复只留最新 / 状态不明一律保留",
        "counts": {v: sum(1 for x in plan if x["verdict"] == v) for v in ("KEEP", "CANDIDATE", "UNKNOWN")},
        "bytes": {v: total(v) for v in ("KEEP", "CANDIDATE", "UNKNOWN")},
        "reclaimable_gib": round(total("CANDIDATE") / 1024**3, 2),
        "items": plan,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"扫描 {STAGE}")
    print(f"条目 {len(plan)} 个")
    print()
    for v in ("KEEP", "CANDIDATE", "UNKNOWN"):
        print(f"  {v:10s} {summary['counts'][v]:4d} 项  {summary['bytes'][v]/1024**3:6.2f} GiB")
    print()
    print(f"可回收（CANDIDATE）: {summary['reclaimable_gib']} GiB")
    print()
    print("=== CANDIDATE 明细（前 25）===")
    cands = sorted((x for x in plan if x["verdict"] == "CANDIDATE"),
                   key=lambda x: -x["bytes"])
    for x in cands[:25]:
        print("  %-46s %8.1f MB  %s" % (x["name"][:46], x["bytes"] / 1024**2, x["reason"]))
    if len(cands) > 25:
        print(f"  …… 另有 {len(cands)-25} 项，见 JSON")
    print()
    print(f"清单已写 {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
