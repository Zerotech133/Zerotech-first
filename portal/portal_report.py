# -*- coding: utf-8 -*-
"""青萍之末分析师报告引擎（portal_report）—— K244。

把门户个股页现有全部维度（行情/多维加权/缠论结论与操作方案/筹码/
板块联动/公告后验/量能事件/龙虎榜/4策略信号）聚合成「事实包」，
再交给本地 Ollama（qwen3.5:9b，2026-10-07 随本机 Ollama 模型升级从 qwen3:8b-8k 切换，同 nl_query.py 修复）以「青萍之末分析师」口吻写成
一份大白话图文报告；同时产出结构化飞书卡片 JSON，供一键发送。

设计原则：
  · 事实层（collect_facts）纯只读聚合，零 LLM——可单测、可复现；
  · 叙事层（narrate）只把事实变成人话，不许编造数字（prompt 硬约束：
    只准引用事实包内数字，缺失就说缺失）；
  · 报告可离线落盘（reports/stock_reports/<code>_<date>.md），
    飞书发送复用 runner.push_feishu（card=True）。

对外接口：
  collect_facts(code, name=None) -> dict     事实包
  build_report(code, name=None, use_llm=True) -> dict
      {ok, code, name, facts, md, card, ts, llm_ok}
  push_report(code, name=None) -> (ok, msg)  生成并推飞书
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
QBT = os.path.join(STATION, "quant_backtest")
REPORTS = os.path.join(QBT, "reports")
OUT_DIR = os.path.join(REPORTS, "stock_reports")
OLLAMA = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")

_SUF = {"6": ".SH", "0": ".SZ", "3": ".SZ", "4": ".BJ", "8": ".BJ", "9": ".BJ"}


def _ts_code(code):
    """sz000993 / 000993 / 000993.SZ → 000993.SZ；非 A 股返回 None。"""
    c = str(code or "").strip()
    m = re.search(r"(\d{6})", c)
    if not m:
        return None
    d6 = m.group(1)
    return d6 + _SUF.get(d6[0], "")


def _load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _r(x, n=2):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _pct(x, n=1):
    v = _r(x, n + 2)
    return None if v is None else round(v * 100, n)


# ── 事实层：各维度只读聚合 ───────────────────────────────────
def _facts_quote(code, name):
    """实时行情。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "news_collector"))
        import stock_analyze

        q = stock_analyze.fetch_quote(code)
        if q:
            return {
                "px": _r(q.get("price")),
                "pct": _r(q.get("pct"), 2),
                "open": _r(q.get("open")),
                "high": _r(q.get("high")),
                "low": _r(q.get("low")),
                "prev": _r(q.get("prev")),
                "vol": q.get("volume"),
                "amount": q.get("amount"),
                "turnover": _r(q.get("turnover"), 2),
                "vr": _r(q.get("vol_ratio"), 2),
                "ts": q.get("ts"),
            }
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/portal_report.py", exc)
    return None


def _facts_resonance(code, name):
    """自选池多维加权快照（rps_latest.json 的 res 行）。"""
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json")) or {}
    for r in snap.get("res") or []:
        if r.get("code") == code or (name and r.get("name") == name):
            return {
                "score": r.get("score"),
                "action": r.get("action"),
                "dims": r.get("dims"),
                "tech": r.get("tech"),
                "jev": r.get("jev"),
                "rps": r.get("rps"),
                "env": r.get("env"),
                "longs": r.get("longs"),
                "sigs": r.get("sigs"),
                "added": r.get("added"),
                "date": r.get("date"),
                "in_pool": True,
                "env_safe": next(
                    (
                        bool(e.get("safe"))
                        for e in (snap.get("envs") or [])
                        if "A股" in str(e.get("label", ""))
                    ),
                    None,
                ),
            }
    return {"in_pool": False}


