# -*- coding: utf-8 -*-
"""账本叙述层：把形态字段翻成「能对客户说出口」的话。

为什么单列一层
--------------
缠论字段串（中枢③[23.50-31.48] · 下破沿首笔 · 沿下28.7% · 常量 · 盘整背驰）
是给同行核对用的，不是给人听的。它的问题不在不专业，而在：
  没有主语（谁在做什么）、没有动作（磨/捅/丢）、没有因果（所以呢）、
  没有结论（我该怎么办）。字段拼贴的信息量为零——客户听完不知道要干什么。

商业化要的是同一件事的三种说法，按听众切换：
  name   形态名（4 字）—— 能记住、能复述、能当标签喊出来
  one    一句话 —— 当面说得出，不看屏幕也能讲完
  story  三句 —— 之前怎样 → 现在怎样 → 所以怎样
  act    结论 + 动作 —— 可看 / 观察 / 别碰
  terms  术语串 —— 专业核对，折叠在详情里

诚实条款
--------
每句话后面挂真实统计（同形态 N 笔、持有 10 日平均）。这不是装饰：
市面上讲缠论靠玄，我们每句形态话术后面都能查账。这是唯一能商业化的护城河。
"""
from __future__ import annotations

# 形态分桶的历史表现（2026-11-12 全窗 1162 条三买、零成本、10 日持有）
# (样本数, 平均收益 %, t 值) —— 与 _form_leave_test 同口径
_STAT = {
    "up": (473, -1.40, -2.8),
    "in": (365, -1.08, -2.2),
    "down": (324, 0.97, 2.5),
}

_N2 = lambda v: "%.2f" % v  # noqa: E731


def _S1(v, sign=True):
    """百分比：统一用中文负号，客户面版式里 ASCII 减号看着像连字符。"""
    s = ("%+.1f" % v) if sign else ("%.1f" % abs(v))
    return s.replace("-", "−") + "%"


def _vol_tag(vr):
    if vr is None:
        return ""
    try:
        vr = float(vr)
    except (TypeError, ValueError):
        return ""
    if vr >= 1.8:
        return "放量"
    if vr >= 1.15:
        return "温和放量"
    if vr < 0.85:
        return "缩量"
    return ""


def _classify(fm):
    """→ (key, 组别 up/in/down)。key 决定用哪套话。"""
    pos = fm.get("pos") or 0.0
    cur, zd = fm.get("cur"), fm.get("zd")
    dy = (cur / zd - 1) * 100 if (cur and zd) else 0.0
    lv = fm.get("leave")
    nba = fm.get("nbi_after") or 1
    if lv == "up":
        if pos <= 1:
            return "huitie", "up"
        if pos <= 3:
            return "xuankong", "up"
        if pos <= 8:
            return "pashneg", "up"
        return "zhuigao", "up"
    if lv == "in":
        return ("ganghui" if nba <= 2 else "xiangnei"), "in"
    if dy > -3:
        return "shishou", "down"
    if dy > -15:
        return "powwei", "down"
    return "shendie", "down"


