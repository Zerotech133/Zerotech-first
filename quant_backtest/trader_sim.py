# -*- coding: utf-8 -*-
"""K68 三操盘手模拟锦标赛（影子模拟层，绝不触真实账本）

用户愿景 2026-10-04：选股策略出票 → 基本面投委会（基金经理/分析师/
执行组织）开会辩论 → 结果交给三位操盘手 —— 激进/保守/中立 —— 各按
两种盘算（积极/保守）模拟建仓，用真实日 K（ts_daily）做买卖点，
最终评价「每个策略选出的每只股票」在各位操盘手手上的结果。

管线挂接（全部复用存量，零新数据源）：
  council_proposals.jsonl（投委会留痕，含 vote/directive.sizing.k/
  params 观点价位线/views stances）→ 本模块 →
  reports/trader_sim.json（六本账：3 操盘手 × 2 盘算）
  reports/trader_sim_eval.json（战绩榜：策略×股票×操盘手 对照基准）

规则口径（V1，全部留痕可回放）：
  · 入场门槛：投票「反对」不接单；支持/中性 → 辩论日次一交易日开盘进场
  · 仓位：名义系数 = 盘算基数 × 系数k(K66) × 操盘手个性系数——
    激进 1.2（aggr stance 与净多空同向才放大）/ 保守 0.8（cons 同向
    才缩小）/ 中立 1.0 —— 即「激进和保守在净多空一致时分别放大和
    缩小仓位」在执行层的落地；clamp [0.2, 1.5]
  · 止损线：激进=ML 动量失效线 / 保守=RL 风控线（K64c 观点线优先，
    无效则兜底：激进-6% / 保守-8% / 中立按桶纪律线）
  · 止盈：TP 到达——保守盘算全出落袋；积极盘算出一半、余仓保本+
    追踪止盈（高点回撤 8%）
  · 时间：保守盘算 20 交易日 / 积极盘算 40 交易日未离场则收盘平
  · 基准：同窗买入持有，供 alpha 对照
铁律：只写 reports/ 下两个 json，LLM 与本模块都不碰真实账本。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
SIM = os.path.join(REPORTS, "trader_sim.json")
EVAL = os.path.join(REPORTS, "trader_sim_eval.json")
PROPOSALS = os.path.join(REPORTS, "council_proposals.jsonl")
# K88 预演账：预演票（council_sim 留痕）喂独立六账——同样的操盘规则，
# 评价「预演票如果照单执行会怎样」，与真实账零干扰（独立文件对）。
SIM_B = os.path.join(REPORTS, "trader_sim_simbooks.json")
EVAL_B = os.path.join(REPORTS, "trader_sim_simbooks_eval.json")
SIM_PROPOSALS = os.path.join(REPORTS, "council_sim_proposals.jsonl")

TRADERS = [                      # (操盘手, 对应角色key, 同向个性系数, 止损观点线)
    ("激进", "aggr", 1.2, "ML"),
    ("保守", "cons", 0.8, "RL"),
    ("中立", None, 1.0, None),
]
PLANS = {                       # 盘算：基数仓位 / TP 行为 / 追踪 / 最长持有
    "积极": {"base": 1.0, "half_at_tp": True, "trail": 0.92, "max_days": 40},
    "保守": {"base": 0.5, "half_at_tp": False, "trail": None, "max_days": 20},
}
BOOKS = ["%s|%s" % (t[0], p) for t in TRADERS for p in PLANS]
CAP = 1.0    # K71 账本总仓位上限：六本账各一个固定资金池（100%），
             # 持仓合计 size_eff ≤ CAP——仓位有限才有取舍，取舍才有决策

# K70 策略来源标签（与 stock_council.SID_DESC 同步；本地冗余避免
# import LLM 链——trader_sim 只依赖 datamart，保持轻量可独立跑）
SID_LABEL = {"qpzm": "青萍之末", "9dim": "九维评分", "sig4": "四策略信号",
             "chanlun": "缠论结构", "manual": "手动"}


def _sid_label(sid):
    return SID_LABEL.get(sid or "", sid or "—")

_BAR_CACHE = {}


def _bars(code):
    """个股全量日K（升序），datamart ts_daily 真源。"""
    if code in _BAR_CACHE:
        return _BAR_CACHE[code]
    import datamart as dm
    df = dm.read("daily", codes=[code])
    rows = sorted(df.to_dict("records"),
                  key=lambda r: str(r.get("trade_date") or ""))
    out = [{"d": str(r["trade_date"]), "o": float(r["open"]),
            "h": float(r["high"]), "l": float(r["low"]),
            "c": float(r["close"])}
           for r in rows if r.get("trade_date") and r.get("open")]
    _BAR_CACHE[code] = out
    return out


def _load_json(fp, default):
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic(fp, obj):
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)


def _mult_of(role_key, mult, rec):
    """K66 口径在操盘手层的落地：角色 stance 与净多空同向才放大/缩小。
    兼容两种结构：立案留痕（directive/views 原始）与 pending（展平）。"""
    if role_key is None:
        return 1.0
    net = rec.get("net")
    if net is None:
        net = (rec.get("directive") or {}).get("net_stance") or 0
    if not net:
        return 1.0
    st = rec.get(role_key)
    if st is None:
        st = ((rec.get("views") or {}).get(role_key) or {}).get("stance") or 0
    return mult if (net > 0 and st > 0) or (net < 0 and st < 0) else 1.0


def _used(book):
    return round(sum(p.get("size_eff", 0) for p in
                     (book.get("pos") or {}).values()), 2)


def _weakness(p):
    """持仓弱势分：浮亏越深越弱；同收益时时间窗消耗越多越弱。
    返回 (浮亏深度, 时间消耗)，越小越弱（min 取最弱）。"""
    bars = _bars(p["code"])
    c = bars[-1]["c"] if bars else p["entry"]
    ret = (c / p["entry"] - 1.0) * 100.0
    aged = (p.get("n_days") or 0) / float(p.get("max_days") or 1)
    return (ret, -aged)


def _admit(book, bk, trader, pk, t, b0, size_eff):
    """K71 建仓取舍：仓位固定（CAP），满仓时有取有舍——
    激进：挤掉最弱持仓换仓（浮亏最深，平手挤时间窗消耗最多者）；
    保守：不挤仓，放弃新票（等旧票自然退出）；
    中立：只挤「过半时间窗且浮亏」的持仓，否则放弃。
    返回 {"ok", "size_eff"(最终), "rec"(决策留痕，ok 时 victim 非空才有)}。"""
    free = round(CAP - _used(book), 2)
    if size_eff <= free:
        return {"ok": True, "size_eff": size_eff, "rec": None}
    pool = []
    if trader == "激进":
        pool = list((book.get("pos") or {}).items())
    elif trader == "中立":
        pool = [(k, p) for k, p in (book.get("pos") or {}).items()
                if (p.get("n_days") or 0) >= (p.get("max_days") or 1) / 2
                and _weakness(p)[0] < 0]
    if not pool:
        return {"ok": False, "size_eff": 0.0, "rec": {
            "d": b0["d"], "bk": bk, "action": "放弃",
            "new_pkey": pk, "new_code": t["code"], "new_name": t["name"],
            "new_open": b0["o"],
            "free": free, "size_eff": size_eff,
            "note": "仓位已满·%s" % ("无过半窗浮亏可挤" if trader == "中立"
                                    else "保守不挤仓")}}
    vk, vp = min(pool, key=lambda kv: _weakness(kv[1]))
    px = None                      # victim 让位价=建仓当日收盘
    for b in _bars(vp["code"]):
        if b["d"] == b0["d"]:
            px = b["c"]
            break
    px = vp["entry"] if px is None else px
    fr = vp.get("frac_rem", 1.0)
    ret = (px / vp["entry"] - 1.0) * 100.0
    book["trades"].append({
        "d": time.strftime("%Y-%m-%d %H:%M:%S"), "code": vp["code"],
        "name": vp["name"], "sid": vp["sid"], "plan": vp["plan"],
        "pkey": vk, "entry_d": vp.get("entry_d"), "entry": vp.get("entry"),
        "exit": px, "exit_d": b0["d"], "ret": round(ret, 2),
        "frac": fr, "contrib": round(ret * vp["size_eff"] * fr, 2),
        "reason": "换仓让位"})
    del book["pos"][vk]
    free2 = round(CAP - _used(book), 2)
    note = ""
    if size_eff > free2:           # 腾出后仍不够 → 缩仓开（保下限 0.2）
        note = "余仓压缩 %s→%s" % (size_eff, max(0.2, free2))
        size_eff = round(max(0.2, free2), 2)
    rec = {"d": b0["d"], "bk": bk, "action": "换仓",
           "new_pkey": pk, "new_code": t["code"], "new_name": t["name"],
           "new_open": b0["o"], "size_eff": size_eff,
           "victim_pkey": vk, "victim_code": vp["code"],
           "victim_name": vp["name"], "victim_exit": px,
           "victim_ret": round(ret, 2),
           "victim_md": vp.get("max_days"), "victim_nd": vp.get("n_days"),
           "free": free, "note": note}
    return {"ok": True, "size_eff": size_eff, "rec": rec}


def _hyp_ret(code, start_px, start_d, n_bars):
    """假想收益：start_px 起持有 n_bars 根日K 的收盘收益（决策观察用）。"""
    if not start_px or not n_bars or n_bars <= 0:
        return None
    bars = [b for b in _bars(code) if b["d"] > (start_d or "")]
    if not bars:
        return None
    end = bars[:n_bars][-1]
    return round((end["c"] / start_px - 1.0) * 100.0, 2)


def _stop_of(trader, line_key, rec, entry):
    """止损线：观点线（ML/RL）优先，无效则兜底纪律线。返回 (价, 口径)。"""
    p = rec.get("params") or {}
    try:
        line = float(p.get(line_key)) if line_key else None
    except (TypeError, ValueError):
        line = None
    if line and entry * 0.70 < line < entry * 0.98:      # 合理性窗
        return line, "观点线%s" % line_key
    fb = {"激进": 0.94, "保守": 0.92}.get(trader)
    if fb is None:                                       # 中立：桶纪律线
        fb = 0.96 if rec.get("style") == "激进" else 0.92
    return round(entry * fb, 3), "纪律线%+d%%" % round((fb - 1) * 100)


def _tp_of(rec, entry):
    try:
        tp = float((rec.get("params") or {}).get("TP"))
    except (TypeError, ValueError):
        return None
    return tp if entry * 1.05 < tp < entry * 3.0 else None


def _recs(fp=PROPOSALS):
    """立案留痕按 (sid,code) 后读覆盖取最新（与 _style_map 同口径）。
    K88：fp=SIM_PROPOSALS 时读预演票（council_sim 留痕）。"""
    out = {}
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("code"):
                    out["%s|%s" % (r.get("sid"), r.get("code"))] = r
    except OSError:
        pass
    return out


def _open_pos(book, sid, code):
    for pkey, p in (book.get("pos") or {}).items():
        if p.get("sid") == sid and p.get("code") == code:
            return pkey
    return None


def _trade(pos, d, px, reason, frac):
    ret = (px / pos["entry"] - 1.0) * 100.0
    return {"pkey": pos["pkey"], "sid": pos["sid"], "code": pos["code"],
            "name": pos["name"], "trader": pos["trader"], "plan": pos["plan"],
            "entry_d": pos["entry_d"], "exit_d": d,
            "entry": pos["entry"], "exit": round(px, 3),
            "frac": frac, "ret": round(ret, 2),
            "contrib": round(ret * pos["size_eff"] * frac, 2),
            "size_eff": pos["size_eff"], "reason": reason}


def _walk(pos, bars):
    """逐根日K执行买卖点。返回 (trades, closed)。"""
    trades = []
    pl = PLANS[pos["plan"]]
    for b in bars:
        # ① 止损：开盘跳空按开盘价，盘中按止损价（frac=余仓比例，
        #    TP 半出后只算剩余半仓，避免贡献重复计）
        fr = pos.get("frac_rem", 1.0)
        if b["o"] <= pos["stop"]:
            trades.append(_trade(pos, b["d"], b["o"], "跳空止损", fr))
            return trades, True
        if b["l"] <= pos["stop"]:
            trades.append(_trade(pos, b["d"], pos["stop"], "止损", fr))
            return trades, True
        # ② TP：保守盘算全出落袋；积极盘算出一半+保本+追踪
        tp = pos.get("tp")
        if tp and not pos.get("half_done"):
            px = b["o"] if b["o"] >= tp else (tp if b["h"] >= tp else None)
            if px is not None:
                if pl["half_at_tp"]:
                    trades.append(_trade(pos, b["d"], px, "TP半止盈", 0.5))
                    pos["half_done"] = True
                    pos["frac_rem"] = 0.5
                    pos["stop"] = max(pos["stop"], pos["entry"])
                    pos["hw"] = max(pos.get("hw") or px, b["h"])
                else:
                    trades.append(_trade(pos, b["d"], px, "TP落袋", 1.0))
                    return trades, True
        # ③ 追踪止盈（积极盘算 TP 半出后：高点回撤 8%）
        if pos.get("half_done") and pl.get("trail"):
            pos["hw"] = max(pos.get("hw") or b["h"], b["h"])
            pos["stop"] = max(pos["stop"], round(pos["hw"] * pl["trail"], 3))
        # ④ 时间平仓
        pos["n_days"] = pos.get("n_days", 0) + 1
        if pos["n_days"] >= pl["max_days"]:
            trades.append(_trade(pos, b["d"], b["c"], "时间平仓",
                                 pos.get("frac_rem", 1.0)))
            return trades, True
        pos["last_d"] = b["d"]
    return trades, False


def run(rebuild=False, preset=False):
    """增量推进六本账：新立案入 pending → 有日K即建仓 → 持仓逐日执行。
    rebuild=True 从留痕全量重放（影子层安全，只写两个 json）。
    K88 preset=True：预演票喂独立预演账（SIM_B/EVAL_B + 预演票源），
    与真实六账零干扰。"""
    sim_fp, ev_fp = (SIM_B, EVAL_B) if preset else (SIM, EVAL)
    recs = _recs(SIM_PROPOSALS if preset else PROPOSALS)
    sim = {"v": 1, "books": {}, "pending": {}, "skipped": {}} \
        if rebuild else _load_json(sim_fp, None)
    if not isinstance(sim, dict) or "books" not in sim:
        sim = {"v": 1, "books": {}, "pending": {}, "skipped": {}}
    sim.setdefault("books", {k: {"pos": {}, "trades": [],
                                 "decisions": []} for k in BOOKS})
    for k in BOOKS:
        sim["books"].setdefault(k, {"pos": {}, "trades": []})
        sim["books"][k].setdefault("decisions", [])
    sim.setdefault("pending", {})
    sim.setdefault("skipped", {})

    # ① 立案 → pending / skipped（反对票不接单；同票持仓中不重复开）
    for key, r in recs.items():
        pk = "%s|%s" % (key, r.get("d"))
        if pk in sim["pending"] or pk in sim["skipped"]:
            continue
        if any(pk in sim["books"][bk]["pos"] for bk in BOOKS) or any(
                tr.get("pkey") == pk for bk in BOOKS
                for tr in sim["books"][bk]["trades"]):
            continue
        if any(_open_pos(sim["books"][bk], r.get("sid"), r.get("code"))
               for bk in BOOKS):
            continue
        if (r.get("vote") or {}).get("verdict") == "反对":
            sim["skipped"][pk] = {"sid": r.get("sid"), "code": r.get("code"),
                                  "name": r.get("name"), "d": r.get("d"),
                                  "why": "投委会投票反对，操盘手不接单"}
            continue
        if not _bars(r.get("code")):
            sim["skipped"][pk] = {"sid": r.get("sid"), "code": r.get("code"),
                                  "name": r.get("name"), "d": r.get("d"),
                                  "why": "无日K数据（ts_daily 未覆盖）"}
            continue
        sim["pending"][pk] = {"sid": r.get("sid"), "code": r.get("code"),
                              "name": r.get("name"), "d": r.get("d"),
                              "k": ((r.get("directive") or {})
                                    .get("sizing") or {}).get("k") or 1.0,
                              "net": (r.get("directive") or {})
                              .get("net_stance") or 0,
                              "aggr": (((r.get("views") or {})
                                        .get("aggr") or {}).get("stance")
                                       or 0),
                              "cons": (((r.get("views") or {})
                                        .get("cons") or {}).get("stance")
                                       or 0),
                              "style": r.get("style"),
                              "params": r.get("params") or {}}

    # ② pending → 建仓（辩论日次一交易日开盘）
    for pk in list(sim["pending"]):
        t = sim["pending"][pk]
        bars = _bars(t["code"])
        nxt = [b for b in bars if b["d"] > (t["d"] or "").replace("-", "")]
        if not nxt:
            continue                       # 等日K（正常：数据未更新到建仓后）
        b0 = nxt[0]
        opened = []
        for trader, role_key, mult, line_key in TRADERS:
            for plan, pl in PLANS.items():
                bk = "%s|%s" % (trader, plan)
                book = sim["books"][bk]
                stop, stop_rule = _stop_of(trader, line_key, t, b0["o"])
                size_eff = pl["base"] * (t.get("k") or 1.0) * _mult_of(
                    role_key, mult, t)
                size_eff = round(max(0.2, min(1.0, size_eff)), 2)
                dec = _admit(book, bk, trader, pk, t, b0, size_eff)  # K71
                if dec["rec"]:
                    book["decisions"].append(dec["rec"])
                if not dec["ok"]:
                    print("[SIM] %s 放弃 %s %s（余仓 %.2f/%.2f·%s）"
                          % (bk, t["code"], t["name"], dec["rec"]["free"],
                             size_eff, dec["rec"]["note"]))
                    continue
                size_eff = dec["size_eff"]
                book["pos"][pk] = {
                    "pkey": pk, "sid": t["sid"], "code": t["code"],
                    "name": t["name"], "trader": trader, "plan": plan,
                    "entry_d": b0["d"], "entry": b0["o"],
                    "size_eff": size_eff,
                    "stop": stop, "stop_rule": stop_rule,
                    "tp": _tp_of(t, b0["o"]),
                    "max_days": pl["max_days"], "n_days": 0,
                    "hw": b0["h"], "half_done": False, "frac_rem": 1.0,
                    "last_d": b0["d"]}
                opened.append(bk)
        del sim["pending"][pk]
        print("[SIM] 开仓 %s %s @%s（%s）"
              % (t["code"], t["name"], b0["o"],
                 "、".join(opened) if opened else "全部账本放弃"))

    # ③ 持仓逐日执行（幂等：last_d 之后的新K线才处理）
    for bk in BOOKS:
        book = sim["books"][bk]
        for pk in list(book["pos"]):
            pos = book["pos"][pk]
            bars = [b for b in _bars(pos["code"])
                    if b["d"] > (pos.get("last_d") or "")]
            trades, closed = _walk(pos, bars)
            book["trades"].extend(trades)
            for tr in trades:
                print("[SIM] %s %s %s|%s %s @%s（%+.1f%%）"
                      % (bk, pos["code"], pos["name"], pos["plan"],
                         tr["reason"], tr["exit"], tr["ret"]))
            if closed:
                del book["pos"][pk]

    sim["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic(sim_fp, sim)
    ev = evaluate(sim, preset=preset)
    _atomic(ev_fp, ev)
    print("[SIM%s] books=%d pending=%d skipped=%d trades=%d → %s"
          % ("-预演" if preset else "", len(BOOKS),
             len(sim["pending"]), len(sim["skipped"]),
             sum(len(sim["books"][b]["trades"]) for b in BOOKS),
             os.path.basename(ev_fp)))
    return sim


def evaluate(sim, preset=False):
    """战绩榜：每立案票 × 六本账收益率 + 买入持有基准 + 汇总。
    K88 preset=True：预演账口径（附 vs_real 对照）。"""
    rows = {}
    for bk in BOOKS:
        book = sim["books"].get(bk) or {}
        for tr in book.get("trades") or []:
            r = rows.setdefault(
                tr["pkey"],
                {"pkey": tr["pkey"], "sid": tr["sid"], "code": tr["code"],
                 "name": tr["name"], "entry_d": tr["entry_d"],
                 "entry": tr["entry"], "books": {}, "status": "已平"})
            agg = r["books"].get(bk) or {"ret": 0.0, "contrib": 0.0,
                                         "reason": [], "exit_d": ""}
            agg["ret"] = round(agg["ret"]
                               + tr["ret"] * tr["frac"], 2)
            agg["contrib"] = round(agg["contrib"] + tr["contrib"], 2)
            agg["reason"].append(tr["reason"])
            agg["exit_d"] = tr["exit_d"]
            r["books"][bk] = agg
        for pk, p in (book.get("pos") or {}).items():
            bars = _bars(p["code"])
            if not bars:
                continue
            last = bars[-1]
            unreal = (last["c"] / p["entry"] - 1.0) * 100.0
            r = rows.setdefault(
                pk, {"pkey": pk, "sid": p["sid"], "code": p["code"],
                     "name": p["name"], "entry_d": p["entry_d"],
                     "entry": p["entry"], "books": {}, "status": "持仓中"})
            r["books"][bk] = {
                "ret": round(unreal * p.get("frac_rem", 1.0), 2),
                "contrib": round(unreal * p["size_eff"]
                                 * p.get("frac_rem", 1.0), 2),
                "reason": ["持仓中"], "exit_d": last["d"],
                "size_eff": p["size_eff"], "stop": p["stop"],
                "tp": p.get("tp")}
            r["status"] = "持仓中"
    # 基准：买入持有到各账最晚退出日（或最新）收盘
    for r in rows.values():
        bars = _bars(r["code"])
        ds = [b["d"] for b in bars if b["d"] >= r["entry_d"]]
        if not ds or not bars:
            continue
        last_d = max([b["exit_d"] for b in r["books"].values()]
                     + [ds[0]])
        ref = next((b["c"] for b in reversed(bars) if b["d"] <= last_d),
                   bars[-1]["c"])
        r["baseline"] = round((ref / r["entry"] - 1.0) * 100.0, 2)
        r["best"] = max(r["books"].items(),
                        key=lambda kv: kv[1]["contrib"])[0] \
            if r["books"] else ""
    aggs = {}
    for bk in BOOKS:
        rs = [r["books"][bk] for r in rows.values() if bk in r["books"]]
        closed = [x for x in rs if "持仓中" not in (x.get("reason") or [])]
        wins = [x for x in rs if x["contrib"] > 0]
        alpha = [x["contrib"] - r["baseline"] for r in rows.values()
                 if bk in r["books"] for x in [r["books"][bk]]
                 if r.get("baseline") is not None]
        aggs[bk] = {"n": len(rs), "closed": len(closed),
                    "win": len(wins),
                    "winrate": round(100.0 * len(wins) / len(rs), 1)
                    if rs else 0.0,
                    "avg_contrib": round(sum(x["contrib"] for x in rs)
                                         / len(rs), 2) if rs else 0.0,
                    "avg_alpha": round(sum(alpha) / len(alpha), 2)
                    if alpha else 0.0}
    sids = {}
    for r in rows.values():
        s = sids.setdefault(r["sid"], {"n": 0, "label": _sid_label(r["sid"])})
        s["n"] += 1
        for bk, x in r["books"].items():
            s.setdefault(bk, 0.0)
            s[bk] = round((s.get(bk) or 0) + x["contrib"], 2)
    # K71 取舍决策观察：换仓/放弃 事后对错（假想对照），供进化取数
    decs = []
    for bk in BOOKS:
        for dc in (sim["books"].get(bk) or {}).get("decisions") or []:
            x = dict(dc)
            x["bk"] = bk
            rrow = rows.get(dc.get("new_pkey")) or {}
            new_ret = ((rrow.get("books") or {}).get(bk) or {}).get("ret")
            if dc.get("action") == "换仓":
                vh = _hyp_ret(dc.get("victim_code"), dc.get("victim_exit"),
                              dc.get("d"), (dc.get("victim_md") or 20)
                              - (dc.get("victim_nd") or 0))
                x["victim_hyp"] = vh
                x["new_ret"] = new_ret
                x["correct"] = (None not in (vh, new_ret)
                                and new_ret > vh)
            else:                       # 放弃：假想按本账规则满仓持有
                nh = _hyp_ret(dc.get("new_code"), dc.get("new_open"),
                              dc.get("d"),
                              PLANS.get(bk.split("|")[1],
                                        {}).get("max_days") or 20)
                x["new_hyp"] = nh
                x["correct"] = (nh is not None and nh <= 0)
            decs.append(x)
    dagg = {}
    for x in decs:
        a = dagg.setdefault(x["bk"], {"换仓": [0, 0], "放弃": [0, 0]})
        if x.get("correct") is not None:
            a[x["action"]][0] += 1
            a[x["action"]][1] += 1 if x["correct"] else 0
    tips = []                            # 进化方向提示（≥5 样本才评）
    for bk in sorted(dagg):
        a = dagg[bk]
        if a["换仓"][0] >= 5 and a["换仓"][1] * 2 < a["换仓"][0]:
            tips.append("%s 换仓正确率 %.0f%%——挤仓标准可收紧"
                        % (bk, 100.0 * a["换仓"][1] / a["换仓"][0]))
        if a["放弃"][0] >= 5 and a["放弃"][1] * 2 < a["放弃"][0]:
            tips.append("%s 放弃正确率 %.0f%%——错过好票，可放宽挤仓门槛"
                        "或降单票下限"
                        % (bk, 100.0 * a["放弃"][1] / a["放弃"][0]))
    decisions = {"rows": sorted(decs, key=lambda z: z.get("d") or "",
                                reverse=True),
                 "agg": {bk: {"swap_n": v["换仓"][0],
                              "swap_ok": v["换仓"][1],
                              "skip_n": v["放弃"][0],
                              "skip_ok": v["放弃"][1]}
                         for bk, v in dagg.items()},
                 "tips": tips}
    for sid, s in sids.items():               # K70 策略来源 × 操盘手汇总
        bks = {bk: v for bk, v in s.items() if bk in BOOKS}
        s["best_bk"] = max(bks, key=lambda k: bks[k]) if bks else ""
        s["total"] = round(sum(bks.values()), 2)
    out_rows = [dict(r, sid_label=_sid_label(r.get("sid")))
                for r in rows.values()]
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "rows": out_rows, "books": aggs, "sids": sids,
           "decisions": decisions,
           "pending": sim.get("pending") or {},
           "skipped": sim.get("skipped") or {}}
    if preset:                       # K88 预演质量对照（用钱衡量预演价值）
        out["preset"] = True
        out["vs_real"] = _vs_real(out)
    return out


def _verdict_map(fp):
    """(sid,code) → 裁决（支持/中性/反对），票源后读覆盖取最新。"""
    return {(r.get("sid"), r.get("code")):
            (r.get("vote") or {}).get("verdict")
            for r in _recs(fp).values()}


def _vs_real(ev_sim):
    """K88 预演账 vs 真实账对照：同票同账贡献差 + 接单差集 +
    裁决一致率。预演票建仓日通常早一天，贡献差=「预演领先一步」的
    真金白银度量；一致率低=预演辩论与真会漂移大，喂账成绩仅供参考。"""
    real = _load_json(EVAL, {})
    rr = {(r.get("sid"), r.get("code")): r for r in real.get("rows") or []}
    sr = {(r.get("sid"), r.get("code")): r for r in ev_sim.get("rows") or []}
    both = [k for k in sr if k in rr]
    books = {}
    for bk in BOOKS:
        ds = []
        for k in both:
            a = (sr[k].get("books") or {}).get(bk)
            b = (rr[k].get("books") or {}).get(bk)
            if a and b:
                ds.append((a["contrib"] - b["contrib"],
                           a["contrib"], b["contrib"]))
        books[bk] = {
            "n": len(ds),
            "sim": round(sum(d[1] for d in ds) / len(ds), 2) if ds else 0.0,
            "real": round(sum(d[2] for d in ds) / len(ds), 2) if ds else 0.0,
            "diff": round(sum(d[0] for d in ds) / len(ds), 2) if ds else 0.0}
    sv, rv = _verdict_map(SIM_PROPOSALS), _verdict_map(PROPOSALS)
    common = [k for k in sv if k in rv and sv[k] and rv[k]]
    agree = sum(1 for k in common if sv[k] == rv[k])
    return {"n_sim": len(sr), "n_real": len(rr), "n_both": len(both),
            "sim_only": [sr[k].get("name") or k[1]
                         for k in sr if k not in rr][:10],
            "real_only": [rr[k].get("name") or k[1]
                          for k in rr if k not in sr][:10],
            "books": books,
            "verdict": {"n": len(common), "agree": agree,
                        "pct": round(100.0 * agree / len(common), 1)
                        if common else 0.0}}


if __name__ == "__main__":
    import argparse
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="K68 三操盘手模拟锦标赛")
    ap.add_argument("--rebuild", action="store_true",
                    help="从立案留痕全量重放")
    ap.add_argument("--preset", action="store_true",
                    help="K88 预演账：预演票喂独立六账（council_sim 留痕）")
    a = ap.parse_args()
    run(rebuild=a.rebuild, preset=a.preset)
