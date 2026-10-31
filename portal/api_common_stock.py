# -*- coding: utf-8 -*-
"""api_common 四组拆分：stock 组（2026-10-31 WO-COMMON-SPLIT-20261031）。

自 api_common.py 纯移动而来，函数体/常量零改动；对外仍可从
api_common 导入（兼容 re-export）。
"""
from __future__ import annotations

import json
import os
import re

from api_common import (
    STATION,
    _read_json,
    log_exc,
)


MKT_LABEL = {"cn": "A股", "us": "美股", "hk": "港股", "crypto": "加密货币"}

def _pool_bear_news(codes_names, days=5, min_conf=0.75, limit=12):
    """预警池利空消息警示（news.db 只读）：近 days 天 bias=bear 且
    置信≥min_conf、可归属到池内个股的消息。

    依据（docs/ops/portal_llm观察记录.md 回测 v2）：偏空消息 T+1 跑输全市场 ~0.5pp，
    「避雷」方向有效 —— 只提示不拦截、不用于增强买入。
    归属三路：① stocks 列真 ts_code ② 标题前缀代码 [600853 龙建股份]
    / [个股研报·org] 300059 ③ 股票名出现在标题。
    返回 [{ts, code, name, title, conf, reason}]，新→旧。"""
    import sqlite3
    from datetime import datetime as _dt, timedelta as _td
    if not codes_names:
        return []
    since = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d %H:%M")
    c6 = {c.split(".")[0]: c for c in codes_names}
    name2code = {n: c for c, n in codes_names.items() if n and len(n) >= 2}
    out, seen = [], set()
    try:
        db = sqlite3.connect(os.path.join(STATION, "news_collector",
                                          "news.db"))
        rows = db.execute(
            "SELECT created_at, title, stocks, jev FROM news "
            "WHERE jev IS NOT NULL AND created_at>=? "
            "ORDER BY id DESC LIMIT 4000", (since,)).fetchall()
        db.close()
    except Exception:
        log_exc("_pool_bear_news:news.db 查询")  # C2批1
        return out
    for ca, title, stocks, jev in rows:
        if not title:
            continue
        try:
            jv = json.loads(jev)
        except Exception:
            continue
        if jv.get("bias") != "bear" or (jv.get("conf") or 0) < min_conf:
            continue
        if jv.get("scope") != "cn":
            # 只取 A 股域消息：宏观/港股/商品域新闻按名归属噪声大
            # （实测：新世界发展≠新世界、金龙指数提及比亚迪、
            #  法国政局挂到金正大），警示层宁可漏报不可误报
            continue
        hit = None
        try:
            lst = json.loads(stocks) if stocks else []
        except Exception:
            lst = []
        for x in lst if isinstance(lst, list) else []:
            if isinstance(x, str) and x in codes_names:
                hit = x
                break
        if hit is None:
            m = re.match(r"^\[(\d{6})[\s·\]]", title)
            if not m:
                m = re.match(r"^\[[^\]]+\]\s*(\d{6})\s", title)
            if m and m.group(1) in c6:
                hit = c6[m.group(1)]
        if hit is None:
            for n, c in name2code.items():
                if n in title:
                    hit = c
                    break
        if hit is None:
            continue
        key = (hit, title[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append({"ts": (ca or "")[:16], "code": hit,
                    "name": codes_names.get(hit, ""),
                    "title": title[:60],
                    "conf": round((jv.get("conf") or 0) * 100),
                    "reason": (jv.get("reason") or "")[:40]})
        if len(out) >= limit:
            break
    return out

_SEC_CACHE = {}

# ==== 批6 随行提级：CANDLE_CN/_matrix_cards/_stock_chip_summary/_stock_sector_rows ====
# ── K239 个股所属板块联动（tdx 板块定义/成分 + 板块指数涨跌） ──
_SEC_CACHE = {"ts": 0.0, "rev": None, "rps": None, "daily": None}

CANDLE_CN = {"early_seal": "早盘封板", "seal": "封板", "late_seal": "尾盘封板",
             "broken": "炸板", "late_surge": "尾盘抢筹", "late_dump": "尾盘砸盘"}

def _matrix_cards(cross_data, cross_hit, sig_states, sig_err, items):
    """个股全景页「方法论矩阵」卡片组（纯函数：数据进 → 卡片 HTML 列表出）。

    A 段：遍历 cross_poll.STRATEGIES 注册表逐策略出卡——命中显示
    label+方向色（long→buy / short→avoid / neutral→watch），未命中
    mute 半透明；新增策略只改 cross_poll.STRATEGIES，本函数零改。
    B 段：本地附加卡（实时 4 策略持仓 → 锚点④ / JEV → ⑤ / AI → ⑥），
    不属于 cross 聚合口径，故不进注册表。

    参数：cross_data=build_cross() 聚合结果（含 strategies 注册表镜像）、
    cross_hit=该票在 stocks[] 的条目或 None、sig_states={sn: 多头|空头|
    空仓}、sig_err=实时计算失败原因、items=JEV 已判断新闻列表。
    """
    import html as _h
    e = _h.escape
    mcards = []
    _dir_cls = {"long": "buy", "short": "avoid", "neutral": "watch"}
    hits_by_sid = {h["sid"]: h for h in (cross_hit or {}).get("hits", [])}
    for s in cross_data.get("strategies", []):
        hit = hits_by_sid.get(s["id"])
        icon = s.get("icon") or "🧪"
        if hit:
            extra = (e(str(hit["dt"])) + " · " if hit.get("dt") else "")
            mcards.append(
                "<a class='mcard' href='%s'>"
                "<div class='mt'>%s %s</div>"
                "<div class='mv %s'>%s</div>"
                "<div class='mu'>%s→ 工作台</div></a>"
                % (s["url"], icon, e(str(s["name"])),
                   _dir_cls.get(hit["dir"], ""), e(str(hit["label"])),
                   extra))
        else:
            mcards.append(
                "<a class='mcard mute' href='%s'>"
                "<div class='mt'>%s %s</div>"
                "<div class='mv'>未命中</div>"
                "<div class='mu'>→ 工作台</div></a>"
                % (s["url"], icon, e(str(s["name"]))))
    # B1. 4 策略实时持仓（实时计算，比快照口径更细 → 锚点 ④）
    if sig_states:
        _nl = sum(1 for v in sig_states.values() if v == "多头")
        _cls = "buy" if _nl >= 3 else ("avoid" if _nl == 0 else "")
        mcards.append(
            "<a class='mcard' href='#sig4'>"
            "<div class='mt'>📈 实时持仓 · 4策略</div>"
            "<div class='mv %s'>%d/4 多头</div>"
            "<div class='mu'>↓ 见下文 ④</div></a>" % (_cls, _nl))
    else:
        mcards.append(
            "<a class='mcard mute' href='#sig4'>"
            "<div class='mt'>📈 实时持仓 · 4策略</div>"
            "<div class='mv'>%s</div>"
            "<div class='mu'>↓ 见下文 ④</div></a>" % (sig_err or "—"))
    # B2. JEV 消息面（→ 锚点 ⑤）
    if items:
        _n_bull = sum(1 for it in items if it["bias_cn"] == "利多")
        _n_bear = sum(1 for it in items if it["bias_cn"] == "利空")
        _dom = ("利多" if _n_bull > _n_bear else
                "利空" if _n_bear > _n_bull else "分歧")
        _cls = ("buy" if _dom == "利多" else
                "avoid" if _dom == "利空" else "watch")
        mcards.append(
            "<a class='mcard' href='#jev'>"
            "<div class='mt'>⚖️ JEV 消息面</div>"
            "<div class='mv %s'>%s · %d 条</div>"
            "<div class='mu'>↓ 见下文 ⑤</div></a>"
            % (_cls, _dom, len(items)))
    else:
        mcards.append(
            "<a class='mcard mute' href='/bt-jev'>"
            "<div class='mt'>⚖️ JEV 消息面</div>"
            "<div class='mv'>近7天无判断</div>"
            "<div class='mu'>→ JEV 主页</div></a>")
    # B3. AI 深度分析（→ 锚点 ⑥）
    mcards.append(
        "<a class='mcard' href='#ai'>"
        "<div class='mt'>🤖 AI 深度分析</div>"
        "<div class='mv'>即时跑 · 2-4 分钟</div>"
        "<div class='mu'>↓ 见下文 ⑥</div></a>")
    return mcards

def _stock_chip_summary(code, px):
    """个股筹码摘要（K239 结论卡用）：只读本地 cyq_chips parquet 最新
    交易日（不在线拉，避免页首阻塞），avg=筹码加权均成本，
    winner=现价下方筹码占比。返回 {d, avg, winner, pos} 或 None。"""
    import re as _re_chip
    m6 = _re_chip.search(r"(\d{6})", str(code or ""))
    if not m6:
        return None
    d6 = m6.group(1)
    ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
               if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return None
    cfile = os.path.join(STATION, "quant_backtest", "data", "tushare",
                         "cyq_chips", ts_code.replace(".", "_") + ".parquet")
    try:
        import pandas as _pd_chip
        if not os.path.exists(cfile):
            return None
        df = _pd_chip.read_parquet(cfile)
        if df is None or not len(df):
            return None
        last = str(df["trade_date"].max())
        rows = df[df["trade_date"].astype(str) == last]
        pr = [(float(p), float(pc)) for p, pc in
              zip(rows["price"], rows["percent"]) if float(p) > 0]
        if not pr:
            return None
        avg = round(sum(p * pc for p, pc in pr) / 100.0, 2)
        winner = pos = None
        try:
            close = round(float(px), 2)
        except (TypeError, ValueError):
            close = None
        if close:
            winner = round(sum(pc for p, pc in pr if p <= close), 1)
            pos = round((close / avg - 1) * 100, 1) if avg else None
        return {"d": "%s-%s-%s" % (last[:4], last[4:6], last[6:8]),
                "avg": avg, "winner": winner, "pos": pos}
    except Exception:
        return None

def _stock_sector_rows(code, limit=4):
    """个股 → 所属板块行（K239 板块联动）：[{sector, idx, chg1, chg5,
    rps50}...]，按 RPS50 降序取前 limit。

    数据源（全部进程缓存 1 小时）：
      tdx_sector_def（板块定义，排除 stat/region）+ tdx_sector_member
      （成分，con_code=6 位纯码）→ 个股倒排；
      reports/sector_effect.json → 板块 RPS50；
      tdx_sector_daily → 板块指数当日/5 日涨跌。
    异常/无数据返回 []（页面安全降级，不阻塞个股页）。"""
    import time as _t_sec
    m6 = "".join(ch for ch in str(code or "") if ch.isdigit())[-6:]
    if len(m6) != 6:
        return []
    now = _t_sec.time()
    try:
        if _SEC_CACHE["rev"] is None or now - _SEC_CACHE["ts"] > 3600:
            import pandas as _pd_sec
            import glob as _glob_sec
            tdx = os.path.join(STATION, "quant_backtest", "data", "tdx")

            def _rp(tbl, cols=None):
                parts = sorted(_glob_sec.glob(
                    os.path.join(tdx, tbl, "part_*.parquet")))
                if not parts:
                    return _pd_sec.DataFrame()
                return _pd_sec.concat(
                    [_pd_sec.read_parquet(p, columns=cols) for p in parts],
                    ignore_index=True)

            seg = _rp("tdx_sector_def", ["code", "name", "seg"])
            seg = seg[~seg["seg"].isin({"stat", "region"})] \
                if len(seg) else seg
            name2idx = {str(n): str(c) for c, n in
                        zip(seg["code"], seg["name"])} if len(seg) else {}
            mem = _rp("tdx_sector_member", ["block_name", "con_code"])
            rev = {}
            if len(mem):
                for b, cc in zip(mem["block_name"], mem["con_code"]):
                    ic = name2idx.get(str(b))
                    if not ic:
                        continue          # 只认有指数的板块（881 行业等）
                    rev.setdefault(str(cc).zfill(6), []).append(
                        (str(b), ic))
            eff = _read_json(os.path.join(STATION, "quant_backtest",
                                          "reports",
                                          "sector_effect.json")) or {}
            rps = {s.get("sector"): s.get("rps50")
                   for s in (eff.get("sectors") or []) if s.get("sector")}
            daily = _rp("tdx_sector_daily",
                        ["code", "trade_date", "close"])
            _SEC_CACHE.update({"rev": rev, "rps": rps,
                               "daily": daily if len(daily) else None,
                               "ts": now})
        rev, rps, daily = (_SEC_CACHE["rev"], _SEC_CACHE["rps"],
                           _SEC_CACHE["daily"])
        hits = (rev or {}).get(m6) or []
        if not hits:
            return []
        rows = []
        for bname, ic in hits:
            chg1 = chg5 = None
            if daily is not None:
                sub = daily[daily["code"].astype(str) == str(ic)]
                if len(sub) >= 2:
                    sub = sub.sort_values("trade_date")
                    cl = sub["close"].astype(float).tolist()
                    if cl[-1]:
                        chg1 = (cl[-1] / cl[-2] - 1) * 100 if cl[-2] else None
                        chg5 = ((cl[-1] / cl[-6] - 1) * 100
                                if len(cl) >= 6 and cl[-6] else None)
            rows.append({"sector": bname, "idx": ic, "chg1": chg1,
                         "chg5": chg5, "rps50": rps.get(bname)})
        rows = [r for r in rows
                if r["chg1"] is not None or r["rps50"] is not None]
        rows.sort(key=lambda r: (r["rps50"] if r["rps50"] is not None
                                 else -1), reverse=True)
        return rows[:limit]
    except Exception:
        return []

CACHE_TTL = 20
