# -*- coding: utf-8 -*-
"""数据仓可用数据目录：盘点 curated parquet 的行数/列/维度，不写 DB。"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated")
OUT = Path(r"C:\Temp\dw_catalog.json")

DIM = {
    "daily": "行情", "adj_factor": "复权", "daily_basic": "估值", "income": "利润",
    "balancesheet": "资产负债", "cashflow": "现金流", "fina_indicator": "财务指标",
    "moneyflow": "资金流", "margin_detail": "融资融券", "top_list": "龙虎榜",
    "limit_list_d": "涨跌停", "cyq_perf": "筹码", "cyq_chips": "筹码分布",
    "anns_d": "公告", "news": "新闻", "major_news": "要闻", "research_report": "研报",
    "stock_basic": "股票主数据", "stock_company": "公司资料", "sw_classify_l1": "申万行业",
    "sw_l1_member": "申万成分", "index_daily": "指数行情", "index_weight": "指数权重",
    "top10_holders": "十大股东", "top10_floatholders": "十大流通股东",
    "stk_holdernumber": "股东户数", "stk_managers": "管理层", "irm_qa_sz": "互动易",
    "hk_basic": "港股主数据", "hk_income": "港股利润", "hk_balancesheet": "港股资产负债",
    "hk_cashflow": "港股现金流", "hk_fina_indicator": "港股财务指标",
    "us_basic": "美股主数据", "us_daily": "美股行情", "us_income": "美股利润",
    "us_balancesheet": "美股资产负债", "us_cashflow": "美股现金流",
    "us_fina_indicator": "美股财务指标", "trade_cal": "交易日历",
}


def main() -> int:
    rows = []
    for p in sorted(DW.glob("*.parquet")):
        try:
            md = pq.ParquetFile(p).metadata
            names = [f.name for f in pq.ParquetFile(p).schema_arrow]
            rows.append({
                "name": p.stem,
                "dimension": DIM.get(p.stem, "其他"),
                "rows": md.num_rows,
                "columns": names,
                "bytes": p.stat().st_size,
            })
        except Exception as e:  # noqa: BLE001
            rows.append({"name": p.stem, "error": str(e)[:120]})
    rows.sort(key=lambda r: -int(r.get("rows") or 0))
    out = {
        "schema": "zhice.dw_catalog/v1",
        "read_only": True,
        "root": str(DW),
        "datasets": rows,
        "summary": {
            "datasets": len(rows),
            "rows": sum(int(r.get("rows") or 0) for r in rows),
            "dimensions": sorted({r.get("dimension") for r in rows if r.get("dimension")}),
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    for r in rows:
        print(f"{r.get('rows',0):>12,}  {r['name']:<22} {r.get('dimension')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
