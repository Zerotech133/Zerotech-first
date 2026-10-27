# -*- coding: utf-8 -*-
"""C6 每日指标（2026-10-27 WO-P0-DEV8-METRICS）。

复用 `tools/dev_weekly_report.py` 的只读计算，落
`agent_platform/data/dev/daily_metrics.json`，并对 C6 阈值做告警：
采集份额（近 7 天 dev 单）≥5、归属率 ≥85%、验证覆盖 ≥90%、E3 触发 4/4。

用法：
  python -X utf8 tools/dev_metrics_daily.py            # 生成当日文件
  python -X utf8 tools/dev_metrics_daily.py --json     # 只打印
  python -X utf8 tools/dev_metrics_daily.py --check    # 校验文件存在且结构完整
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import dev_weekly_report as DWR  # noqa: E402

OUT = ROOT / "agent_platform" / "data" / "dev" / "daily_metrics.json"
E3_SEATS = ("ARCH1", "DEV1", "DEV5", "OPS1")


def thresholds():
    return {"dev_tasks_recent_min": 5, "owner_legal_rate_min": 0.85,
            "verification_min": 0.90, "e3_required": 4}


def evaluate_alerts(metrics, th=None):
    """纯函数：根据指标与阈值返回告警列表（空=全达标）。"""
    th = th or thresholds()
    alerts = []
    if (metrics.get("dev_tasks_recent") or 0) < th["dev_tasks_recent_min"]:
        alerts.append("dev 采集份额不足：%s < %s"
                      % (metrics.get("dev_tasks_recent"),
                         th["dev_tasks_recent_min"]))
    rate = metrics.get("owner_legal_rate_recent")
    if rate is not None and rate < th["owner_legal_rate_min"]:
        alerts.append("归属率不足：%.1f%% < %.0f%%"
                      % (rate * 100, th["owner_legal_rate_min"] * 100))
    tagged = metrics.get("dev_dept_tagged") or 0
    covered = metrics.get("dev_dept_with_acceptance") or 0
    if tagged:
        cov = covered / tagged
        if cov < th["verification_min"]:
            alerts.append("验证覆盖不足：%.1f%% < %.0f%%"
                          % (cov * 100, th["verification_min"] * 100))
    e3 = metrics.get("e3_trigger") or {}
    ok = sum(1 for v in e3.values() if (v or {}).get("ok"))
    if ok < th["e3_required"]:
        alerts.append("E3 触发不足：%d/%d" % (ok, th["e3_required"]))
    return alerts


def build_daily(now=None):
    now = now or time.time()
    tasks = DWR.load_tasks()
    events = DWR.load_events()
    routing = DWR._load_json(DWR.ROUTING_PATH, {})
    gate = DWR._load_json(DWR.GATE_PATH, {"phase": "warn"})
    m = DWR.compute(tasks, events, routing=routing, gate=gate)
    daily = {
        "schema": "dev_daily_metrics/v1",
        "date": time.strftime("%Y-%m-%d", time.localtime(now)),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "window_days": m.get("window_days", 7),
        "metrics": {
            "tasks_total": m.get("tasks_total"),
            "tasks_recent": m.get("tasks_recent"),
            "dev_tasks_recent": m.get("dev_tasks_recent"),
            "dev_merged_recent": m.get("dev_merged_recent"),
            "dev_capture_share_recent": m.get("dev_capture_share_recent"),
            "owner_legal_rate_recent": m.get("owner_legal_rate_recent"),
            "dev_dept_tagged": m.get("dev_dept_tagged"),
            "dev_dept_with_acceptance": m.get("dev_dept_with_acceptance"),
            "dev_leadtime_p50_min": m.get("dev_leadtime_p50_min"),
            "dev_leadtime_n": m.get("dev_leadtime_n"),
            "e3_trigger": m.get("e3_trigger"),
        },
        "thresholds": thresholds(),
    }
    daily["alerts"] = evaluate_alerts(m)
    return daily


def main(argv=None):
    ap = argparse.ArgumentParser(description="C6 每日指标（只读）")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if args.check:
        ok = out.exists()
        text = out.read_text(encoding="utf-8") if ok else ""
        try:
            data = json.loads(text) if ok else {}
        except ValueError:
            data = {}
        ok = ok and all(k in data for k in
                        ("schema", "date", "metrics", "thresholds", "alerts"))
        print(("PASS" if ok else "FAIL") + "：" + str(out))
        return 0 if ok else 1
    daily = build_daily()
    if args.json:
        print(json.dumps(daily, ensure_ascii=False, indent=1))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(daily, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print("daily_metrics -> %s" % out)
    print("alerts=%s" % (daily["alerts"] or "无（全达标）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
