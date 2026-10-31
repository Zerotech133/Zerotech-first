# -*- coding: utf-8 -*-
"""P4 批7-SCR：数据/筛选域 Mixin（screen 页族 + build_screen/kline/chips 函数族）。
Handler 壳经基座多继承并入（禁 import *）。pandas 均为函数内局部导入。
"""
import json
import logging
import os
import re
import sys
import threading
import time
from api_backtest import build_daily_pulse, _pulse_ago
from api_portfolio import _pf_reason_cat, _council_holdings
from api_common import (
    _read_json,
    log_exc,
    STATION,
    REPORTS_DIR,
    QBT_DIR,
    pill_nav,
    _screen_cache,
    _fmt_pct, _TP_JS, build_alerts, _council_stats, _pool_bear_news,
    data_freshness_bg)
from api_screen_kline import _kline_qy, _kline_data, _ts_daily_prepend  # noqa: F401  re-export（A5 拆分首批）

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen



def _chips_data(q, d, x=None):
    """个股筹码分布瀑布（S5）：cyq_chips 单日按需 + parquet 缓存。

    A股专属；x=当日收盘价（计算获利盘/平均成本，仅展示用途）。
    返回按价升序 rows=[[price,percent]...]（已滤 price<=0 垃圾行）。
    """
    import re as _re
    m6 = _re.search(r"(\d{6})", str(q or ""))
    ts_code = ""
    if m6:
        d6 = m6.group(1)
        ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
                   if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return {"ok": False, "err": "筹码分布为 A 股专属"}
    dd = _re.sub(r"\D", "", str(d or ""))[:8]
    if len(dd) != 8:
        return {"ok": False, "err": "日期无效"}
    cdir = os.path.join(STATION, "quant_backtest", "data",
                        "tushare", "cyq_chips")
    cfile = os.path.join(cdir, ts_code.replace(".", "_") + ".parquet")
    rows = None
    try:
        import pandas as pd
        df = pd.read_parquet(cfile) if os.path.exists(cfile) else None
        if df is not None and len(df) and (df["trade_date"] == dd).any():
            rows = df[df["trade_date"] == dd]
        else:
            with _CHIPS_FLOCK:  # 在线拉取+落盘串行化（单用户门户足够）
                df2 = pd.read_parquet(cfile) if os.path.exists(cfile) else None
                if df2 is not None and len(df2) and \
                        (df2["trade_date"] == dd).any():
                    rows = df2[df2["trade_date"] == dd]  # 双检：等锁期间已被拉过
                else:
                    # 区间拉取（45天）：休市幻影K线回退最近交易日，
                    # 顺带预热缓存供 dataZoom 回滚复用
                    import datetime as _dt
                    d0 = _dt.datetime.strptime(dd, "%Y%m%d") - \
                        _dt.timedelta(days=45)
                    sys.path.insert(0, os.path.join(STATION,
                                                    "quant_backtest"))
                    import tushare_sync as _TS
                    data = _TS.ts_call("cyq_chips", {
                        "ts_code": ts_code,
                        "start_date": d0.strftime("%Y%m%d"),
                        "end_date": dd})
                    fields = data.get("fields") or []
                    fi = {f: i for i, f in enumerate(fields)}
                    ndf = pd.DataFrame(
                        [[str(r[fi["trade_date"]]), float(r[fi["price"]]),
                          float(r[fi["percent"]])]
                         for r in (data.get("items") or [])
                         if r[fi["price"]] is not None
                         and float(r[fi["price"]]) > 0],
                        columns=["trade_date", "price", "percent"])
                    base = df2 if df2 is not None else df
                    merged = pd.concat([t for t in (base, ndf)
                                        if t is not None and len(t)])
                    if len(merged):
                        merged = merged[merged["trade_date"].astype(str)
                                        <= dd]
                    if not len(merged):
                        return {"ok": False,
                                "err": "该日无筹码数据（%s）" % dd}
                    merged = merged.drop_duplicates(
                        ["trade_date", "price"]).sort_values(
                        ["trade_date", "price"])
                    os.makedirs(cdir, exist_ok=True)
                    merged.to_parquet(cfile, index=False)
                    last = str(merged["trade_date"].max())
                    rows = merged[merged["trade_date"].astype(str) == last]
                    dd = last  # 实际取到的交易日
        rr = sorted((round(float(p), 2), round(float(pc), 3))
                    for p, pc in zip(rows["price"], rows["percent"]))
        try:
            close = round(float(x), 2)
        except (TypeError, ValueError):
            close = None
        winner = avg = None
        if close and rr:
            winner = round(sum(pc for p, pc in rr if p <= close), 1)
            avg = round(sum(p * pc for p, pc in rr) / 100.0, 2)
        return {"ok": True, "code": ts_code,
                "d": "%s-%s-%s" % (dd[:4], dd[4:6], dd[6:8]),
                "close": close,
                "winner": winner, "avg": avg, "rows": rr}
    except Exception as ex:
        return {"ok": False, "err": "筹码获取失败：%s" % str(ex)[:120]}
