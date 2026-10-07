# -*- coding: utf-8 -*-
"""缠论工作台 P4 验证层 · 三类买卖点事件统计与策略回测（纸面验证）。

口径说明（查看器页面同步标注，简化口径如实声明）：
· 事件统计：每个信号以理论价（信号点价格）为基准，统计其后 5/10/20/60
  根 K 线收盘价的方向收益（买点=涨为胜，卖点=跌为胜）；窗口数据不足记
  空值不参与统计。
· 策略回测：买点开多 / 卖点平仓（空仓遇卖点忽略、持仓遇买点忽略），
  理论成交价 ±（手续费+滑点）bps；期末仍持仓按最后收盘价扣成本强平并
  标记 open_pos。逐笔累计净值口径粗粒度，仅供信号质量参考。
· 防未来函数声明：信号确认依赖后续笔形成，理论价非可成交价，全部结果
  仅作纸面信号质量验证，不构成实盘依据。

run_backtest(bars, signals, cost_bps=3, slip_bps=1) 返回：
  "events":  [{"type","dt","price","r5","r10","r20","r60"}] 方向收益(小数)
  "by_type": {"1买": {"n","win","avg"}, ...} 20 根窗口按类型聚合（%）
  "stats":   {"trades","wins","win_rate","total_return","max_dd",
              "buy_hold","open_pos"}

eval_samples(rows) 样本库可成交口径复核（样本统计面板数据源）：截断
  重放找信号最早可检出时点 lag，以检测窗末根收盘为可成交基准重算
  r20/r60，并对照理论价口径——理论价 win 偏高系确认滞后未来函数假象。
"""
from chanlun_signal import analyze as _az
from chanlun_flow import aligned as _reg_aligned
from chanlun_composite import judge_all as _comp_judge_all, \
    RULES as _COMP_RULES


def _dir_ret(sig_type, base, later):
    """方向收益：买点=涨为正，卖点=跌为正。"""
    try:
        move = later / base - 1.0
    except (TypeError, ZeroDivisionError):
        return None
    return move if sig_type.endswith("买") else -move