def _facts_bars(code, days=150):
    """日线（Close/Open/High/Low/Volume）用于量能与 K 线联动。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "news_collector"))
        sys.path.insert(0, QBT)
        import data_loader as _D
        import runner as _R

        start = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y-%m-%d")
        for mk, sy, _nm in _R.resolve_watchlist(
            [{"name": "", "code": code, "aliases": []}]
        ):
            df = _D.load_bars(mk, [sy], start, None).get(sy)
            if df is not None and len(df) > 20:
                return df.tail(days)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/portal_report.py", exc)
    return None


def _facts_chanlun(portal, code):
    """缠论结论 + 操作方案（复用门户 _chanlun_sl_tp 的多级别中枢）。"""
    try:
        stp = portal._chanlun_sl_tp(code)
    except Exception:
        stp = None
    if not stp:
        return None
    px = stp.get("px")
    out = {
        "px": px,
        "stop": stp.get("stop"),
        "tp1": stp.get("tp1"),
        "tp2": stp.get("tp2"),
        "basis": stp.get("basis"),
        "tpb": stp.get("tpb"),
        "dzg": stp.get("dzg"),
        "dzd": stp.get("dzd"),
        "mzg": stp.get("mzg"),
        "mzd": stp.get("mzd"),
    }
    # K247：带出日线中枢 state（供买点定位器做门禁：构建中的中枢无三买）
    try:
        import sys as _s

        _qb = r"C:\项目\老田的AI助手\quant_backtest"
        if _qb not in _s.path:
            _s.path.insert(0, _qb)
        import chanlun_card as _cc

        _cd = code.upper() if "." in code.upper() else code
        _one = (_cc.card(_cd).get("levels") or {}).get("1d") or {}
        out["pivot_state"] = (_one.get("pivot") or {}).get("state")
    except Exception:
        out["pivot_state"] = None

    def _d(base):
        try:
            return round((float(base) / float(px) - 1) * 100, 1)
        except Exception:
            return None

    out["stop_pct"] = _d(stp.get("stop"))
    out["tp1_pct"] = _d(stp.get("tp1"))
    out["tp2_pct"] = _d(stp.get("tp2"))
    # 操作方案（与门户结论卡同口径）
    zd, zg = out.get("dzd"), out.get("dzg")
    if px and zg and px > zg:
        out["plan"] = (
            "日线3买强结构：回踩 %.2f 不破可低吸做T；"
            "跌破 %.2f（中枢上沿=结构失效）坚决走" % (zg, out["stop"])
        )
    elif zd and zg:
        out["plan"] = (
            "日线中枢 %.2f~%.2f：贴 %.2f 下沿低吸、%.2f 上沿"
            "减仓做T；跌破 %.2f（最近笔低点）坚决走" % (zd, zg, zd, zg, out["stop"])
        )
    elif out.get("stop"):
        out["plan"] = "以 %.2f 为纪律线，跌破坚决走" % out["stop"]
    return out


def _facts_chips(portal, code, px):
    """平均筹码（本地 cyq_chips 最新日）。"""
    try:
        return portal._stock_chip_summary(code, px)
    except Exception:
        return None


def _facts_sectors(portal, code):
    """所属板块联动（涨跌 + RPS）。"""
    try:
        return portal._stock_sector_rows(code) or []
    except Exception:
        return []


def _facts_notices(code):
    """公告（tdx_info.db 直查）。"""
    tsc = _ts_code(code)
    if not tsc:
        return []
    db = os.path.join(QBT, "data", "tdx", "tdx_info.db")
    if not os.path.exists(db):
        return []
    try:
        cn = sqlite3.connect(db, timeout=5)
        try:
            return [
                {"title": x[0], "date": str(x[1]), "url": x[2]}
                for x in cn.execute(
                    "SELECT title, notice_date, url FROM stock_notice "
                    "WHERE ts_code=? ORDER BY notice_date DESC LIMIT 10",
                    (tsc,),
                )
            ]
        finally:
            cn.close()
    except Exception:
        return []


def _facts_lhb(code, days=130):
    """龙虎榜近 N 日明细（datamart ts_top_list）。"""
    tsc = _ts_code(code)
    if not tsc:
        return []
    try:
        sys.path.insert(0, QBT)
        import datamart as dm

        ws = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        tl = dm.read(
            "ts_top_list",
            start=ws,
            fields=[
                "ts_code",
                "trade_date",
                "pct_change",
                "net_amount",
                "l_buy",
                "l_sell",
                "reason",
            ],
        )
        if tl is None or tl.empty:
            return []
        sub = tl[tl["ts_code"].astype(str) == tsc]
        rows = []
        for _, r in sub.iterrows():
            rows.append(
                {
                    "date": str(r["trade_date"])[:8],
                    "pct": _r(r.get("pct_change"), 2),
                    "net": (
                        _r(r["net_amount"] / 1e4, 1)
                        if r.get("net_amount") is not None
                        else None
                    ),
                    "buy": (
                        _r(r["l_buy"] / 1e4, 1) if r.get("l_buy") is not None else None
                    ),
                    "sell": (
                        _r(r["l_sell"] / 1e4, 1)
                        if r.get("l_sell") is not None
                        else None
                    ),
                    "reason": str(r.get("reason") or "")[:40],
                }
            )
        rows.sort(key=lambda x: x["date"], reverse=True)
        return rows[:8]
    except Exception:
        return []


def _d2_gap(d1, d2):
    """两个 YYYYMMDD 之间相差的自然日数（带符号，d2-d1）。"""
    from datetime import datetime as _dt

    a = _dt.strptime(str(d1)[:8], "%Y%m%d")
    b = _dt.strptime(str(d2)[:8], "%Y%m%d")
    return (b - a).days


def _facts_entry_signal(df, chanlun):
    """买点定位器（K246）：找出「日线中枢突破日」= 三买日。

    目的：回答「哪天才是买点」——不能凭感觉说「资金进的那天」。
    口径（与缠论三买定义对齐）：
      1. 取日线中枢上沿 zg（chanlun.dzg）；
      2. 从近端往回找：首次「收盘站上 zg」且此前 N 日内曾在 zg 之下
         （=从中枢内/下方离开上沿）的那天 → 突破日 t0；
      3. 用突破日的量比（vol/5日均量）确认「放量突破」成色；
      4. 输出突破日前后的关键价位、距现价涨幅、突破至今涨幅。

    返回 {zg, zd, brk_date, brk_close, brk_vol_mult, brk_pct,
          before_date, before_close, since_pct, peak_date, peak_close,
          from_peak_pct, n_bars_above, entry_gap_days, entry_gap_pct}
    或 None（无中枢/无突破）。
    """
    if df is None or len(df) < 30 or not chanlun:
        return None
    import numpy as np

    zg = chanlun.get("dzg")
    zd = chanlun.get("dzd")
    if not zg:
        return None
    # K247 修正：中枢 state 门禁——只有中枢「已有效离开」（上方离开）时，
    # 才存在真正的三买。若中枢 state=「构建中」，中枢尚未走完，价格与
    # 上沿的关系只是震荡穿越，此时「站上 zg」不是三买突破，判为无买点。
    pstate = chanlun.get("pivot_state")
    if pstate and pstate not in ("上方离开", "离开", "向上离开"):
        return None
    pstate = pstate or "—"
    df = df.copy()
    cl = df["close"].astype(float).to_numpy()
    cols = {c.lower(): c for c in df.columns}
    vcol = cols.get("volume") or cols.get("vol")
    v = df[vcol].astype(float) if vcol else None
    v5 = v.rolling(5).mean().to_numpy() if v is not None else None
    dts = [str(x)[:10].replace("-", "") for x in df.index]
    zg = float(zg)
    # 突破日口径（关键）：找「此后不再跌破 zg」的首次站上日。
    # 做法：定位最后一个「收盘 ≤ zg」的交易日 k，则买点 = k 之后首个
    # 收盘 > zg 的那天（且需 k 之前有足够多的中枢内/下交易日）。
    # 这个口径排除了中枢上沿反复穿越（如 7 月的假突破），只认最后
    # 一次干净离开，正好对应缠论「中枢上方离开」的起点 = 三买。
    below = [i for i in range(len(df)) if cl[i] <= zg]
    if len(below) < 3:
        return None
    k = below[-1]  # 最后一次在中枢内/下
    brk_i = next((i for i in range(k + 1, len(df)) if cl[i] > zg), None)
    if brk_i is None:
        return None
    before_i = k
    # 该买点距现在太远（>150 根 K 之外）则意义不大
    if len(df) - brk_i > 150:
        return None
    brk_pct = ((cl[brk_i] / cl[brk_i - 1] - 1) * 100) if brk_i >= 1 else None
    vm = (
        float(v.iloc[brk_i] / v5[brk_i])
        if v is not None and v5 is not None and v5[brk_i]
        else None
    )
    # 突破前一日（最后一个在 zg 之下的收盘）
    last = float(cl[-1])
    # 突破至今的峰值
    seg = cl[brk_i:]
    peak_i = brk_i + int(np.argmax(seg))
    entry_gap = None
    if before_i is not None and before_i != brk_i:
        entry_gap = (cl[brk_i] / cl[before_i] - 1) * 100
    return {
        "zg": round(zg, 2),
        "zd": round(float(zd), 2) if zd else None,
        "pivot_state": pstate,
        "brk_date": dts[brk_i],
        "brk_close": round(float(cl[brk_i]), 2),
        "brk_pct": _r(brk_pct, 2),
        "brk_vol_mult": _r(vm, 2),
        "before_date": dts[before_i],
        "before_close": round(float(cl[before_i]), 2),
        "entry_gap_pct": _r(entry_gap, 2),
        "since_pct": _r((last / float(cl[brk_i]) - 1) * 100, 1),
        "peak_date": dts[peak_i],
        "peak_close": round(float(cl[peak_i]), 2),
        "from_peak_pct": _r((last / float(cl[peak_i]) - 1) * 100, 1),
        "n_bars_above": brk_i,
        "last": round(last, 2),
    }


def _facts_no_buy_reasons(code, facts):
    """不买决策链（K246 核心）：为什么「看似三买」却选择不买。

    这是报告最有说服力的部分——散户想知道「老手为什么按住手」。
    逐维度自动导出「拒绝买入的量化理由」，每条含：理由 + 当前值 +
    恢复/解除条件。数据源：chanlun_card（三级视图 + 三灯 + 趋势）。

    返回 {verdict, reasons:[{dim, why, cur, need}], entry, hard_no}
    """
    reasons = []
    cl = facts.get("chanlun") or {}
    px = facts.get("px")
    # 1) 取缠论卡完整判定
    card = None
    try:
        import sys as _s

        qb = r"C:\项目\老田的AI助手\quant_backtest"
        if qb not in _s.path:
            _s.path.insert(0, qb)
        import importlib as _il

        _cc = _il.import_module("chanlun_card")
        cd = code.upper() if "." in code.upper() else code
        card = _cc.card(cd)
    except Exception:
        card = None

    # 2) 大级别趋势锁（法层「只做上升趋势」）
    lv = (card or {}).get("levels") or {}
    d_trend = ((lv.get("1d") or {}).get("trend") or {}).get("state")
    m_trend = ((lv.get("30m") or {}).get("trend") or {}).get("state")
    tn = {"up": "上升", "down": "下跌", "range": "震荡"}
    if d_trend and d_trend != "up":
        reasons.append(
            {
                "dim": "大级别趋势",
                "why": "日线趋势是「%s」，策略铁律只做上升趋势"
                % tn.get(d_trend, d_trend),
                "cur": "日线 %s / 30分钟 %s"
                % (tn.get(d_trend, d_trend), tn.get(m_trend, m_trend)),
                "need": "日线需走出「高点创新高+低点抬升」确认上升",
            }
        )
    if m_trend == "down":
        reasons.append(
            {
                "dim": "小级别方向",
                "why": "30 分钟趋势向下，小级别买点属逆势",
                "cur": "30m %s" % tn.get(m_trend, m_trend),
                "need": "30m 转 up（站回中枢上沿）",
            }
        )

    # 3) 三灯（关键位/结构/均线）——不到关键位不动手
    kp = (card or {}).get("key_point") or {}
    if kp and kp.get("n_on") is not None and kp.get("n_on", 0) < 3:
        dets = []
        for k, label in (
            ("pivot_bottom", "中枢底距离"),
            ("sub_done", "次级别结构"),
            ("ma_ok", "关键均线"),
        ):
            d = kp.get(k) or {}
            if not d.get("on"):
                dets.append("%s：%s" % (label, d.get("detail") or "未到"))
        reasons.append(
            {
                "dim": "关键位三灯（%d/3 亮）" % kp.get("n_on", 0),
                "why": "「不到关键位不动手」——三灯未齐",
                "cur": " ｜ ".join(dets) if dets else kp.get("verdict"),
                "need": kp.get("verdict") or "三灯齐再动手",
            }
        )

    # 4) 位置风险：距中枢底太远（悬空）
    zg, zd = cl.get("dzg"), cl.get("dzd")
    if px and zd and zg:
        up_from_zd = (px / zd - 1) * 100
        if up_from_zd > 25:
            reasons.append(
                {
                    "dim": "位置（悬空）",
                    "why": "股价距日线中枢底 %.2f 已 +%.0f%%，"
                    "追进=在半空中接货" % (zd, up_from_zd),
                    "cur": "现价 %.2f / 中枢 %.2f~%.2f" % (px, zd, zg),
                    "need": "回踩中枢上沿 %.2f 不破再考虑" % zg,
                }
            )

    # 5) 高点回落：已从峰值明显回落（结构转弱）
    es = facts.get("entry_signal")
    if es and es.get("from_peak_pct") is not None and es["from_peak_pct"] < -12:
        reasons.append(
            {
                "dim": "高点回落",
                "why": "已从峰值 %.2f 回落 %.1f%%，顶分型已现，"
                "下跌段可能未走完" % (es.get("peak_close"), es.get("from_peak_pct")),
                "cur": "峰值 %s，现价 %s" % (es.get("peak_date"), es.get("last")),
                "need": "重新站上 MA20 或前高再评估",
            }
        )

    # 6) 资金面：近期龙虎榜净卖（炒作资金撤）
    lhb = facts.get("lhb") or []
    if lhb:
        last_lhb = max(lhb, key=lambda x: x["date"])
        if last_lhb.get("net") is not None and last_lhb["net"] < 0:
            reasons.append(
                {
                    "dim": "资金面",
                    "why": "最近一次龙虎榜（%s）净卖 %.0f 万，炒作资金在撤"
                    % (last_lhb["date"], abs(last_lhb["net"])),
                    "cur": "净卖 %.0f 万" % abs(last_lhb["net"]),
                    "need": "再现连续净买且量能配合",
                }
            )

    # 7) 筹码：获利盘高 + 现价低于平均筹码（套牢盘压顶）
    ch = facts.get("chips") or {}
    if ch.get("avg") and px:
        avg = float(ch["avg"])
        if px < avg:
            reasons.append(
                {
                    "dim": "筹码",
                    "why": "现价 %.2f 低于平均筹码 %.2f，上方套牢盘待解套" % (px, avg),
                    "cur": "均筹 %.2f / 获利盘 %s%%"
                    % (avg, ch.get("winner") if ch.get("winner") is not None else "—"),
                    "need": "站上平均筹码并放量",
                }
            )

    hard_no = len(
        [r for r in reasons if r["dim"] in ("大级别趋势", "关键位三灯", "位置（悬空）")]
    )
    verdict = (
        "不买（硬性 %d 条）" % hard_no
        if hard_no >= 2
        else "不买（%d 条压制）" % len(reasons)
        if reasons
        else "可考虑（无强压制）"
    )
    return {
        "verdict": verdict,
        "reasons": reasons,
        "n_reasons": len(reasons),
        "hard_no": hard_no,
        "card_ok": card is not None,
    }


def _facts_vol_events(df):
    """量能事件（巨量 ≥2×/地量 ≤0.5× 5日均量）+ 公告×量能关联 + K线联动。

    返回 {events:[...], links:[...], kline:[...]}：
      events 近 12 次量能事件（日期/类型/量比/当日涨跌）；
      links  公告日 ±3 交易日窗口内的量能事件配对（含「公告前/后」标记
             ——巨量出现在公告前=资金先动，公告后=消息兑现）；
      kline  公告日附近 K 线位置（相对 MA20、当日振幅、量价配合）。
    """
    if df is None or len(df) < 12:
        return {"events": [], "links": [], "kline": []}
    import numpy as np

    df = df.copy()
    cols = {c.lower(): c for c in df.columns}
    vcol = cols.get("volume") or cols.get("vol")
    if not vcol:
        return {"events": [], "links": [], "kline": []}
    dts = [str(x)[:10].replace("-", "") for x in df.index]
    cl = df["close"].astype(float).to_numpy()
    v = df[vcol].astype(float)
    v5 = v.rolling(5).mean().to_numpy()
    ma20 = df["close"].astype(float).rolling(20).mean().to_numpy()
    events = []
    for i in range(len(df)):
        r = v5[i]
        vi = v.iloc[i]
        if np.isnan(vi) or not r or r <= 0:
            continue
        mult = float(vi / r)
        tag = "巨量" if mult >= 2 else "地量" if mult <= 0.5 else None
        if tag:
            pc = ((cl[i] / cl[i - 1] - 1) * 100) if i >= 1 and cl[i - 1] else None
            events.append(
                {
                    "date": dts[i],
                    "tag": tag,
                    "mult": round(mult, 2),
                    "pct": _r(pc, 2),
                    "idx": i,
                }
            )
    return {"events": events, "v5": v5, "cl": cl, "ma20": ma20, "dts": dts}


def _link_notices_events(notices, ve, kline_n=3):
    """公告 × 量能关联：公告日 ±kline_n 交易日窗口内的量能事件配对。"""
    if not ve or not ve.get("events"):
        return [], []
    import numpy as np

    dts = ve["dts"]
    {d: i for i, d in enumerate(dts)}
    links = []
    for a in notices:
        ad = re.sub(r"\D", "", str(a.get("date") or ""))[:8]
        if len(ad) != 8:
            continue
        # 公告日 = ≥ad 的首个交易日（顺延）
        i0 = next((i for i, d in enumerate(dts) if d >= ad), None)
        if i0 is None:
            continue
        for ev in ve["events"]:
            delta = ev["idx"] - i0
            if -kline_n <= delta <= kline_n:
                links.append(
                    {
                        "notice_date": dts[i0],
                        "notice": (a.get("title") or "")[:34],
                        "ev_date": ev["date"],
                        "tag": ev["tag"],
                        "mult": ev["mult"],
                        "ev_pct": ev["pct"],
                        "delta": delta,
                        "timing": (
                            "公告前 %d 日" % -delta
                            if delta < 0
                            else "公告当日"
                            if delta == 0
                            else "公告后 %d 日" % delta
                        ),
                    }
                )
    links.sort(key=lambda x: (x["notice_date"], x["delta"]), reverse=True)
    # K 线联动：公告日相对 MA20 + 当日振幅
    kl = []
    cl, ma20 = ve["cl"], ve["ma20"]
    o_arr = ve.get("_open")
    hi_arr = ve.get("_high")
    lo_arr = ve.get("_low")
    for a in notices[:6]:
        ad = re.sub(r"\D", "", str(a.get("date") or ""))[:8]
        if len(ad) != 8:
            continue
        i0 = next((i for i, d in enumerate(dts) if d >= ad), None)
        if i0 is None or i0 < 1:
            continue
        px = cl[i0]
        m20 = ma20[i0]
        above = None if (m20 is None or np.isnan(m20)) else bool(px > m20)
        amp = None
        try:
            o0 = float(o_arr[i0]) if o_arr is not None else None
            h0 = float(hi_arr[i0]) if hi_arr is not None else None
            l0 = float(lo_arr[i0]) if lo_arr is not None else None
            if o0 and h0 is not None and l0 is not None:
                amp = round((h0 - l0) / o0 * 100, 2)
        except Exception:
            amp = None
        kl.append(
            {
                "date": dts[i0],
                "notice": (a.get("title") or "")[:30],
                "px": _r(px),
                "ma20": _r(m20),
                "above_ma20": above,
                "amp": amp,
            }
        )
    return links, kl


def _resolve_name(code, name=None):
    """名称兜底：显式 name → rps_latest res 行 → tdx 名称库 → code。"""
    if name:
        return name
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json")) or {}
    for r in snap.get("res") or []:
        if r.get("code") == code and r.get("name"):
            return r["name"]
    try:
        tsc = _ts_code(code)
        if tsc:
            names = _load_json(os.path.join(QBT, "data", "names.json")) or {}
            if names.get(tsc):
                return names[tsc]
            sys.path.insert(0, os.path.join(STATION, "news_collector"))
            import stock_analyze

            e = stock_analyze.resolve_stock(code)
            if e and e.get("name"):
                return e["name"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/portal_report.py", exc)
    return code


def collect_facts(portal, code, name=None):
    """聚合全部维度 → 事实包 dict（纯只读，零 LLM）。"""
    facts = {
        "code": code,
        "name": _resolve_name(code, name),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    facts["quote"] = _facts_quote(code, facts["name"])
    facts["resonance"] = _facts_resonance(code, facts["name"])
    facts["chanlun"] = _facts_chanlun(portal, code)
    px = (facts.get("quote") or {}).get("px") or (facts.get("chanlun") or {}).get("px")
    facts["px"] = px
    facts["chips"] = _facts_chips(portal, code, px)
    facts["sectors"] = _facts_sectors(portal, code)
    facts["notices"] = _facts_notices(code)
    facts["lhb"] = _facts_lhb(code)
    df = _facts_bars(code)
    facts["has_bars"] = df is not None
    if df is not None:
        cols = {c.lower(): c for c in df.columns}
        # K246 买点定位：日线中枢突破日（三买日）——回答「哪天才是买点」
        facts["entry_signal"] = _facts_entry_signal(df, facts.get("chanlun"))
        ve = _facts_vol_events(df)
        ve["_open"] = (
            df[cols["open"]].astype(float).to_numpy() if "open" in cols else None
        )
        ve["_high"] = (
            df[cols["high"]].astype(float).to_numpy() if "high" in cols else None
        )
        ve["_low"] = df[cols["low"]].astype(float).to_numpy() if "low" in cols else None
        # 量能事件只留近 12 次
        ve["events"] = ve["events"][-12:]
        links, kl = _link_notices_events(facts["notices"], ve)
        facts["vol_events"] = [
            {"date": x["date"], "tag": x["tag"], "mult": x["mult"], "pct": x["pct"]}
            for x in ve["events"]
        ]
        facts["notice_vol_links"] = links[:12]
        facts["kline_links"] = kl
    else:
        facts["entry_signal"] = None
        facts["vol_events"] = []
        facts["notice_vol_links"] = []
        facts["kline_links"] = []
    # K246 不买决策链（依赖 chanlun + entry_signal + 筹码 + 龙虎榜）
    facts["no_buy_reasons"] = _facts_no_buy_reasons(code, facts)
    return facts


# ── 叙事层：LLM 生成大白话报告 ───────────────────────────────
_PROMPT = """你是「青萍之末」量化策略团队的分析师，风格：说人话、不绕弯、\
有观点、敢下判断，但绝不编造数字。请只根据下面提供的事实数据，写一份给\
散户看的大白话个股点评（不是研报腔，像老手在茶桌上跟你讲）。

