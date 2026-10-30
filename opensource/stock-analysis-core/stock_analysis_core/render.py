# -*- coding: utf-8 -*-
"""Markdown 报告渲染（H5/PDF 可在此基础上扩展）。"""
from typing import Any, Dict


def _fmt(v, suffix="") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%.2f" % v) + suffix
    return str(v) + suffix


def render(ctx: Dict[str, Any]) -> str:
    snap = ctx["snapshot"]
    feat = ctx["features"]
    v = ctx["verdict"]
    checks = ctx["checks"]
    tech = feat.get("technical", {})
    val = feat.get("valuation", {})
    fun = feat.get("fundamental", {})
    lines = [
        "# %s（%s）个股研究 · 开源内核演示" % (snap.name or snap.code, snap.code),
        "",
        "> 数据 as-of %s ｜ 引擎 stock-analysis-core %s ｜ 规则版本 %s ｜ 研究分析，不构成投资建议"
        % (snap.as_of or tech.get("last_date", ""), ctx.get("version", ""), v.policy_version),
        "",
        "## 一、结论摘要",
        "",
        "**研究评级：%s**（评分 %s/100，置信度 %s，时间窗 %s）" % (v.rating, v.score, v.confidence, v.horizon),
        "",
        v.thesis,
        "",
        "| 维度 | 得分 |",
        "|---|---|",
    ]
    for k, x in v.factors.items():
        lines.append("| %s | %s |" % (k, x))
    lines += ["", "关键信号：" + "；".join(v.key_signals), "",
              "## 二、公司画像", "",
              "- 代码：%s ｜ 名称：%s" % (snap.code, snap.name or "—"),
              "- 数据：日线 %s 根 ｜ 估值 %s 日 ｜ 财务 %s 期 ｜ 事件 %s 条"
              % (tech.get("bars"), val.get("days"), fun.get("periods"), feat.get("events", {}).get("count")),
              "", "## 三、财务", "",
              "| 指标 | 值 |", "|---|---|",
              "| 报告期 | %s |" % _fmt(fun.get("end_date")),
              "| 营收 | %s |" % _fmt(fun.get("revenue"), " 亿"),
              "| 归母净利 | %s |" % _fmt(fun.get("net_profit"), " 亿"),
              "| 营收同比 | %s |" % _fmt(fun.get("revenue_yoy"), "") if fun.get("revenue_yoy") is not None else "| 营收同比 | — |",
              "| 净利同比 | %s |" % _fmt(fun.get("net_profit_yoy"), "") if fun.get("net_profit_yoy") is not None else "| 净利同比 | — |",
              "| 毛利率 | %s |" % _fmt(fun.get("gross_margin"), "%"),
              "| 资产负债率 | %s |" % _fmt(fun.get("debt_ratio"), "%"),
              "", "## 四、估值", "",
              "| 指标 | 值 |", "|---|---|",
              "| PE | %s |" % _fmt(val.get("pe")),
              "| PS | %s |" % _fmt(val.get("ps")),
              "| PB | %s |" % _fmt(val.get("pb")),
              "| PS 历史分位 | %s |" % _fmt(val.get("ps_pct_hist"), "%"),
              "", "## 五、交易面", "",
              "| 指标 | 值 |", "|---|---|",
              "| 最新收盘 | %s |" % _fmt(tech.get("last_close")),
              "| 20/60/250 日收益 | %s / %s / %s |" % (_fmt(tech.get("ret_20")), _fmt(tech.get("ret_60")), _fmt(tech.get("ret_250"))),
              "| MA20 / MA60 | %s / %s |" % (_fmt(tech.get("ma20")), _fmt(tech.get("ma60"))),
              "| 250 日分位 | %s |" % _fmt(tech.get("pct_rank_250"), "%"),
              "", "## 六、事件", "",
              "共 %s 条，类别：%s" % (feat.get("events", {}).get("count"), "、".join(feat.get("events", {}).get("categories") or []) or "—"),
              "", "## 七、风险", "",
              "1. 数据与口径限制：本演示仅使用合成数据；真实使用需接入自有数据源并校验。",
              "2. 规则打分参数为示例，不代表任何真实投资判断。",
              "3. 研究分析，不构成投资建议。",
              "", "## 八、质量门", "",
              "| 门 | 通过 | 分数 | 问题 |", "|---|---|---|---|",
    ]
    for c in checks:
        lines.append("| %s | %s | %s | %s |" % (c.name, "✅" if c.passed else "❌", _fmt(c.score), "；".join(c.issues) or "—"))
    lines += ["", "## 九、数据口径与免责", "",
              "- 本报告由开源内核生成，数据来自用户自备数据源（演示为合成数据）。",
              "- 研究分析，不构成投资建议。", ""]
    return "\n".join(lines)
