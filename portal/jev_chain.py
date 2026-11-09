# -*- coding: utf-8 -*-
"""接力层 —— 连板梯队 / 龙虎榜席位 / 上榜原因（2026-11-08）

回答一个具体问题：**谁在接力**。三张表各管一段，缺一张就答不出：

- `ts_limit_step` 某票某日几连板 → 梯队高度与拥挤度（有几层、哪层空着）
- `ts_hm_detail`   龙虎榜席位明细 → 谁买的（机构/北向/量化/游资，带名字）
- `ts_top_list`    龙虎榜汇总     → 为什么上榜、净买多少、占成交额多少

数据是 **xtcloud 已接入的付费源**，直查、不落盘、不爬。三张表合计
12 / 175 / 63 行（11-06），三次请求 ~1.5s，加 TTL 缓存 300s。

三个口径坑（都踩过，写在这里省下次）：

1. **`reason` 有 19 种措辞，语义只有 6 类**：同一件事因板块规则不同写成
   「非S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%」和「连续三个
   交易日内，涨幅偏离值累计达到20%」。按原文分组会得到 19 个 1~8 只的小组，
   什么都看不出来。必须先归一化。
2. **判断顺序**：`"有价格涨跌幅限制的日收盘价格跌幅偏离值达到7%"` 同时含
   「涨跌幅」和「跌幅」——先判「跌幅」，再判「涨幅」，否则跌幅榜会被算成涨。
3. **席位名 ≠ 营业部名**：`hm_name` 是称号（T王 / 章盟主 / 量化打板），
   `hm_orgs` 才是营业部。同一个营业部可能挂多个称号，按称号归类即可，
   但**不能**按营业部归类（「国泰海通证券总部」名下既有量化基金也有游资）。
"""
from __future__ import annotations

import os
import sys
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_TTL_S = 300
_CACHE: dict = {}
_XT = None

# 席位的四类。**不做「综合资金分」**：这四类钱的行为完全不同，加权平均
# 出来的数看着精确，其实把「能持三个月的机构」和「隔日就走的量化打板」
# 混成了一个「净买入」——那是把问题抹掉，不是回答。
SEAT_LABEL = {"inst": "机构", "north": "北向", "quant": "量化", "hot": "游资/大户"}

# 接力方式定性阈值：某一类占「净买总额」过半就归它。
STYLE_RATIO = 0.5


def _xt():
    global _XT
    if _XT is None:
        p = os.path.join(STATION, "tools")
        if p not in sys.path:
            sys.path.insert(0, p)
        import xtcloud_client
        _XT = xtcloud_client.client()
    return _XT


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL_S:
        return hit[1]
    return None


def _cache_put(key, val):
    _CACHE[key] = (time.time(), val)
    return val


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rows(tbl, day, limit=5000):
    """查当日表。日期过滤在这三张表上**是有效的**（不像 ts_stk_mins），
    total 也是过滤后的行数（不像 ts_stk_mins 的坑 3），可以放心用。"""
    d8 = "".join(ch for ch in str(day) if ch.isdigit())[:8]
    j = _xt().table(tbl, start_date=d8, end_date=d8, limit=limit)
    cols = j.get("columns") or []
    out = []
    for r in (j.get("rows") or []):
        out.append(dict(zip(cols, r)))
    return out


def seat_kind(name):
    """席位 → 四类之一。用称号不是营业部（见模块 docstring 坑 3）。"""
    n = str(name or "")
    if "机构专用" in n:
        return "inst"
    if "股通专用" in n or n.startswith(("深股通", "沪股通", "港股通")):
        return "north"
    if "量化" in n:
        return "quant"
    return "hot"


