# -*- coding: utf-8 -*-
"""公告分类 v2：columns_txt（官方类型）+ 标题关键词 双层规则。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANN_DB = ROOT / "zerobound-kg" / "data" / "ann_history" / "ann_history.db"
if not ANN_DB.exists():
    ANN_DB = Path(r"C:\项目\zerobound-kg\data\ann_history\ann_history.db")

COLUMNS_RULES = [
    ("业绩预告", ("业绩预告", "业绩快报", "业绩预披露", "预增", "预减", "预盈", "预亏")),
    ("定期报告", ("年度报告", "半年度报告", "一季度报告", "三季度报告", "季度报告", "报告摘要")),
    ("增减持", ("减持", "增持", "股份变动", "权益变动")),
    ("回购", ("回购",)),
    ("重组并购", ("重组", "并购", "收购", "重大资产", "要约")),
    ("融资定增", ("非公开发行", "定增", "可转债", "配股", "发行股份", "向特定对象", "募集资金")),
    ("分红权益", ("分配预案", "分配方案", "权益分派", "分红", "派息", "利润分配")),
    ("股权激励", ("股权激励", "员工持股", "限制性股票")),
    ("诉讼处罚风险", ("诉讼", "仲裁", "处罚", "立案", "警示", "退市", "风险提示", "问询", "监管措施")),
    ("担保", ("担保",)),
    ("澄清异动", ("澄清", "异常波动", "异动")),
    ("停复牌", ("停牌", "复牌")),
    ("高管变动", ("高管", "任职变动", "辞职", "聘任", "换届")),
    ("股东大会", ("股东大会",)),
    ("调研关系", ("调研", "投资者关系")),
    ("对外投资关联", ("对外投资", "关联交易", "投资理财", "增资", "设立子公司")),
    ("治理合规", ("董事会", "监事会", "议事规则", "管理办法", "管理制度", "独立董事",
                  "审计机构", "内部控制", "审计报告", "专项说明", "保荐", "核查意见",
                  "法律意见书", "公司债券", "评级")),
    ("ESG", ("ESG", "社会责任", "环境")),
]

TITLE_RULES = [
    ("定期报告", ("年度报告", "半年度报告", "一季度报告", "三季度报告", "季度报告", "报告摘要")),
    ("治理合规", ("董事会", "监事会", "股东大会", "独立董事", "审计机构", "审计报告",
                  "保荐", "核查意见", "法律意见书", "内部控制", "议事规则", "管理办法",
                  "管理制度", "专项说明", "评级", "公司债券")),
    ("调研关系", ("调研", "投资者关系")),
    ("业绩预告", ("业绩预告", "业绩快报", "预增", "预减", "预盈", "预亏")),
    ("增减持", ("减持", "增持")),
    ("回购", ("回购",)),
    ("重组并购", ("重组", "并购", "收购", "重大资产", "要约")),
    ("融资定增", ("非公开发行", "定增", "可转债", "配股", "发行股份", "向特定对象")),
    ("重大合同中标", ("中标", "重大合同", "框架协议", "签订")),
    ("分红权益", ("权益分派", "分红", "利润分配", "派息")),
    ("股权激励", ("股权激励", "员工持股", "限制性股票")),
    ("诉讼处罚风险", ("诉讼", "仲裁", "处罚", "立案", "警示", "退市", "风险提示", "问询")),
    ("担保", ("担保",)),
    ("澄清异动", ("澄清", "异常波动", "异动")),
    ("停复牌", ("停牌", "复牌")),
    ("高管变动", ("辞职", "聘任", "换届", "董事长", "总经理", "董事")),
    ("对外投资关联", ("对外投资", "设立子公司", "增资", "关联交易")),
]


def classify(columns_txt: str, title: str = "") -> str:
    cols = columns_txt or ""
    for cat, kws in COLUMNS_RULES:
        if any(k in cols for k in kws):
            return cat
    t = title or ""
    for cat, kws in TITLE_RULES:
        if any(k in t for k in kws):
            return cat
    return "其他"


def coverage(ann_db: Path | None = None, since: str = "2023-01-01") -> dict:
    db = Path(ann_db) if ann_db is not None else ANN_DB
    con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True, timeout=60)
    rows = con.execute(
        "SELECT a.columns_txt, a.title, s.stock_code FROM ann a JOIN ann_stock s "
        "ON a.art_code=s.art_code WHERE a.notice_date>=?", (since,)).fetchall()
    con.close()
    cnt = Counter(classify(c or "", t or "") for c, t, _sc in rows)
    total = sum(cnt.values())
    other = cnt.get("其他", 0)
    return {"tool": "ann_classify", "since": since, "events": total,
            "other": other, "other_pct": round(other * 100.0 / total, 2) if total else 0,
            "categories": dict(cnt.most_common())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公告分类 v2 / 覆盖率检查")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--since", default="2023-01-01")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = coverage(since=args.since)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("分类覆盖：%d 事件 / 其他 %d（%.1f%% / 目标<20%%）" % (
            res["events"], res["other"], res["other_pct"]))
        for k, v in res["categories"].items():
            print("  %-14s %8d %5.1f%%" % (k, v, v * 100.0 / res["events"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