_CHIPS_FLOCK = threading.Lock()
_TS_DAILY_DF = None
def build_screen():
    """大屏 /api/screen 数据源（60s 缓存），全部只读产线快照：
    ① envs        四市场环境灯（rps_latest.json envs）
    ② strategies  每策略最新选股数 × 市场分布（cross_poll.build_cross）
    ③ pick_detail 最新一期选股明细（多策略命中数排序，前 80）
    ④ industry    A 股行业细分（九维 full 池行业计数 top12）
    ⑤ portfolio   统一虚拟账户（portfolio.json：两子账户净值/持仓）
    ⑥ books       既有账本成绩（sig4=positions.json / chanlun=chanlun_paper）
    单项失败即缺项，不拖垮整屏。"""
    now = time.time()
    if _screen_cache["d"] is not None and now - _screen_cache["t"] < SCREEN_TTL:
        return _screen_cache["d"]
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    # ① 四市场环境灯
    rps = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
    envs = []
    for e in rps.get("envs") or []:
        lb = e.get("label") or ""
        mkt = ("CN" if lb.startswith("A股") else
               "US" if lb.startswith("美股") else
               "HK" if lb.startswith("港股") else
               "CRYPTO" if lb.startswith("加密") else None)
        if mkt:
            envs.append({"market": mkt, "score": e.get("score"),
                         "safe": e.get("safe"), "state": e.get("state"),
                         "breadth": e.get("breadth")})
    out["envs"] = envs
    out["rps_ts"] = rps.get("ts", "")

    # ②③ cross_poll 聚合：每策略选股×市场 + 明细
    strategies, detail = [], []
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import cross_poll
        cross = cross_poll.build_cross()
        stocks = cross.get("stocks") or []
        for s in cross.get("strategies") or []:
            sid = s.get("id")
            hits = [st for st in stocks
                    if any(h.get("sid") == sid for h in st.get("hits") or [])]
            bm = {}
            for st in hits:
                bm[st["market"]] = bm.get(st["market"], 0) + 1
            strategies.append({"id": sid, "name": s.get("name"),
                               "icon": s.get("icon"), "badge": s.get("badge"),
                               "color": s.get("color"), "url": s.get("url"),
                               "markets": s.get("markets"),
                               "n": len(hits), "by_market": bm})
        for st in stocks:
            detail.append({"market": st["market"], "code": st["code"],
                           "name": st["name"], "n_hit": st.get("n_hit", 0),
                           "n_long": st.get("n_long", 0),
                           "state": st.get("state"),
                           "hits": [h.get("sid") for h in st.get("hits") or []]})
    except Exception:
        log_exc("_screen_page:cross_poll 聚合")  # C2批1
    out["strategies"] = strategies
    out["pick_detail"] = sorted(detail, key=lambda x: -x["n_hit"])[:80]

    # ④ A 股行业细分（九维 full 池）
    nd = _read_json(os.path.join(QBT_DIR, "data", "nine_dim_scores.json"))
    ind = {}
    for r in nd.get("rows") or []:
        if r.get("full") and r.get("industry"):
            ind[r["industry"]] = ind.get(r["industry"], 0) + 1
    out["industry"] = sorted(({"name": k, "n": v} for k, v in ind.items()),
                             key=lambda x: -x["n"])[:12]
    out["nd_ts"] = nd.get("generated_at", "")

    # ⑤ 统一虚拟账户（K44 每日 23:50 模拟建仓 + 择时择势自动平仓）
    #    K54-A1/A2/A3：份额净值 ps + 机构指标（SR/MaxDD/波动）+ 母基金视图
    pf = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    pfc = _read_json(os.path.join(QBT_DIR, "data", "portfolio_config.json"))
    pm = None
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import perf_metrics as _pm
        pm = _pm
    except Exception:
        pm = None

    def _cps(c):
        return c.get("ps") if c.get("ps") else 1.0 + (c.get("ret") or 0) / 100.0

    accounts = {}
    fof_curves, fof_shares = {}, {}
    for sid, ac in (pf.get("accounts") or {}).items():
        curve = ac.get("nav_curve") or []
        closed = ac.get("closed") or []
        wins = [c for c in closed if (c.get("exit_pnl_pct") or 0) > 0]
        acc = (pfc.get("accounts") or {}).get(sid) or {}
        # K51-C 熔断状态（与引擎 daily_run 同口径：平仓后实时净值 vs 峰值）
        nav_now = (ac.get("cash") or 0) + sum(
            p["qty"] * (p.get("last_px") or p.get("entry_px") or 0)
            for p in (ac.get("open") or []))
        peak = max([c["nav"] for c in curve]
                   + [ac.get("capital") or 0, nav_now])
        dd = round((peak - nav_now) / peak * 100, 2) if peak > 0 else 0.0
        mdd_cfg = float(acc.get("max_drawdown_pct",
                                pfc.get("max_drawdown_pct", 10)))
        # K54-B4 三级回撤墙（与引擎 _dd_level 同口径：1 警戒/2 熔断/3 深回撤）
        dd_warn = float(acc.get("dd_warn_pct", pfc.get("dd_warn_pct", 5)))
        dd_cut = float(acc.get("dd_cut_pct", pfc.get("dd_cut_pct", 15)))
        dd_lv = (3 if (dd_cut > 0 and dd >= dd_cut)
                 else 2 if (mdd_cfg > 0 and dd >= mdd_cfg)
                 else 1 if (dd_warn > 0 and dd >= dd_warn) else 0)
        # K54-A2 机构指标（份额收益 ps 序列：申赎不污染曲线）
        m = {"sharpe": None, "sortino": None, "vol_ann": None,
             "max_dd_pct": None, "total_ret": None}
        if pm and len(curve) >= 2:
            try:
                m = pm.compute([{"d": c["d"], "nav": _cps(c)} for c in curve])
            except Exception:
                log_exc("_screen_page:perf_metrics.compute")  # C2批1
        # K51-B 平仓原因归类统计 + 平均持有交易日
        cats = {}
        for c in closed:
            cat = _pf_reason_cat(c.get("reason"))
            d = cats.setdefault(cat, {"n": 0, "pnl": 0.0})
            d["n"] += 1
            d["pnl"] += c.get("exit_pnl_pct") or 0
        for d in cats.values():
            d["pnl"] = round(d["pnl"] / d["n"], 2)
        holds = [c.get("hold_days") or 0 for c in closed]
        accounts[sid] = {
            "name": ac.get("name"), "icon": ac.get("icon"),
            "capital": ac.get("capital"), "cash": ac.get("cash"),
            "shares": ac.get("shares") or ac.get("capital"),
            "start_date": acc.get("start_date") or pfc.get("start_date") or "",
            "stop_loss_pct": acc.get("stop_loss_pct",
                                     pfc.get("stop_loss_pct", 8)),
            "take_profit_pct": acc.get("take_profit_pct",
                                       pfc.get("take_profit_pct", 15)),
            "max_pos": int(acc.get("max_pos", 20)),
            "max_drawdown_pct": mdd_cfg,
            "drawdown": dd, "dd_lv": dd_lv,
            "dd_review": ac.get("dd_review") or None,
            "close_reasons": cats,
            "avg_hold": (round(sum(holds) / len(holds), 1) if holds else None),
            "trades_recent": (ac.get("trades") or [])[-12:],
            "nav": curve[-1]["nav"] if curve else ac.get("capital"),
            "ret": curve[-1]["ret"] if curve else 0.0,
            "ps": round(_cps(curve[-1]), 4) if curve else 1.0,
            "sharpe": m["sharpe"], "sortino": m["sortino"],
            "vol_ann": m["vol_ann"], "maxdd_pct": m["max_dd_pct"],
            "today": (curve[-1]["ret"] - curve[-2]["ret"]
                      if len(curve) > 1 else
                      (curve[-1]["ret"] if curve else 0.0)),
            "n_open": len(ac.get("open") or []),
            "n_closed": len(closed),
            "win_rate": (round(len(wins) / len(closed), 3) if closed else None),
            "closed_avg_pnl": (round(
                sum(c.get("exit_pnl_pct") or 0 for c in closed) / len(closed), 2)
                if closed else None),
            "closed_recent": [{"name": c.get("name"), "market": c.get("market"),
                               "reason": c.get("reason"),
                               "pnl_pct": c.get("exit_pnl_pct"),
                               "exit_date": c.get("exit_date")}
                              for c in closed[-10:]],
            "open": sorted(ac.get("open") or [],
                           key=lambda p: -(p.get("pnl_pct") or 0)),
            "curve": [[c["d"], c["nav"]] for c in curve],
            "curve_ps": [[c["d"], round(_cps(c), 4)] for c in curve]}
        if curve:
            fof_curves[sid] = [{"d": c["d"], "ps": _cps(c)} for c in curve]
            fof_shares[sid] = ac.get("shares") or ac.get("capital") or 1_000_000
    # K54-A3 母基金（四账户份额加权 ps 曲线 + 机构指标）
    fof = None
    if pm and len(fof_curves) >= 2:
        try:
            ps_c = pm.fof_merge(fof_curves, fof_shares)
            fm = pm.compute([{"d": x["d"], "nav": x["ps"]} for x in ps_c])
            fof = {"curve_ps": [[x["d"], round(x["ps"], 4)] for x in ps_c],
                   "ps": round(ps_c[-1]["ps"], 4), "ret": fm["total_ret"],
                   "sharpe": fm["sharpe"], "maxdd_pct": fm["max_dd_pct"],
                   "vol_ann": fm["vol_ann"]}
        except Exception:
            fof = None
    out["portfolio"] = {"accounts": accounts, "ts": pf.get("ts", ""),
                        "hold_max": int(pfc.get("hold_max") or 20),
                        "fof": fof, "bac": pf.get("bac")}

    # ⑥ 既有账本成绩（独立账本，不入统一账户避免双记账）
    books = {}
    pos = _read_json(os.path.join(QBT_DIR, "positions.json"))  # runner 账本（根目录）
    if pos:
        o, c = pos.get("open") or [], pos.get("closed") or []
        books["sig4"] = {
            "name": "四策略信号账本", "n_pending": len(pos.get("pending") or []),
            "n_open": len(o), "n_closed": len(c),
            "open_avg_pnl": (round(sum(e.get("pnl") or 0 for e in o) / len(o), 4)
                             if o else None),
            "win_rate": (round(sum(1 for e in c if (e.get("pnl") or 0) > 0)
                               / len(c), 3) if c else None),
            "closed_avg_pnl": (round(sum(e.get("pnl") or 0 for e in c) / len(c), 4)
                               if c else None),
            "ts": pos.get("ts", "")}
    paper = _read_json(os.path.join(QBT_DIR, "data", "chanlun_paper.json"))
    if paper:
        o, c = paper.get("open") or [], paper.get("closed") or []
        recent = c[-50:]          # 近期战绩：最近 50 笔出场（月度口径月初恒空）
        books["chanlun"] = {
            "name": "缠论纸面仓", "n_open": len(o), "n_closed": len(c),
            "n_exit_recent": len(recent),
            "recent_avg_pnl": (round(sum(e.get("pnl") or 0 for e in recent)
                                     / len(recent), 4) if recent else None),
            "recent_win_rate": (round(
                sum(1 for e in recent if (e.get("pnl") or 0) > 0)
                / len(recent), 3) if recent else None),
            "ts": paper.get("updated_at", "")}
    out["books"] = books

    # ── K57 大屏 v2：每日流水 / 预警增量 / 缠论尾15票 / 当日买卖点 / 新鲜度 ──
    try:
        out["pulse"] = build_daily_pulse()
    except Exception:
        log_exc("_screen_page:build_daily_pulse")  # C2批1
        out["pulse"] = []
    try:
        out["alerts"] = (build_alerts().get("alerts") or [])
    except Exception:
        log_exc("_screen_page:build_alerts")  # C2批1
        out["alerts"] = []
    try:
        pool_path = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
        rows = []
        with open(pool_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except Exception as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)

        def _sig(r):
            return {"code": r.get("code"), "name": r.get("name"),
                    "market": r.get("market"), "period": r.get("period"),
                    "type": r.get("type"), "dt": r.get("dt"),
                    "detect": r.get("detect_date"),
                    "px": r.get("entry_px") or r.get("price"),
                    "npv": r.get("npv"), "tbe": r.get("tbe"),
                    "trend": r.get("trend"), "lag": r.get("lag")}

        days = sorted({r.get("detect_date") for r in rows
                       if r.get("detect_date")})
        sig_day = days[-1] if days else ""
        today_rows = [r for r in rows if r.get("detect_date") == sig_day]
        out["sig_day"] = sig_day
        out["signals"] = [_sig(r) for r in today_rows[:60]]
        out["fifteen"] = [_sig(r) for r in reversed(rows[-15:])]
    except Exception:
        log_exc("_screen_page:chanlun_pool 当日信号")  # C2批1
        out["sig_day"] = ""
        out["signals"] = []
        out["fifteen"] = []
    try:
        fr = data_freshness_bg(1800)
        rs = (fr or {}).get("rows") or []
        out["fresh"] = ({"ok": sum(1 for r in rs if r.get("status") == "ok"),
                         "total": len(rs)} if rs else None)
    except Exception:
        log_exc("_screen_page:freshness 新鲜度")  # C2批1
        out["fresh"] = None

    # ── 态势感知 ops：① 服务实时探活（60s 缓存内只探一轮）
    #    ② 数据链最新锚点距今 ③ 调度时刻表（前端算下一班倒计时） ──
    def _probe_ops():
        import socket as _sk

        def http_ok(url):
            try:
                return safe_urlopen(url, timeout=2.5).status == 200
            except Exception:
                return False

        def tcp_ok(port):
            try:
                s = _sk.create_connection(("127.0.0.1", port), timeout=2)
                s.close()
                return True
            except Exception:
                return False

        return [("门户API", http_ok("http://127.0.0.1:8091/health")),
                ("nginx网关", http_ok("http://127.0.0.1:8080/")),
                ("量化前端", http_ok("http://127.0.0.1:5175/")),
                ("QD桥接", tcp_ok(9103)),
                ("卡片回调", tcp_ok(9104))]

    try:
        out["ops"] = [{"name": n, "ok": ok} for n, ok in _probe_ops()]
    except Exception:
        log_exc("_screen_page:ops 服务探活")  # C2批1
        out["ops"] = []
    try:
        import datetime as _dt

        def _pts(x):
            s = str(x).strip()
            try:
                if "T" in s:
                    t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                    if t.tzinfo:
                        t = t.astimezone().replace(tzinfo=None)
                    return t
                return _dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
            except Exception:
                return None

        cand = [(p["ts"], _pts(p["ts"])) for p in out.get("pulse") or []
                if p.get("ts")]
        cand = [(s0, t) for s0, t in cand if t]
        if cand:
            s0, t = max(cand, key=lambda x: x[1])
            out["ops_meta"] = {
                "last_task": next(p["name"] for p in out["pulse"]
                                  if p.get("ts") == s0),
                "last_ts": s0,
                "last_ago": _pulse_ago(s0),
                "server_now": _dt.datetime.now().strftime("%H:%M:%S")}
    except Exception:
        log_exc("_screen_page:ops_meta 最近任务锚")  # C2批1
        out["ops_meta"] = None

    # ── K65 财经快讯 + Jev 判断：news_collector/news.db 只读
    #    hit=1 = 关键词命中（有影响）；jev 列 = 本地模型结构化判断
    #    ai_briefs = 每日 AI 晨报（总览+多空因素）──
    try:
        import sqlite3 as _sq
        import datetime as _dt

        _ndb = os.path.join(STATION, "news_collector", "news.db")
        _con = _sq.connect("file:%s?mode=ro" % _ndb.replace("\\", "/"),
                           uri=True, timeout=2)
        _c = _con.cursor()
        _since = (_dt.datetime.now() - _dt.timedelta(hours=24)
                  ).strftime("%Y-%m-%d %H:%M")
        rows = list(_c.execute(
            "SELECT ts, source, title, stocks, jev FROM news "
            "WHERE hit=1 AND ts >= ? ORDER BY ts DESC LIMIT 60",
            (_since,)))
        news, n_bull, n_bear, n_neu, scopes = [], 0, 0, 0, {}
        for ts, src, title, stocks, jev in rows:
            j = {}
            try:
                j = json.loads(jev) if jev else {}
            except Exception:
                j = {}
            b = j.get("bias")
            if b == "bull":
                n_bull += 1
            elif b == "bear":
                n_bear += 1
            elif b == "neutral":
                n_neu += 1
            sc = j.get("scope_cn") or "未分类"
            scopes[sc] = scopes.get(sc, 0) + 1
            news.append({"ts": ts, "source": src, "title": title,
                         "stocks": stocks if stocks and stocks != "[]" else "",
                         "bias": j.get("bias_cn") or "",
                         "conf": j.get("conf"),
                         "scope": j.get("scope_cn") or "",
                         "reason": j.get("reason") or ""})
        out["news"] = news
        out["news_stats"] = {"n": len(rows), "bull": n_bull, "bear": n_bear,
                             "neutral": n_neu,
                             "scopes": sorted(scopes.items(),
                                              key=lambda x: -x[1])[:6]}
        try:
            br = list(_c.execute(
                "SELECT date, ts, brief FROM ai_briefs "
                "ORDER BY ts DESC LIMIT 1"))
            if br:
                out["news_brief"] = {"date": br[0][0], "ts": br[0][1],
                                     "brief": (br[0][2] or "")[:900]}
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
        _con.close()
    except Exception:
        log_exc("_screen_page:news.db 快讯/晨报")  # C2批1
        out["news"] = []
        out["news_stats"] = None
        out.setdefault("news_brief", None)

    # ── 利空消息警示（大屏侧）：预警池/持仓股近5日高置信利空消息 ──
    #    口径同预警池页 _pool_bear_news（scope='cn' 守卫降噪）；
    #    回测 v2：偏空 T+1 跑输全市场 ~0.5pp，只提示不拦截
    out["bear_pool"] = []
    try:
        from collections import deque as _dq
        cn_map = {}
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as _pf:
            for _ln in _dq(_pf, maxlen=200):
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _r = json.loads(_ln)
                except Exception:
                    continue
                if _r.get("code"):
                    cn_map[_r["code"]] = (_r.get("name") or "").strip()
        for _p in (paper or {}).get("open") or []:
            if _p.get("code"):
                cn_map.setdefault(_p["code"], (_p.get("name") or "").strip())
        if cn_map:
            out["bear_pool"] = _pool_bear_news(cn_map)
    except Exception:
        log_exc("_screen_page:bear_pool 利空警示")  # C2批1
        out["bear_pool"] = []

    # ── K61 投委会锚（罗盘迷你卡消费；态势带第⑥路走 alerts 同源）
    #    K63 扩展：持仓管理明细 hold + 指令分布 dist（管理面板消费）
    try:
        co = dict(_council_stats() or {})
        opens = set()
        for sid, ac in ((out.get("portfolio") or {}).get("accounts")
                        or {}).items():
            for p in ac.get("open") or []:
                if p.get("code"):
                    opens.add((sid, p.get("code")))
        co["hold"] = _council_holdings(co, opens)
        co["dist"] = {}
        for h in co["hold"]:
            co["dist"][h["act"]] = co["dist"].get(h["act"], 0) + 1
        out["council"] = co
    except Exception:
        log_exc("_screen_page:council 投委会锚")  # C2批1
        out["council"] = None

    _screen_cache.update(t=now, d=out)
    return out