def reason_kind(r):
    """上榜原因 → 6 类。

    坑 2 有**两层**，第二层是 2026-11-08 用例才发现的：
    - 第一层：必须先判「跌幅」再判「涨幅」（跌幅榜的措辞里也含「涨幅」二字）
    - 第二层：「涨跌幅限制」是个固定词组，里面的「跌幅」不是跌的意思。
      `"有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%"` 若不先把「涨跌幅」
      剥掉，会被判成 **down** —— 11-06 有 15 只票踩这条（涨跌幅限制类规则
      是主板/创业板最常见的上榜原因之一，等于涨幅榜被算成跌幅榜）。
    """
    s = str(r or "").replace("涨跌幅", "")   # 固定词组，先剥掉再判方向
    if "退市整理" in s or "上市首日" in s:
        return "special"
    if "跌幅" in s:
        return "down"
    if "振幅" in s:
        return "amplitude"
    if "换手率" in s:
        return "turnover"
    if "涨幅" in s:
        return "up"
    return "other"


REASON_LABEL = {"up": "涨偏离", "down": "跌偏离", "turnover": "换手",
                "amplitude": "振幅", "special": "特殊", "other": "其他"}


def reason_span(r):
    """上榜原因的时间跨度：`1d`=当日，`3d`=连续三个交易日累计。

    **必须区分**：同样归为「涨偏离」，含义完全不同——
    - 当日涨偏离  → 今天在涨
    - 三日累计涨偏离 → 三天涨够了，今天可能只涨 1%、甚至已经回落
    11-06 先导基电就是后者：当日 +1.23%，但三日累计涨幅偏离过 20%，
    同时龙虎榜净卖 5.77 亿——这是派发，不是接力。只看 `reason_kind`
    会把它当成「今天在涨的票」。
    """
    s = str(r or "")
    return "3d" if ("连续" in s or "三个交易日" in s) else "1d"


