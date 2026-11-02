# -*- coding: utf-8 -*-
"""S3-08 终验收报告渲染器：读 final_acceptance JSON + KPI，输出标准 Markdown 报告。

用法：
  python tools/datawarehouse/final_report_render.py --date 20261103
  python tools/datawarehouse/final_report_render.py --date 20261103 --accept-json <path> --out <md>

边界：只读 JSON/registry；只写指定报告 MD；不改数据/catalog/registry。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACCEPT_TMPL = REPO / "research" / "reports" / "final_acceptance_{date}.json"
OUT_TMPL = REPO / "deliverables" / "{date}_S3-08终验收报告.md"
KPI_JSON = REPO / "research" / "reports" / "kpi_baseline_20261102.json"
GATE_DESC = {
    "G1": "TdxSync 四批（17:35/45/55/18:05）",
    "G2": "看门狗严格判定（19:20）",
    "G3": "资质 K8（unknown<40）",
    "G4": "XmapSync（06:00）",
    "G5": "KgDailySync + catalog/registry（06:20）",
    "G6": "DataWarehouseSnapshot 首跑（06:40，K3）",
    "G7": "NoticeBackfill（06:45，PT8H）",
    "G8": "A3RotationCanary（07:00，accepted-risk）",
    "G9": "SentimentBT 首跑（07:20）",
    "G10": "KPI 复跑（kpi_report）",
}


def _iso(ts: str | None) -> str:
    if not ts:
        return "-"
    return str(ts).replace("T", " ")[:19]


def load_kpi() -> dict:
    try:
        d = json.loads(KPI_JSON.read_text(encoding="utf-8"))
        return d.get("kpis") or d
    except (OSError, ValueError):
        return {}


def render(accept: dict, kpi: dict, date: str) -> str:
    counts = accept.get("counts") or {}
    verdict = accept.get("verdict") or "UNKNOWN"
    lines = []
    lines.append("# S3-08 终验收报告（%s）" % date)
    lines.append("")
    lines.append("> 生成：%s｜工具：`tools/datawarehouse/final_acceptance.py`｜"
                 "判定：**%s**" % (datetime.now().strftime("%Y-%m-%d %H:%M"), verdict))
    lines.append("")
    lines.append("## 一、判定总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append("| verdict | **%s** |" % verdict)
    for k in ("pass", "fail", "expected-risk", "unknown", "total"):
        lines.append("| %s | %s |" % (k, counts.get(k, "-")))
    lines.append("")
    lines.append("## 二、Gate 明细")
    lines.append("")
    lines.append("| Gate | 描述 | 状态 | 证据/原因 |")
    lines.append("|---|---|---|---|")
    for g in accept.get("gates") or []:
        gid = g.get("gate") or "?"
        lines.append("| %s | %s | %s | %s |" % (
            gid, GATE_DESC.get(gid, ""), g.get("status") or "-",
            str(g.get("reason") or "").replace("|", "/")[:180]))
    lines.append("")
    lines.append("## 三、KPI（实测/当前）")
    lines.append("")
    lines.append("| # | KPI | 值 | 状态 |")
    lines.append("|---|---|---|---|")
    for key in ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10"):
        v = kpi.get(key) or {}
        val = v.get("value")
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False)[:80]
        lines.append("| %s | %s | %s | %s |" % (
            key, v.get("name") or key, str(val)[:80] if val is not None else "-",
            v.get("status") or "-"))
    lines.append("")
    lines.append("## 四、签字矩阵")
    lines.append("")
    lines.append("| 角色 | 结论 | 时间 | 备注 |")
    lines.append("|---|---|---|---|")
    for role, note in (("DATA1", "数据质量/资质"), ("DSH-Lead", "交付/证据"),
                       ("A0/老田", "G8 accepted-risk")):
        lines.append("| %s |  |  | %s |" % (role, note))
    lines.append("")
    lines.append("## 五、证据索引")
    lines.append("")
    lines.append("- 本报告数据：`research/reports/final_acceptance_%s.json`" % date)
    lines.append("- 首夜验收：`deliverables/2026-11-02_首夜验收_回执.md`")
    lines.append("- 历史数据验收：`deliverables/2026-11-02_历史数据验收测试汇总与收口判定.md`")
    lines.append("- 代码审查：`deliverables/2026-11-02_数据仓代码审查_核心工具_v1.md` 等")
    lines.append("- KPI：`deliverables/2026-11-02_S3-08_KPI基线_v1.md`")
    lines.append("- 11-05 窗口：`deliverables/2026-11-02_11-05窗口执行runbook_v1.md`")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="渲染 S3-08 终验收报告")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--accept-json", default="", help="final_acceptance JSON 路径")
    ap.add_argument("--out", default="", help="输出 MD 路径")
    args = ap.parse_args(argv)
    accept_path = Path(args.accept_json) if args.accept_json else ACCEPT_TMPL.with_name(
        ACCEPT_TMPL.name.format(date=args.date))
    if not accept_path.exists():
        print("ERR 缺少 final_acceptance JSON: %s" % accept_path, file=sys.stderr)
        return 2
    try:
        accept = json.loads(accept_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print("ERR JSON 解析失败: %s" % exc, file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else OUT_TMPL.with_name(
        OUT_TMPL.name.format(date=args.date))
    md = render(accept, load_kpi(), args.date)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("OK verdict=%s -> %s" % (accept.get("verdict"), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
