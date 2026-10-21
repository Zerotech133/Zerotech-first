# -*- coding: utf-8 -*-
"""A4 PG 持久化回执探针（全程只读）。

scope = READONLY_RECEIPT_NOT_ADOPTION_EVIDENCE

只做三件事：
1. 只读查询 PG（docker exec quant-pg psql -c SELECT），按 trade_date 分组统计
   strategy.signals_combined 的行数 / created_at 范围 / divergence / superseded。
2. 只 ls 枚举 portal/data/picks_history/todays_*.json 归档日，对每个归档日调用
   strategy_layer.a4_comparison_cli --trade-date YYYYMMDD（只读对账）。
3. 汇总写 quant_backtest/reports/a4_pg_receipt_report.json + stdout 摘要表。

不改任何代码、不提交、不写库。本报告仅是回执观察，不构成采用/切换证据。
"""
import datetime
import glob
import json
import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QB = os.path.join(PROJECT, "quant_backtest")
REPORT_PATH = os.path.join(QB, "reports", "a4_pg_receipt_report.json")
PICKS_HISTORY = os.path.join(PROJECT, "portal", "data", "picks_history")
MANAGED_PY = r"C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

PG_GROUP_SQL = (
    "SELECT trade_date, count(*) AS rows, min(created_at) AS min_created, "
    "max(created_at) AS max_created, "
    "count(*) FILTER (WHERE divergence) AS divergence_rows, "
    "count(*) FILTER (WHERE contributions::text LIKE '%superseded%') AS superseded_rows "
    "FROM strategy.signals_combined GROUP BY trade_date ORDER BY trade_date;"
)


def pg_grouped_rows():
    """docker exec quant-pg psql 只读 SELECT，返回 CSV 行列表。"""
    cmd = ["docker", "exec", "-i", "quant-pg", "psql", "-U", "quant_user",
           "-d", "quant_system", "-P", "pager=off", "-F", "|", "-t", "-A",
           "-c", PG_GROUP_SQL]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("psql failed: %s" % out.stderr.strip())
    rows = []
    for line in out.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) != 6:
            continue
        rows.append({
            "trade_date": parts[0],
            "rows": int(parts[1]),
            "min_created_at": parts[2],
            "max_created_at": parts[3],
            "divergence_rows": int(parts[4]),
            "superseded_rows": int(parts[5]),
        })
    return rows


def archive_dates():
    """只 ls 枚举归档日。"""
    dates = []
    for p in sorted(glob.glob(os.path.join(PICKS_HISTORY, "todays_*.json"))):
        name = os.path.basename(p)  # todays_YYYYMMDD.json
        dates.append(name[len("todays_"):-len(".json")])
    return dates


