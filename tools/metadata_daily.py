#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""元数据日报（P0-1，2026-10-30）：一致性/新鲜度/备份覆盖 → 每日一条事件。

来源（全部只读，各工具自带 --json）：
  1. tools/datawarehouse/metadata_consistency.py：catalog×registry×lifecycle×物理 store；
  2. tools/datawarehouse/freshness_report.py：数据新鲜度；
  3. tools/datawarehouse/backup_receipt_check.py：备份回执与覆盖。

输出：
  · 一行摘要（stdout，供事件层/日报引用）；
  · agent_platform/data/reports/metadata_daily/<YYYYMMDD>.json + .md；
  · --emit 时写事件层（kind=patrol_verdict）。

用法：
  python tools/metadata_daily.py              # 采集+落盘+打印一行
  python tools/metadata_daily.py --emit        # 另写事件层（计划任务用）
  python tools/metadata_daily.py --check       # 有异常 → exit 1（告警用）
  python tools/metadata_daily.py --json        # 只输出完整 JSON
退出码：0=正常；1=有异常（--check 时判定）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DW = HERE / "datawarehouse"
OUT_DIR = REPO / "agent_platform" / "data" / "reports" / "metadata_daily"


def _load_tools():
    """惰性导入 datawarehouse 工具（避免测试时拉起真实 IO）。"""
    if str(DW) not in sys.path:
        sys.path.insert(0, str(DW))
    import common  # noqa: F401
    import metadata_consistency as mc
    import freshness_report as fr
    import backup_receipt_check as bc

    return common, mc, fr, bc


def summarize(meta: dict, fresh: dict, backup: dict, sha: str) -> dict:
    """把三份工具输出归一成日报结构（纯函数，便于测试）。"""
    issues = []
    for src, rep in (("meta", meta), ("fresh", fresh), ("backup", backup)):
        src_issues = list(rep.get("issues") or [])
        for it in src_issues[:10]:
            issues.append("%s:%s" % (src, it))
        # 有明细 issues 时不再重复记 code（避免一条问题被计两次）
        if rep.get("code") not in (0, None) and not src_issues:
            issues.append("%s:code=%s" % (src, rep.get("code")))
    missing = list(backup.get("missing") or [])
    if missing:
        issues.append("backup:missing=%s" % ",".join(map(str, missing))[:80])
    fresh_checks = fresh.get("checks") or []
    return {
        "date": dt.date.today().isoformat(),
        "catalog_sha": sha,
        "catalog_version": meta.get("catalog_version"),
        "tables": meta.get("tables"),
        "physical_count": meta.get("physical_count"),
        "meta_ok": not (meta.get("issues") or []),
        "fresh_ok": fresh.get("code") == 0,
        "fresh_checks": len(fresh_checks),
        "backup_generation": backup.get("latest_generation"),
        "backup_age_hours": backup.get("age_hours"),
        "backup_missing": missing,
        "backup_ok": (backup.get("code") == 0) and not missing,
        "anomalies": issues,
        "generated_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
    }


def render_line(s: dict) -> str:
    """一行摘要（事件层 / 日报引用）。"""
    return (
        "metadata_daily sha=%s tables=%s/%s ver=%s | meta=%s | fresh=%s(%d检查) | "
        "backup=%s gen=%s age=%sh missing=%s | anomalies=%d"
        % (
            (s.get("catalog_sha") or "?")[:12],
            s.get("physical_count"),
            s.get("tables"),
            s.get("catalog_version"),
            "ok" if s.get("meta_ok") else "FAIL",
            "ok" if s.get("fresh_ok") else "FAIL",
            s.get("fresh_checks") or 0,
            "ok" if s.get("backup_ok") else "FAIL",
            s.get("backup_generation"),
            s.get("backup_age_hours"),
            len(s.get("backup_missing") or []),
            len(s.get("anomalies") or []),
        )
    )


def collect() -> dict:
    common, mc, fr, bc = _load_tools()
    meta = mc.check()
    fresh = fr.collect()
    backup = bc.collect()
    try:
        sha = mc.sha256_file(Path(str(common.CATALOG)))[:16]
    except Exception:  # noqa: BLE001
        sha = ""
    return summarize(meta, fresh, backup, sha)


def _write_reports(s: dict) -> None:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        day = s["date"]
        (OUT_DIR / ("%s.json" % day)).write_text(
            json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [
            "# 元数据日报 %s" % day,
            "",
            "- 摘要：`%s`" % render_line(s),
            "- catalog：sha=%s version=%s 表 %s（物理 %s）"
            % (s.get("catalog_sha"), s.get("catalog_version"), s.get("tables"), s.get("physical_count")),
            "- 新鲜度：%s（%d 项检查）" % ("ok" if s.get("fresh_ok") else "FAIL", s.get("fresh_checks") or 0),
            "- 备份：%s generation=%s age=%sh missing=%s"
            % ("ok" if s.get("backup_ok") else "FAIL", s.get("backup_generation"),
               s.get("backup_age_hours"), s.get("backup_missing")),
            "- 异常：%s" % (s.get("anomalies") or "无"),
        ]
        (OUT_DIR / ("%s.md" % day)).write_text("\n".join(lines) + "\n", encoding="utf-8")
        with (OUT_DIR / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("[metadata_daily] 落盘失败（不阻断）：%r" % exc, file=sys.stderr)


def emit_event(line: str, success: bool) -> None:
    try:
        subprocess.run(
            [sys.executable, str(HERE / "events.py"), "emit",
             "--kind", "patrol_verdict", "--task", "DATA-METADATA-DAILY",
             "--agent", "GUANYU", "--ide", "DSH-GUANYU", "--model", "deepseek-flash",
             "--detail", line],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        print("[metadata_daily] 事件层写入失败（不阻断）：%r" % exc, file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="元数据日报（一致性/新鲜度/备份覆盖）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--emit", action="store_true", help="写事件层（patrol_verdict）")
    ap.add_argument("--check", action="store_true", help="有异常 → exit 1")
    args = ap.parse_args(argv)

    s = collect()
    _write_reports(s)
    line = render_line(s)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print(line)
    if args.emit:
        emit_event(line, not s["anomalies"])
    return 1 if (args.check and s["anomalies"]) else 0


if __name__ == "__main__":
    sys.exit(main())