【铁律】
1. 只准引用事实包里出现的数字，事实包没有的一律不写、不推测具体数值；
2. 数字要带上，但别一上来堆数据，先用一句话给结论；
3. 涉及操作要具体：关键价位（止损/目标）、怎么买、什么时候走；
4. 语气专业但不端着，可以说"这票要注意""别追高"这类人话；
5. 全文 600~900 字，用小标题分 4~5 段，每段开头一个 emoji；
6. **各段必须讲不同角度，严禁整段复制粘贴**——「消息面与量能」段
   专讲公告与量能/龙虎榜的时间关系，「风险提示」段只讲风险，
   别重复前面的走势描述；同一句话只出现一次。
7. **必须讲清「买点在哪天」**：事实包 entry_signal 里的 brk_date 就是
   日线中枢突破日=标准三买的那天。要用「先/后」讲清买点与资金进场日
   的先后（若资金上榜日比买点晚，就是「形态先出买点、资金次日确认」，
   不要读反成资金先动），别把「资金进场那天」当成买点。
8. **必须讲清「为什么不买」**（本报告最有说服力的部分）：事实包
   no_buy_reasons 里逐条列出压制买入的理由（大级别趋势/三灯/位置/
   资金/筹码等）。要用大白话把这些理由讲成「老手为什么按住手」，
   每条给出「什么条件下才该出手」，让散户觉得「这分析师是替我把
   风险的」。这一段的篇幅要足，不能一笔带过。