# 每套话：name / one(一句话) / s1(之前) / s2(现在) / s3(所以) / act / level
# 用 .format 填数，字段名固定
_SAY = {
    "huitie": dict(
        name="回踩贴沿", level="可看",
        one="{zg} 这条上沿踩住了，收在框上方 {pos}%——回踩不破，是教科书里那个位置。",
        s1="价格先在 {zd}~{zg} 之间来回磨了 {p_n_bi} 笔，围出一个箱体；",
        s2="向上捅出去之后又回来了，回到 {zg} 附近只高 {pos}%，上沿没丢；",
        s3="这是标准三买该有的样子——突破、回踩、不破。",
        act="可看。位置站得住，也就看得懂怎么管：{zg} 是防守位，收盘跌破先出来，别扛。"),
    "xuankong": dict(
        name="贴沿悬空", level="观察",
        one="踩着 {zg} 的上沿，但脚没落下来，悬空 {pos}%，差一口气。",
        s1="箱体在 {zd}~{zg}，磨了 {p_n_bi} 笔才围出来；",
        s2="现在是站在上沿外侧，但没踩回去，{pos}% 这个距离很尴尬；",
        s3="贴着上沿吊着，往下扑一下就打回箱内，往上冲又没启动。",
        act="观察。盯住 {zg}——站住才算数，掉回箱内这个形态就作废。"),
    "pashneg": dict(
        name="沿上爬升", level="观察",
        one="已经跑到框上方 {pos}%，这不算回踩了，是在往上追。",
        s1="箱体 {zd}~{zg}，{p_n_bi} 笔磨完才选择方向；",
        s2="离开箱体已经走了 {nbi_after} 笔，现在比上沿高 {pos}%；",
        s3="位置偏高，回踩的空间还没走出来。",
        act="观察。别在这追，等它回踩到 {zg} 附近再谈——追进去等于把止损位丢了。"),
    "zhuigao": dict(
        name="高位悬空", level="回避",
        one="离框 {pos}%，这不是三买，是追高。",
        s1="箱体 {zd}~{zg} 早就被抛在下面；",
        s2="一路走上来已经 {nbi_after} 笔，现价高出上沿 {pos}%；",
        s3="结构上的买点（贴着上沿那一脚）早就走完了，现在买的是别人的浮盈。",
        act="回避。买点过去太远，进去没地方放止损。要等它跌回 {zg} 附近重新做起结构。"),
    "ganghui": dict(
        name="箱内回落", level="观察",
        one="价格又回到 {zd}~{zg} 的箱子里，位置在上沿下方。",
        s1="箱体 {zd}~{zg} 是它的震荡区间；",
        s2="现在收在箱体内，距上沿还有 {gap_up}%，中枢之后走了 {nbi_after} 笔；",
        s3="箱体里的位置两头不占——上面是套牢盘，下面才是支撑。",
        act="观察。上沿 {zg} 站不上去就还是震荡，箱底 {zd} 才是真支撑；两头都不占的时候别动手。"),
    "xiangnei": dict(
        name="箱内打转", level="观察",
        one="价格还在 {zd}~{zg} 的箱子里打转，方向没选。",
        s1="箱体 {zd}~{zg}，已经反复磨了 {p_n_bi} 笔；",
        s2="中枢结束之后走了 {nbi_after} 笔，价格仍在箱内 {pos}% 的位置；",
        s3="买点落在箱体中间是最差的位置——上有压力下有支撑，两头都不占。",
        act="观察。箱体中间没有赔率——上有 {zg} 压着，下有 {zd} 托着，等它选完方向再进。"),
    "shishou": dict(
        name="箱底失守", level="回避",
        one="箱底 {zd} 刚丢，破位才刚开始。",
        s1="箱体 {zd}~{zg} 撑了一段时间；",
        s2="现在跌到箱底下方，比下沿低 {dy}%；",
        s3="刚破位的票，第一波反抽通常最弱。",
        act="回避。破位初段不是买点。先看三天内能不能收回 {zd}，收不回来就是下跌中继。"),
    "powwei": dict(
        name="破位下行", level="回避",
        one="箱底 {zd} 丢了，现价比它低 {dy}%——这是下跌途中的反抽，不是三买。",
        s1="箱体 {zd}~{zg} 已被有效跌破；",
        s2="离开段走了 {nbi_after} 笔，现价在箱底下方 {dy}%；",
        s3="所谓三买在这个位置根本不成立——买点在原箱体上沿，不在破位之后。",
        act="回避。三买的买点在原箱体上沿 {zg}，不在破位之后。要重新有戏，先站回 {zd} 以上。"),
    "shendie": dict(
        name="深度破位", level="回避",
        one="比箱底还低 {dy}%，结构已经走坏，别在这找买点。",
        s1="原箱体 {zd}~{zg} 早被击穿；",
        s2="现价比箱底低 {dy}%，已经离开结构很远；",
        s3="这不是回踩，是趋势破坏之后的下跌延续。",
        act="回避。缠论在这里没有买点，只有等新的中枢重新长出来；离 {zd} 已经太远，没有参考位。"),
}