def load_chain(day):
    """读当日接力三件套。→ {has, day, ladder, ladder_stat, seats, top, n_*, note}

    缺表不报错：每一项独立降级，页面照常出图（跟 jev_flow / jev_social 同构）。
    """
    key = ("chain", day)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"has": False, "day": day, "ladder": [], "ladder_stat": {},
           "seats": {}, "top": {}, "n_ladder": 0, "n_seat_codes": 0,
           "n_top": 0, "errors": [], "note": None}

    # ── 连板梯队 ──
    try:
        rows = _rows("ts_limit_step", day)
        lad = []
        for x in rows:
            code = str(x.get("ts_code") or "")
            try:
                nums = int(_f(x.get("nums")))
            except (TypeError, ValueError):
                nums = 0
            if not code or nums <= 0:
                continue
            lad.append({"code": code, "c6": code.split(".")[0],
                        "name": x.get("name"), "nums": nums})
        lad.sort(key=lambda z: (-z["nums"], z["code"]))
        dist = {}
        for z in lad:
            dist[z["nums"]] = dist.get(z["nums"], 0) + 1
        mx = max(dist) if dist else 0
        # 断层：从最高板往下数，第一处缺层。梯队断层=中间没人接，
        # 高位板的「空间」是虚的（下面没接力，只有上面几只孤零零的）
        # 只数到 2 板：1 板是首板，本来就不算「梯队」，数进去会得到
        # gap_at=1 这种假断层（每个正常交易日都会报断层）。
        gap = None
        for n in range(mx, 1, -1):
            if n not in dist:
                gap = n
                break
        out["ladder"] = lad
        out["ladder_stat"] = {
            "max_board": mx,
            "n_at_max": dist.get(mx, 0),
            "dist": {str(k): v for k, v in sorted(dist.items(), reverse=True)},
            "gap_at": gap,          # 断层高度（None=无断层）
            "n_total": len(lad),
        }
        out["n_ladder"] = len(lad)
        out["has"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"].append("limit_step: %s" % exc)

    # ── 席位明细 ──
    try:
        rows = _rows("ts_hm_detail", day)
        by = {}
        for x in rows:
            code = str(x.get("ts_code") or "")
            if not code:
                continue
            k = seat_kind(x.get("hm_name"))
            s = by.setdefault(code, {"c6": code.split(".")[0],
                                     "name": x.get("ts_name"),
                                     "buy": {}, "sell": {},
                                     "seats": []})
            buy = _f(x.get("buy_amount")) / 1e4      # 元 → 万元
            sell = _f(x.get("sell_amount")) / 1e4
            s["buy"][k] = s["buy"].get(k, 0.0) + buy
            s["sell"][k] = s["sell"].get(k, 0.0) + sell
            s["seats"].append({"name": x.get("hm_name"), "kind": k,
                               "net": round(buy - sell, 1),
                               "org": x.get("hm_orgs"),
                               "buy": round(buy, 1), "sell": round(sell, 1)})
        for code, s in by.items():
            s["seats"].sort(key=lambda z: -z["net"])
            s["seat_buy"] = round(sum(s["buy"].values()), 1)
            s["seat_sell"] = round(sum(s["sell"].values()), 1)
            # 已披露席位的净额。**不等于龙虎榜净额**：龙虎榜是全量汇总，
            # 席位明细只披露部分营业部（平均 2.8 席/只）。两者方向都可能
            # 相反（11-06 东方海洋：席位 -18 万 vs 龙虎榜 +9834 万），
            # 所以总量只认龙虎榜，这里只用于看「谁在买、谁在跑」。
            s["seat_net"] = round(s["seat_buy"] - s["seat_sell"], 1)
            s["style"] = _relay_style(s)
            s["buy_by_kind"] = {k: round(v, 1) for k, v in s["buy"].items()}
            s["sell_by_kind"] = {k: round(v, 1) for k, v in s["sell"].items()}
            # 买方榜按**买入额**排，不按净额：净额最大的人可能是「买很多
            # 又卖很多」的换手席位，那不是「谁在接」的答案。
            buyers = sorted(s["seats"], key=lambda z: -z["buy"])
            s["top_buyer"] = buyers[0]["name"] if buyers and buyers[0]["buy"] > 0 else None
            s["top_buyer_amt"] = buyers[0]["buy"] if buyers else None
            sellers = sorted([z for z in s["seats"] if z["sell"] > 0],
                             key=lambda z: -z["sell"])
            s["top_seller"] = sellers[0]["name"] if sellers else None
            s["top_seller_amt"] = sellers[0]["sell"] if sellers else None
            s["n_seats"] = len(s["seats"])
            s["n_sellers"] = len(sellers)
        out["seats"] = by
        out["n_seat_codes"] = len(by)
        out["has"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"].append("hm_detail: %s" % exc)

    # ── 龙虎榜汇总 ──
    try:
        rows = _rows("ts_top_list", day)
        tp = {}
        for x in rows:
            code = str(x.get("ts_code") or "")
            if not code:
                continue
            rk = reason_kind(x.get("reason"))
            cur = tp.get(code)
            # 一只票可能因多条规则同时上榜（涨偏离 + 换手），合并去重；
            # 净买额只取一次（是同一份榜单数据，不是两笔钱，不能相加）
            if cur:
                if rk not in cur["reasons"]:
                    cur["reasons"].append(rk)
                # 同一只票只要有一条是「连续三日」规则，就按 3d 算：
                # 宁可把跨度报大，也不能把「三天涨够了」显示成「今天在涨」。
                if reason_span(x.get("reason")) == "3d":
                    cur["span"] = "3d"
                continue
            tp[code] = {"c6": code.split(".")[0], "name": x.get("name"),
                        "pct": round(_f(x.get("pct_change")), 2),
                        "net": round(_f(x.get("net_amount")) / 1e4, 1),
                        "l_buy": round(_f(x.get("l_buy")) / 1e4, 1),
                        "l_sell": round(_f(x.get("l_sell")) / 1e4, 1),
                        "amount": round(_f(x.get("amount")) / 1e4, 1),
                        "reasons": [rk],
                        "span": reason_span(x.get("reason")),
                        "reason_raw": x.get("reason")}
        for code, t in tp.items():
            # 净买占成交额比：龙虎榜资金的实际分量。1% 和 15% 是一回事吗？
            # 光看「净买 1.8 亿」不知道是大还是小，除以成交额才有尺度。
            t["net_pct_amount"] = (round(t["net"] / t["amount"] * 100, 2)
                                   if t["amount"] else None)
        out["top"] = tp
        out["n_top"] = len(tp)
        out["has"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"].append("top_list: %s" % exc)

    # ── 派发：涨得好看但龙虎榜在净卖 ──
    # 这是整个接力层最硬的一条判据：涨停板上出货，盘口看不出来（封住了），
    # 龙虎榜看得出来。11-06 万向德农当日 +10.04% 涨停、龙虎榜净卖 1.01 亿；
    # 先导基电当日只涨 1.23%（三日累计涨偏离）、净卖 5.77 亿。
    DIST_PCT = 7.0
    try:
        dist = [{"code": c, "c6": T["c6"], "name": T["name"],
                 "pct": T["pct"], "net": T["net"], "span": T.get("span"),
                 "net_pct_amount": T.get("net_pct_amount"),
                 "reasons": [REASON_LABEL.get(k, k) for k in T["reasons"]]}
                for c, T in (out["top"] or {}).items()
                if (T.get("pct") or 0) >= DIST_PCT and (T.get("net") or 0) < 0]
        dist.sort(key=lambda z: z["net"])       # 净卖最多的排最前
        out["distribution"] = dist
        out["dist_pct_th"] = DIST_PCT
    except Exception as exc:  # noqa: BLE001
        out["distribution"] = []
        out["errors"].append("distribution: %s" % exc)

    if not out["has"]:
        out["note"] = ("当日无接力数据；三张表查询全部失败：%s"
                       % "; ".join(out["errors"]))
    else:
        out["note"] = ("连板梯队=ts_limit_step（几连板）；席位四类按称号归"
                       "（机构专用/股通专用/量化*/其余为游资大户），不做"
                       "「综合资金分」——四类钱的行为不同，加权会把差异抹平；"
                       "龙虎榜净买占成交额比才有尺度，单看绝对额看不出轻重。")
    return _cache_put(key, out)


def _relay_style(s):
    """按**买入额**构成给接力方式定性 —— 「谁在接力」问的是谁在买，不是净额。

    **必须用买入额，不能用净额**：净额会把买卖方向相反的同类别席位互相
    抵消。11-06 浙文影业（5 板）四个席位：游资买 6703+4470 万同时另一个
    游资卖 7778 万、量化买 5085 万。按净额算 hot=3396 万、quant=5085 万，
    量化占 60% → 判成「量化打板」；按买入额算游资 11174 万 vs 量化 5085 万
    → 游资 69%，是真游资在接力。前者把结论说反了。

    - config    机构+北向 买 ≥ 半数 → 配置盘，能持
    - quant     量化 买 ≥ 半数 → 打板资金，隔日就走，不构成接力
    - hot_money 游资/大户 买 ≥ 半数 → 击鼓传花（看是谁：章盟主类高位接力
                与 T王类散户聚集完全不同，前端用 top_buyer 显示）
    - no_buy    披露席位全部只卖不买 → 高位派发，不是接力
    """
    buy = s.get("buy") or {}
    tot = sum(buy.values())
    if tot <= 0:
        return "no_buy"
    if (buy.get("inst", 0.0) + buy.get("north", 0.0)) >= tot * STYLE_RATIO:
        return "config"
    if buy.get("quant", 0.0) >= tot * STYLE_RATIO:
        return "quant"
    return "hot_money"


STYLE_LABEL = {"config": "配置盘（机构+北向）", "quant": "量化打板（隔日走）",
               "hot_money": "游资接力", "no_buy": "只卖不买（派发）"}


def board_chain(chain, board_name, members):
    """焦点板块在梯队里的位置 + 板块内接力质量。

    这一步回答的是「这个板块有没有接力链」，而不是「今天谁最强」：
    - 板块内 0 只连板 → **板块刚启动（全是首板）**，没有接力，涨得再猛也是
      第一天的事，第二天谁接不知道
    - 板块内有 ≥2 只同一高度 → 有梯队（一批票同步晋级，是真板块效应）
    - 板块内最高板孤立（该高度全市场只有它） → 孤板，断板就没有下一棒
    """
    if not chain or not chain.get("has"):
        return {"has": False, "note": (chain or {}).get("note")}

    lad = {z["code"]: z for z in (chain.get("ladder") or [])}
    seats = chain.get("seats") or {}
    top = chain.get("top") or {}
    stat = chain.get("ladder_stat") or {}

    rows = []
    for z in members:
        L = lad.get(z["code"])
        if not L:
            continue
        S = seats.get(z["code"]) or {}
        T = top.get(z["code"]) or {}
        rows.append({
            "code": z["code"], "c6": z["c6"], "name": z["name"],
            "nums": L["nums"], "pct": z.get("pct"),
            "on_list": bool(T),
            # 总量只认龙虎榜（全量）；seat_net 是已披露席位合计，两者可以
            # 方向相反，不能互相替代（11-06 东方海洋：席位 -18 万 vs 榜 +9834 万）
            "list_net": T.get("net"),
            "net_pct_amount": T.get("net_pct_amount"),
            "reasons": [REASON_LABEL.get(k, k) for k in (T.get("reasons") or [])],
            "span": T.get("span"),
            "style": S.get("style"), "style_label": STYLE_LABEL.get(S.get("style")),
            "top_buyer": S.get("top_buyer"), "top_seller": S.get("top_seller"),
            "top_seller_amt": S.get("top_seller_amt"),
            "n_seats": S.get("n_seats"), "n_sellers": S.get("n_sellers"),
            "seat_buy": S.get("seat_buy"), "seat_sell": S.get("seat_sell"),
            "seat_net": S.get("seat_net"),
            "buy_by_kind": S.get("buy_by_kind"),
        })
    rows.sort(key=lambda r: (-r["nums"], -(r["list_net"] or 0)))

    # 板块内高度分布：同一高度 ≥2 只 = 有同步晋级的梯队
    dist = {}
    for r in rows:
        dist[r["nums"]] = dist.get(r["nums"], 0) + 1
    max_in = max(dist) if dist else 0
    peers = dist.get(max_in, 0) if max_in else 0

    # 全市场同高度只数：判断是不是「孤板」。全市场该层只有它一只，
    # 断板之后没有同层伙伴兜着，风险只由它自己承担。
    sole = (max_in > 0 and (stat.get("dist") or {}).get(str(max_in)) == 1)

    n_on_list = sum(1 for r in rows if r["on_list"])
    n_net_sell = sum(1 for r in rows if (r["list_net"] or 0) < 0)

    return {
        "has": True,
        "board": board_name,
        "n_members": len(members),
        "n_in_ladder": len(rows),
        "max_board_in_board": max_in,
        "n_at_max_in_board": peers,
        "sole_at_max": sole,
        "market_max_board": stat.get("max_board"),
        "market_gap_at": stat.get("gap_at"),
        "n_on_list": n_on_list,
        "n_net_sell": n_net_sell,
        "dist_in_board": {str(k): v for k, v in sorted(dist.items(), reverse=True)},
        "items": rows,
        "verdict": _verdict(rows, max_in, peers, sole, stat, n_net_sell),
    }


def _verdict(rows, max_in, peers, sole, stat, n_net_sell):
    """一句话结论。**必须有**——只给一堆数字等于没回答「谁在接力」。"""
    if not rows:
        return ("板块内 0 只连板：全是首板，**还没有接力**。今天涨停的是第一棒，"
                "明天谁接不知道；要盯的是次日能否出现同板块 2 板。")
    mkt = stat.get("max_board") or 0
    if max_in >= mkt and sole:
        return ("板块最高 %d 板且全市场同高度仅此一只（孤板）：断板后没有同层"
                "伙伴承接，接力链条只有这一根。" % max_in)
    if peers >= 2:
        tail = ("；但板块内有 %d 只次日净卖出，已在派发" % n_net_sell) if n_net_sell else ""
        return ("板块最高 %d 板、同高度 %d 只同步晋级：**有梯队**，接力链条成立%s。"
                % (max_in, peers, tail))
    return ("板块最高 %d 板、同高度仅 1 只：接力靠单只票撑，没有板块厚度。"
            % max_in)
