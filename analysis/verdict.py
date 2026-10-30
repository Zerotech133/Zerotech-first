import logging
# -*- coding: utf-8 -*-
"""S5 结论层：规则引擎（评级/情景/关键位/风险/观察清单）。

原则：评级只做研究口径；所有结论都要能在 features/events 中找到依据。
"""


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

    # P0L：非经常性损益识别——一次性收益不得抬高评级
    nr = fnd.get("nonrecurring") or {}
    nr_ratio = _num(nr.get("ratio"))
    nr_extra = _num(nr.get("extra_item_yi"))
    big_nr = (nr_ratio is not None and nr_ratio >= 0.5
              and (nr_extra is None or abs(nr_extra) >= 0.5))
    if big_nr:
        score -= 1.0  # 抵消上面"盈利 +1.0"的口径污染
        reasons[:] = [r for r in reasons if not r.startswith("最新报告期盈利")]
        if np_ is not None and np_ > 0:
            reasons.append("利润主要来自非经常性损益（%.2f 亿，占归母 %.0f%%）"
                           % (nr_extra or 0, nr_ratio * 100))
        if nr.get("profit_dedt_yi") is not None:
            risks.append("扣非净利仅 %.2f 亿，盈利质量不支撑头条利润" % nr["profit_dedt_yi"])
        if nr.get("dt_yoy") is not None and nr["dt_yoy"] < 0:
            risks.append("扣非净利同比 %.1f%%，主业未改善" % nr["dt_yoy"])
        signals.append("非经常性损益占比 %.0f%%（一次性）" % (nr_ratio * 100))
    elif nr.get("profit_dedt_yi") is not None:
        signals.append("扣非净利 %.2f 亿" % nr["profit_dedt_yi"])

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

    # ---- 扩展数据（sync 缓存）：一致预期/业绩预告/龙虎榜/股东行为 ----
    ex = (feat.get("extras") or {})
    cons = ex.get("consensus")
    if cons:
        signals.append("一致预期：近180天 %d 篇研报，最新评级 %s" % (cons.get("reports_180d", 0), cons.get("latest_rating") or "—"))
        if cons.get("eps_last") is not None:
            signals.append("最新预测 EPS %s" % round(float(cons["eps_last"]), 2))
        if cons.get("buy_ratio") is not None and cons["buy_ratio"] < 0.5:
            risks.append("卖方近半年买入/增持比例偏低（%.0f%%）" % (cons["buy_ratio"] * 100))
    fc = ex.get("forecast")
    if fc:
        t = fc.get("type") or ""
        if any(k in t for k in ("预增", "扭亏", "略增")):
            signals.append("业绩预告：%s（%s）" % (t, fc.get("summary", "")))
            if not big_nr:
                score += 0.3
        elif any(k in t for k in ("续亏", "预亏", "首亏", "略减")):
            risks.append("业绩预告：%s（%s）" % (t, fc.get("summary", "")))
    lhb = ex.get("lhb")
    if lhb:
        signals.append("龙虎榜：近90天 %d 次" % lhb.get("count_90d", 0))
        if lhb.get("seat_net_wan") is not None:
            if lhb["seat_net_wan"] > 0:
                signals.append("席位净买入 %.0f 万" % lhb["seat_net_wan"])
            else:
                risks.append("席位净卖出 %.0f 万" % abs(lhb["seat_net_wan"]))
    ha = ex.get("holder_actions") or {}
    sell_ratio = sum(abs(x.get("ratio") or 0) for x in (ha.get("holdertrade") or []) if x.get("in_de") == "DE")
    if sell_ratio > 1:
        risks.append("近一年重要股东减持合计 %.1f%%" % sell_ratio)
    if (ha.get("pledge_ratio") or 0) > 30:
        risks.append("质押比例 %.1f%%，需关注平仓风险" % ha["pledge_ratio"])
    if ha.get("float_next_180d"):
        mx = max([x.get("ratio") or 0 for x in ha["float_next_180d"]] or [0])
        if mx >= 5:
            risks.append("未来180天解禁比例最高 %.1f%%" % mx)
    if ha.get("repurchase"):
        signals.append("回购：%s" % (ha["repurchase"].get("proc") or ""))

    # 财报日历门禁：临近披露不确定性上升
    try:
        from datetime import date, datetime
        today = date.today()
        days = None
        for c in (snap.get("calendar") or []):
            p = str(c.get("pre_date") or "")
            if len(p) == 8:
                d = datetime.strptime(p, "%Y%m%d").date()
                dd = (d - today).days
                if 0 <= dd <= 30:
                    days = dd if days is None else min(days, dd)
        if days is not None and days <= 7:
            risks.append("财报披露临近（%d 天），业绩不确定性上升" % days)
            if rating == "买入":
                rating = "观察偏多"
            elif rating == "观察偏多" and days <= 2:
                rating = "观察"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/verdict.py", exc)

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
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/verdict.py", exc)
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
        "nonrecurring": nr or None,
        "score": round(score, 2),
        "disclaimer": "研究分析，不构成投资建议",
    }
    return verdict