def narrate(fm, fac=None, meta=None):
    """form dict → 叙述 dict。

    fm    ledger_form.form_of() 的输出
    fac   扫描器因子（vr 等）
    meta  {"gap", "dev", "score", "dt"} 扫描口径对照
    """
    fac, meta = fac or {}, meta or {}
    vr = fac.get("vr")
    vt = _vol_tag(vr)

    if not fm or not fm.get("ok"):
        why = (fm or {}).get("why") or "结构不足"
        return {"name": "结构不足", "level": "观察", "vtag": "",
                "one": "K 线不够（%s），画不出完整结构。" % why,
                "story": [], "act": "跳过", "stat": None, "warn": None,
                "terms": ""}

    zg, zd, pos = fm.get("zg"), fm.get("zd"), fm.get("pos")
    cur, chain = fm.get("cur"), fm.get("chain") or []

    # 只有笔、没有中枢：这一层要如实说，不能硬编
    if not zg or not zd:
        n = fm.get("n_bi") or 0
        return {"name": "未成中枢", "level": "观察", "vtag": vt,
                "one": "全窗 %d 笔还没围出中枢，谈不上三买。" % n,
                "story": ["笔序：" + " → ".join("%.2f" % x for x in chain)],
                "act": "跳过。没有中枢就没有 zg/zd，位置无从谈起。",
                "stat": None, "warn": None,
                "terms": "笔 %d 根 · 无中枢" % n}

    key, grp = _classify(fm)
    say = _SAY[key]
    d = dict(zg=_N2(zg), zd=_N2(zd), pos=("%.1f" % (pos or 0)).replace("-", "−"),
             cur=_N2(cur or 0), p_n_bi=fm.get("p_n_bi") or 0,
             nbi_after=fm.get("nbi_after") or 1,
             gap_up="%.1f" % abs(pos or 0),
             dy=("%.1f" % abs((cur / zd - 1) * 100)) if zd else "0.0",
             width=fm.get("width") or 0)

    # 量能不塞进形态名：表格列宽只有一句话的位置，「破位下行（温和放量）」会挤掉正文
    name = say["name"]
    one = say["one"].format(**d)
    story = [say["s1"].format(**d), say["s2"].format(**d), say["s3"].format(**d)]

    # 箱体极窄时补一句：整个结构被压在一个很薄的区间里
    if d["width"] and float(d["width"]) < 6:
        story.insert(1, "箱体只有 %.1f%% 宽，上下都被压得很死；" % float(d["width"]))

    st = _STAT.get(grp)
    stat = None
    if st:
        n, avg, t = st
        # 统计句必须带方向判断，否则「可看 + 历史 −1.4%」自相矛盾，
        # 客户一眼就看出是在糊弄。位置质量与盈利预期是两件事，分开说。
        if avg < 0 and t <= -1.5:
            # 「位置标准」这种话只能配给 level=可看 的档，否则牛头不对马嘴
            tail = ("这一档历史上是亏的——位置标准不等于能赚，别拿形态好看当买入理由。"
                    if say["level"] == "可看" else
                    "这一档历史上也是亏的，跟上面的判断一致。")
        elif avg > 0 and t >= 1.5:
            tail = "这一档历史上是赚的，但赚的是超跌反弹的钱，不是三买的钱。"
        elif avg < 0:
            tail = "这一档历史上偏负，但没到显著，只能当参考。"
        else:
            tail = "这一档历史上偏正，但没到显著，只能当参考。"
        stat = "同形态历史 %d 笔，持有 10 日平均 %s（t=%.1f）。%s" % (
            n, _S1(avg), t, tail)

    # 扫描口径警示：老信号在展示日早已不是买点
    warn = None
    gap, dev = meta.get("gap"), meta.get("dev")
    if dev is not None and dev <= -10:
        warn = ("这条是 %s 天前扫出来的信号，现价比当时的买点低 %s，"
                "买点在扫出之后就已经破了——图上看的是今天的形态，不是那天的。"
                % (gap if gap is not None else "?", _S1(dev, sign=False)))

    # 术语串：留给同行核对，不进正文
    terms = ["中枢%d[%s-%s]" % (fm.get("pidx") or 0, _N2(zd), _N2(zg)),
             {"up": "上破沿", "in": "箱内", "down": "下破沿"}.get(fm.get("leave"), ""),
             "离开段%d笔" % d["nbi_after"],
             "pos %+.1f%%" % (pos or 0),
             "宽 %s%%" % d["width"]]
    if fac.get("bei24"):
        terms.append("盘整背驰")
    if fac.get("tbe24"):
        terms.append("趋势背驰")
    if fac.get("mbei"):
        terms.append("MACD背离")
    if vr is not None:
        try:
            terms.append("vr %.2f" % float(vr))
        except (TypeError, ValueError):
            pass
    terms = " · ".join(x for x in terms if x)

    return {"name": name, "level": say["level"], "vtag": vt, "one": one,
            "story": story, "act": say["act"].format(**d), "stat": stat, "warn": warn,
            "terms": terms, "key": key}


