#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""量化面最小研究：监控规则历史样本模拟（PIT / as_of 口径）。

- 只读：调用 analysis.monitor.run_monitor(dry_run=True)（不写 runs/alerts 表）。
- 输入：本地 watchlist 股票池；as_of 取 2026-11-02..2026-11-05。
- 输出：JSON 逐日告警明细 + 汇总 CSV + 数据版本清单（catalog/registry/db sha256+mtime）。
- 用途：11-08 结果包「量化与投资研究」最小成果证据；非投资建议，禁真实交易。
"""
import csv
import hashlib
import json
import os
import sys
import time

MAIN = r"C:\项目\老田的AI助手"
OUT = r"C:\Temp\monitor_sim_20261106"
sys.path.insert(0, MAIN)

DATES = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def versions():
    work = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
    files = {
        "catalog": os.path.join(MAIN, "quant_backtest", "data", "catalog.json"),
        "registry": os.path.join(MAIN, "quant_backtest", "data", "warehouse_registry.json"),
        "xueqiu_db": os.path.join(MAIN, "quant_backtest", "data", "xueqiu", "xueqiu_info.db"),
        "monitor_db": os.path.join(work, "monitor", "monitor.db"),
        "monitor_pools": os.path.join(work, "monitor", "pools.json"),
        "watchlist": os.path.join(MAIN, "analysis", "watchlist.txt"),
    }
    out = {}
    for k, p in files.items():
        if os.path.exists(p):
            st = os.stat(p)
            out[k] = {"path": p, "bytes": st.st_size,
                      "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                      "sha256": sha256(p) if st.st_size < 60 * 1024 * 1024 else "(large, skipped)"}
        else:
            out[k] = {"path": p, "exists": False}
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    from analysis import monitor as m  # noqa: E402

    ver = versions()
    with open(os.path.join(OUT, "data_versions.json"), "w", encoding="utf-8") as f:
        json.dump(ver, f, ensure_ascii=False, indent=2)

    rows = []
    all_runs = []
    for d in DATES:
        try:
            r = m.run_monitor(pool="watchlist", as_of=d, dry_run=True)
            item = {"as_of": d, "ok": r.get("ok"), "scanned": r.get("scanned"),
                    "alerts": r.get("alerts"), "by_severity": r.get("by_severity"),
                    "run_id": r.get("run_id"), "error": r.get("error")}
            all_runs.append({"as_of": d, "result": r})
            for a in (r.get("items") or r.get("alerts") or []):
                if not isinstance(a, dict):
                    continue
                rows.append({
                    "as_of": d,
                    "code": a.get("code"), "rule": a.get("rule"),
                    "severity": a.get("severity"), "trade_date": a.get("trade_date"),
                    "dedupe_key": a.get("dedupe_key"), "title": a.get("title"),
                })
            print("%s ok=%s scanned=%s alerts=%s sev=%s" % (
                d, r.get("ok"), r.get("scanned"), r.get("alerts"), r.get("by_severity")))
        except Exception as ex:  # noqa: BLE001
            print("%s ERROR %s: %s" % (d, type(ex).__name__, ex))
            all_runs.append({"as_of": d, "error": "%s: %s" % (type(ex).__name__, ex)})

    with open(os.path.join(OUT, "runs_raw.json"), "w", encoding="utf-8") as f:
        json.dump(all_runs, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(OUT, "alerts_flat.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["as_of", "code", "rule", "severity",
                                          "trade_date", "dedupe_key", "title"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    summary = {"dates": DATES, "total_alerts": len(rows),
               "by_severity": {}, "by_rule": {}}
    for r in rows:
        summary["by_severity"][r["severity"]] = summary["by_severity"].get(r["severity"], 0) + 1
        summary["by_rule"][r["rule"]] = summary["by_rule"].get(r["rule"], 0) + 1
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("TOTAL_ALERTS=%d" % len(rows))
    print("BY_SEVERITY=%s" % json.dumps(summary["by_severity"], ensure_ascii=False))
    print("BY_RULE=%s" % json.dumps(summary["by_rule"], ensure_ascii=False))
    print("OUT_DIR=%s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