SCREEN_TTL = 60
def get_quotes():
    try:
        with open(os.path.join(STATION, "news_collector", "quotes_status.json"),
                  "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 豁免（C2批1）：quotes_status.json 未生成属常态（首次运行前）
        return {"ok": None, "items": []}


# ══ Handler 方法（经 MRO 并入基座）══

class Handler:

    @staticmethod
    def _stock_notices(code):
        """A股公告（带原文 url）+ 研报（pdf_url）：tdx_info.db 直查。
        返回 (anns, rpts)；非 A 股或无库静默 ([], [])。"""
        import sqlite3
        anns, rpts = [], []
        try:
            m6 = re.search(r"\d{6}", code or "")
            if not m6:
                return [], []
            c6 = m6.group(0)
            ts_code = (c6 + ".SH" if c6[0] == "6" else
                       c6 + ".SZ" if c6[0] in "03" else
                       c6 + ".BJ" if c6[0] in "48" else "")
            if not ts_code:
                return [], []
            db = os.path.join(QBT_DIR, "data", "tdx", "tdx_info.db")
            if not os.path.exists(db):
                return [], []
            cn = sqlite3.connect(db, timeout=5)
            try:
                anns = [{"title": x[0], "date": x[1], "url": x[2]}
                        for x in cn.execute(
                            "SELECT title, notice_date, url FROM "
                            "stock_notice WHERE ts_code=? ORDER BY "
                            "notice_date DESC LIMIT 8", (ts_code,))]
                rpts = [{"name": x[0], "rating": x[1], "org": x[2],
                         "date": x[3], "url": x[4]}
                        for x in cn.execute(
                            "SELECT report_name, rating, org, pub_date, "
                            "pdf_url FROM stock_report WHERE ts_code=? "
                            "ORDER BY pub_date DESC LIMIT 5", (ts_code,))]
            finally:
                cn.close()
        except Exception:
            return [], []
        return anns, rpts

    @classmethod
    def _stock_fundamental(cls, code):
        """个股基本面核心指标 → (rows, note)；rows=[(标签,值)]。
        A股：估值（1日缓存）+ 财务（nine_dim._fina 5日缓存，ROE/毛利
        /增速/负债率）；港股：tushare 逐码 fina parquet 最新行；
        美股：财报库为数字代码体系（无字母 ticker 文件），暂缺。"""
        try:
            rows, note = [], ""
            c = str(code or "")
            m6 = re.search(r"\d{6}", c)
            mhk = re.search(r"hk(\d{1,5})", c, re.I)
            if m6:                       # A股（6 位连续数字，兼容
                c6 = m6.group(0)         # sh600519 / 600519.SH 等形态）
                ts_code = (c6 + ".SH" if c6[0] == "6" else
                           c6 + ".SZ" if c6[0] in "03" else
                           c6 + ".BJ" if c6[0] in "48" else "")
                if not ts_code:
                    return [], ""
                val = cls._stock_valuation(ts_code)
                if val:
                    try:
                        rows += [("估值", "PE(TTM)",
                                  "%.1f" % float(val["pe_ttm"]))
                                 if val.get("pe_ttm") else
                                 ("估值", "PE(TTM)", "—"),
                                 ("估值", "总市值(亿)", "%.0f" % (
                                     float(val["total_mv"]) / 1e4))
                                 if val.get("total_mv") else
                                 ("估值", "总市值(亿)", "—")]
                    except (TypeError, ValueError) as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
                    rows.append(("估值", "换手率", _fmt_pct(
                        val.get("turnover_rate"))))
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import nine_dim as _ND
                fin = (_ND._fina([ts_code]) or {}).get(ts_code) or {}
                cur = fin.get("cur") or {}
                prv = fin.get("prev") or {}

                def _g2(k):
                    # K235 成长组：本期 + 前期对照（财报趋势一眼可见）
                    v = _fmt_pct(cur.get(k))
                    p = prv.get(k)
                    if v != "—" and p not in (None, ""):
                        try:
                            v += "（前值 %s）" % _fmt_pct(float(p))
                        except (TypeError, ValueError) as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
                    return v
                _eb_s = "—"
                try:                      # tushare ebitda 实测单位：元
                                          # （K-STOCK-386 实证 000993.H1
                                          # ebitda=1.2e8 → 1.20亿；旧注释
                                          # 「万元」错，÷1e4 再标亿放大 1e4 倍）
                    _ebv = float(cur.get("ebitda") or 0)
                    if _ebv:
                        _eb_s = (("%.2f亿" % (_ebv / 1e8))
                                 if abs(_ebv) >= 1e8
                                 else "%.0f万" % (_ebv / 1e4))
                except (TypeError, ValueError) as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
                # K235 扩容：(组, 标签, 值) 三元组——估值/盈利/成长/健康
                # 四组汇总；新增现金流覆盖(ocf_to_or)与 EBITDA
                rows += [("盈利", "ROE", _fmt_pct(cur.get("roe"))),
                         ("盈利", "毛利率", _fmt_pct(
                             cur.get("grossprofit_margin"))),
                         ("盈利", "现金流/营收", _fmt_pct(
                             cur.get("ocf_to_or"))),
                         ("盈利", "EBITDA", _eb_s),
                         ("成长", "营收增速", _g2("or_yoy")),
                         ("成长", "净利增速", _g2("netprofit_yoy")),
                         ("健康", "资产负债率", _fmt_pct(
                             cur.get("debt_to_assets")))]
                if cur.get("end_date"):
                    note = "财务报告期 %s · 估值 %s" % (
                        cur["end_date"], val.get("trade_date", "—")
                        if val else "—")
            elif mhk or re.fullmatch(r"\d{1,5}", c):   # 港股
                sym = (mhk.group(1) if mhk else c).zfill(5)
                p = os.path.join(QBT_DIR, "data", "tushare",
                                 "hk_fina_indicator", sym + "_HK.parquet")
                if not os.path.exists(p):
                    return [], "暂无基本面数据（港股财报库未覆盖 %s）" % sym
                import pandas as _pd
                df = _pd.read_parquet(p)
                r = df.sort_values("end_date").iloc[-1]
                rows = []
                try:
                    if r.get("pe_ttm"):
                        rows.append(("估值", "PE(TTM)",
                                     "%.1f" % float(r["pe_ttm"])))
                    if r.get("total_market_cap"):
                        rows.append(("估值", "总市值(亿)", "%.0f" % (
                            float(r["total_market_cap"]) / 1e8)))
                except (TypeError, ValueError) as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
                rows += [("盈利", "ROE", _fmt_pct(r.get("roe_avg"))),
                         ("盈利", "毛利率", _fmt_pct(
                             r.get("gross_profit_ratio"))),
                         ("成长", "营收增速", _fmt_pct(
                             r.get("operate_income_yoy"))),
                         ("成长", "净利增速", _fmt_pct(
                             r.get("holder_profit_yoy"))),
                         ("健康", "资产负债率", _fmt_pct(
                             r.get("debt_asset_ratio")))]
                note = "港股 · %s（%s 财币）" % (
                    r.get("report_type") or "", r.get("currency") or "HKD")
            else:                        # 美股等：数字代码体系不覆盖
                return [], ("暂无基本面数据（美股财报库未覆盖字母"
                            "代码 %s）" % (c or "—"))
            return rows, note
        except Exception:
            return [], "基本面数据暂不可用"

    @classmethod
    def _stock_valuation(cls, ts_code):
        """A股估值（tushare daily_basic 单股）：pe_ttm/总市值/换手率，
        portal 侧独立 1 日小缓存，命中零 API。"""
        cf = os.path.join(QBT_DIR, "data", "portal_stock_val_cache.json")
        cache = {}
        try:
            if time.time() - os.path.getmtime(cf) < 86400:
                cache = json.load(open(cf, encoding="utf-8"))
        except Exception:
            cache = {}
        if ts_code in cache:
            return cache[ts_code]
        val = None
        try:
            if QBT_DIR not in sys.path:
                sys.path.insert(0, QBT_DIR)
            import nine_dim as _ND
            rows, fields = _ND._ts(
                "daily_basic", ts_code=ts_code,
                fields="ts_code,trade_date,pe_ttm,total_mv,turnover_rate")
            i = {f: k for k, f in enumerate(fields)}
            if rows:
                val = {f: rows[0][i[f]] for f in fields if f != "ts_code"}
                cache[ts_code] = val
                try:
                    json.dump(cache, open(cf, "w", encoding="utf-8"),
                              ensure_ascii=False)
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_screen.py", exc)
        except Exception:
            val = None
        return val
    # ── K64 当日买卖点全量页（大屏"全屏 »"下钻目标） ──
    def _screen_sig_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        s = build_screen()
        rows = s.get("signals") or []
        day = s.get("sig_day") or "—"

        def _f(v):
            return e(str(v)) if v is not None else "—"

        trs = []
        for r in rows:
            t = str(r.get("type") or "")
            strong = t in ("1买", "1卖") or (r.get("tbe") or 0) >= 80
            col = ("#f85149" if "买" in t else "#3fb950" if "卖" in t
                   else "#e6edf3")
            trs.append(
                "<tr data-t='%s' data-q='%s %s %s' style='cursor:pointer'"
                " onclick=\"location.href='/bt-linkage?code=%s'\""
                "%s><td><b style='color:%s'>%s%s</b></td>"
                "<td>%s <span class='dim num'>%s</span></td>"
                "<td class='num dim'>%s</td><td class='num dim'>%s</td>"
                "<td class='num dim'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td></tr>"
                % (e(t), e(str(r.get("name") or "")), e(str(r.get("code") or "")),
                   e(str(r.get("period") or "")), _q(str(r.get("code") or "")),
                   (" style='box-shadow:inset 2px 0 0 #f59e0b'" if strong
                    else ""), col, ("⚡" if strong else ""), e(t),
                   e(str(r.get("name") or "")), e(str(r.get("code") or "")),
                   _f(r.get("period")), _f(r.get("dt")), _f(r.get("detect")),
                   _f(r.get("px")), _f(r.get("npv"))))
        n_buy = sum(1 for r in rows if "买" in str(r.get("type")))
        n_sell = sum(1 for r in rows if "卖" in str(r.get("type")))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta http-equiv='refresh' content='60'>"
                "<title>当日买卖点 · %s</title>"
                "<style>body{background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:16px}"
                "table{border-collapse:collapse;width:100%%;font-size:13px}"
                "th{background:#21262d;text-align:left;padding:7px 10px;"
                "color:#8b949e;font-weight:600;position:sticky;top:0}"
                "td{padding:6px 10px;border-bottom:1px solid #21262d;"
                "white-space:nowrap}tr:hover td{background:#1c2128}"
                ".dim{color:#8b949e}.num{font-family:Consolas,monospace}"
                ".bar{display:flex;gap:10px;align-items:center;"
                "flex-wrap:wrap;margin:8px 0 12px}"
                ".chip{padding:4px 12px;border-radius:14px;cursor:pointer;"
                "border:1px solid #30363d;font-size:12.5px;background:#161b22}"
                ".chip.on{border-color:#58a6ff;color:#58a6ff}"
                "input{background:#161b22;border:1px solid #30363d;"
                "border-radius:6px;padding:6px 10px;color:#e6edf3;"
                "font-size:13px;width:180px}</style></head><body>"
                "<div style='display:flex;align-items:baseline;gap:14px'>"
                "<span style='font-size:19px;font-weight:700;color:#e6edf3'>"
                "⚡ 当日买卖点 · %s</span>"
                "<span style='font-size:12.5px;color:#8b949e'>检出 %d 条 · "
                "买 %d · 卖 %d · 60s 自动刷新 · 点击行进联动页</span></div>"
                "<div class='bar'>"
                "<span class='chip on' onclick=\"flt('')\">全部</span>"
                "<span class='chip' onclick=\"flt('买')\">买点</span>"
                "<span class='chip' onclick=\"flt('卖')\">卖点</span>"
                "<input placeholder='搜索 名称/代码/周期' oninput='flt(this.value)'"
                " id='q'></div>"
                "<table><tr><th>级别</th><th>标的</th><th>周期</th>"
                "<th>信号日</th><th>检出日</th><th>价</th><th>NPV</th>"
                "</tr>%s</table>"
                "<script>function flt(v){v=(v||'').trim();var t=v===''?'':"
                "(v==='买'||v==='卖'?v:'');var q=(t?'':v).toLowerCase();"
                "document.querySelectorAll('tr[data-t]').forEach(function(r){"
                "var ok=(!t||r.dataset.t.indexOf(t)>=0)&&(!q||r.dataset.q."
                "toLowerCase().indexOf(q)>=0);r.style.display=ok?'':'none'});"
                "document.querySelectorAll('.chip').forEach(function(c){"
                "c.classList.toggle('on',c.textContent.indexOf(v)===0&&v)});"
                "if(v!=='买'&&v!=='卖'&&v)document.getElementById('q').focus()"
                "}</script></body></html>"
                % (e(day), e(day), len(rows), n_buy, n_sell,
                   "".join(trs))).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    # ── K56-B4 数据大屏 /screen：四市场穿透视图（此前只有 /api/screen
    #    数据源、页面 404——导航「📺 数据大屏」按钮常年空指，本批补齐）──
    def _screen_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        s = build_screen()
        _TB = ("border-collapse:collapse;width:100%;font-size:12.5px")
        _TD = ("padding:5px 10px;border-bottom:1px solid #21262d;"
               "color:#e6edf3;white-space:nowrap")

        def _sec(title, href, hint):
            return ("<div style='margin:14px 0 6px;display:flex;"
                    "align-items:baseline;gap:10px'>"
                    "<span style='font-size:14px;font-weight:700;"
                    "color:#e6edf3'>%s</span>"
                    "<a href='%s' style='font-size:11px;color:#58a6ff;"
                    "text-decoration:none'>进详情 →</a>"
                    "<span style='font-size:11px;color:#8b949e'>%s</span></div>"
                    % (title, href, hint))

        # ① 四市场环境灯（K56 穿透：每卡 🔍 直达底表数据行）
        _MKT = [("CN", "🇨🇳 A股", "daily"), ("US", "🇺🇸 美股", "us_daily"),
                ("HK", "🇭🇰 港股", "hk_daily"), ("CRYPTO", "🪙 加密", "crypto_daily")]
        env_by = {x.get("market"): x for x in s.get("envs") or []}
        cards = []
        for mk, label, tb in _MKT:
            x = env_by.get(mk) or {}
            sc = x.get("score")
            col = ("#3fb950" if x.get("safe")
                   else "#f85149" if sc is not None and sc < 40
                   else "#d29922")
            state = e(str(x.get("state") or "—"))
            cards.append(
                "<div style='flex:1;min-width:180px;background:#161b22;"
                "border:1px solid #30363d;border-radius:10px;padding:10px 14px'>"
                "<div style='font-size:14px;font-weight:700;color:#e6edf3'>%s"
                "<span style='cursor:pointer;margin-left:8px;font-size:12px' "
                "title='穿透看 %s 数据' onclick=\"tp_toggle('%s')\">🔍</span>"
                "<span style='font-size:11px;color:%s;margin-left:8px'>%s</span>"
                "</div><div style='margin-top:6px;font-size:12px;"
                "font-family:Consolas,monospace'>评分 <b style='color:%s'>%s</b>"
                "</div>%s"
                "<div id='tpr-%s' style='display:none'>"
                "<div id='tpc-%s' style='padding:6px 0 0'></div></div>"
                "</div>"
                % (label, tb, tb, col, state, col,
                   ("—" if sc is None else "%d" % sc),
                   ("<div style='margin-top:4px;font-size:11px;color:#8b949e'>"
                    "环境快照缺（待 rps 刷新）</div>" if not x else ""),
                   tb, tb))
        m_env = ("<div style='display:flex;gap:10px;flex-wrap:wrap'>%s</div>"
                 % "".join(cards))

        # ② 策略概览
        st_rows = []
        for st in s.get("strategies") or []:
            bm = st.get("by_market") or {}
            st_rows.append(
                "<tr><td style='%s'>%s <a href='%s' style='color:#58a6ff;"
                "text-decoration:none'>%s</a></td><td style='%s;color:%s'>%d</td>"
                "<td style='%s;color:#8b949e'>CN %d · US %d · HK %d · CR %d</td>"
                "</tr>"
                % (_TD, e(str(st.get("icon") or "")),
                   e(str(st.get("url") or "#")), e(str(st.get("name") or "")),
                   _TD, e(str(st.get("color") or "#e6edf3")),
                   st.get("n") or 0, _TD,
                   bm.get("CN", 0), bm.get("US", 0), bm.get("HK", 0),
                   bm.get("CRYPTO", 0)))
        m_st = (_sec("② 策略概览 · 选股×市场", "/bt-rps",
                     "每策略最新选股数与市场分布")
                + ("<table style='%s'><tr>%s</tr>%s</table>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("策略", "选股", "市场分布")),
                      "".join(st_rows))
                   if st_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "无策略数据（cross_poll 待跑）</div>"))

        # ③ 选股明细 top40（代码→图谱下钻）
        pd_rows = []
        for p in (s.get("pick_detail") or [])[:40]:
            mk = e(str(p.get("market") or ""))
            code = e(str(p.get("code") or ""))
            pd_rows.append(
                "<tr><td style='%s;color:#8b949e'>%s</td>"
                "<td style='%s'><a href='/kg?c=%s' style='color:#58a6ff;"
                "text-decoration:none'>%s</a></td>"
                "<td style='%s'>%s</td><td style='%s;color:#d29922'>%d</td>"
                "<td style='%s;color:#8b949e;font-size:11px'>%s</td></tr>"
                % (_TD, mk, _TD, _q(str(p.get("code") or "")), code, _TD,
                   e(str(p.get("name") or "")), _TD, p.get("n_hit") or 0, _TD,
                   e(" ".join(str(h) for h in p.get("hits") or []))))
        m_pd = (_sec("③ 选股明细 · 多策略命中", "/kg", "点代码进图谱")
                + ("<div style='max-height:340px;overflow-y:auto'>"
                   "<table style='%s'><tr>%s</tr>%s</table></div>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("市场", "代码", "名称", "命中", "策略")),
                      "".join(pd_rows))
                   if pd_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "本日无选股</div>"))

        # ④ 行业细分（top12 条形）
        ind = s.get("industry") or []
        mx = max((x["n"] for x in ind), default=0) or 1
        bars = "".join(
            "<div style='display:flex;align-items:center;gap:8px;"
            "margin:3px 0'><span style='width:110px;font-size:11.5px;"
            "color:#8b949e;overflow:hidden;text-overflow:ellipsis;"
            "white-space:nowrap'>%s</span><div style='height:12px;"
            "border-radius:3px;background:linear-gradient(90deg,#1f6feb,"
            "#388bfd);width:%d%%'></div><span style='font-size:11px;"
            "color:#e6edf3'>%d</span></div>"
            % (e(str(x.get("name") or "")), round(x.get("n", 0) * 100 / mx),
               x.get("n", 0))
            for x in ind)
        m_ind = (_sec("④ A股行业细分", "/sa", "九维 full 池行业计数")
                 + (bars or "<div style='color:#8b949e;font-size:12px'>"
                             "无行业数据（九维待跑）</div>"))

        # ⑤ 账户态势（四盘 + 母基金）
        pf = s.get("portfolio") or {}
        accs = pf.get("accounts") or {}
        ac_rows = []
        for sid, a in accs.items():
            ret = a.get("ret") or 0
            today = a.get("today") or 0
            dd = a.get("drawdown") or 0
            ddv = a.get("dd_lv") or 0
            dd_col = ("#f85149" if ddv >= 2 else "#d29922" if ddv == 1
                      else "#3fb950")
            ac_rows.append(
                "<tr><td style='%s'>%s %s</td>"
                "<td style='%s;color:%s'>%.2f%%</td>"
                "<td style='%s;color:%s'>%+.2f%%</td>"
                "<td style='%s;color:%s'>%.2f%%<span style='font-size:10px;"
                "margin-left:4px'>L%d</span></td>"
                "<td style='%s;color:#8b949e'>胜率 %s · 持/平 %d/%d</td>"
                "<td style='%s'>%s</td></tr>"
                % (_TD, e(str(a.get("icon") or "")), e(str(a.get("name") or sid)),
                   _TD, "#f85149" if ret >= 0 else "#3fb950", ret,
                   _TD, "#f85149" if today >= 0 else "#3fb950", today,
                   _TD, dd_col, dd, ddv,
                   _TD,
                   ("%.0f%%" % (a.get("win_rate") * 100))
                   if a.get("win_rate") is not None else "—",
                   a.get("n_open") or 0, a.get("n_closed") or 0,
                   _TD, e("SR %s" % ("%.2f" % a["sharpe"])
                          if a.get("sharpe") is not None else "—")))
        fof = pf.get("fof")
        fof_line = (""
                    if not fof else
                    "<div style='margin-top:6px;font-size:12px;"
                    "color:#8b949e'>母基金 ps %s · 累计 %s%% · SR %s · "
                    "MaxDD %s%%</div>"
                    % (fof.get("ps"), fof.get("ret"), fof.get("sharpe"),
                       fof.get("maxdd_pct")))
        m_pf = (_sec("⑤ 账户态势 · 模拟盘", "/bt-report", "点进回测报告")
                + (("<table style='%s'><tr>%s</tr>%s</table>"
                    % (_TB,
                       "".join("<th style='%s;background:#21262d;text-align:"
                               "left'>%s</th>" % (_TD, x)
                               for x in ("账户", "累计", "今日", "回撤",
                                         "胜率/持仓", "指标")),
                       "".join(ac_rows))
                    + fof_line) if ac_rows else
                   ("<div style='color:#8b949e;font-size:12px'>无账户数据"
                    "</div>" + fof_line)))

        # ⑥ 账本成绩
        books = s.get("books") or {}
        bk_rows = []
        for bid, b in books.items():
            bk_rows.append(
                "<tr><td style='%s'>%s</td><td style='%s'>%d / %d</td>"
                "<td style='%s;color:%s'>%s</td><td style='%s;color:#8b949e'>"
                "%s</td></tr>"
                % (_TD, e(str(b.get("name") or bid)),
                   _TD, b.get("n_open") or 0, b.get("n_closed") or 0,
                   _TD, "#3fb950",
                   ("%.0f%%" % ((b.get("recent_win_rate")
                                 or b.get("win_rate") or 0) * 100)),
                   _TD, e(str(b.get("ts") or ""))))
        m_bk = (_sec("⑥ 独立账本", "/bt-report", "不入统一账户")
                + ("<table style='%s'><tr>%s</tr>%s</table>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("账本", "持仓/平仓", "胜率", "时点")),
                      "".join(bk_rows))
                   if bk_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "无账本</div>"))

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<title>数据大屏 · 老田的AI助手</title></head><body "
                "style='background:#0d1117;margin:0;padding:12px 16px'>"
                + self._COCKPIT_CSS + self._KG_CSS
                + pill_nav("screen")
                + "<div style='color:#e6edf3;font-size:18px;font-weight:700;"
                  "margin:10px 0 2px'>📺 数据大屏 · 四市场穿透</div>"
                  "<div style='color:#8b949e;font-size:12px;margin-bottom:6px'>"
                  "A股 · 美股 · 港股 · 加密 —— 每张卡 🔍 直达底表数据行 · "
                  "快照 %s（环境 %s · 九维 %s）</div>"
                % (e(str(s.get("ts") or "")), e(str(s.get("rps_ts") or "")),
                   e(str(s.get("nd_ts") or "")))
                + _sec("① 四市场环境灯", "/bt-data-health", "🔍 穿透到底表")
                + m_env + m_st + m_pd + m_ind + m_pf + m_bk + _TP_JS
                + "<div style='color:#8b949e;font-size:11px;margin:16px 0'>"
                  "数据源 /api/screen（60s 缓存）· 穿透 /api/bridge/"
                  "table_preview（白名单 15 表）</div>"
                + "</body></html>")
        self._html(body)