def run_backtest(bars, signals, cost_bps=3, slip_bps=1):
    bars = bars or []
    signals = signals or []
    n = len(bars)
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    one_way = (cost_bps + slip_bps) / 10000.0

    # ── 事件统计 ─────────────────────────────────────────
    events = []
    for s in signals:
        i0 = idx.get(s["dt"])
        if i0 is None:
            continue
        ev = {"type": s["type"], "dt": s["dt"], "price": s["price"]}
        for tag, w in (("r5", 5), ("r10", 10), ("r20", 20), ("r60", 60)):
            j = i0 + w
            ev[tag] = (round(_dir_ret(s["type"], s["price"],
                                      bars[j]["c"]), 4)
                       if j < n and s["price"] else None)
        events.append(ev)
    agg = {}
    for ev in events:
        v = ev["r20"]
        if v is None:
            continue
        a = agg.setdefault(ev["type"], {"n": 0, "win": 0, "sum": 0.0})
        a["n"] += 1
        a["win"] += 1 if v > 0 else 0
        a["sum"] += v
    by_type = {k: {"n": a["n"], "win": round(a["win"] * 100 / a["n"]),
                   "avg": round(a["sum"] * 100 / a["n"], 2)}
               for k, a in agg.items()}

    # ── 策略回测：买点开 / 卖点平 ─────────────────────────
    trades = []
    pos = None
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for s in signals:
        if pos is None:
            if s["type"].endswith("买") and s["price"]:
                pos = {"buy_dt": s["dt"],
                       "buy_px": s["price"] * (1 + one_way)}
        elif s["type"].endswith("卖") and s["price"]:
            sell_px = s["price"] * (1 - one_way)
            ret = sell_px / pos["buy_px"] - 1.0
            trades.append({"buy_dt": pos["buy_dt"], "sell_dt": s["dt"],
                           "ret": round(ret * 100, 2), "open": False})
            nav *= (1 + ret)
            peak = max(peak, nav)
            max_dd = max(max_dd, 1 - nav / peak)
            pos = None
    if pos is not None and n:
        last = bars[-1]["c"] * (1 - one_way)
        ret = last / pos["buy_px"] - 1.0
        trades.append({"buy_dt": pos["buy_dt"], "sell_dt": None,
                       "ret": round(ret * 100, 2), "open": True})
        nav *= (1 + ret)
        peak = max(peak, nav)
        max_dd = max(max_dd, 1 - nav / peak)
        pos = None
    wins = sum(1 for t in trades if t["ret"] > 0)
    stats = {
        "trades": len(trades),
        "wins": wins,
        "win_rate": round(wins * 100 / len(trades), 1) if trades else None,
        "total_return": round((nav - 1) * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "buy_hold": round((bars[-1]["c"] / bars[0]["c"] - 1) * 100, 2)
        if n and bars[0]["c"] else None,
        "open_pos": pos is not None,
    }
    return {"events": events, "by_type": by_type, "stats": stats,
            "trade_list": trades}


def analyze(bars):
    """P4 入口：原始升序 K 线 → 信号层结果 + 回测验证。"""
    st = _az(bars or [])
    st.update(run_backtest(bars or [], st["signals"]))
    return st


TYPES = ("1买", "2买", "3买", "1卖", "2卖", "3卖")
PASS_N = 10          # 达标线：可成交口径 n≥10 且方向均值不反转


def _med(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return v[len(v) // 2] if len(v) % 2 else \
        (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2.0


def _pct_stats(vals):
    """方向收益小数列表 → n/win%/avg%/med%（样本不足返回 None）。"""
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return {"n": len(v),
            "win": round(sum(1 for x in v if x > 0) * 100.0 / len(v)),
            "avg": round(sum(v) * 100.0 / len(v), 2),
            "med": round(_med(v) * 100.0, 2)}


def eval_samples(rows, max_lag=60):
    """样本库可成交口径复核（样本统计面板数据源，约每样本 ≤60 次重放）。

    方法：对每条样本按 m=1..max_lag 逐根截短 K 线重放 analyze，找信号
    (type, dt) 最早可检出的时点 lag——检测窗末根收盘即真实可成交基准；
    以该基准重算 r20/r60（同 _dir_ret 方向收益口径），并统计确认时相对
    理论价的偏移。返回 {"types", "n_rows", "n_symbols", "skipped"}，
    每类型含 detect（滞后）/slip（确认偏离）/theo（库内理论价口径）/
    exe、exe60（可成交口径）与 pass（n≥PASS_N 且 exe 均值不反转）。
    """
    from chanlun_data import load_kline
    rows = rows or []
    by_mc = {}
    for r in rows:
        by_mc.setdefault((r.get("market"), r.get("code")), []).append(r)
    types = {}
    skipped = 0
    for (mkt, code), rs in sorted(by_mc.items()):
        kl = load_kline(code, "1d", 500)
        if not kl.get("ok"):
            skipped += len(rs)
            continue
        bars = kl["bars"]
        n = len(bars)
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        for r in rs:
            i0 = idx.get(r.get("dt"))
            if i0 is None:
                skipped += 1
                continue
            t = r["type"]
            a = types.setdefault(t, {"n": 0, "lags": [], "slips": [],
                                     "theo": [], "exe": [], "exe60": [],
                                     "flt_n": 0, "flt_exe": [],
                                     "flt_exe60": [],
                                     "flt2_n": 0, "flt2_exe": [],
                                     "flt2_exe60": [],
                                     "flt3_n": 0, "flt3_exe": [],
                                     "flt3_exe60": [],
                                     "flt4_n": 0, "flt4_cov": 0,
                                     "flt4_exe": [], "flt4_exe60": [],
                                     "flt5_n": 0, "flt5_cov": 0,
                                     "flt5_exe": [], "flt5_exe60": [],
                                     "flt6_n": 0, "flt6_cov": 0,
                                     "flt6_exe": [], "flt6_exe60": [],
                                     "flt7_n": 0, "flt7_cov": 0,
                                     "flt7_exe": [], "flt7_exe60": [],
                                     "flt8_n": 0, "flt8_cov": 0,
                                     "flt8_exe": [], "flt8_exe60": [],
                                     "comp": {}})
            a["n"] += 1
            if r.get("mt") is not None:
                a["flt4_cov"] += 1       # 次级别数据覆盖、可判共振的样本
            if r.get("reg") is not None:
                a["flt6_cov"] += 1       # 大盘指数可判（CN/CRYPTO）
            # 注：flt8_cov 不在此处统计——P9 reg_trend cov 与 exe/n 同在
            # replay 成功后统计（见下方），避免双重计数
            if r.get("r20") is not None:
                a["theo"].append(r["r20"])
            lag = None
            hit = None
            for m in range(1, min(max_lag, max(1, n - 1 - i0)) + 1):
                st = analyze(bars[:i0 + 1 + m])
                hs = [s for s in st["signals"]
                      if s["type"] == t and s["dt"] == r.get("dt")]
                if hs:
                    lag, hit = m, hs[0]
                    break
            if lag is None:
                continue          # 重放检不出：在线/离线不一致，不入可成交统计
            a["lags"].append(lag)
            ei = i0 + lag
            px = bars[ei]["c"]
            if px and r.get("price"):
                a["slips"].append(px / r["price"] - 1.0)
            if px:
                if ei + 20 < n:
                    a["exe"].append(_dir_ret(t, px, bars[ei + 20]["c"]))
                if ei + 60 < n:
                    a["exe60"].append(_dir_ret(t, px, bars[ei + 60]["c"]))
                # P5：盘整背驰标注为 True 的子集（仅 3买/3卖）
                if t in ("3买", "3卖") and hit.get("bei") is True:
                    a["flt_n"] += 1
                    if ei + 20 < n:
                        a["flt_exe"].append(
                            _dir_ret(t, px, bars[ei + 20]["c"]))
                    if ei + 60 < n:
                        a["flt_exe60"].append(
                            _dir_ret(t, px, bars[ei + 60]["c"]))
                # P6-b：正宗盘整背驰（进入笔 vs 离开笔）标注为 True 的子集
                if t in ("3买", "3卖") and hit.get("bei2") is True:
                    a["flt2_n"] += 1
                    if ei + 20 < n:
                        a["flt2_exe"].append(
                            _dir_ret(t, px, bars[ei + 20]["c"]))
                    if ei + 60 < n:
                        a["flt2_exe60"].append(
                            _dir_ret(t, px, bars[ei + 60]["c"]))
                # P6-a：MACD 背驰标注为 True 的子集（仅 1买/1卖）
                if t in ("1买", "1卖") and hit.get("mbei") is True:
                    a["flt3_n"] += 1
                    if ei + 20 < n:
                        a["flt3_exe"].append(
                            _dir_ret(t, px, bars[ei + 20]["c"]))
                    if ei + 60 < n:
                        a["flt3_exe60"].append(
                            _dir_ret(t, px, bars[ei + 60]["c"]))
                # P7：样本行存储的 60m 次级别共振为 True 的子集
                if r.get("mt") is True:
                    a["flt4_n"] += 1
                    if ei + 20 < n:
                        a["flt4_exe"].append(
                            _dir_ret(t, px, bars[ei + 20]["c"]))
                    if ei + 60 < n:
                        a["flt4_exe60"].append(
                            _dir_ret(t, px, bars[ei + 60]["c"]))
                # P8-a：量能缩量配合（重放命中直接取 vv，全类型）
                if hit.get("vv") is not None:
                    a["flt5_cov"] += 1
                    if hit.get("vv") is True:
                        a["flt5_n"] += 1
                        if ei + 20 < n:
                            a["flt5_exe"].append(
                                _dir_ret(t, px, bars[ei + 20]["c"]))
                        if ei + 60 < n:
                            a["flt5_exe60"].append(
                                _dir_ret(t, px, bars[ei + 60]["c"]))
                # P8-b：大盘顺风（买 up / 卖 down；行内 reg，CN/CRYPTO）
                if _reg_aligned(r.get("reg"), t) is True:
                    a["flt6_n"] += 1
                    if ei + 20 < n:
                        a["flt6_exe"].append(
                            _dir_ret(t, px, bars[ei + 20]["c"]))
                    if ei + 60 < n:
                        a["flt6_exe60"].append(
                            _dir_ret(t, px, bars[ei + 60]["c"]))
                # P6-c：完整版趋势背驰（双中枢+末段衰减；重放命中取 tbe）
                if hit.get("tbe") is not None:
                    a["flt7_cov"] += 1
                    if hit.get("tbe") is True:
                        a["flt7_n"] += 1
                        if ei + 20 < n:
                            a["flt7_exe"].append(
                                _dir_ret(t, px, bars[ei + 20]["c"]))
                        if ei + 60 < n:
                            a["flt7_exe60"].append(
                                _dir_ret(t, px, bars[ei + 60]["c"]))
                # P9：长周期趋势 regime（close>MA200=True；行内 reg_trend，
                # 全类型——牛市 regime 对买卖信号均视为有利，不区分方向）
                if r.get("reg_trend") is not None:
                    a["flt8_cov"] += 1
                    if r.get("reg_trend") is True:
                        a["flt8_n"] += 1
                        if ei + 20 < n:
                            a["flt8_exe"].append(
                                _dir_ret(t, px, bars[ei + 20]["c"]))
                        if ei + 60 < n:
                            a["flt8_exe60"].append(
                                _dir_ret(t, px, bars[ei + 60]["c"]))
                # P9 复合叠加：全部因子同时成立才算；任一不可判（None）
                # 整条记 None 不计 cov，绝不按 ✗ 处理（零额外重放成本）
                for cid, cv in _comp_judge_all(hit, r, t).items():
                    if cv is None:
                        continue
                    c = a["comp"].setdefault(
                        cid, {"cov": 0, "n": 0, "exe": [], "exe60": []})
                    c["cov"] += 1
                    if cv is True:
                        c["n"] += 1
                        if ei + 20 < n:
                            c["exe"].append(
                                _dir_ret(t, px, bars[ei + 20]["c"]))
                        if ei + 60 < n:
                            c["exe60"].append(
                                _dir_ret(t, px, bars[ei + 60]["c"]))
    out = {}
    for t in TYPES:
        if t not in types:
            continue
        a = types[t]
        lags = a["lags"]
        slips = a["slips"]
        ex = _pct_stats(a["exe"])
        out[t] = {
            "n": a["n"],
            "detect": {"n": len(lags),
                       "lag_med": _med(lags),
                       "lag_max": max(lags) if lags else None,
                       "lag_gt20": sum(1 for x in lags if x > 20)},
            "slip": ({"avg": round(sum(slips) * 100.0 / len(slips), 2),
                      "med": round(_med(slips) * 100.0, 2)} if slips else None),
            "theo": _pct_stats(a["theo"]),
            "exe": ex,
            "exe60": _pct_stats(a["exe60"]),
            "pass": bool(ex and ex["n"] >= PASS_N and ex["avg"] > 0),
        }
        if t in ("3买", "3卖"):
            # P5 盘整背驰影子子集（不拦截；与全集对比验证过滤有效性）
            out[t]["flt"] = {
                "bei_n": a["flt_n"], "detect_n": len(lags),
                "exe": _pct_stats(a["flt_exe"]),
                "exe60": _pct_stats(a["flt_exe60"])}
            # P6-b 正宗盘整背驰（进入 vs 离开）影子子集
            out[t]["flt2"] = {
                "bei_n": a["flt2_n"], "detect_n": len(lags),
                "exe": _pct_stats(a["flt2_exe"]),
                "exe60": _pct_stats(a["flt2_exe60"])}
        if t in ("1买", "1卖"):
            # P6-a MACD 背驰影子子集（不拦截；与全集对比）
            out[t]["flt3"] = {
                "bei_n": a["flt3_n"], "detect_n": len(lags),
                "exe": _pct_stats(a["flt3_exe"]),
                "exe60": _pct_stats(a["flt3_exe60"])}
        # P7 次级别共振子集（cov=60m 数据覆盖窗口可判的样本数；
        # 其余为缺失 None，绝不按无共振处理）
        out[t]["flt4"] = {
            "bei_n": a["flt4_n"], "cov_n": a["flt4_cov"],
            "n": a["n"],
            "exe": _pct_stats(a["flt4_exe"]),
            "exe60": _pct_stats(a["flt4_exe60"])}
        # P8-a 量能缩量子集（cov=重放命中且有量数据；全类型）
        out[t]["flt5"] = {
            "bei_n": a["flt5_n"], "cov_n": a["flt5_cov"],
            "exe": _pct_stats(a["flt5_exe"]),
            "exe60": _pct_stats(a["flt5_exe60"])}
        # P8-b 大盘顺风气集（cov=有指数环境判定的样本；US/HK 为 None）
        out[t]["flt6"] = {
            "bei_n": a["flt6_n"], "cov_n": a["flt6_cov"],
            "exe": _pct_stats(a["flt6_exe"]),
            "exe60": _pct_stats(a["flt6_exe60"])}
        # P6-c 趋势背驰完整版（cov=重放命中且信号前≥2中枢可判；仅1类）
        out[t]["flt7"] = {
            "bei_n": a["flt7_n"], "cov_n": a["flt7_cov"],
            "exe": _pct_stats(a["flt7_exe"]),
            "exe60": _pct_stats(a["flt7_exe60"])}
        # P9 长周期趋势 regime（cov=MA200 可判；全类型；close>MA200=True）
        out[t]["flt8"] = {
            "bei_n": a["flt8_n"], "cov_n": a["flt8_cov"],
            "exe": _pct_stats(a["flt8_exe"]),
            "exe60": _pct_stats(a["flt8_exe60"])}
        # P9 复合叠加（按 RULES 顺序输出，cov=0 规则保留供页面灰显）
        comp_out = []
        for rl in _COMP_RULES:
            if t not in rl["types"]:
                continue
            c = a["comp"].get(rl["id"]) or \
                {"cov": 0, "n": 0, "exe": [], "exe60": []}
            comp_out.append({
                "id": rl["id"], "name": rl["name"],
                "factors": list(rl["factors"]),
                "cov": c["cov"], "n": c["n"],
                "exe": _pct_stats(c["exe"]),
                "exe60": _pct_stats(c["exe60"])})
        out[t]["comp"] = comp_out
    return {"types": out, "n_rows": len(rows),
            "n_symbols": len(by_mc), "skipped": skipped}
