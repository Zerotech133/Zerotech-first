# -*- coding: utf-8 -*-
"""S5 结论层：规则引擎（评级/情景/关键位/风险/观察清单）。

原则：评级只做研究口径；所有结论都要能在 features/events 中找到依据。
"""
from . import config


def _num(x):
    return x if isinstance(x, (int, float)) else None


def build_verdict(snap, feat, events):
    tech, val, fnd = feat["technical"], feat["valuation"], feat["fundamental"]
    mf = feat.get("moneyflow", {})
    chips = feat.get("chips", {})
    tags = fnd.get("special_status") or []
    reasons, risks, signals = [], [], []
    score = 0.0

    # 基本面
    np_ = _num(fnd.get("net_profit_latest_yi"))
    rev_yoy, np_yoy = _num(fnd.get("revenue_yoy")), _num(fnd.get("net_profit_yoy"))
    if np_ is not None:
        if np_ > 0:
            score += 1.0
            reasons.append("最新报告期盈利（%.2f 亿）" % np_)
        else:
            reasons.append("最新报告期亏损（%.2f 亿）" % np_)
            risks.append("尚未盈利，盈利拐点未确认")
            if np_yoy is not None and np_yoy > 0:
                score += 0.3
                reasons.append("亏损同比收窄（%+.1f%%）" % np_yoy)
    if rev_yoy is not None:
        if rev_yoy > 5:
            score += 0.7
            reasons.append("营收同比增长 %.1f%%" % rev_yoy)
        elif rev_yoy < -5:
            score -= 0.5
            risks.append("营收同比下滑 %.1f%%" % rev_yoy)
        else:
            reasons.append("营收基本持平（%+.1f%%）" % rev_yoy)
    gm = _num(fnd.get("gross_margin"))
    if gm is not None:
        if gm >= 40:
            score += 0.3
            reasons.append("毛利率较高（%.1f%%）" % gm)
        elif gm < 20:
            score -= 0.3

    # 估值
    pct = _num(val.get("peer_percentile"))
    if pct is not None:
        if pct <= 30:
            score += 0.8
            reasons.append("PS 处于同业低位（%.1f 分位）" % pct)
        elif pct >= 70:
            score -= 0.6
            risks.append("PS 高于同业（%.1f 分位）" % pct)
        signals.append("PS 同业分位 %.1f" % pct)

    # 技术
    price, ma20, ma60 = _num(tech.get("price")), _num(tech.get("ma20")), _num(tech.get("ma60"))
    rank = _num(tech.get("pct_rank_250d"))
    if price and ma20:
        if price > ma20:
            score += 0.4
        else:
            score -= 0.3
            risks.append("价格位于 MA20 下方")
    if price and ma60:
        if price > ma60:
            score += 0.4
        else:
            score -= 0.2
    if rank is not None:
        if rank >= 70:
            score += 0.4
            signals.append("250 日分位 %.0f%%" % rank)
        elif rank <= 30:
            score -= 0.3
    r20 = _num(tech.get("ret_20d"))
    if r20 is not None and r20 > 25:
        risks.append("20 日涨幅 %.0f%%，短期获利盘厚" % r20)
        signals.append("20 日 +%.0f%%" % r20)

    # 资金/筹码
    n20 = _num(mf.get("main_net_20d_wan"))
    if n20 is not None:
        if n20 < -5000:
            score -= 0.4
            risks.append("主力 20 日净流出 %.0f 万" % n20)
        elif n20 > 5000:
            score += 0.3
            signals.append("主力 20 日净流入 %.0f 万" % n20)
    win = _num(chips.get("winner_rate"))
    if win is not None and win > 90:
        risks.append("获利盘 %.0f%%，回撤易触发止盈" % win)

    # 事件/特殊状态
    if "ST" in tags:
        risks.append("ST 风险警示（5% 涨跌幅）")
        score -= 1.0
    if "退市风险" in tags:
        risks.append("存在退市风险警示事项")
        score -= 1.5
    if "重整" in tags:
        risks.append("重整/预重整程序存在不确定性")
        signals.append("重整程序推进中")
    if len([e for e in events if e["category"] in ("问询/监管", "诉讼", "退市/ST")]) >= 2:
        risks.append("近期存在监管/诉讼/退市类负面事件")

    # 评级
    if "退市风险" in tags or "重整" in tags:
        rating = "事件观察"
        horizon = "事件驱动"
    elif score >= 2.5:
        rating = "买入"
        horizon = "1季"
    elif score >= 1.2:
        rating = "观察偏多"
        horizon = "1月"
    elif score >= -0.5:
        rating = "观察"
        horizon = "1月"
    else:
        rating = "回避"
        horizon = "1月"
    if "ST" in tags and rating == "买入":
        rating = "观察"

    # 盘后/停牌/控制权变更等事件覆盖：事件驱动优先
    try:
        pending = [e for e in (events or []) if str(e.get("date", "")) > snap["as_of"]
                   and ("停牌" in (e.get("title") or "") or "控制权" in (e.get("title") or "")
                        or e.get("category") in ("停牌/控制权", "重组并购", "重整"))]
    except Exception:
        pending = []
    if pending:
        rating = "事件观察"
        horizon = "事件驱动"
        signals.append("盘后事件：" + (pending[0].get("title") or "")[:34])
        risks.append("停牌/控制权变更/重组事项存在不确定性（事件驱动）")

    # 关键位
    levels = list(tech.get("levels") or [])
    pivots = []
    ch = tech.get("chanlun") or {}
    if ch.get("ok"):
        for p in (ch.get("pivots") or [])[-2:]:
            try:
                pivots.append({"type": "pivot", "price": round(float(p.get("zd")), 2),
                               "label": "中枢下沿(%s~%s)" % (p.get("s_dt"), p.get("e_dt"))})
                pivots.append({"type": "pivot", "price": round(float(p.get("zg")), 2),
                               "label": "中枢上沿(%s~%s)" % (p.get("s_dt"), p.get("e_dt"))})
            except Exception:
                pass
    levels = pivots[:4] + levels

    # 情景
    scen = [
        {"id": "sA", "name": "基本面向好兑现", "probability": 0.35 if rating in ("买入", "观察偏多") else 0.25,
         "trigger": "财报/订单/毛利率验证改善", "meaning": "估值修复", "watch": ["季报", "业绩预告", "合同负债"]},
        {"id": "sB", "name": "区间震荡", "probability": 0.35,
         "trigger": "多空无新证据", "meaning": "跟随板块", "watch": ["成交量", "主力资金"]},
        {"id": "sC", "name": "基本面不及预期", "probability": 0.25 if rating in ("买入", "观察偏多") else 0.3,
         "trigger": "亏损扩大/收入下滑/减值", "meaning": "回踩支撑", "watch": ["季报", "减值公告"]},
        {"id": "sD", "name": "事件/主题催化", "probability": 0.1 if rating in ("买入", "观察偏多") else 0.2,
         "trigger": "政策/行业事件/重组", "meaning": "短期弹性", "watch": ["公告", "板块成交额"]},
    ]

    # 观察清单
    watch = []
    for c in (snap.get("calendar") or []):
        pd_ = c.get("pre_date") or ""
        ed = c.get("end_date") or ""
        if len(pd_) >= 8:
            watch.append({"date": pd_[:4] + "-" + pd_[4:6] + "-" + pd_[6:8],
                          "item": "预约披露（报告期 %s）" % (ed[:4] + "-" + ed[4:6] + "-" + ed[6:8] if len(ed) >= 8 else ed),
                          "type": "财报"})
    watch.append({"date": snap["as_of"], "item": "关注支撑/压力位与成交量变化", "type": "价格"})
    if events:
        watch.append({"date": events[0]["date"], "item": "最近事件：" + events[0]["title"][:40], "type": "事件"})
    if not watch:
        watch = [{"date": snap["as_of"], "item": "等待新公告/财报", "type": "事件"}]

    # 论点
    thesis = "；".join(reasons[:4]) if reasons else "数据不足，暂不下结论"
    if risks:
        thesis += "。主要风险：" + "；".join(risks[:3])

    verdict = {
        "rating": rating, "confidence": round(min(0.85, max(0.35, 0.45 + abs(score) * 0.08)), 2),
        "horizon": horizon, "thesis": thesis,
        "key_signals": signals[:6], "reasons": reasons[:8],
        "triggers": ["季报/预告验证业绩", "放量突破 250 日高或回踩 MA20 企稳"],
        "invalidations": ["跌破 60 日低", "出现监管立案/退市风险", "核心逻辑被证伪"],
        "levels": levels, "scenarios": scen, "risks": risks[:10], "watch": watch,
        "score": round(score, 2),
        "disclaimer": "研究分析，不构成投资建议",
    }
    return verdict