【事实包】（JSON）
%s
%s

请按以下结构输出 Markdown（不要代码块包裹，不要额外说明）：
## 🎯 一句话结论
## 📊 现在是什么情况
## 🎯 买点在哪儿（三买定位：中枢突破日 vs 资金进场日）
## 🛑 为什么现在不买（逐条讲清「看似三买却按住手」的理由）
## 📈 关键价位与操作
## ⚡ 消息面与量能（公告/龙虎榜/量能事件的关联）
## ⚠️ 风险提示
"""


def _narrative_hints(facts):
    """从关联层提炼显式叙事线索（喂给 LLM 的「重点讲这几点」）。

    这是本报告的价值核心：把「公告 ↔ 量能 ↔ 龙虎榜 ↔ K线」的时间线
    串成资金行为链，而不是罗列孤立数字。"""
    hints = []
    links = facts.get("notice_vol_links") or []
    lhb = facts.get("lhb") or []
    # -1) 不买决策链（K246 最有说服力的一段）：逐条讲清「为什么按住手」
    nb = facts.get("no_buy_reasons") or {}
    if nb.get("reasons"):
        rl = "；".join(
            "%s：%s（%s）" % (r["dim"], r["why"], r.get("need") or "")
            for r in nb["reasons"][:6]
        )
        hints.append(
            "不买决策链（必须逐条讲清「为什么看似三买却按兵不动」，"
            "给出恢复条件，这是散户最想听的）：结论=%s。理由：%s"
            % (nb.get("verdict"), rl)
        )
    # 0) 买点定位（K246 最重要的一条）：日线中枢突破日=三买日，
    #    并回答「资金进场日 vs 真正买点」的先后关系
    es = facts.get("entry_signal")
    if es and es.get("brk_date"):
        zg = es.get("zg")
        gaps = es.get("entry_gap_pct")
        before = es.get("before_date")
        # 资金首次上榜日（净买）
        lhb_asc = sorted(lhb, key=lambda x: x["date"])
        first_buy = next((x for x in lhb_asc if x.get("net") and x["net"] > 500), None)
        tip = (
            "买点定位（务必讲明「买点这天」与「资金进场日」的关系）："
            "%s 收盘 %s 首次站上日线中枢上沿 %s（突破日=标准三买），"
            "此前 %s 收 %s 还在中枢内/下（距突破仅 %s%%），"
            "突破日量能 %s 倍5日均量"
            % (
                es["brk_date"],
                es.get("brk_close"),
                zg,
                before,
                es.get("before_close"),
                gaps,
                es.get("brk_vol_mult"),
            )
        )
        if first_buy:
            tip += "；龙虎榜首次净买在 %s（%.0f 万）" % (
                first_buy["date"],
                first_buy["net"],
            )
            try:
                gap = _d2_gap(first_buy["date"], es["brk_date"])
                # gap>0 = 突破日晚于资金 → 资金先动；gap<0 = 突破日早于资金
                if gap == 0:
                    tip += "（与买点同一天，资金同步确认）"
                elif gap > 0:
                    tip += "（比买点早 %d 天——资金先动、形态后确认）" % gap
                else:
                    tip += (
                        "（比买点晚 %d 天——即形态先出买点，"
                        "资金次日才上榜确认，属「买点先于资金」）" % -gap
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/portal_report.py", exc)
        tip += "。突破至今：%s 最高到 %s，现价 %s（较突破日 %s%%、较高点 %s%%）" % (
            es.get("peak_date"),
            es.get("peak_close"),
            es.get("last"),
            es.get("since_pct"),
            es.get("from_peak_pct"),
        )
        hints.append(tip)
    elif facts.get("chanlun"):
        # 无三买突破日（中枢未有效离开 / 股价仍在震荡 / 突破已太久远）
        # ——明确告知，严禁 LLM 臆造一个买点日期
        cl = facts["chanlun"]
        zg = cl.get("dzg")
        zd = cl.get("dzd")
        pstate = cl.get("pivot_state")
        px = facts.get("px")
        pos_txt = ""
        try:
            if px and zg:
                up = (float(px) / float(zg) - 1) * 100
                pos_txt = (
                    "（现价 %s 在中枢上沿 %s 的%s %.0f%% 处，"
                    "不要把它说成「在 %s~%s 区间内」）"
                    % (px, zg, "上方" if up >= 0 else "下方", abs(up), zd, zg)
                )
        except Exception:
            pos_txt = ""
        if pstate and pstate not in ("上方离开", "离开", "向上离开"):
            reason = (
                "日线中枢仍处于「%s」状态——中枢尚未确认走完、"
                "其区间为 %s~%s；价格虽然运行到中枢上方，但那是"
                "中枢生长期内的震荡上移，不是「离开中枢后不回」"
                "的干净三买突破" % (pstate, zd, zg)
            )
        else:
            reason = (
                "股价尚未出现「从中枢内/下方放量站上中枢上沿%s」"
                "这类干净突破，或突破已太久远" % zg
            )
        hints.append(
            "买点定位：**目前没有有效的日线三买买点**（%s）。这一段"
            "必须如实说「现在没有可操作的买点」，并讲清中枢状态这个"
            "原因%s，严禁编造任何买点日期" % (reason, pos_txt)
        )
    # 1) 资金先动：龙虎榜净买出现在公告前（取时间最贴近公告的净买）
    lhb_asc = sorted(lhb, key=lambda x: x["date"])
    for l in links:
        nd = l["notice_date"]
        pre = [
            x for x in lhb_asc if x["date"] < nd and x.get("net") and x["net"] > 1000
        ]
        if pre:
            p = pre[-1]  # 离公告最近的净买
            hints.append(
                "资金先动链条：%s 龙虎榜净买 %.0f 万（%s）→ %s 公告「%s」"
                "→ %s %s（当日 %s%%）——先有资金进场、后出消息兑现"
                % (
                    p["date"],
                    p["net"],
                    (p.get("reason") or "")[:16],
                    nd,
                    l["notice"][:24],
                    l["timing"],
                    l["tag"],
                    l["ev_pct"],
                )
            )
            break
    # 2) 出货信号：公告后龙虎榜净卖
    for l in links:
        nd = l["notice_date"]
        post = [
            x for x in lhb_asc if x["date"] > nd and x.get("net") and x["net"] < -3000
        ]
        if post:
            p = post[0]
            hints.append(
                "出货信号：%s 公告后，%s 龙虎榜净卖 %.0f 万（%s）——"
                "炒作资金撤退"
                % (nd, p["date"], abs(p["net"]), (p.get("reason") or "")[:16])
            )
            break
    # 3) 缩量/放量拐点：公告附近的地量
    for l in links:
        if l["tag"] == "地量" and (l.get("ev_pct") or 0) < -3:
            hints.append(
                "%s 公告「%s」伴随地量 %.1f× 且当日 %s%%——缩量急跌，"
                "抛压释放但需防继续下探"
                % (l["notice_date"], l["notice"][:22], l["mult"], l["ev_pct"])
            )
            break
    # 4) 高点回落与 K 线位置
    kl = facts.get("kline_links") or []
    if len(kl) >= 2:
        hi = max(kl, key=lambda k: k.get("px") or 0)
        cur = facts.get("px")
        if hi.get("px") and cur:
            drop = (cur / hi["px"] - 1) * 100
            hints.append(
                "高点回落：%s 曾到 %.2f，现价 %.2f（%+.1f%%），"
                "MA20 %s 上方=%s"
                % (
                    hi["date"],
                    hi["px"],
                    cur,
                    drop,
                    hi.get("ma20"),
                    hi.get("above_ma20"),
                )
            )
    return hints


def narrate(facts, timeout=240):
    """调用本地 Ollama 生成报告正文；失败返回 None。"""
    import requests

    payload = json.dumps(facts, ensure_ascii=False, default=str)
    hints = _narrative_hints(facts)
    hint_s = (
        (
            "\n【必须讲清楚的叙事线索（按顺序融入正文，用大白话）】\n"
            + "\n".join("- " + h for h in hints)
        )
        if hints
        else ""
    )
    try:
        r = requests.post(
            OLLAMA,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": _PROMPT % (payload, hint_s)}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.45, "num_predict": 1400},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        txt = (r.json().get("message") or {}).get("content", "").strip()
        # 去掉可能的思考/代码块包裹
        txt = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", txt.strip())
        return txt or None
    except Exception:
        return None


def _fallback_md(facts):
    """LLM 不可用时的模板兜底（保证一键发送永远有内容）。"""
    q = facts.get("quote") or {}
    cl = facts.get("chanlun") or {}
    rs = facts.get("resonance") or {}
    ch = facts.get("chips") or {}
    lines = [
        "## 🎯 一句话结论",
        "%s 现价 %s%s；%s"
        % (
            facts.get("name"),
            facts.get("px") or "—",
            ("（%+.2f%%）" % q["pct"]) if q.get("pct") is not None else "",
            (
                "自选池综合分 %s · %s" % (rs.get("score"), rs.get("action"))
                if rs.get("in_pool")
                else "未在自选池"
            ),
        ),
    ]
    if cl:
        lines += ["", "## 📈 关键价位与操作"]
        if cl.get("stop"):
            lines.append("- 止损 **%.2f**（%s）" % (cl["stop"], cl.get("basis") or ""))
        if cl.get("tp1"):
            lines.append(
                "- 目标1 **%.2f** ｜ 目标2 **%.2f**（%s）"
                % (cl["tp1"], cl.get("tp2"), cl.get("tpb") or "")
            )
        if cl.get("plan"):
            lines.append("- 操作：%s" % cl["plan"])
        es = facts.get("entry_signal")
        if es and es.get("brk_date"):
            lines += [
                "",
                "## 🎯 买点在哪儿（三买定位）",
                "- **买点（日线中枢突破日）**：**%s** 收 %.2f，"
                "首次站上中枢上沿 %.2f（标准三买）"
                % (es["brk_date"], es["brk_close"], es["zg"]),
                "- 突破前一日：%s 收 %.2f（还在中枢内/下，"
                "距突破仅 %s%%）"
                % (es["before_date"], es["before_close"], es.get("entry_gap_pct")),
                "- 突破日量能 %.2f 倍5日均量；突破至今最高 %.2f"
                "（%s），现价较突破日 %s%%、较高点 %s%%"
                % (
                    es.get("brk_vol_mult") or 0,
                    es["peak_close"],
                    es["peak_date"],
                    es.get("since_pct"),
                    es.get("from_peak_pct"),
                ),
            ]
    nb = facts.get("no_buy_reasons") or {}
    if nb.get("reasons"):
        lines += ["", "## 🛑 为什么现在不买（%s）" % nb.get("verdict")]
        for r in nb["reasons"]:
            lines.append(
                "- **%s**：%s；%s"
                % (r["dim"], r["why"], ("→ " + r["need"]) if r.get("need") else "")
            )
    if ch.get("avg"):
        lines += [
            "",
            "## 📊 筹码",
            "平均筹码 %.2f，获利盘 %s%%"
            % (ch["avg"], ch.get("winner") if ch.get("winner") is not None else "—"),
        ]
    secs = facts.get("sectors") or []
    if secs:
        lines += [
            "",
            "## 🏛 所属板块",
            " ｜ ".join(
                "%s %s%%"
                % (
                    s["sector"],
                    ("%+.1f" % s["chg1"]) if s.get("chg1") is not None else "—",
                )
                for s in secs
            ),
        ]
    links = facts.get("notice_vol_links") or []
    if links:
        lines += ["", "## ⚡ 公告×量能关联"]
        for l in links[:4]:
            lines.append(
                "- %s「%s」→ %s %s（%.1f×，当日 %s%%）"
                % (
                    l["notice_date"],
                    l["notice"],
                    l["timing"],
                    l["tag"],
                    l["mult"],
                    l["ev_pct"],
                )
            )
    lines += [
        "",
        "## ⚠️ 风险提示",
        "以上为纸面研究，不构成投资建议；数据以本地行情为准，请自行控制仓位。",
    ]
    return "\n".join(lines)


# ── 飞书卡片 ─────────────────────────────────────────────────
def build_card(facts, md):
    """结构化飞书卡片（markdown 正文 + 跳转按钮）。"""
    name = facts.get("name") or facts.get("code")
    rs = facts.get("resonance") or {}
    cl = facts.get("chanlun") or {}
    q = facts.get("quote") or {}
    sub = []
    if facts.get("px"):
        sub.append("现价 %s" % facts["px"])
    if q.get("pct") is not None:
        sub.append("%+.2f%%" % q["pct"])
    if rs.get("in_pool"):
        sub.append("综合分 %s" % rs.get("score"))
    # 卡片正文限长（飞书 markdown 元素上限较宽松，取 3500 字保护）
    body = md[:3500]
    tpl = "blue"
    act = str((rs.get("action") or "") + str(cl.get("plan") or ""))
    if "买入" in act or "低吸" in act:
        tpl = "green"
    elif "回避" in act or "坚决走" in act:
        tpl = "orange"
    portal_url = os.environ.get("PORTAL_BASE_URL", "http://localhost:8080")
    # K249：把缠论结构图嵌进卡片（图证层）——上传失败自动降级为无图卡
    elements = [{"tag": "markdown", "content": body}]
    img_key = None
    try:
        import sys as _s2

        _qb2 = r"C:\项目\老田的AI助手\quant_backtest"
        if _qb2 not in _s2.path:
            _s2.path.insert(0, _qb2)
        import report_card_img as _RCI

        _cd2 = facts.get("code") or ""
        img_key = _RCI.card_image_key(_cd2, "1d", 150)
    except Exception:
        img_key = None
    if img_key:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "img",
                "img_key": img_key,
                "alt": {"tag": "plain_text", "content": "缠论结构图 · 日线"},
            }
        )
    elements.append({"tag": "hr"})
    # K250：订阅按钮（A 方案）——把「等回踩 X 不破再考虑」一句变成可点。
    # 触发位取义：有买点 → 中枢上沿 zg（回踩不破位）；无买点 → 中枢底 zd
    # （跌破则彻底放弃的观察位）。取不到价就不给订阅按钮（宁缺毋滥）。
    _sub_btn = None
    try:
        import sys as _s3

        _qb3 = r"C:\项目\老田的AI助手\quant_backtest"
        if _qb3 not in _s3.path:
            _s3.path.insert(0, _qb3)
        import subscribe_action as _SA

        _es = facts.get("entry_signal") or {}
        _cl = facts.get("chanlun") or {}
        _zg, _zd = _cl.get("dzg"), _cl.get("dzd")
        _code3 = facts.get("code") or ""
        if _es:
            _px = _es.get("brk_close") or _zg
            _lab = "回踩不破"
            _ktxt = "📌 订阅这个买点"
        elif _zd:
            _px = _zd
            _lab = "跌破转弱"
            _ktxt = "📌 订阅提醒"
        else:
            _px = None
        if _px:
            _sub_btn = _SA.subscribe_button(
                _code3, _px, _lab, kind="buy_point", text=_ktxt
            )
    except Exception:
        _sub_btn = None
    _buttons = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "📄 打开个股页"},
            "type": "primary",
            "url": "%s/bt/stock?c=%s" % (portal_url, facts.get("code") or ""),
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🧭 缠论结构图"},
            "type": "default",
            "url": "%s/chanlun-png?c=%s" % (portal_url, facts.get("code") or ""),
        },
    ]
    if _sub_btn:
        _buttons.insert(0, _sub_btn)
    elements.append({"tag": "action", "actions": _buttons})
    elements.append(
        {
            "tag": "markdown",
            "text_size": "notation",
            "content": "<font color='grey'>青萍之末量化策略 · 纸面研究 "
            "不构成投资建议 · %s</font>" % facts.get("ts"),
        }
    )
    return {
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": "🌊 青萍之末分析师 · %s" % name},
            "subtitle": {
                "tag": "plain_text",
                "content": " ｜ ".join(sub) or facts.get("ts"),
            },
            "template": tpl,
        },
        "elements": elements,
    }


def build_report(portal, code, name=None, use_llm=True):
    """聚合事实 → 生成报告（md + card）+ 落盘。返回结果 dict。"""
    facts = collect_facts(portal, code, name)
    md = narrate(facts) if use_llm else None
    llm_ok = bool(md)
    if not md:
        md = _fallback_md(facts)
    card = build_card(facts, md)
    fname = "%s_%s.md" % (
        re.sub(r"\W", "_", str(code)),
        datetime.now().strftime("%Y%m%d"),
    )
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            f.write(md)
    except OSError:
        fname = None
    return {
        "ok": True,
        "code": code,
        "name": facts.get("name"),
        "px": facts.get("px"),
        "facts": facts,
        "md": md,
        "card": card,
        "llm_ok": llm_ok,
        "file": fname,
        "ts": facts["ts"],
    }


def push_report(portal, code, name=None):
    """生成报告 + 推飞书卡片。返回 (ok, msg)。"""
    rep = build_report(portal, code, name)
    try:
        sys.path.insert(0, QBT)
        import runner as _R

        ok = _R.push_feishu(json.dumps(rep["card"], ensure_ascii=False), card=True)
    except Exception as ex:
        return False, "推送异常：%s" % str(ex)[:120]
    return bool(ok), (
        "已推送「%s」分析师报告到飞书（%s）" % (rep.get("name"), rep.get("ts"))
    )


if __name__ == "__main__":
    # 冒烟：命令行生成报告（需门户实例提供 _chanlun_sl_tp 等）
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    import portal_api

    _portal = portal_api.Handler() if hasattr(portal_api, "Handler") else portal_api
    r = build_report(_portal, a.code, use_llm=not a.no_llm)
    print(r["md"][:600])
    print("...\nllm_ok=%s file=%s" % (r["llm_ok"], r["file"]))
