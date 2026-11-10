# -*- coding: utf-8 -*-
"""选股屏 K 线数据族（A5 拆分首批，2026-10-31 关羽 P129）。

纯移动自 api_screen.py（函数体零改动）：
_kline_qy / _kline_data / _ts_daily_prepend

依赖：api_common.STATION/log_exc；api_backtest._bt_resolve/_bt_jev_news。
api_screen.py 保留 `from api_screen_kline import ...` 兼容旧调用点。
"""
import logging
import os
import re
import sys

from api_common import STATION, log_exc
from api_backtest import _bt_jev_news, _bt_resolve

# JEV conf 已弃用（反相关 AUC 0.414）：K 线新闻同日去重改用事件类型分层
if os.path.join(STATION, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(STATION, "tools"))
from jev_signal import event_type_tier


def _kline_qy(c):
    """个股季/年线 JSON（K200：K147-F 季/年线级别落地）。

    深历史：datamart daily 别名 = ts_daily（xtcloud 2023-07 起）+
    tushare 深历史 daily（by_date，1990 起，freshness 20230728）；
    按请求区间降级——end≤20230730 命中深表（实测单票 0.6s/2925 行），
    start≥20230731 命中 ts_daily。两段拼接 (code,date) 去重。
    服务端聚合季/年 OHLCV + MA + chanlun_bi 结构；仅 A 股（深源为
    tushare CN）。懒加载独立端点，默认 /api/kline 零改动零回归。"""
    e = _bt_resolve(c)
    if not e:
        return {"ok": False, "err": "未识别「%s」" % c}
    code = e.get("code", "")
    m6 = re.search(r"(\d{6})", code or "")
    if not m6:
        return {"ok": False, "err": "季/年线仅支持 A 股（深历史源为 CN）"}
    d6 = m6.group(1)
    ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
               if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return {"ok": False, "err": "季/年线仅支持 A 股（北交所 %s 无深历史"
                                     "确认）" % d6}
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as _dm
        d1 = _dm.read("daily", codes=[ts_code], start="19900101",
                      end="20230730")
        d2 = _dm.read("daily", codes=[ts_code], start="20230731")
    except Exception as ex:
        return {"ok": False, "err": "深历史读取失败 %s" % str(ex)[:80]}
    import pandas as _pd
    df = _pd.concat([x for x in (d1, d2) if x is not None and len(x)] ,
                    ignore_index=True)
    if df is None or not len(df):
        return {"ok": False, "err": "%s 无日线深历史" % ts_code}
    df = df.drop_duplicates("trade_date").sort_values("trade_date") \
        .reset_index(drop=True)
    if "close" not in df.columns or len(df) < 12:
        return {"ok": False, "err": "深历史不足（%d 根）" % len(df)}
    td = df["trade_date"].astype(str)
    dts = td.str.slice(0, 4) + "-" + td.str.slice(4, 6) + "-" + \
        td.str.slice(6, 8)

    def _agg(freq):
        if freq == "q":
            key = dts.str.slice(0, 4) + "Q" + (
                (dts.str.slice(5, 7).astype(int) - 1) // 3 + 1).astype(str)
        else:
            key = dts.str.slice(0, 4)
        g = df.assign(_k=key).groupby("_k", sort=True)
        out = []
        for k, x in g:
            out.append({"d": dts.iloc[x.index[-1]],
                        "w0": dts.iloc[x.index[0]],
                        "o": round(float(x["open"].iloc[0]), 2),
                        "h": round(float(x["high"].max()), 2),
                        "l": round(float(x["low"].min()), 2),
                        "c": round(float(x["close"].iloc[-1]), 2),
                        "v": float(x["volume"].sum()) if "volume"
                        in x.columns else 0.0})
        for kk in (5, 20, 60, 250):
            vals = [b["c"] for b in out]
            for i, b in enumerate(out):
                b["m%d" % kk] = (round(sum(vals[i - kk + 1:i + 1]) / kk, 3)
                                 if i >= kk - 1 else None)
        from chanlun_bi import analyze as _clq
        cl = _clq([{"dt": b["d"], "h": b["h"], "l": b["l"]} for b in out],
                  [b["c"] for b in out])
        bis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["bis"]]
        pivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                   "zd": p["zd"], "n": p.get("n_bi", 0)}
                  for p in cl["pivots"]]
        fxl = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cl["fractals"]]
        sgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "nb": b.get("n_bi", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["segments"]]
        sigs = [{"d": g2["d"], "p": g2["p"], "t": g2["t"],
                 "sub": g2.get("sub", "")} for g2 in cl["signals"]]
        return {"bars": out, "bis": bis, "pivots": pivots, "fx": fxl,
                "sgs": sgs, "sigs": sigs, "marks": [], "news": {}}

    try:
        q = _agg("q")
        y = _agg("y")
    except Exception as ex:
        return {"ok": False, "err": "季/年结构计算失败 %s" % str(ex)[:80]}
    return {"ok": True, "name": e["name"], "code": code,
            "q": q, "y": y,
            "deep_span": "%s..%s" % (dts.iloc[0], dts.iloc[-1]),
            "n_deep": int(len(df))}


