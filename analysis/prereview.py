# -*- coding: utf-8 -*-
"""事件 AI 预审清单（只出建议，不代替人审）。

对 pending 事件按改进后的规则重分类，并标记：
  approve   = 建议通过（类别与现库一致）
  reclassify= 建议改类别（给出 suggested_category）
  reject    = 建议驳回（程序性/治理类文件，非投研事件）
输出 CSV + Markdown 汇总，供人工在门户 /sa 或 CLI 批量确认；本模块不写复核状态。

用法：
  python -m analysis.prereview --code 600337.SH,688023.SH,605258.SH
  python -m analysis.prereview --all --out C:\\项目\\deliverables\\... 
"""
import argparse
import csv
import json
import os
import time
from collections import Counter

from . import config, events as events_mod, review

# 程序性/治理类文件：不是投研事件，建议驳回
NOISE = (
    "实施细则", "管理制度", "公司章程", "章程", "独立董事", "审计委员会", "保荐",
    "持续督导", "法律意见书", "会议材料", "股东大会", "会议通知", "核查意见",
    "述职报告", "社会责任", "内部控制", "募集资金", "鉴证报告", "专项报告",
    "候选人", "名单", "薪酬管理制度", "信息披露管理制度",
)

# 预审分类（顺序敏感；在 config.EVENT_RULES 之前做更精确的覆盖）
PRERULES = [
    ("重整", ("重整", "预重整", "破产清算", "债权人会议")),
    ("停牌/控制权", ("停牌", "复牌", "控制权", "要约")),
    ("退市/ST", ("风险警示", "退市", "终止上市", "*ST")),
    ("重组并购", ("重组", "并购", "发行股份购买", "收购", "重大资产")),
    ("业绩", ("业绩预告", "业绩快报", "年度报告", "半年度报告", "季度报告", "预增", "预亏", "业绩说明会")),
    ("减持/增持", ("减持", "增持", "权益变动")),
    ("回购", ("回购",)),
    ("质押", ("质押",)),
    ("解禁", ("解除限售", "限售股", "解禁")),
    ("分红", ("利润分配", "分红", "权益分派")),
    ("诉讼", ("诉讼", "仲裁", "起诉", "判决")),
    ("问询/监管", ("问询函", "监管函", "警示函", "立案", "处罚", "监管工作函")),
    ("股权激励", ("限制性股票", "股权激励", "员工持股")),
    ("高管变动", ("辞职", "辞任", "离任", "聘任", "高级管理人员")),
    ("经营/订单", ("中标", "订单", "合同", "战略合作", "停产", "复产", "产能", "项目")),
    ("行业政策", ("政策", "规划", "指导意见", "标准")),
    ("市场异动", ("异常波动", "股票交易")),
]


def suggest(title):
    t = title or ""
    if any(k in t for k in NOISE):
        return "治理/程序", "reject", 0.9
    for cat, kws in PRERULES:
        if any(k in t for k in kws):
            return cat, None, 0.85
    return "其他", None, 0.5


def build(codes=None, limit=1000, path=None):
    rows = []
    for code in codes or []:
        for e in review.pending(code=code, limit=limit, path=path):
            cat, forced, conf = suggest(e.get("title"))
            action = forced or ("approve" if cat == e.get("category") else "reclassify")
            flags = []
            if forced == "reject":
                flags.append("程序性文件")
            elif cat != e.get("category"):
                flags.append("旧类别=%s" % e.get("category"))
                if cat == "重整" and e.get("category") == "退市/ST":
                    flags.append("重整误标退市")
            rows.append({
                "code": e.get("code"), "date": e.get("date"), "event_id": e.get("event_id"),
                "current_category": e.get("category"), "suggested_category": cat,
                "action": action, "confidence": conf, "flags": ";".join(flags),
                "title": e.get("title"), "url": e.get("url"),
            })
    rows.sort(key=lambda r: (r["code"], r["date"]))
    return rows


def summary(rows):
    by_code = {}
    for r in rows:
        d = by_code.setdefault(r["code"], Counter())
        d[r["action"]] += 1
    trans = Counter("%s → %s" % (r["current_category"], r["suggested_category"])
                    for r in rows if r["action"] == "reclassify")
    return {"total": len(rows),
            "approve": sum(1 for r in rows if r["action"] == "approve"),
            "reclassify": sum(1 for r in rows if r["action"] == "reclassify"),
            "reject": sum(1 for r in rows if r["action"] == "reject"),
            "by_code": {k: dict(v) for k, v in by_code.items()},
            "top_transitions": trans.most_common(12)}


def write_outputs(rows, outdir, prefix="events_prereview"):
    os.makedirs(outdir, exist_ok=True)
    day = time.strftime("%Y%m%d")
    csvp = os.path.join(outdir, "%s_%s.csv" % (prefix, day))
    with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["code", "date", "event_id", "current_category",
                                          "suggested_category", "action", "confidence",
                                          "flags", "title", "url"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    st = summary(rows)
    md = ["# 事件 AI 预审清单（%s）" % time.strftime("%F %T"), "",
          "> 只出建议，不代替人审。复核动作请到门户 `/sa` 或 `python -m analysis.review`。",
          "",
          "| 项 | 数 |", "|---|---|",
          "| 预审总数 | %d |" % st["total"],
          "| 建议通过 | %d |" % st["approve"],
          "| 建议改类别 | %d |" % st["reclassify"],
          "| 建议驳回（程序性） | %d |" % st["reject"], "",
          "## 分标的", "", "| 标的 | 通过 | 改类别 | 驳回 |", "|---|---|---|---|"]
    for code, c in st["by_code"].items():
        md.append("| %s | %s | %s | %s |" % (code, c.get("approve", 0), c.get("reclassify", 0), c.get("reject", 0)))
    md += ["", "## 主要类别修正（旧 → 建议）", "", "| 修正 | 条数 |", "|---|---|"]
    for k, v in st["top_transitions"]:
        md.append("| %s | %d |" % (k, v))
    md += ["", "## 明细", "", "见 `%s`。" % os.path.basename(csvp),
           "", "人工确认后可批量执行（示例）：", "",
           "```powershell",
           "python -X utf8 -m analysis.review --approve <event_id> --category <建议类别>",
           "python -X utf8 -m analysis.review --reject <event_id> --note 程序性文件",
           "```"]
    mdp = os.path.join(outdir, "events_prereview_%s.md" % day)
    with open(mdp, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return {"csv": csvp, "md": mdp, "summary": st}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--out", default=r"C:\项目\deliverables\2026-10-26_事件AI预审清单")
    a = ap.parse_args()
    codes = [c.strip() for c in a.code.split(",") if c.strip()]
    if not codes or a.all:
        codes = ["600337.SH", "688023.SH", "605258.SH"]
    rows = build(codes, a.limit)
    outp = write_outputs(rows, a.out)
    print(json.dumps(outp["summary"], ensure_ascii=False, indent=1))
    print("csv:", outp["csv"])
    print("md:", outp["md"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
