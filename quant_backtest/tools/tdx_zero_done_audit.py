# -*- coding: utf-8 -*-
"""tdx zero-done / 分片一致性只读对账（A3 配套，20261020 ws-a3g）。

用途：扫 _manifest.json，列出疑似假完成与落盘不一致的表，并对比远端广告行数。
只读：不重拉、不改数据、不写 manifest；远端仅只读 GET（复用 tdx_sync 既有
status 接口 /api/data/status 与数据探针 _get）。

用法：
  python tools/tdx_zero_done_audit.py                # 含远端对比（只读 GET）
  python tools/tdx_zero_done_audit.py --no-remote    # 纯本地，不触网

输出：reports/tdx_zero_done_audit.json + reports/tdx_zero_done_audit.txt
"""

import argparse
import glob
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.dirname(HERE)
sys.path.insert(0, QB)

import tdx_sync as ts  # noqa: E402

REPORTS = os.path.join(QB, "reports")

# 拍板包 §一 已核实的 8 张 zero-done 表（仅用于报告标注，不做任何写操作）
KNOWN_ZERO_DONE = {
    "ts_etf_index",
    "ts_fut_index_daily",
    "ts_fut_weekly_monthly",
    "ts_hk_mins",
    "ts_index_member",
    "ts_opt_daily",
    "ts_opt_mins",
    "ts_rt_k",
}


def audit(no_remote=False):
    m = ts._load_manifest()
    remote = {}
    if not no_remote:
        remote = ts._remote_tables()  # 只读 GET /api/data/status

    findings = []
    for tbl in sorted(set(list(m.keys()) + _orphan_tables(m))):
        st = m.get(tbl) or {}
        done = bool(st.get("done"))
        mrows = st.get("rows", 0)
        parts = sorted(glob.glob(os.path.join(ts.OUT, tbl, "part_*.parquet")))
        n_parts = len(parts)
        issues = []

        if done and mrows == 0:
            issues.append("zero-done：done=True 且 rows=0（疑似假完成）")
        # sqlite 三表数据在 tdx_info.db，无 parquet 分片，不参与分片一致性检查
        if tbl not in ts.SQLITE_TABLES:
            if done and n_parts == 0 and not os.path.isdir(os.path.join(ts.OUT, tbl)):
                issues.append("done-无分片：目录不存在（拍板包 §二-2 复现态）")
            if done and n_parts == 0 and os.path.isdir(os.path.join(ts.OUT, tbl)):
                issues.append("done-零分片：目录在但无 part_*.parquet")
            if not done and n_parts > 0:
                issues.append(
                    "中断续传中：未 done 但已有 %d 个分片（正常可续传）" % n_parts
                )

        rrows = remote.get(tbl, {}).get("rows")
        cmp_note = ""
        if rrows is not None and mrows:
            diff = abs(mrows - rrows)
            pct = diff / float(rrows) * 100 if rrows else 0.0
            cmp_note = "diff=%d (%.1f%%)" % (diff, pct)
            if rrows > 0 and mrows == 0:
                issues.append("远端广告 rows=%s 而本地记账 0 行" % rrows)
            elif rrows > 0 and pct > 1.0:
                issues.append(
                    "本地记账 %d 行 vs 远端广告 %s 行，差异超 1%%" % (mrows, rrows)
                )

        if issues or tbl in KNOWN_ZERO_DONE:
            findings.append(
                {
                    "table": tbl,
                    "done": done,
                    "manifest_rows": mrows,
                    "parts": n_parts,
                    "remote_rows": rrows,
                    "compare": cmp_note,
                    "known_zero_done": tbl in KNOWN_ZERO_DONE,
                    "issues": issues,
                }
            )

    # 孤儿分片：目录有分片但 manifest 无该表条目
    for tbl in _orphan_tables(m):
        for f in findings:
            if (
                f["table"] == tbl
                and "manifest 无该表条目（孤儿分片）" not in f["issues"]
            ):
                f["issues"].append("manifest 无该表条目（孤儿分片）")

    result = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "manifest": ts.MANIFEST,
        "remote_queried": not no_remote,
        "total_tables_in_manifest": len(m),
        "finding_count": len(findings),
        "findings": findings,
    }
    return result


def _orphan_tables(m):
    """目录存在但 manifest 没有该表条目的表名。"""
    orphans = []
    if not os.path.isdir(ts.OUT):
        return orphans
    for name in os.listdir(ts.OUT):
        if name.startswith("_"):
            continue
        p = os.path.join(ts.OUT, name)
        if (
            os.path.isdir(p)
            and glob.glob(os.path.join(p, "part_*.parquet"))
            and name not in m
        ):
            orphans.append(name)
    return orphans


def render_text(r):
    buf = io.StringIO()
    w = buf.write
    w("tdx zero-done / 分片一致性对账（只读） %s\n" % r["generated_at"])
    w(
        "manifest=%s remote_queried=%s tables=%d findings=%d\n\n"
        % (
            r["manifest"],
            r["remote_queried"],
            r["total_tables_in_manifest"],
            r["finding_count"],
        )
    )
    hdr = "%-24s %-5s %10s %6s %12s  %s\n"
    w(hdr % ("table", "done", "local", "parts", "remote", "issues"))
    w("-" * 100 + "\n")
    for f in r["findings"]:
        w(
            hdr
            % (
                f["table"],
                "Y" if f["done"] else "N",
                f["manifest_rows"],
                f["parts"],
                "-" if f["remote_rows"] is None else f["remote_rows"],
                "；".join(f["issues"]) if f["issues"] else "(无，仅名单在册)",
            )
        )
    w("\n说明：本脚本只对账，不重拉不改数据。zero-done 愈合重拉由主会话另行安排。\n")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description="tdx zero-done 只读对账")
    ap.add_argument("--no-remote", action="store_true", help="不触网，仅本地")
    a = ap.parse_args()
    r = audit(no_remote=a.no_remote)
    os.makedirs(REPORTS, exist_ok=True)
    jp = os.path.join(REPORTS, "tdx_zero_done_audit.json")
    tp = os.path.join(REPORTS, "tdx_zero_done_audit.txt")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    with open(tp, "w", encoding="utf-8") as f:
        f.write(render_text(r))
    print(render_text(r))
    print("written: %s\nwritten: %s" % (jp, tp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