def _selfcheck():
    """话术自检：占位符能不能全填上、有没有残留、评级合不合规。

    2026-11-12 踩过：act 里加了 {zd} 却忘了 .format()，页面上直接印出
    「离 {zd} 已经太远」。占位符不是文案的私事，它会原样出现在客户面前。
    """
    fake = dict(zg="24.30", zd="18.50", pos="0.8", cur="24.50", p_n_bi=7,
                nbi_after=2, gap_up="3.0", dy="4.5", width="31.4")
    bad = []
    for k, v in _SAY.items():
        for f in ("one", "s1", "s2", "s3", "act"):
            try:
                t = v[f].format(**fake)
            except (KeyError, IndexError) as e:
                bad.append((k, f, "缺字段 %s" % e))
                continue
            if "{" in t or "}" in t:
                bad.append((k, f, "残留占位符"))
        if v.get("level") not in ("可看", "观察", "回避"):
            bad.append((k, "level", "评级非法：%s" % v.get("level")))
        if "{zg}" not in v["act"] and "{zd}" not in v["act"]:
            bad.append((k, "act", "结论里没有关键价位（客户拿不到动作）"))
    print("自检：%d 套话术，%d 处问题" % (len(_SAY), len(bad)))
    for b in bad:
        print("  ×", b)
    return not bad


def _demo():
    """给一组真实形态跑一遍，人工看话术顺不顺。"""
    cases = [
        ("回踩贴沿", dict(ok=True, zg=24.30, zd=18.50, cur=24.50, pos=0.8,
                          leave="up", nbi_after=1, p_n_bi=7, pidx=3, width=31.4)),
        ("贴沿悬空", dict(ok=True, zg=24.30, zd=18.50, cur=24.80, pos=2.1,
                          leave="up", nbi_after=2, p_n_bi=9, pidx=3, width=31.4)),
        ("高位悬空", dict(ok=True, zg=24.30, zd=18.50, cur=27.40, pos=12.8,
                          leave="up", nbi_after=4, p_n_bi=6, pidx=2, width=31.4)),
        ("箱内打转", dict(ok=True, zg=24.30, zd=18.50, cur=21.00, pos=-13.6,
                          leave="in", nbi_after=5, p_n_bi=8, pidx=3, width=31.4)),
        ("破位下行", dict(ok=True, zg=31.48, zd=23.50, cur=16.76, pos=-46.8,
                          leave="down", nbi_after=3, p_n_bi=9, pidx=3, width=33.9)),
    ]
    for tag, fm in cases:
        n = narrate(fm, {"vr": 0.7}, {"gap": 33, "dev": -37.0})
        print("=" * 64)
        print("[%s]  %s  评级：%s" % (tag, n["name"], n["level"]))
        print("  一句话：", n["one"])
        for i, s in enumerate(n["story"]):
            print("    %d) %s" % (i + 1, s))
        print("  结论：", n["act"])
        if n["stat"]:
            print("  统计：", n["stat"])
        if n["warn"]:
            print("  警示：", n["warn"])
        print("  术语：", n["terms"])


if __name__ == "__main__":
    ok = _selfcheck()
    _demo()
    raise SystemExit(0 if ok else 1)