def run_a4_cli(trade_date):
    """调用只读对账 CLI，返回解析后的 JSON。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = QB
    out = subprocess.run([MANAGED_PY, "-m", "strategy_layer.a4_comparison_cli",
                          "--trade-date", trade_date],
                         cwd=QB, env=env, capture_output=True, text=True,
                         timeout=300)
    if out.returncode != 0:
        raise RuntimeError("a4 cli failed for %s: %s" % (trade_date, out.stderr.strip()))
    line = out.stdout.strip().splitlines()[-1]
    return json.loads(line)


def main():
    pg_rows = pg_grouped_rows()
    dates = archive_dates()
    a4_days = []
    for d in dates:
        r = run_a4_cli(d)
        a4_days.append({
            "trade_date": r.get("trade_date"),
            "history_status": r.get("history_status"),
            "observation_comparability": r.get("comparison", {}).get("observation_comparability"),
            "cutover_verdict": r.get("cutover_verdict"),
            "issues": r.get("issues", []),
        })

    # ---- 异常观察（如实报告，不修复）----
    anomalies = []
    today = "20261019"
    hist_days = [r for r in pg_rows if r["trade_date"] != today]
    if any(r["rows"] == 0 for r in pg_rows):
        anomalies.append("存在行数为 0 的日期")
    if any(r["superseded_rows"] != 0 for r in hist_days):
        anomalies.append("历史日出现 superseded（方案 C 应仅今日后生效）")
    if hist_days and all(r["superseded_rows"] == 0 for r in hist_days):
        pass  # 预期
    superseded_today = [r for r in pg_rows if r["trade_date"] == today]
    if not superseded_today or superseded_today[0]["superseded_rows"] == 0:
        anomalies.append("今日未见 superseded 行")
    for r in pg_rows:
        if r["min_created_at"] != r["max_created_at"]:
            anomalies.append("%s created_at min!=max，疑似多批次写入" % r["trade_date"])
    pg_dates = {r["trade_date"] for r in pg_rows}
    for d in dates:
        if d not in pg_dates:
            anomalies.append("归档日 %s 在 PG 中无任何行（PG 覆盖窗口外）" % d)
    # PG 中出现归档日之外的日期（如周末）仅作为观察项记录
    for r in pg_rows:
        if r["trade_date"] not in dates and r["trade_date"] != today:
            anomalies.append(
                "PG 含归档集之外日期 %s（%d 行），如为非交易日需业务确认" % (r["trade_date"], r["rows"]))
    mismatch_days = [d["trade_date"] for d in a4_days
                     if d["cutover_verdict"] != "BLOCK_CUTOVER"]
    if mismatch_days:
        anomalies.append("未恒为 BLOCK_CUTOVER: %s" % ",".join(mismatch_days))

    report = {
        "scope": "READONLY_RECEIPT_NOT_ADOPTION_EVIDENCE",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "pg_receipt": {
            "table": "strategy.signals_combined",
            "container": "quant-pg",
            "access": "docker exec quant-pg psql -U quant_user -d quant_system (SELECT only)",
            "days": pg_rows,
        },
        "a4_multiday": {
            "archive_source": "portal/data/picks_history/todays_*.json",
            "archive_dates": dates,
            "days": a4_days,
        },
        "anomalies": anomalies,
        "conclusions": {
            "pg_covers_dates": [r["trade_date"] for r in pg_rows],
            "superseded_only_on_or_after_today":
                all(r["superseded_rows"] == 0 for r in hist_days)
                and bool(superseded_today and superseded_today[0]["superseded_rows"] > 0),
            "all_days_block_cutover": all(d["cutover_verdict"] == "BLOCK_CUTOVER" for d in a4_days),
        },
    }

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ---- stdout 摘要表 ----
    print("PG 回执（strategy.signals_combined，只读）")
    print("%-10s %6s  %-28s %-28s %10s %12s" %
          ("trade_date", "rows", "min_created", "max_created", "divergence", "superseded"))
    for r in pg_rows:
        print("%-10s %6d  %-28s %-28s %10d %12d" %
              (r["trade_date"], r["rows"], r["min_created_at"], r["max_created_at"],
               r["divergence_rows"], r["superseded_rows"]))
    print()
    print("多日 A4 对账（归档日 %d 个）" % len(a4_days))
    print("%-10s %-14s %-16s %-16s" %
          ("trade_date", "history_status", "comparability", "cutover_verdict"))
    for d in a4_days:
        print("%-10s %-14s %-16s %-16s" %
              (d["trade_date"], d["history_status"],
               d["observation_comparability"], d["cutover_verdict"]))
    print()
    print("结论：superseded 仅今日后出现 = %s；全部 BLOCK_CUTOVER = %s；异常 %d 项"
          % (report["conclusions"]["superseded_only_on_or_after_today"],
             report["conclusions"]["all_days_block_cutover"], len(anomalies)))
    for a in anomalies:
        print("  [异常] %s" % a)
    print("报告：%s" % REPORT_PATH)


if __name__ == "__main__":
    sys.exit(main())
