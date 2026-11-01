# -*- coding: utf-8 -*-
"""对外能力导出：最新 Trend Template 筛选报告 → portal/static/trend_template_latest.json

对外口径（v1）：
  - 公开字段：交易日期/股票池/八条口径/通过数/条件通过率/候选清单（含近似 RS 标注）/边界/免责声明
  - 不输出：原始行情、内部路径、账户/持仓信息
  - 数据为研究输出，不构成投资建议；每次运行覆盖 latest（历史见 research/reports/）
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
REPORTS = REPO / "research" / "reports"
OUT = REPO / "portal" / "static" / "trend_template_latest.json"

COND_SPEC = [
    {"id": "C1", "rule": "P > S150 且 P > S200", "note": "等号不通过"},
    {"id": "C2", "rule": "S150 > S200", "note": ""},
    {"id": "C3", "rule": "S200 至少上升 1 个月", "note": "端点代理（S200(t)>S200(t-21)），非逐日单调"},
    {"id": "C4", "rule": "S50 > S150 且 S50 > S200", "note": ""},
    {"id": "C5", "rule": "P > S50", "note": ""},
    {"id": "C6", "rule": "P ≥ 1.30 × 近一年低点", "note": "书版 30% 变体；部分社区用 25%"},
    {"id": "C7", "rule": "P ≥ 0.75 × 近一年高点", "note": "距高点 ≤25%，等号允许"},
    {"id": "C8", "rule": "RS ≥ 70", "note": "偏好 90+；本实现为全 A 近似，非 IBD 专有 RS"},
]


def _load_v11() -> dict | None:
    import glob as _g
    fs = sorted(_g.glob(str(REPO / "research/reports/trend_template_v11_*.json")))
    if not fs:
        return None
    d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    r = d.get("results") or []
    if not r:
        return None
    b = r[0]
    return {"window": d["meta"]["window"], "best": {k: b[k] for k in ("universe", "rs", "exit", "cagr_pct", "mdd_pct", "vol_pct", "trades")},
            "findings": ["RS 口径：R_252 优于 ret12+ret3 近似", "退出：8% 固定止损优于 200日线退出（A 股 whipsaw）",
                          "流动性/规模过滤在 TT 候选上不 binding（三档结果相同）",
                          "最佳组合 CAGR 14.1% vs 市场 13.4%，MDD -48.7% → 风险调整后不通过"],
            "report": "research/reports/" + Path(fs[-1]).name}


def main() -> int:
    import re as _re
    files = sorted(f for f in glob.glob(str(REPORTS / "trend_template_*.json"))
                   if _re.fullmatch(r"trend_template_\d{8}\.json", Path(f).name))
    if not files:
        print("no report", flush=True)
        return 1
    r = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    meta = r.get("meta", {})
    cands = []
    for x in r.get("candidates", []):
        cands.append({
            "code": x.get("code"), "name": x.get("name"),
            "close": x.get("close"), "rs_approx": x.get("RS_approx"),
            "s50": x.get("S50"), "s150": x.get("S150"), "s200": x.get("S200"),
            "low_dist_pct": x.get("low_dist_pct"), "high_dist_pct": x.get("high_dist_pct"),
        })
    near = []
    for x in (r.get("near_miss_list") or [])[:30]:
        near.append({"code": x.get("code"), "name": x.get("name"),
                     "rs_approx": x.get("RS_approx"),
                     "missing": [k.split("_")[0] for k in r.get("condition_pass_rate", {}) if not x.get(k)]})
    payload = {
        "api_version": "1.0",
        "kind": "trend_template_screen",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trade_date": meta.get("date"),
        "dataset": {"universe_all": r.get("universe", {}).get("all_codes"),
                     "universe_base": r.get("universe", {}).get("base_codes")},
        "conditions": COND_SPEC,
        "results": {"pass_all": r.get("pass_counts", {}).get("pass_all"),
                     "near_miss": r.get("near_miss_7"),
                     "condition_pass_rate_pct": r.get("condition_pass_rate")},
        "candidates": cands,
        "near_miss_sample": near,
        "method": {"adjust": meta.get("adjust"), "year_bars": meta.get("year_bars"),
                    "c3_method": meta.get("c3_method"), "rs_method": meta.get("rs_method")},
        "backtest_v11": _load_v11(),
        "boundary_notes": [
            "筛选器仅实现八条入场条件，不含 VCP/买点；v1.1 组合回测已做（20仓×5%，2019-2026）。",
            "股票池未做指数成分过滤（流动性代理在 TT 候选上不区分）；组合级回测显示风险调整后不通过（CAGR≈市场，MDD -49%）。",
            "C3 为端点代理；RS 为全 A 近似（非 IBD 专有）；一年窗口 250 交易日近似。",
            "数据为研究输出，不构成任何投资建议；使用者自行承担风险。",
        ],
        "sources": [
            "Pineify: Minervini Trend Template（书版 8 条基线）https://pineify.app/tradingview/screeners/minervini-trend-template",
            "O'Neil RS Rating 方法（专有排名说明）",
            "MarketInOut: 14 股票池回测 / TSX 25 年回测",
            "Trade-Ideas: VCP 详解",
        ],
        "learning": {"notes": "知识库：米勒维尼SEPA体系_学习笔记；资源库：米勒维尼SEPA_全网资源库",
                      "cards": "/kb_cards.html", "search": "/kb_reports.html"},
        "owner": "老田的AI助手 / DSH-Lead",
        "disclaimer": "仅供研究学习，不构成投资建议；历史与规则不保证未来收益。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"trade_date": payload["trade_date"], "pass_all": payload["results"]["pass_all"],
                      "candidates": len(cands), "near_miss": len(near), "out": str(OUT)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