def _kline_data(q, n=250):
    """个股 K 线（S1）：四市场日线尾部 n 根 + MA5/20/60/250 + A股图谱结构
    信号标注 + JEV 新闻 bias 标注。resolve → load_bars，纯查询。"""
    n = max(30, min(500, int(n or 250)))
    e = _bt_resolve(q)
    if not e:
        return {"ok": False, "err": "未识别「%s」" % q}
    name, code = e["name"], e.get("code", "")
    aliases = e.get("aliases") or []
    # A股 ts_code（结构信号标注与 MA 历史兜底共用）
    import re as _re_k
    m6 = _re_k.search(r"(\d{6})", code or "")
    ts_code = ""
    if m6:
        d6 = m6.group(1)
        ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
                   if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    bars = []
    try:
        from datetime import datetime as _dt, timedelta as _td
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import runner as _R
        import data_loader as _D
        # MA250 需视图外 ≥250 根历史：窗口 ≈ 视图 1.6 倍 + 420 自然日
        start = (_dt.now() - _td(days=int(n * 1.6) + 420)
                 ).strftime("%Y-%m-%d")
        for mk, sy, _nm in _R.resolve_watchlist(
                [{"name": name, "code": code, "aliases": aliases}]):
            df = _D.load_bars(mk, [sy], start, None).get(sy)
            if df is not None and len(df):
                # K152 排序强保证：parquet 原序不可控，统一按日期升序
                # （日/周/月三级别与 MA/MACD 全链路的顺序根基）
                bars = df.sort_index()
                break
    except Exception:
        log_exc("_kline_data:load_bars 日线加载 %s/%s" % (name, code))  # C2批1
        bars = []
    if bars is None or not len(bars):
        return {"ok": False, "err": "无日线数据（%s · %s）" % (name, code)}
    # MA 先全量算（保证切尾后头部均线完整），再取尾部 n 根。
    # 本地日线池短于 250+n 根时（ashare 池 2025-02 起），用 tdx ts_daily
    # 收盘价向前补（两源同口径不复权，实测逐日一致）
    closes = list(bars["close"])
    pre = []
    if ts_code and len(closes) - n < 250:
        pre = _ts_daily_prepend(ts_code, bars.index[0], 300)
        if pre:
            closes = [c for _dd, c in pre] + closes
    mas = {k: [None] * len(closes) for k in (5, 20, 60, 250)}
    for k in (5, 20, 60, 250):
        s = 0.0
        for i, c in enumerate(closes):
            s += c
            if i >= k:
                s -= closes[i - k]
            if i >= k - 1:
                mas[k][i] = round(s / k, 3)
    off = len(pre)
    out = []
    idxs = list(bars.index)
    for j, i in enumerate(idxs):
        out.append({"d": i.strftime("%Y-%m-%d"),
                    "o": round(float(bars.at[i, "open"]), 3),
                    "h": round(float(bars.at[i, "high"]), 3),
                    "l": round(float(bars.at[i, "low"]), 3),
                    "c": round(float(bars.at[i, "close"]), 3),
                    "v": float(bars.at[i, "volume"] or 0),
                    "m5": mas[5][j + off], "m20": mas[20][j + off],
                    "m60": mas[60][j + off], "m250": mas[250][j + off]})
    out = out[-n:]
    # A股：图谱结构信号标注（缠论买卖点，kg_portal 纯查询）
    marks = []
    if ts_code:
        try:
            import kg_portal as _KP
            st = _KP.stock(ts_code, days=n)
            for s in (st.get("structures") or [])[:40]:
                marks.append({"d": s.get("date") or "",
                              "t": s.get("type") or "",
                              "c": round(s.get("conf") or 0, 2)})
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
    # JEV 新闻 bias 标注（按日期去重，同日多条取 conf 最高）
    news = {}
    for it in _bt_jev_news(name, aliases):
        d = (it.get("ts") or "")[:10]
        if d:
            tier = event_type_tier(it.get("event_type"))
            cur = news.get(d)
            # 同日内 strong 事件类型优先（conf 反相关，不再据 conf 选）
            if cur is None or (tier == "strong" and cur.get("tier") != "strong"):
                news[d] = {"c": it.get("conf") or 0, "b": it.get("bias_cn"), "tier": tier}
    # K136 笔中枢上墙：全量窗 P3 结构（chanlun_bi，四市场纯几何，
    # 不限 A股）；dt=原始 K 线日期，前端按视窗类目自动过滤
    # K139 周线级别：ISO 周一 groupby 重采样，dt=周内最后交易日
    # （与前端 _wk() 的 w.d 口径对齐），analyze 出 wbis/wpivots
    bis, pivots, wbis, wpivots = [], [], [], []
    mbis, mpivots, fx, wfx, mfx = [], [], [], [], []
    sgs, wsgs, msgs = [], [], []
    sigs, wsigs, msigs = [], [], []
    try:
        import pandas as pd
        from chanlun_bi import analyze as _cl3
        cl = _cl3([{"dt": i.strftime("%Y-%m-%d"),
                    "h": float(bars.at[i, "high"]),
                    "l": float(bars.at[i, "low"])}
                   for i in bars.index],
                  [float(bars.at[i, "close"]) for i in bars.index])
        # K141 笔段挂 d(dir 方向)/pp(涨跌幅%)，中枢挂 n(段数)
        bis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["bis"]]
        pivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                   "zd": p["zd"], "n": p.get("n_bi", 0)}
                  for p in cl["pivots"]]
        # K142 分型标注：P2 全量分型（顶/底），t=1 顶 / -1 底
        fx = [{"d": f["dt"], "p": f["price"],
               "t": 1 if f["type"] == "顶" else -1}
              for f in cl["fractals"]]
        # K149 线段上墙：破坏确认法段（nb=段内笔数）
        sgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "nb": b.get("n_bi", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["segments"]]
        # K151 三类买卖点（引擎简化判据）透传；K154 sub 子类（1 类：
        # tbe 趋势背驰/pbe 盘整背驰，标注不拦截）
        sigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                 "sub": g.get("sub", "")}
                for g in cl["signals"]]
        keys = [i - pd.Timedelta(days=i.dayofweek) for i in bars.index]
        g = bars.groupby(keys)
        wdf = pd.DataFrame({"d": g.apply(lambda x: x.index[-1]),
                            "h": g["high"].max(), "l": g["low"].min(),
                            "c": g["close"].last()})
        cw = _cl3([{"dt": r["d"].strftime("%Y-%m-%d"),
                    "h": float(r["h"]), "l": float(r["l"])}
                   for _, r in wdf.iterrows()],
                  [float(r["c"]) for _, r in wdf.iterrows()])
        wbis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cw["bis"]]
        wpivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                    "zd": p["zd"], "n": p.get("n_bi", 0)}
                   for p in cw["pivots"]]
        wfx = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cw["fractals"]]
        wsgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "nb": b.get("n_bi", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cw["segments"]]
        wsigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                  "sub": g.get("sub", "")}
                 for g in cw["signals"]]
        # K140 月线级别：Period(M) 标量键 groupby（元组键会被 pandas
        # 当多列 key 误解析），dt=月内最后交易日（同周口径）
        gm = bars.groupby([i.to_period("M") for i in bars.index])
        mdf = pd.DataFrame({"d": gm.apply(lambda x: x.index[-1]),
                            "h": gm["high"].max(), "l": gm["low"].min(),
                            "c": gm["close"].last()})
        cmm = _cl3([{"dt": r["d"].strftime("%Y-%m-%d"),
                     "h": float(r["h"]), "l": float(r["l"])}
                    for _, r in mdf.iterrows()],
                   [float(r["c"]) for _, r in mdf.iterrows()])
        mbis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cmm["bis"]]
        mpivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                    "zd": p["zd"], "n": p.get("n_bi", 0)}
                   for p in cmm["pivots"]]
        mfx = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cmm["fractals"]]
        msgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "nb": b.get("n_bi", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cmm["segments"]]
        msigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                  "sub": g.get("sub", "")}
                 for g in cmm["signals"]]
    except Exception:
        log_exc("_kline_data:缠论多级别结构")  # C2批1
    # K172 P2 15 形态识别 + 信号日共振映射（四市场纯 OHLC 规则，
    # chanlun_pattern 自研引擎；ptmap 仅含结构信号日出现的形态）
    pats, ptmap = [], {}
    try:
        from chanlun_pattern import detect as _pdt, resonmap as _pres
        pbars = [{"dt": x["d"], "o": x["o"], "h": x["h"],
                  "l": x["l"], "c": x["c"]} for x in out]
        pats = _pdt(pbars)
        ptmap = _pres(pats, [g["d"] for g in sigs])
    except Exception:
        log_exc("_kline_data:chanlun_pattern 形态识别")  # C2批1
    return {"ok": True, "name": name, "code": code,
            "bars": out, "marks": marks, "news": news,
            "bis": bis, "pivots": pivots,
            "wbis": wbis, "wpivots": wpivots,
            "mbis": mbis, "mpivots": mpivots,
            "fx": fx, "wfx": wfx, "mfx": mfx,
            "sgs": sgs, "wsgs": wsgs, "msgs": msgs,
            "sigs": sigs, "wsigs": wsigs, "msigs": msigs,
            "patterns": pats, "ptmap": ptmap}


def _ts_daily_prepend(ts_code, first_dt, need):
    """tdx ts_daily 取 first_dt 之前的收盘价尾部 need 根（MA 历史兜底）。

    返回 [(YYYYMMDD, close)...] 升序；全量 df 进程级缓存（首读 ~1.5s）。
    """
    global _TS_DAILY_DF
    try:
        import pandas as pd
        if _TS_DAILY_DF is None:
            import glob as _glob
            fs = sorted(_glob.glob(os.path.join(
                STATION, "quant_backtest", "data", "tdx", "ts_daily",
                "*.parquet")))
            _TS_DAILY_DF = pd.concat(
                [pd.read_parquet(f, columns=["ts_code", "trade_date",
                                              "close"]) for f in fs],
                ignore_index=True)
        fd = first_dt.strftime("%Y%m%d")
        d = _TS_DAILY_DF[_TS_DAILY_DF["ts_code"] == ts_code]
        d = d[d["trade_date"].astype(str) < fd].sort_values("trade_date")
        d = d.tail(need)
        return list(zip(d["trade_date"].astype(str),
                        d["close"].astype(float)))
    except Exception:
        return []


