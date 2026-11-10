# -*- coding: utf-8 -*-
"""盘前分析引擎：六层框架（数据源为 tushare + zhice-db + news.db JEV）。
层1 外围联动（指数/汇率缺源标注） · 层2 政策宏观（CPI/PPI+政策新闻JEV）
层3 流动性（两融/北向/Shibor） · 层4 板块联动（自选美股行业代理→A股映射）
层5 情绪资金（JEV聚合+宽度） · 层6 量化技术与风险预警
用法: python premarket.py [--push]
"""

import argparse
import glob
import logging
import html
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rps_resonance as RR  # 复用 _ts/_db_query/_page/pill 导航

try:
    import tdx_read as tdxr  # TDX 数据仓本地读取层（data/tdx/）
except Exception:
    tdxr = None

REPORTS = os.path.join(HERE, "reports")
SNAP = os.path.join(REPORTS, "rps_latest.json")
NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")

# ── 层1 外围指数：代码/名称/权重（tushare index_global 实测可用代码，
#    恒生科技无代码，权重并入恒指） ──────────────────────────
GLOBAL_IDX = [
    ("IXIC", "纳斯达克综合", 0.32),
    ("SPX", "标普500", 0.26),
    ("DJI", "道琼斯", 0.10),
    ("FTSE", "英国富时", 0.10),
    ("GDAXI", "德国DAX", 0.10),
    ("HSI", "恒生指数", 0.12),
]

# ── 层4 板块联动：美股行业分组 → A股映射 ─────────────────
# K91 中概走港股二次上市：zhice-db US universe 不含中概（仅 BIDU 有
# 注册），HK 侧 7 家二次上市全有且数据更新更及时（港股 10-02 vs 美股
# 10-01）；PDD 无港股上市，留美股代码。asof 透出行内实际数据日期。
SECTOR_MAP = {
    "半导体": {
        "us": ["NVDA", "AMD", "AVGO", "INTC", "TSM", "QCOM", "MU"],
        "cn": "A股半导体/电子",
    },
    "科技巨头": {
        "us": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"],
        "cn": "沪深300大盘科技",
    },
    "AI软件": {"us": ["PLTR", "CRM", "ORCL", "NOW", "SNOW"], "cn": "A股软件/计算机"},
    "汽车新能源": {
        "us": ["TSLA", "RIVN"],
        "hk": ["09866", "09868", "02015"],  # 蔚来/小鹏/理想
        "cn": "新能源车产业链",
    },
    "互联网中概": {
        "hk": ["09988", "09618", "09999", "09888"],  # 阿里/京东/网易/百度
        "cn": "恒生科技/中概互联",
    },
}


def _clip(v, lo=-100, hi=100):
    return max(lo, min(hi, v))


def _parse_stocks(s):
    """news.stocks 列（JSON 数组，如 ["600853.SH"]）→ 代码列表（坏值空）。"""
    try:
        v = json.loads(s or "[]")
        return [str(x) for x in v][:6] if isinstance(v, list) else []
    except Exception:
        return []


def _stock_a(code, label=None):
    """行内个股下钻链接（11-10 叠加改版）。

    旧版指向门户个股页 /kg/stock/（门户已下线，h5_pack 里被去壳成纯文本，
    个股名变不可点）。改为站内深链 → 结构日报个股证据卡（pool_report_h5
    的 findRow 按 300607.SZ 全码匹配，Q.code 自动打开详情）。
    """
    c = str(code)
    return "<a href='pool_report_h5.html?code=%s'>%s</a>" % (
        c,
        html.escape(label or c),
    )


def _dim_tds(t, d0=None):
    """每票多维三列：板块 / 总市值(亿) / 龙虎榜（游资上榜，悬浮看
    上榜原因；净买红/净卖绿，带 /bt/lhb 下钻链接）。缺数据显示 —。
    盘前/盘后共用。"""
    lb = t.get("lhb")
    ny = (lb or {}).get("net_yi")
    txt = ("榜 %+.2f亿" % ny) if lb and ny is not None else ("榜" if lb else "—")
    if lb:
        txt = "<a href='/bt/lhb?date=%s'>%s</a>" % (d0 or "", txt)
    return (
        "<td class='dim'>%s</td>"
        "<td class='dim'>%s</td>"
        "<td class='%s' title='%s'>%s</td>"
        % (
            html.escape(t.get("ind") or "—"),
            t["mv"] if t.get("mv") is not None else "—",
            "buy" if (ny or 0) > 0 else ("avoid" if (ny or 0) < 0 else "dim"),
            html.escape((lb or {}).get("reason") or ""),
            txt,
        )
    )


def _tv_tds(t):
    """换手率 / 量比 列（量能刻画：巨量换手 vs 缩量秒板）。缺 —。"""
    turn, vr = t.get("turn"), t.get("vr")
    return "<td class='dim'>%s</td>" % (
        "%s%% / %s" % (turn if turn is not None else "—", vr if vr is not None else "—")
    )


def _style_td(t):
    """涨停方式列：一字（顶死）/ T字（盘中开）/ 实体（换手板）。"""
    s = t.get("style")
    cls = "buy" if s == "一字" else ("avoid" if s == "T字" else "dim")
    return "<td class='%s'>%s</td>" % (cls, s or "—")


# ── 模拟数据源（无权限接口的占位；锚点按需校准，拿到真实源即替换） ──
MOCK_ANCHOR = {
    "USDCNH": 7.12,  # 美元/离岸人民币 锚点
    "VIX": 17.0,
}  # VIX 恐慌指数 锚点
MOCK_VOL = {"USDCNH": 0.0015, "VIX": 0.05}  # 日波动率量级


def _mock_series(key, days=14):
    """确定性随机游走：日期做种子 → 同一交易日多次刷新结果稳定。"""
    import random

    rng = random.Random(key + datetime.now().strftime("%Y%m%d"))
    v = MOCK_ANCHOR[key] * (1 + rng.uniform(-0.02, 0.02))
    out = []
    for _ in range(days):
        v *= 1 + rng.gauss(0, MOCK_VOL[key])
        out.append(v)
    return out


def _mock_quote(key):
    s = _mock_series(key)
    return {
        "val": round(s[-1], 4 if key == "USDCNH" else 1),
        "pct": round((s[-1] / s[-2] - 1) * 100, 2),
        "chg5": round((s[-1] / s[-6] - 1) * 100, 2),
        "source": "sim",
    }


def layer1_global():
    """外围市场联动因子：隔夜指数涨跌加权 → [-100,100]。
    汇率为模拟源（展示不进分）；商品(油/金/铜)缺权限。"""
    st = (datetime.now() - timedelta(days=12)).strftime("%Y%m%d")
    rows, score, wsum = [], 0.0, 0.0
    for code, cn, w in GLOBAL_IDX:
        try:
            items, fields = RR._ts(
                "index_global", ts_code=code, start_date=st, end_date="20991231"
            )
        except Exception:
            rows.append({"name": cn, "pct": None})
            continue
        if len(items) < 2:
            rows.append({"name": cn, "pct": None})
            continue
        i = {f: k for k, f in enumerate(fields)}
        # index_global 按日期降序返回；防御性再排一次取最新两根
        pts = sorted(
            [
                (x[i["trade_date"]], float(x[i["close"]]))
                for x in items
                if x[i["close"]]
            ],
            reverse=True,
        )
        if len(pts) < 2:
            rows.append({"name": cn, "pct": None})
            continue
        pct = (pts[0][1] / pts[1][1] - 1) * 100
        score += w * _clip(pct * 18)  # 1% 涨跌 ≈ 18 分
        wsum += w
        rows.append({"name": cn, "pct": round(pct, 2), "date": pts[0][0]})
    fx = _mock_quote("USDCNH")
    rows.append(
        {"name": "美元/离岸人民币", "pct": fx["pct"], "val": fx["val"], "sim": True}
    )
    # BTC 隔夜风险偏好（数据仓 crypto_daily 现算，7×24，展示不进分）
    try:
        import datamart as dm

        cs = (datetime.now() - timedelta(days=6)).strftime("%Y%m%d")
        bc = dm.read(
            "crypto_daily",
            start=cs,
            end="20991231",
            codes=["BTC/USDT"],
            fields=["ts_code", "trade_date", "close", "pct_chg"],
        )
        if bc is not None and not bc.empty:
            r = bc.sort_values("trade_date").iloc[-1]
            out_btc = {
                "pct": round(float(r["pct_chg"]), 2),
                "close": round(float(r["close"]), 0),
                "date": str(r["trade_date"])[:8],
            }
            rows.append(
                {
                    "name": "BTC/USDT（风险偏好代理）",
                    "pct": out_btc["pct"],
                    "date": out_btc["date"],
                }
            )
            btc = out_btc
        else:
            btc = None
    except Exception:
        btc = None
    return {
        "score": round(score / wsum) if wsum else None,
        "rows": rows,
        "fx": fx,
        "btc": btc,
        "note": "商品(油/金/铜)缺权限 · 汇率为模拟源（不进分，"
        "人民币贬值利空进出口板块，仅供链路参考）· BTC "
        "7×24 连续交易，为全球风险偏好代理（不进分）",
    }


def layer2_policy():
    """政策宏观因子：CPI/PPI 最新 + 近3天政策类新闻 JEV 聚合。"""
    out = {"macro": [], "jev": None, "news": []}
    m = (datetime.now() - timedelta(days=45)).strftime("%Y%m")
    for api, name, col in (
        ("cn_cpi", "CPI 同比", "nt_yoy"),
        ("cn_ppi", "PPI 同比", "ppi_yoy"),
    ):
        try:
            items, fields = RR._ts(api, month=m)
            if items:
                idx = fields.index(col) if col in fields else None
                if idx is not None and items[0][idx] is not None:
                    out["macro"].append(
                        {"name": name, "val": items[0][idx], "month": m}
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "premarket.py", exc
            )
    # 政策/央行/监管新闻 JEV（近3天）
    import sqlite3

    kw = (
        "政策",
        "央行",
        "监管",
        "利率",
        "关税",
        "MLF",
        "降准",
        "降息",
        "加息",
        "美联储",
        "政治局",
        "会议",
    )
    since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        hits = []
        for title, j, content, stocks in db.execute(
            "SELECT title, jev, content, stocks FROM news "
            "WHERE jev IS NOT NULL AND created_at>=? "
            "ORDER BY id DESC LIMIT 2000",
            (since,),
        ):
            if title and any(k in title for k in kw):
                hits.append((title, json.loads(j), content, stocks))
        db.close()
        bull = bear = neu = 0
        for title, jv in hits:
            b = jv.get("bias")
            if b == "bull":
                bull += 1
            elif b == "bear":
                bear += 1
            else:
                neu += 1
        n = len(hits)
        if n:
            out["jev"] = {
                "n": n,
                "bull": bull,
                "bear": bear,
                "neu": neu,
                "score": round(_clip((bull - bear) * 100 / n)),
            }
        out["news"] = [
            {
                "title": t[:44],
                "bias_cn": j.get("bias_cn"),
                "conf": j.get("conf"),
                "content": (c or "")[:280],
                "stocks": _parse_stocks(s),
            }
            for t, j, c, s in hits[:5]
        ]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def layer3_liquidity():
    """流动性宽松度因子：两融环比 + 北向净流入 + Shibor。"""
    out = {}
    st = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
    # 两融（沪深合计；深交所晚 1 天 → 只用「两个交易所都齐」的完整日）
    try:
        items, fields = RR._ts("margin", start_date=st, end_date="20991231")
        i = {f: k for k, f in enumerate(fields)}
        tot, cnt = {}, {}
        for x in items:
            d = x[i["trade_date"]]
            tot[d] = tot.get(d, 0) + float(x[i["rzye"]] or 0)
            cnt[d] = cnt.get(d, 0) + 1
        full = [d for d in tot if cnt[d] == max(cnt.values())]
        ds = sorted(full)[-2:]
        if len(ds) == 2:
            chg = (tot[ds[1]] / tot[ds[0]] - 1) * 100
            out["margin_yoy"] = round(chg, 3)
            out["margin_yi"] = round(tot[ds[1]] / 1e8)
            out["margin_date"] = ds[1]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    # 北向（接口降序返回，取最新一行；north_money 万元口径 → 亿）
    try:
        items, fields = RR._ts("moneyflow_hsgt", start_date=st, end_date="20991231")
        i = fields.index("north_money")
        pts = sorted(
            [(x[0], float(x[i])) for x in items if x[i] is not None], reverse=True
        )
        if pts:
            d, v = pts[0]
            out["north_yi"] = round(v / 10000)
            out["north_date"] = d
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    # Shibor O/N（降序返回，取最新；14 日变化 = 最新 - 最旧）
    try:
        items, fields = RR._ts("shibor", start_date=st, end_date="20991231")
        i = fields.index("on")
        pts = sorted(
            [(x[0], float(x[i])) for x in items if x[i] is not None], reverse=True
        )
        if pts:
            out["shibor_on"] = pts[0][1]
            out["shibor_chg"] = round(pts[0][1] - pts[-1][1], 3)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    s = 0
    if "margin_yoy" in out:
        s += _clip(out["margin_yoy"] * 800)  # 日环比 0.1% ≈ 80
    if "north_yi" in out:
        s += _clip(out["north_yi"] * 0.8)  # 百亿流入≈满格
    if "shibor_chg" in out:
        s -= _clip(out["shibor_chg"] * 60)  # 利率上行=紧
    out["score"] = round(_clip(s / 3))
    return out


def layer4_sector():
    """板块联动：自选美股/港股行业均涨跌 → A股映射（zhice-db 无ETF，
    用行业成分股聚合代理）。K91 中概走港股二次上市（见 SECTOR_MAP 注），
    asof=组内最新bar日期（美股/港股同步节奏不同，透明标注防误读）。"""
    try:
        days = 6
        st = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        us_syms = sorted({s for g in SECTOR_MAP.values() for s in g.get("us") or []})
        hk_syms = sorted({s for g in SECTOR_MAP.values() for s in g.get("hk") or []})
        rows = RR._db_query(
            "SELECT symbol, trade_date, close FROM v2_bar_daily "
            "WHERE ((market='US' AND symbol = ANY(%s)) OR "
            "(market='HK' AND symbol = ANY(%s))) AND trade_date >= %s "
            "AND close IS NOT NULL ORDER BY symbol, trade_date",
            [us_syms, hk_syms, st],
        )
        px, asof = {}, {}
        for sym, td, c in rows:
            px.setdefault(sym, []).append(float(c))
            asof[sym] = str(td)
        out = []
        for sec, g in SECTOR_MAP.items():
            syms = (g.get("us") or []) + (g.get("hk") or [])
            pcts = []
            for s in syms:
                v = px.get(s)
                if v and len(v) >= 2:
                    pcts.append((v[-1] / v[-2] - 1) * 100)
            if pcts:
                avg = sum(pcts) / len(pcts)
                out.append(
                    {
                        "sector": sec,
                        "cn": g["cn"],
                        "avg": round(avg, 2),
                        "n": len(pcts),
                        "m": len(syms),
                        "asof": max(asof[s] for s in syms if s in asof),
                        "score": round(_clip(avg * 18)),
                    }
                )
        return out
    except Exception:
        return []


def layer5_sentiment(snap):
    """市场情绪因子：JEV 近3天聚合 + A股宽度闸门。"""
    import sqlite3

    out = {}
    since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        bull = bear = neu = 0
        for (j,) in db.execute(
            "SELECT jev FROM news WHERE jev IS NOT NULL AND created_at>=? LIMIT 2000",
            (since,),
        ):
            b = json.loads(j).get("bias")
            if b == "bull":
                bull += 1
            elif b == "bear":
                bear += 1
            else:
                neu += 1
        db.close()
        n = bull + bear + neu
        if n:
            out = {
                "n": n,
                "bull": bull,
                "bear": bear,
                "neu": neu,
                "score": round(_clip((bull - bear) * 100 / n)),
            }
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    env = None
    for e in snap.get("envs") or []:  # rps_latest.json envs 列表
        if "A股" in str(e.get("label", "")):
            env = {
                "score": e.get("score"),
                "safe": e.get("safe"),
                "verdict": e.get("state", ""),
                "breath": round((e.get("breadth") or 0) * 100),
                "med20": round((e.get("ret20") or 0) * 100, 1),
            }
            break
    if env:
        out["cn_env"] = env
    out["vix"] = _mock_quote("VIX")  # 模拟源：仅展示/预警参考
    return out


# ── 层5+ 赚钱效应 / 层3+ 量能位置（数据仓 daily/index_daily 现算，零 API） ──
def _closed_trade_days():
    """已收盘交易日序列（交易日历截到最近已收盘日，剔未来日期）。"""
    import bisect
    import datamart as dm

    days = dm._recent_trade_days()
    exp = dm._expected_trade_day(days)
    return days[: bisect.bisect_right(days, exp)] if exp else []


def _limit_thr(code, is_st):
    """涨跌停判定阈值（pct_chg 口径，留 0.2pct 容差防四舍五入漏判）：
    主板 ±10% · 创业/科创 ±20% · 北交 ±30% · ST ±5%。"""
    c = str(code)
    if is_st:
        return 4.8
    if c[:3] in ("300", "301", "688", "689"):
        return 19.8
    if c[0] in ("8", "4") or c[:2] == "92":
        return 29.7
    return 9.8


def money_effect():
    """赚钱效应（情绪层补充）：昨涨停/跌停家数 · 最高连板 · 涨跌家数比 ·
    Top 涨停股（量排序）。口径：收盘涨停（pct_chg 阈值近似，一字/盘中
    炸板不区分）；上市≤5日新股无涨跌幅限制，剔除；连板=窗口内连续
    收盘涨停天数（12 交易日窗口，超长连板截断为 12）；温度计分位=
    涨停家数/炸板率在近 60 交易日的百分位（≥40 日样本才输出）。"""
    out = {}
    try:
        import datamart as dm

        days = _closed_trade_days()
        if len(days) < 4:
            return out
        win = days[-62:]
        d0, d1 = win[-1], win[-2]
        df = dm.read(
            "daily",
            start=win[0],
            end=d0,
            fields=[
                "ts_code",
                "trade_date",
                "close",
                "high",
                "open",
                "low",
                "pre_close",
                "pct_chg",
                "amount",
                "vol",
            ],
        )
        if df is None or df.empty:
            return out
        df = df.copy()
        df["d8"] = df["trade_date"].astype(str).str[:8]
        sb = dm.read("stock_basic", fields=["ts_code", "name", "list_date"])
        st, names, newbk = set(), {}, set()
        if sb is not None and not sb.empty:
            for _, r in sb.iterrows():
                nm = str(r.get("name") or "")
                names[r["ts_code"]] = nm
                if "ST" in nm.upper():
                    st.add(r["ts_code"])
            # 新股（上市≤5个交易日）无涨跌幅限制；list_date 容错 NaN
            ld = sb["list_date"].astype(str)
            newbk = set(sb.loc[ld.str.match(r"\d{8}") & (ld >= win[-6]), "ts_code"])
        df = df[~df["ts_code"].isin(newbk)]
        df["thr"] = [_limit_thr(c, c in st) for c in df["ts_code"]]
        d0r = df[df["d8"] == d0]
        d1r = df[df["d8"] == d1]
        pc = d0r["pct_chg"]
        # 涨停幅硬上限 31.5%：拦截无涨跌幅限制的新股/异常（stock_basic
        # 未收录时兜底，北交所 30% 为合法最大板）
        CAP = 31.5
        lu_mask = (d0r["pct_chg"] >= d0r["thr"]) & (d0r["pct_chg"] <= CAP)
        ld_mask = (d0r["pct_chg"] <= -d0r["thr"]) & (d0r["pct_chg"] >= -CAP)
        out = {
            "date": d0,
            "n_stock": int(len(d0r)),
            "up": int((pc > 0).sum()),
            "dn": int((pc < 0).sum()),
            "flat": int((pc == 0).sum()),
            "limit_up": int(lu_mask.sum()),
            "limit_dn": int(ld_mask.sum()),
        }
        # 连板：窗口内每日涨停集合 → 自 d0 回溯连续天数
        lu_all = (df["pct_chg"] >= df["thr"]) & (df["pct_chg"] <= CAP)
        win_lu = df[lu_all].groupby("d8")["ts_code"].apply(set).to_dict()
        lu_codes = set(d0r.loc[lu_mask, "ts_code"])
        streak = {}
        for code in lu_codes:
            k = 0
            for d in reversed(win[-12:]):
                if code in win_lu.get(d, ()):
                    k += 1
                else:
                    break
            streak[code] = k
        rows = d0r.loc[lu_mask].set_index("ts_code")
        # 连板天梯：streak>=2 分层，每层按成交额取3只（盘后复盘用）
        lad = {}
        for c, k in streak.items():
            if k >= 2:
                lad.setdefault(k, []).append(c)
        out["ladder"] = [
            {
                "k": k,
                "n": len(v),
                "top": [
                    {
                        "code": c,
                        "name": names.get(c, c),
                        "pct": round(float(rows.loc[c, "pct_chg"]), 2),
                        "amt_yi": round(float(rows.loc[c, "amount"]) / 1e5, 1),
                    }
                    for c in sorted(v, key=lambda x: -float(rows.loc[x, "amount"]))[:3]
                ],
            }
            for k, v in sorted(lad.items(), reverse=True)
        ]
        top = sorted(
            (
                {
                    "code": c,
                    "name": names.get(c, c),
                    "streak": streak[c],
                    "pct": round(float(rows.loc[c, "pct_chg"]), 2),
                    "amt_yi": round(float(rows.loc[c, "amount"]) / 1e5, 1),
                }
                for c in lu_codes
            ),
            key=lambda x: -x["amt_yi"],
        )
        out["top"] = top[:5]
        # 全量涨停清单（门户展开全览用，不截断）
        out["lu_all"] = top
        # 多维补充：板块（limit_list_d.industry）/ 市值（bak_basic 股本×
        # 收盘）/ 龙虎榜（ts_top_list 上榜=游资活跃，单位元）
        lld = dm.read("limit_list_d", start=d0, end=d0, fields=["ts_code", "industry"])
        ind = {}
        if lld is not None and not lld.empty:
            ind = {
                r["ts_code"]: str(r.get("industry") or "").strip()
                for r in lld.to_dict("records")
                if r.get("industry")
            }
        shs = {}
        bb = dm.read(
            "bak_basic",
            start=d0,
            end=d0,
            fields=["ts_code", "total_share", "float_share"],
        )
        if bb is not None and not bb.empty:
            for r in bb.to_dict("records"):
                t_, f_ = r.get("total_share"), r.get("float_share")
                shs[r["ts_code"]] = (
                    float(t_) if t_ is not None and t_ == t_ else None,
                    float(f_) if f_ is not None and f_ == f_ else None,
                )
        lhb = {}
        try:
            tl = dm.read(
                "ts_top_list",
                start=d0,
                end=d0,
                fields=["ts_code", "net_amount", "reason"],
            )
            if tl is not None and not tl.empty:
                for r in tl.to_dict("records"):
                    v = r.get("net_amount")
                    lhb[r["ts_code"]] = {
                        "net_yi": round(float(v) / 1e8, 2)
                        if v is not None and v == v
                        else None,
                        "reason": str(r.get("reason") or "")[:16],
                    }
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "premarket.py", exc
            )

        def _dim(code, rs):
            """每票四维：ind 板块 / mv 总市值 / cmv 流通市值（亿）/
            lhb 龙虎榜（None=未上榜）。缺数据容错 None。
            bak_basic 股本为亿股口径（实测 600059=9.12），市值=亿股×元。
            """
            try:
                cl = float(rs.loc[code, "close"])
            except Exception:
                cl = None
            t_, f_ = shs.get(code, (None, None))
            return {
                "ind": ind.get(code) or None,
                "mv": round(t_ * cl, 1) if (t_ and cl) else None,
                "cmv": round(f_ * cl, 1) if (f_ and cl) else None,
                "lhb": lhb.get(code),
            }

        for t_ in top:
            t_.update(_dim(t_["code"], rows))
        # 涨停方式 / 换手率 / 量比（三板类共用 helper）
        # 换手率%(数值)=vol手×100股/(流通亿股×1e8)×100=vol/f_/1e4；
        # 量比=当日额/前5日均额
        prev5 = (
            df[df["d8"] < d0]
            .sort_values(["ts_code", "d8"])
            .groupby("ts_code")["amount"]
            .apply(lambda s: s.iloc[-5:].mean())
        )

        def _tv(rs, c):
            """换手率% + 量比（换手/量能刻画：巨量换手 vs 缩量秒板）。"""
            try:
                v = float(rs.loc[c, "vol"])
            except Exception:
                v = None
            f_ = shs.get(c, (None, None))[1]
            turn = round(v / f_ / 1e4, 1) if (v and f_) else None
            vr = None
            try:
                a0, a5 = float(rs.loc[c, "amount"]), float(prev5[c])
                vr = round(a0 / a5, 1) if a5 else None
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "premarket.py", exc
                )
            return {"turn": turn, "vr": vr}

        def _style(rs, c):
            """涨停方式：一字（开/低均触板顶死）/ T字（开触板盘中开）
            / 实体（开未触板收盘封）。"""
            try:
                r = rs.loc[c]
                thr = float(r["thr"])
                pc = float(r["pre_close"])
                op = float(r["open"]) / pc * 100 - 100
                lo = float(r["low"]) / pc * 100 - 100
                if lo >= thr - 0.05:
                    return "一字"
                return "T字" if op >= thr - 0.05 else "实体"
            except Exception:
                return None

        for t_ in top:
            t_.update(_tv(rows, t_["code"]))
            t_["style"] = _style(rows, t_["code"])
        # 跌停全量清单（对称涨停：绿色区展开，含多维）
        dr = d0r.loc[ld_mask].set_index("ts_code")
        _ld = []
        for c in dr.index:
            _r = {
                "code": c,
                "name": names.get(c, c),
                "pct": round(float(dr.loc[c, "pct_chg"]), 2),
                "amt_yi": round(float(dr.loc[c, "amount"]) / 1e5, 1),
            }
            _r.update(_dim(c, dr))
            _r.update(_tv(dr, c))
            _ld.append(_r)
        out["ld_all"] = sorted(_ld, key=lambda x: -x["amt_yi"])
        if streak:
            mc = max(streak, key=streak.get)
            out["max_streak"] = streak[mc]
            out["streak_name"] = names.get(mc, mc)
        # 20cm 占比（创业/科创涨停弹性偏好）
        cm = sum(1 for c in lu_codes if str(c)[:3] in ("300", "301", "688", "689"))
        out["cm20_share"] = round(cm * 100 / len(lu_codes), 1) if lu_codes else 0
        # 炸板率：盘中触板（high/pre_close≥阈值）但收盘未封住
        hi_pct = (d0r["high"].astype(float) / d0r["pre_close"].astype(float) - 1) * 100
        touch = (hi_pct >= d0r["thr"]) & (hi_pct <= CAP)
        broke = touch & ~lu_mask
        out["broke_n"] = int(broke.sum())
        _dn = int(broke.sum() + lu_mask.sum())
        out["broke_rate"] = round(broke.sum() * 100 / _dn, 1) if _dn else 0
        # 炸板全量清单（门户展开：盘中触板未封住，按收盘涨幅降序）
        br = d0r.loc[broke].set_index("ts_code")
        _brk = []
        for c in br.index:
            _r = {
                "code": c,
                "name": names.get(c, c),
                "pct": round(float(br.loc[c, "pct_chg"]), 2),
                "amt_yi": round(float(br.loc[c, "amount"]) / 1e5, 1),
            }
            _r.update(_dim(c, br))
            _r.update(_tv(br, c))
            _brk.append(_r)
        out["broke_all"] = sorted(_brk, key=lambda x: -x["amt_yi"])
        # 昨涨停今表现（接力赚钱效应）+ 晋级率（昨涨停→今仍涨停）
        d1_lu = set(
            d1r.loc[(d1r["pct_chg"] >= d1r["thr"]) & (d1r["pct_chg"] <= CAP), "ts_code"]
        )
        if d1_lu:
            tod = d0r[d0r["ts_code"].isin(d1_lu)]
            if not tod.empty:
                out["ylu_avg"] = round(float(tod["pct_chg"].mean()), 2)
                out["ylu_med"] = round(float(tod["pct_chg"].median()), 2)
                out["promote_rate"] = round(len(d1_lu & lu_codes) * 100 / len(d1_lu), 1)
        # 两市成交额（千元→亿，含北交所；全市场口径）
        a0 = float(d0r["amount"].sum()) / 1e5
        a1 = float(d1r["amount"].sum()) / 1e5
        out["amt_yi"] = round(a0)
        out["amt_pct"] = round((a0 / a1 - 1) * 100, 1) if a1 else None
        # 温度计分位：涨停家数/炸板率在近60日的百分位（情绪周期定位）
        hist = [d for d in win[:-1] if d != d0]
        if len(hist) >= 40:
            df["hi_p"] = (
                df["high"].astype(float) / df["pre_close"].astype(float) - 1
            ) * 100
            df["t_flag"] = (df["hi_p"] >= df["thr"]) & (df["hi_p"] <= CAP)
            gd = df.groupby("d8")
            lu_n = gd.apply(
                lambda x: int(
                    ((x["pct_chg"] >= x["thr"]) & (x["pct_chg"] <= CAP)).sum()
                )
            )
            t_n = gd["t_flag"].sum()
            br_s = ((t_n - lu_n) / t_n * 100).dropna()
            h_lu = [float(lu_n.get(d, 0)) for d in hist]
            h_br = [float(br_s.get(d)) for d in hist if d in br_s.index]
            th_lu = (
                (sum(1 for h in h_lu if h <= out["limit_up"]) * 100.0 / len(h_lu))
                if h_lu
                else None
            )
            th_br = (
                (sum(1 for h in h_br if h <= out["broke_rate"]) * 100.0 / len(h_br))
                if len(h_br) >= 30
                else None
            )
            if th_lu is not None and th_br is not None:
                temp = th_lu - th_br  # 涨停分位高且炸板分位低 → 热
                out["th_lu"] = int(th_lu)
                out["th_br"] = int(th_br)
                out["th_temp"] = int(temp)
                out["th_label"] = (
                    "亢奋"
                    if temp >= 60
                    else "回暖"
                    if temp >= 25
                    else "冰点"
                    if temp <= -60
                    else "转弱"
                    if temp <= -25
                    else "常态"
                )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def volume_position():
    """量能与指数位置（流动性层补充）：上证/创业板指收盘距 MA20/MA60/
    MA120（index_daily 现算）+ 全市场成交额 5/20 日均量与量价背离
    （daily 现算）。位置为趋势坐标：正=均线上方。"""
    out = {}
    try:
        import datamart as dm

        days = _closed_trade_days()
        if len(days) < 62:
            return out
        # 指数均线位置（122 交易日 → MA120 年线）
        n_ix = min(122, len(days))
        ix = dm.read(
            "index_daily",
            start=days[-n_ix],
            end=days[-1],
            codes=["000001.SH", "399006.SZ"],
            fields=["ts_code", "trade_date", "close"],
        )
        sh_close = None
        if ix is not None and not ix.empty:
            for code, label in (("000001.SH", "上证指数"), ("399006.SZ", "创业板指")):
                s = (
                    ix[ix["ts_code"] == code]
                    .sort_values("trade_date")["close"]
                    .astype(float)
                )
                if len(s) < 60:
                    continue
                c = s.iloc[-1]
                pos = {
                    "close": round(c, 1),
                    "ma20": round((c / s.iloc[-20:].mean() - 1) * 100, 1),
                    "ma60": round((c / s.iloc[-60:].mean() - 1) * 100, 1),
                }
                if len(s) >= 120:
                    pos["ma120"] = round((c / s.iloc[-120:].mean() - 1) * 100, 1)
                out[label] = pos
                if code == "000001.SH":
                    sh_close = s.reset_index(drop=True)
            out["date"] = days[-1]
        # 均量与量能状态（daily amount 千元 → 亿，按交易日全市场求和）
        n_v = min(22, len(days))
        dv = dm.read(
            "daily", start=days[-n_v], end=days[-1], fields=["trade_date", "amount"]
        )
        if dv is not None and not dv.empty:
            amt = (
                dv.assign(d8=dv["trade_date"].astype(str).str[:8])
                .groupby("d8")["amount"]
                .sum()
                / 1e5
            )
            amt = amt.sort_index()
            if len(amt) >= 6:
                a0, ma5 = float(amt.iloc[-1]), float(amt.iloc[-5:].mean())
                out["amt_ma5"] = round(ma5)
                if len(amt) >= 20:
                    out["amt_ma20"] = round(float(amt.iloc[-20:].mean()))
                r5 = a0 / ma5 - 1
                out["vol_state"] = (
                    "放量" if r5 > 0.10 else "缩量" if r5 < -0.10 else "常量"
                )
                out["vol_pct"] = round(r5 * 100, 1)
                # 量价背离：上证5日涨跌 × 量5日均/前5日均 反向
                if sh_close is not None and len(sh_close) >= 11 and len(amt) >= 11:
                    p5 = float(sh_close.iloc[-1]) / float(sh_close.iloc[-6]) - 1
                    v5 = (
                        float(amt.iloc[-5:].mean()) / float(amt.iloc[-10:-5].mean()) - 1
                    )
                    if p5 * v5 < 0 and abs(p5) > 0.01:
                        out["div_note"] = (
                            "价%s量%s（5日 %+.1f%% vs 量 %+.1f%%）背离"
                            % (
                                "涨" if p5 > 0 else "跌",
                                "缩" if v5 < 0 else "放",
                                p5 * 100,
                                v5 * 100,
                            )
                        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def industry_flow():
    """A股行业主力资金净流入排名（moneyflow × stock_basic 聚合，零 API）。
    口径：最近收盘日全市场个股 net_mf_amount（万元，tushare 大单口径
    主力净流入）按 tushare industry 聚合为行业净额（亿元）；industry
    缺失的退市股归'未分类'（当日基本无记录，漏配≈0）。"""
    out = {}
    try:
        import datamart as dm

        days = _closed_trade_days()
        if not days:
            return out
        d0 = days[-1]
        mf = dm.read(
            "moneyflow",
            start=d0,
            end=d0,
            fields=["ts_code", "trade_date", "net_mf_amount"],
        )
        if mf is None or mf.empty:
            return out
        sb = dm.read("stock_basic", fields=["ts_code", "industry"])
        if sb is None or sb.empty:
            return out
        ind = dict(zip(sb["ts_code"], sb["industry"].fillna("未分类")))
        mf = mf.copy()
        mf["ind"] = [ind.get(c, "未分类") for c in mf["ts_code"]]
        g = mf.groupby("ind")["net_mf_amount"].agg(["sum", "count"]).reset_index()
        g.columns = ["ind", "sum", "n"]
        g["yi"] = (g["sum"] / 1e4).round(1)  # 万元 → 亿
        g = g.sort_values("yi", ascending=False)
        out = {
            "date": d0,
            "total_yi": round(float(g["yi"].sum()), 1),
            "n_ind": int(len(g)),
        }

        def _rows(df):
            return [
                {"ind": r["ind"], "yi": float(r["yi"]), "n": int(r["n"])}
                for _, r in df.iterrows()
            ]

        out["top"] = _rows(g.head(5))
        out["bottom"] = _rows(g.tail(5)[::-1])
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def margin_sentiment():
    """两融杠杆情绪（流动性层补充）：全市场融资余额 + 5日净变化。
    口径：margin 明细按日聚合（rzye 元→亿）；T+1 披露，最新为上一交
    易日。展示不进分（杠杆是助涨助跌放大器，非方向信号）。"""
    out = {}
    try:
        import datamart as dm

        days = _closed_trade_days()
        if len(days) < 7:
            return out
        win = days[-7:]
        df = dm.read(
            "margin",
            start=win[0],
            end=win[-1],
            fields=["ts_code", "trade_date", "rzye", "rqye"],
        )
        if df is None or df.empty:
            return out
        df["d8"] = df["trade_date"].astype(str).str[:8]
        g = df.groupby("d8")[["rzye", "rqye"]].sum() / 1e8
        # 覆盖率过滤：当日明细只同步了一部分时聚合值骤降，差分失真
        cnt = df.groupby("d8")["ts_code"].count()
        good = cnt[cnt >= cnt.max() * 0.6].index
        g = g[g.index.isin(good)].reindex(win).dropna()
        if len(g) < 2:
            return out
        d0 = g.index[-1]
        out["date"] = d0
        out["rzye_yi"] = round(float(g.loc[d0, "rzye"]))
        out["rqye_yi"] = round(float(g.loc[d0, "rqye"]), 1)
        if len(g) >= 6:
            p5 = g["rzye"].iloc[-6]
        else:
            p5 = g["rzye"].iloc[0]
        out["chg5_yi"] = round(float(g.loc[d0, "rzye"] - p5))
        out["chg5_pct"] = (
            round(float((g.loc[d0, "rzye"] / p5 - 1) * 100), 2) if p5 else None
        )
        # 逐日融资净流入（近3日，判断加/减杠杆节奏）
        chg = g["rzye"].diff().dropna()
        out["chg_recent"] = [round(float(x)) for x in chg.tail(3)]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def cb_sentiment():
    """可转债情绪温度计（情绪层补充）：等权涨跌中位 + 上涨占比 + 成交额。
    口径：cb_daily 全体转债 pct_chg 中位数（等权代理）、amount 万元→亿。"""
    out = {}
    try:
        import datamart as dm

        days = _closed_trade_days()
        if len(days) < 7:
            return out
        win = days[-7:]
        df = dm.read(
            "cb_daily",
            start=win[0],
            end=win[-1],
            fields=["ts_code", "trade_date", "pct_chg", "amount"],
        )
        if df is None or df.empty:
            return out
        df["d8"] = df["trade_date"].astype(str).str[:8]
        g = df.groupby("d8")
        med = g["pct_chg"].median().reindex(win).dropna()
        up_r = g["pct_chg"].apply(lambda s: (s > 0).mean() * 100)
        amt = (g["amount"].sum() / 1e4).reindex(win).dropna()
        if med.empty:
            return out
        d0 = med.index[-1]
        out["date"] = d0
        out["eq_pct"] = round(float(med.iloc[-1]), 2)
        out["up_ratio"] = round(float(up_r.get(d0, 0)), 1)
        out["amt_yi"] = round(float(amt.iloc[-1])) if d0 in amt.index else None
        if len(amt) >= 5:
            ma5 = float(amt.iloc[-5:].mean())
            out["amt_vs_ma5"] = (
                round((float(amt.iloc[-1]) / ma5 - 1) * 100, 1) if ma5 else None
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


# ── 层6 个股公告与研报（TDX tdx_info.db 本地读取，零 API） ──
# 公告关键词分类（盘前场景：昨日收盘后~今晨发布）
_ANN_BULL_KW = (
    "预增",
    "预盈",
    "扭亏",
    "中标",
    "签约",
    "回购",
    "增持",
    "提价",
    "扩产",
    "投产",
    "获批",
    "通过注册",
    "认证",
    "重大合同",
    "战略合作",
    "非公开发行.*获批",
    "股权激励",
)
_ANN_BEAR_KW = (
    "预减",
    "预亏",
    "首亏",
    "续亏",
    "减持",
    "问询",
    "处罚",
    "警示函",
    "诉讼",
    "仲裁",
    "冻结",
    "逾期",
    "违约",
    "终止",
    "退市风险",
    "停牌核查",
    "立案",
)
_RPT_RATING_MAP = {
    "买入": "buy",
    "增持": "buy",
    "推荐": "buy",
    "强烈推荐": "buy",
    "审慎推荐": "buy",
    "优于大市": "buy",
    "持有": "hold",
    "中性": "hold",
    "观望": "hold",
    "减持": "sell",
    "卖出": "sell",
    "回避": "sell",
}


def layer6_notices():
    """个股公告与研报因子：TDX 本地库读取近2天公告+研报，零 API。
    公告按关键词分利多/利空/中性；研报提取券商评级分布。"""
    out = {"announcements": [], "reports": [], "ann_stats": None, "rpt_stats": None}
    if tdxr is None:
        return out
    since = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")

    # ── 公告 ──
    try:
        anns = tdxr.read_info("stock_notice", since=since, limit=300)
    except Exception:
        anns = []
    import re

    bull_re = re.compile("|".join(_ANN_BULL_KW))
    bear_re = re.compile("|".join(_ANN_BEAR_KW))
    bull, bear, neu = [], [], []
    for a in anns:
        t = a.get("title", "")
        item = {
            "code": a.get("ts_code", ""),
            "name": a.get("name", ""),
            "title": t,
            "date": a.get("notice_date", ""),
            "url": a.get("url", ""),
        }
        if bear_re.search(t):
            bear.append(item)
        elif bull_re.search(t):
            bull.append(item)
        else:
            neu.append(item)
    out["ann_stats"] = {
        "total": len(anns),
        "bull": len(bull),
        "bear": len(bear),
        "neu": len(neu),
    }
    # 展示：利空全展示（风险优先） + 利多前10 + 中性前5
    out["announcements"] = (
        [dict(x, cat="bear") for x in bear]
        + [dict(x, cat="bull") for x in bull[:10]]
        + [dict(x, cat="neu") for x in neu[:5]]
    )

    # ── 研报（密度低，窗口放宽到 5 天） ──
    since5 = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
    try:
        rpts = tdxr.read_info("stock_report", since=since5, limit=100)
    except Exception:
        rpts = []
    rated = []
    for r in rpts:
        rating = (r.get("rating") or "").strip()
        if not rating or rating not in _RPT_RATING_MAP:
            continue
        rated.append(
            {
                "code": r.get("ts_code", ""),
                "name": r.get("report_name", "")[:40],
                "rating": rating,
                "cat": _RPT_RATING_MAP[rating],
                "org": r.get("org", ""),
                "date": r.get("pub_date", ""),
                "url": r.get("pdf_url", ""),
            }
        )
    buy_n = sum(1 for r in rated if r["cat"] == "buy")
    sell_n = sum(1 for r in rated if r["cat"] == "sell")
    out["rpt_stats"] = {
        "total": len(rpts),
        "rated": len(rated),
        "buy": buy_n,
        "sell": sell_n,
        "hold": len(rated) - buy_n - sell_n,
    }
    out["reports"] = rated[:20]
    # 研报正文结构化：后台线程增量抓 PDF 入库 + 走势回填（不阻塞报告生成）
    try:
        import report_fetch

        def _rpt_bg():
            try:
                report_fetch.fetch_missing(rated[:12], max_fetch=8)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "premarket.py", exc
                )
            try:
                report_fetch.backfill_post_perf()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "premarket.py", exc
                )
            try:
                import report_ai

                report_ai.run_all()  # Ollama 语义抽取（不可用内部熔断）
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "premarket.py", exc
                )

        import threading as _th

        _th.Thread(target=_rpt_bg, daemon=True).start()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)

    # ── 解禁提醒（近7天公告标题含"解禁"；同股去重，优先提示性公告；
    #    limit=10000 防 ORDER BY notice_date DESC 在节前公告井喷时截断较早日期） ──
    out["unlock"] = []
    try:
        since3 = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        anns3 = tdxr.read_info("stock_notice", since=since3, limit=10000)
        picked = {}
        for a in anns3:
            t = a.get("title") or ""
            if "解禁" not in t:
                continue
            code = a.get("ts_code", "")
            item = {
                "code": code,
                "name": a.get("name", ""),
                "title": t[:60],
                "date": a.get("notice_date", ""),
                "url": a.get("url", ""),
                "hint": "上市流通" in t or "流通" in (a.get("notice_type") or ""),
            }
            # 同股多条：提示性公告优先，其次留最新
            if code not in picked or (item["hint"] and not picked[code]["hint"]):
                picked[code] = item
        out["unlock"] = sorted(picked.values(), key=lambda x: x["date"], reverse=True)[
            :8
        ]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return out


def _risk_alerts(l1, l3, l5):
    """层6 风险预警阈值。"""
    alerts = []
    if l3.get("north_yi") is not None and l3["north_yi"] < -80:
        alerts.append("北向单日净流出超 80 亿（%d亿）" % l3["north_yi"])
    if l3.get("shibor_on") is not None and l3["shibor_on"] > 2.5:
        alerts.append("Shibor隔夜 %.2f%% 超 2.5%% 资金面偏紧" % l3["shibor_on"])
    big = [r for r in l1["rows"] if r.get("pct") is not None and abs(r["pct"]) >= 1.5]
    for r in big:
        alerts.append("%s 隔夜 %+.2f%%" % (r["name"], r["pct"]))
    j = (l5 or {}).get("jev") or l5 or {}
    if (
        isinstance(j, dict)
        and j.get("n")
        and j["n"] >= 10
        and j.get("bear", 0) / j["n"] > 0.3
    ):
        alerts.append(
            "新闻负面占比 %.0f%%（%d/%d）"
            % (j["bear"] * 100 / j["n"], j["bear"], j["n"])
        )
    vx = l5.get("vix") if isinstance(l5, dict) else None
    if vx and vx["val"] > 25:
        alerts.append("VIX %.1f 超 25 恐慌阈值（模拟源）" % vx["val"])
    return alerts


def run(push=False):
    snap = {}
    if os.path.exists(SNAP):
        try:
            snap = json.load(open(SNAP, encoding="utf-8"))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "premarket.py", exc
            )
    l1 = layer1_global()
    l2 = layer2_policy()
    l3 = layer3_liquidity()
    l3.update(volume_position())  # 量能位置（数据仓 index_daily 现算）
    l4 = layer4_sector()
    l5 = layer5_sentiment(snap)
    mo = money_effect()  # 赚钱效应（数据仓 daily 现算）
    l5["money"] = mo
    if mo.get("amt_yi"):  # 两市成交额归入层3量能
        l3["amt_yi"], l3["amt_pct"] = mo["amt_yi"], mo.get("amt_pct")
        l3["date"] = mo["date"]
    l4f = industry_flow()  # A股行业主力资金（层4+）
    mg = margin_sentiment()  # 两融杠杆（层3，展示不进分）
    if mg.get("date"):
        l3["margin"] = mg
    cb = cb_sentiment()  # 可转债情绪（层5，展示不进分）
    if cb.get("date"):
        l5["cb"] = cb
    l6 = layer6_notices()

    # 综合开盘倾向：外围 .35 / 情绪 .20 / 流动性 .15 / 政策 .15 / 板块 .15
    parts, w = (
        [],
        {"外围": 0.35, "流动性": 0.15, "政策": 0.15, "板块": 0.15, "情绪": 0.20},
    )
    vals = {
        "外围": l1["score"],
        "流动性": l3.get("score"),
        "政策": (l2.get("jev") or {}).get("score"),
        "板块": round(
            sum(s["score"] for s in l4 if s["n"] >= 2)
            / max(len([s for s in l4 if s["n"] >= 2]), 1)
        )
        if [s for s in l4 if s["n"] >= 2]
        else None,
        "情绪": l5.get("score"),
    }
    for k, v in vals.items():
        if v is not None:
            parts.append((k, v, w[k]))
    tot = sum(x[2] for x in parts)
    overall = round(sum(v * ww for _, v, ww in parts) / tot) if tot else None
    # K97：各层 score 均为 ±100 净占比量纲（0 中性：层1/3/5 的
    # (bull-bear)*100/n、层3 的 avg*18），而 prob/verdict 原按 0~100 量表
    # （中心 50、阈值 55/45）判定——量纲错位致 verdict 恒「偏空」、prob 恒
    # 0%（premarket_history 八日同文案实锤）。改判中心 0：±10 净占比为
    # 显著倾向，斜率 /9 不变（overall=+10 → prob 75%，-10 → 25%）。
    prob = round(100 / (1 + math.exp(-(overall or 0) / 9)))
    verdict = (
        (
            "偏多（开盘溢价概率较大）"
            if overall >= 10
            else "偏空（开盘折价概率较大）"
            if overall <= -10
            else "中性（按个股信号执行）"
        )
        if overall is not None
        else "—"
    )
    alerts = _risk_alerts(l1, l3, l5)
    # 11-10 叠加：收盘线产物（结构池/影子台账/板块层）→ 今日行动卡
    actions = today_actions()
    # `trade_date` + 带时区的 `ts` 是 /api/alerts 的 source_health 契约要求的
    # 两个字段（portal/api_common.py:_alert_source_health 的 dated()）：
    # 只有业务日 + 生产者给出的 tz-aware 完成时间，才算"当期证据"。
    # 2026-10-22 之前这里没有 trade_date、ts 也是裸本地串，于是 premarket 被
    # 判 stale/unproven_completion，AlertWatch 因此连续 4 天拒跑。
    _now = datetime.now().astimezone()
    data = {
        "ts": _now.isoformat(timespec="seconds"),
        "trade_date": _now.strftime("%Y%m%d"),
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "l4": l4,
        "l4f": l4f,
        "l5": l5,
        "l6": l6,
        "vals": vals,
        "overall": overall,
        "prob": prob,
        "verdict": verdict,
        "alerts": alerts,
        "actions": actions,
    }
    with open(
        os.path.join(REPORTS, "premarket_latest.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    # 历史落盘（回放验证用）：每日一行 jsonl，同日覆盖
    hist_path = os.path.join(REPORTS, "premarket_history.jsonl")
    today = datetime.now().strftime("%Y%m%d")
    hist_row = {
        "date": today,
        "overall": overall,
        "prob": prob,
        "verdict": verdict,
        "vals": vals,
        "l6_ann": (l6.get("ann_stats") or {}).get("bear"),
        "alerts": len(alerts),
    }
    old = []
    if os.path.exists(hist_path):
        old = [
            json.loads(l)
            for l in open(hist_path, encoding="utf-8")
            if l.strip() and json.loads(l).get("date") != today
        ]
    old.append(hist_row)
    with open(hist_path, "w", encoding="utf-8") as f:
        for r in old:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(
        "PREMARKET overall=%s prob=%d%% %s alerts=%d"
        % (overall, prob, verdict, len(alerts))
    )
    for a in alerts:
        print("  ⚠", a)
    write_page(data, snap)
    if push:
        doc_url = _doc_sync(build_doc_md(data))
        push_card(data, doc_url)
    return data


LEG_CN = {
    "v3": "现役 v3",
    "d_legacy": "旧方案对照",
    "F1_v3_stop12": "★F1 主腿候选",
    "W1_c6_score_cap25_stop12": "W1 组合观察",
    "A3_slot_score": "A3 评分定仓",
    "D1_score_cap25": "D1 定仓帽25%",
}


def _latest_glob(prefix, where=REPORTS):
    fs = sorted(glob.glob(os.path.join(where, prefix + "_2*.json")))
    return fs[-1] if fs else None


def today_actions():
    """「今日行动卡」数据：收盘线昨天产出、盘前该看的东西（11-10 叠加）。

    盘前页原 11 个分区全是外部环境（外围/政策/流动性/情绪…），自己
    收盘线 18:30 跑出来的三样能力没进来：
      ① 结构池五档快照（pool_state_*.json：九态数量 + 买点档 top5）
      ② 影子台账六腿（data/shadow/<date>.json：F1 转正验收 G-A 门，
         盘前要按 F1 的止损线管理今日持仓动作）
      ③ 板块层强度（sector_state_*.json：465 板块 T+1 晨更，5 日强度 top）
    全 soft：任何一个源缺失/解析失败都给 None，write_page 渲染「未生成」，
    不阻断 08:20 盘前页生成。
    """
    out = {"pool": None, "shadow": None, "sector": None}
    # ① 结构池
    try:
        fp = _latest_glob("pool_state")
        if fp:
            j = json.load(open(fp, encoding="utf-8"))
            bk = j.get("buckets") or {}
            buy_tiers = ["三买", "二买", "一买", "三买待"]
            counts = {k: len(bk.get(k) or []) for k in bk}
            tb = sorted(bk.get("三买") or [], key=lambda r: -(r.get("score") or 0))[:5]
            out["pool"] = {
                "asof": j.get("data_asof"),
                "counts": counts,
                "buy_counts": {k: counts.get(k, 0) for k in buy_tiers},
                "top3buy": [
                    {
                        "code": r.get("code"),
                        "name": r.get("name"),
                        "score": r.get("score"),
                        "pct": r.get("pct"),
                        "sig_type": r.get("sig_type"),
                    }
                    for r in tb
                ],
            }
    except Exception:
        pass
    # ② 影子台账（今天不存在就取最近一份，盘前看的是最新状态）
    try:
        fs = sorted(glob.glob(os.path.join(HERE, "data", "shadow", "2*.json")))
        if fs:
            j = json.load(open(fs[-1], encoding="utf-8"))
            legs = []
            for leg, v in (j.get("legs") or {}).items():
                if not isinstance(v, dict):
                    continue
                legs.append({
                    "leg": leg,
                    "cn": LEG_CN.get(leg, leg),
                    "f1": leg.startswith("F1"),
                    "last_day": v.get("last_day"),
                    "ret_pct": (v.get("summary") or {}).get("ret_pct"),
                    "n_hold": len(v.get("holdings") or []),
                    "holdings": [
                        {
                            "code": h.get("stock"),
                            "pnl_pct": h.get("pnl_pct"),
                            "wgt_pct": h.get("wgt_pct"),
                        }
                        for h in (v.get("holdings") or [])[:4]
                    ],
                })
            out["shadow"] = {"date": j.get("date"), "legs": legs}
    except Exception:
        pass
    # ③ 板块层（T+1：asof 落后一天是设计内）
    try:
        fp = _latest_glob("sector_state")
        if fp:
            j = json.load(open(fp, encoding="utf-8"))
            sc = [s for s in (j.get("sectors") or []) if s.get("r5") is not None]
            sc.sort(key=lambda s: -(s.get("r5") or 0))
            out["sector"] = {
                "asof": j.get("asof"),
                "top": [
                    {
                        "name": s.get("name"),
                        "stage": s.get("stage"),
                        "pct": s.get("pct"),
                        "r5": round((s.get("r5") or 0) * 100, 2),
                        "n_mem": s.get("n_mem"),
                    }
                    for s in sc[:5]
                ],
            }
    except Exception:
        pass
    return out


def write_page(data, snap):
    d, parts = data, []
    parts.append(
        "<h1>🌅 盘前分析 <span class='dim'>%s</span></h1>"
        "<div class='env'>综合开盘倾向 <b style='font-size:22px'>%s</b>"
        " · 开盘溢价概率 <b>%d%%</b> · %s</div>"
        % (d["ts"], d["overall"], d["prob"], d["verdict"])
    )
    if d["alerts"]:
        parts.append(
            "<h2>⚠ 风险预警</h2><div class='env' style='color:"
            "#e8710a'>%s</div>" % "<br>".join("· " + a for a in d["alerts"])
        )

    # 零、今日行动卡（11-10 叠加）：收盘线产物，盘前要按它执行
    acts = d.get("actions") or {}
    ap = acts.get("pool")
    ash = acts.get("shadow")
    ase = acts.get("sector")
    act_html = []
    if ap:
        order = ["三买", "二买", "一买", "三买待"]
        chips = " ".join(
            "<b>%s %d</b>" % (k, ap["buy_counts"][k]) for k in order
            if ap["buy_counts"].get(k)
        )
        t5 = "".join(
            "<tr><td>%s</td><td class='dim'>%s</td><td>%.1f</td>"
            "<td class='%s'>%+.2f%%</td></tr>"
            % (
                _stock_a(r["code"], r["name"]),
                r["code"],
                r.get("score") or 0,
                "buy" if (r.get("pct") or 0) > 0 else "avoid",
                (r.get("pct") or 0) * 100,
            )
            for r in ap["top3buy"]
        )
        act_html.append(
            "<div class='env'><b>① 结构池（数据日 %s）</b>：%s"
            "<table style='margin-top:6px'><tr><th>三买评分 top5</th>"
            "<th>代码</th><th>评分</th><th>昨日涨幅</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>"
            "<a href='pool_pc2.html'>选股工作台 v2（五档全列表 + 个股全关系）→</a>"
            " · <a href='pc.html'>结构工作台 →</a></div></div>"
            % (ap.get("asof") or "?", chips, t5)
        )
    if ash and ash.get("legs"):
        rows = ""
        for lg in ash["legs"]:
            hs = " ".join(
                "%s(%+.1f%%)"
                % (h.get("code") or "?", h.get("pnl_pct") or 0)
                for h in lg["holdings"]
            )
            rp = lg.get("ret_pct")
            rows += (
                "<tr><td>%s%s</td><td>%d</td><td class='%s'>%s</td>"
                "<td class='dim' style='white-space:normal'>%s</td></tr>"
                % (
                    lg["cn"],
                    "（盘前盯止损 −12%/移动止损）" if lg["f1"] else "",
                    lg["n_hold"],
                    "buy" if (rp or 0) > 0 else "avoid",
                    "%+.2f%%" % rp if rp is not None else "—",
                    hs or "—",
                )
            )
        act_html.append(
            "<div class='env'><b>② 影子台账（固化 v1 六腿 · 台账日 %s）</b>"
            "<table style='margin-top:6px'><tr><th>腿</th><th>持仓</th>"
            "<th>累计</th><th>持仓明细（浮盈）</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>六腿影子记账 · "
            "F1 连续核对无异常后按四条件转正 · 台账产物 data/shadow/</div></div>"
            % (ash.get("date") or "?", rows)
        )
    if ase:
        t5 = "".join(
            "<tr><td>%s</td><td>%s</td><td class='%s'>%+.2f%%</td>"
            "<td class='buy'>%+.2f%%</td></tr>"
            % (
                s["name"],
                s.get("stage") or "—",
                "buy" if (s.get("pct") or 0) > 0 else "avoid",
                (s.get("pct") or 0) * 100,
                s["r5"],
            )
            for s in ase["top"]
        )
        act_html.append(
            "<div class='env'><b>③ 板块层强度 top5（asof %s · T+1 晨更为设计内）</b>"
            "<table style='margin-top:6px'><tr><th>板块</th><th>阶段</th>"
            "<th>昨日</th><th>5日</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>"
            "<a href='sector_platform.html'>板块操作平台（465 板块位置 × 买点占比）→</a></div></div>"
            % (ase.get("asof") or "?", t5)
        )
    if act_html:
        missing = [
            nm
            for nm, v in (
                ("结构池", ap),
                ("影子台账", ash),
                ("板块层", ase),
            )
            if v is None
        ]
        miss_note = (
            "<div class='dim'>未生成：%s（对应 18:30 流水线产物缺失，"
            "看 logs/pool_daily.log）</div>" % "、".join(missing)
            if missing
            else ""
        )
        parts.append(
            "<h2>零、今日行动卡 <span class='dim' style='font-weight:400;"
            "font-size:12px'>（收盘线产物 · 08:20 盘前按此执行）</span></h2>"
            "%s%s" % ("".join(act_html), miss_note)
        )

    # 层1
    def _l1_cell(r):
        if r.get("sim"):
            return "%s（%+.2f%%）" % (r.get("val"), r["pct"])
        return ("%+.2f%%" % r["pct"]) if r.get("pct") is not None else "缺源"

    trs = "".join(
        "<tr><td>%s%s</td><td>%s</td><td class='%s'>%s</td></tr>"
        % (
            r["name"],
            " <span class='dim'>⚡模拟</span>" if r.get("sim") else "",
            r.get("date") or "—",
            "buy"
            if (r.get("pct") or 0) > 0
            else ("avoid" if (r.get("pct") or 0) < 0 else ""),
            _l1_cell(r),
        )
        for r in d["l1"]["rows"]
    )
    parts.append(
        "<h2>一、宏观与市场环境 · 外围联动因子 <b>%s</b></h2>"
        "<table><tr><th>指数</th><th>日期</th><th>隔夜涨跌</th>"
        "</tr>%s</table><div class='dim'>%s</div>"
        % (d["l1"]["score"], trs, d["l1"]["note"])
    )
    # 层2
    l2 = d["l2"]
    mac = (
        " · ".join(
            "%s %s月 %s%%" % (m["name"], m["month"], m["val"]) for m in l2["macro"]
        )
        or "缺源"
    )
    j = l2.get("jev")
    jt = (
        (
            "政策新闻 %d 条：利多 %d / 利空 %d / 中性 %d → 因子 <b>%s</b>"
            % (j["n"], j["bull"], j["bear"], j["neu"], j["score"])
        )
        if j
        else "近3天无政策类已判断新闻"
    )
    # 层2 政策新闻：标题可展开（正文摘要 + 关联个股下钻）
    ns = ""
    for n in l2["news"]:
        inner = []
        if n.get("content"):
            inner.append(
                "<div style='margin:4px 0 2px'>%s…</div>" % html.escape(n["content"])
            )
        if n.get("stocks"):
            inner.append(
                "<div class='dim'>关联个股 %s</div>"
                % " ".join(_stock_a(c) for c in n["stocks"])
            )
        ns += (
            "<details class='jev-item'><summary style='cursor:"
            "pointer'>%s ［%s%02d%%］%s</summary>%s</details>"
            % (
                html.escape(n["title"]),
                n["bias_cn"],
                n["conf"],
                " <span class='dim'>↕ 详情</span>" if inner else "",
                "".join(inner),
            )
        )
    parts.append(
        "<h2>二、政策与宏观信号 · 政策敏感因子</h2><div "
        "class='env'>%s<div style='margin-top:4px'>%s</div>%s</div>" % (mac, jt, ns)
    )
    # 层3（含量能位置：数据仓 index_daily/daily 现算）
    l3 = d["l3"]
    vol = ""
    if l3.get("amt_yi"):
        ap = l3.get("amt_pct")
        vol = (
            "<br>两市成交额 <b class='%s'>%s 亿</b>（环比 "
            "<b class='%s'>%+.1f%%</b>，%s）"
            % (
                "buy" if (ap or 0) >= 0 else "avoid",
                l3["amt_yi"],
                "buy" if (ap or 0) >= 0 else "avoid",
                ap or 0,
                l3.get("date", "—"),
            )
        )
        if l3.get("amt_ma5"):
            vol += " · 5日均 %s 亿" % l3["amt_ma5"]
        if l3.get("amt_ma20"):
            vol += " / 20日均 %s 亿" % l3["amt_ma20"]
        if l3.get("vol_state"):
            vol += " · <b class='%s'>%s</b>（vs 5日均 %+.1f%%）" % (
                "buy"
                if l3.get("vol_state") == "放量"
                else ("avoid" if l3.get("vol_state") == "缩量" else ""),
                l3["vol_state"],
                l3.get("vol_pct", 0),
            )
    for lb in ("上证指数", "创业板指"):
        p = l3.get(lb)
        if p:
            vol += (
                "<br>%s %s · 距MA20 <b class='%s'>%+.1f%%</b> / "
                "MA60 <b class='%s'>%+.1f%%</b>"
                % (
                    lb,
                    p["close"],
                    "buy" if p["ma20"] >= 0 else "avoid",
                    p["ma20"],
                    "buy" if p["ma60"] >= 0 else "avoid",
                    p["ma60"],
                )
            )
            if p.get("ma120") is not None:
                vol += " / 年线MA120 <b class='%s'>%+.1f%%</b>" % (
                    "buy" if p["ma120"] >= 0 else "avoid",
                    p["ma120"],
                )
    if l3.get("div_note"):
        vol += "<br><b class='watch'>⚠ %s</b>" % l3["div_note"]
    mg = l3.get("margin") or {}
    if mg:
        mg_h = (
            "两融余额 <b>%s 亿</b>（5日 <b class='%s'>%+.0f 亿 / "
            "%+.2f%%</b> · 融券 %s 亿 · 近3日 %s 亿"
            "<span class='dim'> · T+1披露（%s）</span>"
            % (
                mg.get("rzye_yi", "—"),
                "buy" if (mg.get("chg5_yi") or 0) >= 0 else "avoid",
                mg.get("chg5_yi", 0),
                mg.get("chg5_pct") or 0,
                mg.get("rqye_yi", "—"),
                " / ".join("%+d" % x for x in mg.get("chg_recent", [])),
                mg.get("date", "—"),
            )
        )
    else:
        mg_h = "两融余额 %s 亿（%s 环比 %+.3f%%）" % (
            l3.get("margin_yi", "—"),
            l3.get("margin_date", "—"),
            l3.get("margin_yoy", 0),
        )
    parts.append(
        "<h2>三、市场流动性与量能 · 宽松度因子 <b>%s</b></h2><div "
        "class='env'>%s<br>北向资金 %s 亿"
        "（%s）<br>Shibor隔夜 %s%%（14日变化 %+.3f）%s</div>"
        % (
            l3.get("score"),
            mg_h,
            l3.get("north_yi", "—"),
            l3.get("north_date", "—"),
            l3.get("shibor_on", "—"),
            l3.get("shibor_chg", 0),
            vol,
        )
    )
    # 层4
    if d["l4"]:
        trs = "".join(
            "<tr><td>%s%s</td><td>%s</td><td class='%s'>%+.2f%%</td>"
            "<td class='%s'>%s</td><td class='dim'>%s</td></tr>"
            % (
                s["sector"],
                " <span class='dim'>样本不足</span>" if s["n"] < 2 else "",
                s["cn"],
                "buy" if s["avg"] > 0 else "avoid",
                s["avg"],
                "buy" if s["score"] >= 0 else "avoid",
                s["score"],
                s.get("asof", "—"),
            )
            for s in d["l4"]
        )
        parts.append(
            "<h2>四、行业与板块联动 · 板块 Beta 代理</h2><table>"
            "<tr><th>美股/港股行业（自选%d只）</th><th>A股映射</th>"
            "<th>隔夜均涨跌</th><th>因子</th><th>数据日</th></tr>%s"
            "</table><div class='dim'>代理=自选美股/港股行业成分隔夜"
            "均值（zhice-db 无板块ETF）；中概走港股二次上市（美股 "
            "universe 缺中概）；数据日=组内最新bar（美/港股同步节奏"
            "不同，防误读）</div>" % (max(s["n"] for s in d["l4"]), trs)
        )
    # 层4+ A股行业主力资金（moneyflow 现算）
    f4 = d.get("l4f") or {}
    if f4.get("date"):

        def _ind_a(ind):
            return (
                "<a href='/bt/ind_drill?ind=%s&date=%s' "
                "title='查看成分股涨跌与个股资金明细'>%s ↘</a>"
                % (html.escape(ind, quote=True), f4["date"], html.escape(ind))
            )

        def _f_tr(rows):
            return "".join(
                "<tr><td>%s</td><td class='%s'>%+.1f 亿</td>"
                "<td class='dim'>%d 只</td></tr>"
                % (
                    _ind_a(r["ind"]),
                    "buy" if r["yi"] >= 0 else "avoid",
                    r["yi"],
                    r["n"],
                )
                for r in rows
            )

        parts.append(
            "<h2>四+、A股行业资金流 · 主力净流入排名 <span class='dim' "
            "style='font-weight:400;font-size:12px'>（%s）</span></h2>"
            "<div class='env'>全市场主力净额 <b class='%s'>%+.1f 亿</b>"
            "（%d 个行业）</div><table><tr><th>净流入 Top5</th><th>净额"
            "</th><th>家数</th></tr>%s</table><table><tr><th>净流出 "
            "Bottom5</th><th>净额</th><th>家数</th></tr>%s</table>"
            "<div class='dim'>口径：昨收盘日个股主力净流入（net_mf_"
            "amount，大单口径）按 tushare 行业聚合；与层4 美股代理互为"
            "对照（隔夜预期 vs 昨日实际）</div>"
            % (
                f4["date"],
                "buy" if f4.get("total_yi", 0) >= 0 else "avoid",
                f4.get("total_yi", 0),
                f4.get("n_ind", 0),
                _f_tr(f4.get("top", [])),
                _f_tr(f4.get("bottom", [])),
            )
        )
    # 层5
    l5 = d["l5"]
    if l5.get("n"):
        e = l5.get("cn_env") or {}
        vx = l5.get("vix") or {}
        parts.append(
            "<h2>五、资金与市场情绪 · 情绪因子 <b>%s</b></h2><div "
            "class='env'>近3天新闻判断 %d 条：利多 %d / 利空 %d / 中性 %d"
            "%s%s</div>"
            % (
                l5["score"],
                l5["n"],
                l5["bull"],
                l5["bear"],
                l5["neu"],
                "<br>A股宽度闸门 %s（宽度 %s%%，20日中位 %s%%）"
                % (e.get("verdict", "—"), e.get("breath", "—"), e.get("med20", "—"))
                if e
                else "",
                "<br>VIX %.1f（5日 %+.1f%%）<span class='dim'>⚡模拟源"
                " · &gt;25 触发恐慌预警</span>" % (vx.get("val", 0), vx.get("chg5", 0))
                if vx
                else "",
            )
        )
    # 层5+ 赚钱效应（数据仓 daily 现算）
    mo = l5.get("money") or {}
    if mo.get("date"):
        ur = mo["up"] / mo["dn"] if mo.get("dn") else None
        trs = "".join(
            "<tr><td>%s</td><td>%s</td><td class='buy'>%+.2f%%</td>"
            "<td>%d 连板</td>%s%s%s<td class='dim'>%s 亿</td></tr>"
            % (
                _stock_a(t["code"]),
                html.escape(t["name"]),
                t["pct"],
                t["streak"],
                _style_td(t),
                _dim_tds(t, mo["date"]),
                _tv_tds(t),
                t["amt_yi"],
            )
            for t in mo.get("top", [])
        )
        # 连板天梯条形图（streak≥2，宽度=家数占比，红系 A 股口径）
        lad = mo.get("ladder") or []
        lad_html = ""
        if lad:
            mx = max(v["n"] for v in lad) or 1
            lad_rows = "".join(
                "<div style='display:flex;align-items:center;"
                "gap:6px;margin:2px 0'>"
                "<span style='width:34px;text-align:right' class='buy'>"
                "<b>%d板</b></span>"
                "<span style='width:30px;text-align:right' class='dim'>"
                "%d家</span>"
                "<span style='flex:1'>"
                "<span style='display:inline-block;height:12px;"
                "border-radius:2px;vertical-align:middle;"
                "background:#f85149;opacity:%.2f;width:%.0f%%'></span>"
                "</span>"
                "<span style='width:46%%;white-space:nowrap;overflow:"
                "hidden;text-overflow:ellipsis' class='dim'>%s</span></div>"
                % (
                    v["k"],
                    v["n"],
                    0.45 + 0.55 * v["k"] / lad[0]["k"],
                    v["n"] * 100.0 / mx,
                    "、".join("%s(%s亿)" % (t["name"], t["amt_yi"]) for t in v["top"]),
                )
                for v in lad[:10]
            )
            lad_html = (
                "<div style='margin:6px 0'><b>连板天梯</b>"
                "<span class='dim'>（2板及以上，条长=家数，"
                "右列=每层额前3）</span>%s</div>" % lad_rows
            )
        # 全量涨停/跌停/炸板（默认收起，展开看全部）
        lu_all = mo.get("lu_all") or []
        ld_all = mo.get("ld_all") or []
        br_all = mo.get("broke_all") or []
        drill = ""
        if lu_all or ld_all or br_all:

            def _lu_row(t):
                sb = "buy" if t["streak"] >= 2 else ""
                return (
                    "<tr><td>%s</td><td>%s</td>"
                    "<td class='buy'>%+.2f%%</td>"
                    "<td class='%s'>%s</td>%s%s%s"
                    "<td class='dim'>%s 亿</td></tr>"
                    % (
                        _stock_a(t["code"]),
                        html.escape(t["name"]),
                        t["pct"],
                        sb,
                        ("%d 连板" % t["streak"]) if t["streak"] >= 2 else "首板",
                        _style_td(t),
                        _dim_tds(t, mo["date"]),
                        _tv_tds(t),
                        t["amt_yi"],
                    )
                )

            def _ld_row(t):
                return (
                    "<tr><td>%s</td><td>%s</td>"
                    "<td class='avoid'>%+.2f%%</td>%s%s"
                    "<td class='dim'>%s 亿</td></tr>"
                    % (
                        _stock_a(t["code"]),
                        html.escape(t["name"]),
                        t["pct"],
                        _dim_tds(t, mo["date"]),
                        _tv_tds(t),
                        t["amt_yi"],
                    )
                )

            def _br_row(t):
                return (
                    "<tr><td>%s</td><td>%s</td>"
                    "<td class='%s'>%+.2f%%</td>%s%s"
                    "<td class='dim'>%s 亿</td></tr>"
                    % (
                        _stock_a(t["code"]),
                        html.escape(t["name"]),
                        "buy" if t["pct"] > 0 else "avoid",
                        t["pct"],
                        _dim_tds(t, mo["date"]),
                        _tv_tds(t),
                        t["amt_yi"],
                    )
                )

            _lu_head = (
                "<table style='margin-top:4px'><tr><th>代码</th>"
                "<th>名称</th><th>涨幅</th><th>板型</th>"
                "<th>方式</th><th>板块</th><th>总市值(亿)</th>"
                "<th>龙虎榜</th><th>换手/量比</th><th>成交额"
                "</th></tr>%s</table>" % "".join(_lu_row(t) for t in lu_all)
            )
            _ld_blk = (
                (
                    "<div style='margin:6px 0'>"
                    "<b class='avoid'>跌停明细</b>（收盘跌停，"
                    "按额排序）<table><tr><th>代码</th><th>名称</th>"
                    "<th>跌幅</th><th>板块</th><th>总市值(亿)</th>"
                    "<th>龙虎榜</th><th>换手/量比</th><th>成交额</th>"
                    "</tr>%s</table></div>" % "".join(_ld_row(t) for t in ld_all)
                )
                if ld_all
                else ""
            )
            drill = (
                "<details style='margin:6px 0'><summary>"
                "<b>全部 %d 只涨停</b>（含连板标记） · "
                "<b class='avoid'>%d 只跌停</b> · "
                "<b class='avoid'>%d 只炸板</b> —— 点击展开全量清单"
                "</summary>%s%s"
                "<div style='margin:6px 0'><b class='avoid'>炸板明细"
                "</b>（盘中触板未封住，按额排序）%s</div></details>"
                % (
                    len(lu_all),
                    len(ld_all),
                    len(br_all),
                    _lu_head,
                    _ld_blk,
                    (
                        "<table><tr><th>代码</th><th>名称</th><th>收盘涨幅"
                        "</th><th>板块</th><th>总市值(亿)</th><th>龙虎榜</th>"
                        "<th>换手/量比</th><th>成交额</th></tr>%s</table>"
                        % "".join(_br_row(t) for t in br_all)
                    )
                    if br_all
                    else "<span class='dim'>无</span>",
                )
            )
        extra = (
            "<br>炸板率 <b class='%s'>%s%%</b>（炸 %d / 触板 %d）· "
            "20cm 占比 <b>%s%%</b>"
            % (
                "avoid" if (mo.get("broke_rate") or 0) >= 30 else "buy",
                mo.get("broke_rate", 0),
                mo.get("broke_n", 0),
                (mo.get("broke_n", 0) + mo.get("limit_up", 0)),
                mo.get("cm20_share", 0),
            )
        )
        if mo.get("ylu_avg") is not None:
            extra += (
                "<br>昨涨停今表现 <b class='%s'>%+.2f%%</b>"
                "（中位 %+.2f%%）· 晋级率 <b>%s%%</b>"
                % (
                    "buy" if mo["ylu_avg"] >= 0 else "avoid",
                    mo["ylu_avg"],
                    mo["ylu_med"],
                    mo.get("promote_rate", 0),
                )
            )
        if mo.get("th_label") is not None:
            _tl = mo["th_label"]
            extra += (
                "<br>温度计（60日分位）：涨停 <b>P%d</b> · 炸板 "
                "<b>P%d</b> → 情绪 <b class='%s'>%s</b>"
                % (
                    mo["th_lu"],
                    mo["th_br"],
                    "buy"
                    if _tl in ("亢奋", "回暖")
                    else ("avoid" if _tl in ("冰点", "转弱") else ""),
                    _tl,
                )
            )
        cb = l5.get("cb") or {}
        if cb.get("date"):
            extra += (
                "<br>转债温度（%s）：等权 <b class='%s'>%+.2f%%</b>"
                " · 上涨占比 <b>%s%%</b> · 成交 <b>%s 亿</b>%s"
                "<span class='dim'>（游资风险偏好代理）</span>"
                % (
                    cb["date"],
                    "buy" if (cb.get("eq_pct") or 0) >= 0 else "avoid",
                    cb.get("eq_pct", 0),
                    cb.get("up_ratio", 0),
                    cb.get("amt_yi", "—"),
                    "（vs 5日均 %+.1f%%）" % cb["amt_vs_ma5"]
                    if cb.get("amt_vs_ma5") is not None
                    else "",
                )
            )
        parts.append(
            "<h2>五+、赚钱效应 · 昨日博弈温度（%s）</h2><div class='env'>"
            "涨停 <b class='buy'>%d</b> · 跌停 <b class='avoid'>%d</b> · "
            "最高连板 <b class='buy'>%d 板（%s）</b><br>涨跌家数 "
            "<b class='buy'>%d</b> : <b class='avoid'>%d</b>（平 %d）· "
            "涨跌比 %s%s</div>%s%s%s<div class='dim'>口径：收盘涨停（板块阈值 "
            "10%%/20%%/30%%，ST 5%%，新股剔除，幅值上限 31.5%%）；炸板="
            "盘中触板未收封；连板=12日窗口连续收盘涨停；昨涨停今表现="
            "前日收盘涨停股当日涨跌均值（接力情绪）；温度计=涨停/炸板"
            "的近60日百分位（P=percentile，炸板分位越低越热）；转债="
            "全体可转债等权涨跌中位（cb_daily）；板块=limit_list_d 官方"
            "行业；市值=总股本×收盘（bak_basic，亿）；龙虎榜=ts_top_list"
            " 上榜（游资/机构席位活跃，点击进 /bt/lhb 看上榜明细）；"
            "换手率=成交量/流通股本；量比=当日额/前5日均额；方式：一字="
            "开盘顶死全天封、T字=开盘触板盘中开过、实体=换手板"
            "</div>"
            % (
                mo["date"],
                mo.get("limit_up", 0),
                mo.get("limit_dn", 0),
                mo.get("max_streak", 0),
                mo.get("streak_name", "—"),
                mo.get("up", 0),
                mo.get("dn", 0),
                mo.get("flat", 0),
                ("%.1f" % ur) if ur is not None else "∞",
                extra,
                lad_html,
                drill,
                (
                    "<table><tr><th>代码</th><th>名称</th>"
                    "<th>涨幅</th><th>连板</th><th>方式</th><th>板块</th>"
                    "<th>总市值(亿)</th><th>龙虎榜</th><th>换手/量比</th>"
                    "<th>成交额</th></tr>%s</table>" % trs
                )
                if trs
                else "",
            )
        )
    # 层6 公告与研报（TDX 本地）
    l6 = d.get("l6") or {}
    an = l6.get("ann_stats")
    if an:
        parts.append(
            "<h2>六、个股公告 · 公告因子</h2><div class='env'>近2天公告 "
            "%d 条：利多 %d / 利空 %d / 中性 %d</div>"
            % (an["total"], an["bull"], an["bear"], an["neu"])
        )
        a_rows = l6.get("announcements", [])
        if a_rows:

            def _ann_row(r):
                t = html.escape(r["title"][:50])
                if r.get("url"):
                    t = "<a href='%s' target='_blank' rel='noopener'>%s ↗</a>" % (
                        html.escape(r["url"], quote=True),
                        t,
                    )
                return (
                    "<tr><td>%s</td><td>%s</td><td class='%s'>%s</td>"
                    "<td class='dim'>%s</td></tr>"
                    % (
                        _stock_a(r["code"], r["code"][:10]),
                        html.escape(r["name"] or "—"),
                        "buy"
                        if r["cat"] == "bull"
                        else ("avoid" if r["cat"] == "bear" else ""),
                        t,
                        r["date"],
                    )
                )

            a_tr = "".join(_ann_row(r) for r in a_rows)
            parts.append(
                "<table><tr><th>代码</th><th>简称</th><th>公告标题</th>"
                "<th>日期</th></tr>%s</table>" % a_tr
            )
    # 解禁提醒（层6+，TDX 公告标题扫描）
    unl = l6.get("unlock") or []
    if unl:
        u_tr = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td>"
            "<td class='dim'>%s</td></tr>"
            % (
                _stock_a(u["code"], u["code"][:10]),
                html.escape(u["name"] or "—"),
                (
                    "<a href='%s' target='_blank' rel='noopener'>%s ↗</a>"
                    % (html.escape(u["url"], quote=True), html.escape(u["title"]))
                    if u.get("url")
                    else html.escape(u["title"])
                ),
                u["date"],
            )
            for u in unl
        )
        parts.append(
            "<h2>六+、解禁提醒 · 近7天限售上市流通</h2>%s"
            "<div class='dim'>口径：TDX 公告标题含「解禁」（提示性公告"
            "优先，同股去重）；解禁=潜在卖压，节后首日关注对应个股</div>"
            % (
                "<table><tr><th>代码</th><th>简称</th><th>公告</th>"
                "<th>日期</th></tr>%s</table>" % u_tr
            )
        )
    rp = l6.get("rpt_stats")
    if rp:
        parts.append(
            "<h2>七、券商研报 · 评级因子</h2><div class='env'>近5天研报 "
            "%d 篇：评级覆盖 %d（买入 %d / 中性 %d / 卖出 %d）</div>"
            % (rp["total"], rp["rated"], rp["buy"], rp["hold"], rp["sell"])
        )
        r_rows = l6.get("reports", [])
        if r_rows:
            # 本地正文库：直接结构化展示研报文字（零外链零跳转）
            try:
                import report_fetch

                _rtexts = report_fetch.load_texts()
            except Exception:
                _rtexts = {}
            from urllib.parse import quote as _uq2

            def _rpt_card(r):
                rec = _rtexts.get(r.get("url") or "")
                head = "<b>%s</b> <span class='%s'>%s</span>" % (
                    html.escape(r["org"] or "—"),
                    "buy"
                    if r["cat"] == "buy"
                    else ("avoid" if r["cat"] == "sell" else ""),
                    html.escape(r["rating"]),
                )
                title = html.escape(r["name"][:44])
                meta = "%s · %s" % (_stock_a(r["code"], r["code"][:10]), r["date"])
                if rec:
                    ai = rec.get("ai") or {}
                    if ai:
                        bmap = {
                            "bull": ("AI看多", "buy"),
                            "bear": ("AI看空", "avoid"),
                            "neutral": ("AI中性", "dim"),
                        }
                        bl, bc = bmap.get(ai.get("bias"), ("AI中性", "dim"))
                        head += (
                            " <span class='%s' style='font-weight:400;"
                            "font-size:11px'>%s%.0f%%</span>"
                            % (bc, bl, (ai.get("conf") or 0) * 100)
                        )
                    body = html.escape(rec.get("summary", "")[:200])
                    meta += " · %d页 %d字" % (
                        rec.get("pages") or 0,
                        rec.get("chars") or 0,
                    )
                    ans = rec.get("analysts") or []
                    if ans:
                        meta += " · 分析师 %s" % "/".join(
                            a.get("name", "") for a in ans[:3]
                        )
                    more = " <a href='/bt/report?u=%s' class='dim'>全文→</a>" % _uq2(
                        r["url"], safe=""
                    )
                    seg = (
                        "<div>%s</div><div style='font-size:14px;"
                        "font-weight:600;margin:3px 0'>%s</div>"
                        "<div class='dim' style='font-size:11px'>%s</div>"
                        "<div style='font-size:13px;line-height:1.7;"
                        "margin-top:4px'>%s…%s</div>" % (head, title, meta, body, more)
                    )
                else:
                    seg = (
                        "<div>%s</div><div style='font-size:14px;"
                        "font-weight:600;margin:3px 0'>%s</div>"
                        "<div class='dim' style='font-size:11px'>%s · "
                        "正文待入库</div>" % (head, title, meta)
                    )
                return (
                    "<div style='border-left:3px solid #58a6ff;padding:"
                    "2px 0 2px 10px;margin:10px 0'>%s</div>" % seg
                )

            n_text = sum(1 for r in r_rows if r.get("url") in _rtexts)
            shown, rest = r_rows[:10], r_rows[10:]
            cards = "".join(_rpt_card(r) for r in shown)
            if rest:
                cards += (
                    "<div class='dim' style='margin-top:8px;"
                    "font-size:12px'>另有 %d 篇：%s</div>"
                    % (
                        len(rest),
                        " ｜ ".join(
                            "%s %s"
                            % (html.escape(x["org"] or ""), html.escape(x["name"][:24]))
                            for x in rest
                        ),
                    )
                )
            parts.append(
                "<div class='dim' style='margin:4px 0 8px;font-size:12px'>"
                "正文已入库 %d/%d，摘要直读（点代码看个股 · 全文为站内"
                "本地页）</div>%s" % (n_text, len(r_rows), cards)
            )
    # 层8 共振 Top5（快照时间 + 闸门说明：解释为何某市场缺席）
    res = snap.get("res", [])[:5]
    trs = "".join(
        "<tr><td>%s</td><td>%d</td><td "
        "class='%s'>%s</td></tr>"
        % (
            _stock_a(r["code"], r["name"]),
            r["score"],
            "buy"
            if "买入" in r["action"]
            else ("watch" if "观察" in r["action"] else "avoid"),
            r["action"],
        )
        for r in res
    )
    envs = snap.get("envs") or []
    gated = [
        str(e.get("label") or "").split("(")[0] for e in envs if e.get("safe") is False
    ]
    gate_note = (
        (
            "<div class='dim' style='margin-top:4px'>⚠ 环境闸门收紧："
            "%s —该市场共振信号被一票否决过滤，Top5 仅含放行市场"
            "</div>" % "、".join(gated)
        )
        if gated
        else (
            "<div class='dim' style='margin-top:4px'>四市场环境闸门全放行</div>"
            if envs
            else ""
        )
    )
    parts.append(
        "<h2>八、量化技术与执行 · 共振 Top5 <span class='dim' "
        "style='font-weight:400;font-size:12px'>（共振快照 %s · "
        "每日 17:35 更新）</span></h2>"
        "<table><tr><th>标的</th><th>综合</th><th>建议</th></tr>"
        "%s</table>%s<div class='dim' style='margin-top:2px'>"
        "<a href='bt_hub.html'>共振全景 · 选股中心（四市场榜单/环境闸门）→</a></div>"
        % (snap.get("ts") or "—", trs, gate_note)
    )
    parts.append(
        "<div class='dim' style='margin-top:26px'>每日 14:30（北京）"
        "/ 08:30（巴黎）自动生成 · 源：tushare(指数/两融/北向/Shibor/CPI/PPI) + "
        "news.db(JEV) + zhice-db(美股行业) + TDX(公告/研报) + "
        "数据仓(daily/index_daily/moneyflow/margin/cb_daily："
        "赚钱效应/量能位置/行业资金/两融/转债 现算) · "
        "L2竞价缺权限 · 汇率/VIX 为模拟源（⚡标注，不进综合分）"
        " · 纸面研究不构成投资建议</div>"
    )
    with open(os.path.join(REPORTS, "premarket.html"), "w", encoding="utf-8") as f:
        # K39 面包屑（key="pre" → 首页›量化›盘前）；旧第二行胶囊废除，
        # 互跳由 ☰ 域内下拉承担
        f.write(RR._page("盘前分析", RR._top_nav("pre") + "".join(parts)))
    print("PAGE premarket.html")


def _lark_exe():
    """定位 lark-cli（仓库 bin/ 固定副本优先 K-STOCK-396，PATH 次之，
    插件目录回退，计划任务上下文可用）。"""
    import shutil

    _bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "lark-cli.exe",
    )
    if os.path.isfile(_bin):
        return _bin
    try:
        p = shutil.which("lark-cli")
        if p:
            return p
        root = (
            r"C:\Users\Administrator\.trae-cn\plugins"
            r"\trae-remote-official\lark"
        )
        for d in os.listdir(root):
            c = os.path.join(root, d, "bin", "lark-cli.exe")
            if os.path.exists(c):
                return c
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "premarket.py", exc)
    return None


def _drill(name, code):
    """镜像文档股票名 → 门户个股全景页下钻（K-STOCK-389，复用 388
    runner._stock_drill；portal_url 缺省回落纯文本）。局部 import
    runner 同 push_card 判例，防模块级循环依赖。"""
    try:
        import runner

        return runner._stock_drill(name, code)
    except Exception:
        return str(name or "")


def build_doc_md(d):
    """盘前报告完整 markdown（飞书镜像文档用，比卡片更详细）。"""
    L = [
        "# 🌅 盘前分析 · A股开盘倾向",
        "",
        "> 综合开盘倾向 **%s** · %s · 溢价概率 **%d%%** · 更新 %s"
        % (d["overall"], d["verdict"], d["prob"], d["ts"]),
        "",
    ]

    def _sc(v):
        return ("%+d" % v) if v is not None else "—"

    L += [
        "## 一、外围市场联动（%s）" % _sc(d["vals"].get("外围")),
        "",
        "| 指数 | 涨跌幅 | 数据日 |",
        "|---|---|---|",
    ]
    for r in d["l1"]["rows"]:
        L.append(
            "| %s%s | %s | %s |"
            % (
                r["name"],
                " ⚡模拟" if r.get("sim") else "",
                ("%+.2f%%" % r["pct"]) if r.get("pct") is not None else "—",
                r.get("date", "—"),
            )
        )
    L.append("")

    L += ["## 二、政策敏感（%s）" % _sc(d["vals"].get("政策")), ""]
    for m in d["l2"].get("macro", []):
        L.append("- %s（%s）：%s%%" % (m["name"], m["month"], m["val"]))
    j = d["l2"].get("jev")
    if j:
        L += [
            "",
            "近3天政策类新闻 JEV：%d 条 · 利多 %d / 利空 %d / 中性 %d"
            % (j["n"], j["bull"], j["bear"], j["neu"]),
            "",
        ]
        L += ["| 新闻 | 判断 | 置信度 | 关联个股 |", "|---|---|---|---|"]
        for nw in d["l2"].get("news", []):
            c = nw.get("conf") or 0
            stk = " ".join(
                _drill((x[:6] if x.endswith((".SH", ".SZ")) else x), x)
                for x in (nw.get("stocks") or [])[:6]
            )
            L.append(
                "| %s | %s | %d%% | %s |"
                % (
                    nw["title"],
                    nw.get("bias_cn") or "—",
                    round(c * 100) if c <= 1 else round(c),
                    stk or "—",
                )
            )
    L.append("")

    l3 = d["l3"]
    mg = l3.get("margin") or {}
    if mg:
        L += [
            "## 三、流动性与量能（%s）" % _sc(d["vals"].get("流动性")),
            "",
            "- 两融余额：**%s 亿**（5日 %+.0f 亿 / %+.2f%% · 融券 %s 亿"
            " · 近3日 %s 亿 · T+1披露，%s）"
            % (
                mg.get("rzye_yi", "—"),
                mg.get("chg5_yi", 0),
                mg.get("chg5_pct") or 0,
                mg.get("rqye_yi", "—"),
                " / ".join("%+d" % x for x in mg.get("chg_recent", [])),
                mg.get("date", "—"),
            ),
        ]
    else:
        L += [
            "## 三、流动性与量能（%s）" % _sc(d["vals"].get("流动性")),
            "",
            "- 两融余额：%s 亿（%s，环比 %+.3f%%）"
            % (
                l3.get("margin_yi", "—"),
                l3.get("margin_date", "—"),
                l3.get("margin_yoy", 0),
            ),
        ]
    L += [
        "- 北向资金：%s 亿（%s）"
        % (l3.get("north_yi", "—"), l3.get("north_date", "—")),
        "- Shibor O/N：%s%%（14日变化 %+.3fpct）"
        % (l3.get("shibor_on", "—"), l3.get("shibor_chg", 0)),
    ]
    if l3.get("amt_yi"):
        L.append(
            "- 两市成交额：%s 亿（环比 %+.1f%%，%s）"
            % (l3["amt_yi"], l3.get("amt_pct") or 0, l3.get("date", "—"))
        )
        if l3.get("amt_ma5"):
            L.append(
                "- 均量：5日 %s 亿%s · %s（vs 5日均 %+.1f%%）"
                % (
                    l3["amt_ma5"],
                    " / 20日 %s 亿" % l3["amt_ma20"] if l3.get("amt_ma20") else "",
                    l3.get("vol_state", "—"),
                    l3.get("vol_pct", 0),
                )
            )
    for lb in ("上证指数", "创业板指"):
        p = l3.get(lb)
        if p:
            L.append(
                "- %s：%s · 距MA20 %+.1f%% / MA60 %+.1f%%%s"
                % (
                    lb,
                    p["close"],
                    p["ma20"],
                    p["ma60"],
                    " / 年线MA120 %+.1f%%" % p["ma120"]
                    if p.get("ma120") is not None
                    else "",
                )
            )
    if l3.get("div_note"):
        L.append("- ⚠ 量价%s" % l3["div_note"])
    L.append("")

    L += [
        "## 四、板块联动（%s）" % _sc(d["vals"].get("板块")),
        "",
        "隔夜美股/港股自选分组 → A股映射：",
        "",
        "| 板块 | 隔夜均值 | A股映射 |",
        "|---|---|---|",
    ]
    for s in d["l4"]:
        L.append(
            "| %s%s | %+.2f%%（%d只·数据%s） | %s |"
            % (
                s["sector"],
                " <样本不足>" if s["n"] < 2 else "",
                s["avg"],
                s["n"],
                s.get("asof", "—"),
                s["cn"],
            )
        )
    f4 = d.get("l4f") or {}
    if f4.get("date"):
        L += [
            "",
            "## 四+、A股行业资金流（%s）" % f4["date"],
            "",
            "全市场主力净额 **%+.1f 亿**（%d 个行业）："
            % (f4.get("total_yi", 0), f4.get("n_ind", 0)),
            "",
            "| 方向 | 行业 | 主力净额 | 家数 |",
            "|---|---|---|---|",
        ]
        for r in f4.get("top", []):
            L.append("| 净流入 | %s | %+.1f 亿 | %d |" % (r["ind"], r["yi"], r["n"]))
        for r in f4.get("bottom", []):
            L.append("| 净流出 | %s | %+.1f 亿 | %d |" % (r["ind"], r["yi"], r["n"]))
    L.append("")

    l5 = d["l5"]
    L += [
        "## 五、市场情绪（%s）" % _sc(d["vals"].get("情绪")),
        "",
        "- JEV 新闻（近3天）：%d 条 · 利多 %d / 利空 %d"
        % (l5.get("n", 0), l5.get("bull", 0), l5.get("bear", 0)),
        "- VIX：%.1f（%+.2f%%）⚡模拟源"
        % ((l5.get("vix") or {}).get("val", 0), (l5.get("vix") or {}).get("chg", 0)),
    ]
    env = l5.get("cn_env")
    if env:
        L.append(
            "- A股环境闸门：%s（宽度 %s%% · %s）"
            % (
                env.get("verdict", "—"),
                env.get("breath", "—"),
                (
                    "缺维降权（数据缺失）"
                    if env.get("safe") is None
                    else "放行"
                    if env.get("safe")
                    else "一票否决"
                ),
            )
        )
    mo = l5.get("money") or {}
    if mo.get("date"):
        L.append("")
        L += [
            "## 五+、赚钱效应（%s）" % mo["date"],
            "",
            "- 涨停 **%d** · 跌停 **%d** · 最高连板 **%d 板（%s）**"
            % (
                mo.get("limit_up", 0),
                mo.get("limit_dn", 0),
                mo.get("max_streak", 0),
                mo.get("streak_name", "—"),
            ),
            "- 涨跌家数 %d : %d（平 %d）"
            % (mo.get("up", 0), mo.get("dn", 0), mo.get("flat", 0)),
            "- 炸板率 %s%%（炸 %d / 触板 %d）· 20cm 占比 %s%%"
            % (
                mo.get("broke_rate", 0),
                mo.get("broke_n", 0),
                (mo.get("broke_n", 0) + mo.get("limit_up", 0)),
                mo.get("cm20_share", 0),
            ),
        ]
        if mo.get("ylu_avg") is not None:
            L.append(
                "- 昨涨停今表现 %+.2f%%（中位 %+.2f%%）· 晋级率 "
                "%s%%" % (mo["ylu_avg"], mo["ylu_med"], mo.get("promote_rate", 0))
            )
        if mo.get("th_label") is not None:
            L.append(
                "- 温度计（60日分位）：涨停 P%d · 炸板 P%d → 情绪 "
                "**%s**" % (mo["th_lu"], mo["th_br"], mo["th_label"])
            )
        cb = l5.get("cb") or {}
        if cb.get("date"):
            L.append(
                "- 转债温度（%s）：等权 %+.2f%% · 上涨占比 %s%% · "
                "成交 %s 亿%s"
                % (
                    cb["date"],
                    cb.get("eq_pct", 0),
                    cb.get("up_ratio", 0),
                    cb.get("amt_yi", "—"),
                    "（vs 5日均 %+.1f%%）" % cb["amt_vs_ma5"]
                    if cb.get("amt_vs_ma5") is not None
                    else "",
                )
            )
        if mo.get("top"):
            L += [
                "",
                "| 涨停标的（额Top5） | 涨幅 | 连板 | 成交额 |",
                "|---|---|---|---|",
            ]
            for t in mo["top"]:
                L.append(
                    "| %s%s | %+.2f%% | %d 板 | %s 亿 |"
                    % (
                        _drill(t["name"], t["code"]),
                        t["code"],
                        t["pct"],
                        t["streak"],
                        t["amt_yi"],
                    )
                )
    L.append("")

    l6 = d.get("l6") or {}
    an = l6.get("ann_stats")
    if an:
        L += [
            "## 六、个股公告",
            "",
            "近2天公告 **%d** 条：利多 %d / 利空 %d / 中性 %d"
            % (an["total"], an["bull"], an["bear"], an["neu"]),
            "",
        ]
        for a in l6.get("announcements", []):
            tag = {"bull": "🔴", "bear": "🟢"}.get(a["cat"], "⚪")
            L.append(
                "- %s %s %s：%s（%s）%s"
                % (
                    tag,
                    a["code"][:10],
                    _drill(a["name"], a["code"]),
                    a["title"][:60],
                    a["date"],
                    "[原文](%s)" % a["url"] if a.get("url") else "",
                )
            )
        L.append("")
    unl = l6.get("unlock") or []
    if unl:
        L += ["## 六+、解禁提醒（近7天）", ""]
        for u in unl:
            L.append(
                "- 🟢 %s %s：%s（%s）%s"
                % (
                    u["code"][:10],
                    _drill(u["name"], u["code"]),
                    u["title"][:56],
                    u["date"],
                    "[原文](%s)" % u["url"] if u.get("url") else "",
                )
            )
        L.append("")
    rp = l6.get("rpt_stats")
    if rp:
        L += [
            "## 七、券商研报",
            "",
            "近5天研报 **%d** 篇：评级覆盖 %d（买入 %d / 中性 %d / 卖出 %d）"
            % (rp["total"], rp["rated"], rp["buy"], rp["hold"], rp["sell"]),
            "",
        ]
        for r in l6.get("reports", []):
            # 修存量潜伏 bug：原格式串 7 占位符对 6 参数（该分支空数据未触发）
            L.append(
                "- %s %s｜%s｜%s：%s（%s）"
                % (
                    r["code"][:10],
                    r["org"],
                    r["rating"],
                    _drill(r["name"], r["code"]),
                    r["date"],
                    "[全文](%s)" % r["url"] if r.get("url") else "",
                )
            )
        L.append("")

    L += ["## 八、风险预警", ""]
    L += (
        ["- ⚠ " + a for a in d["alerts"]]
        if d["alerts"]
        else ["- 无触发（阈值：北向>80亿流出 / Shibor>2.5% / 指数±1.5% / 负面>30%）"]
    )
    L += [
        "",
        "---",
        "*数据源：tushare + JEV(news.db) + zhice-db + "
        "TDX(公告/研报) + 数据仓(daily/index_daily/moneyflow/"
        "crypto_daily/margin/cb_daily 现算) · 汇率/VIX 为模拟源（仅展示）"
        "· 本报告为研究工具，不构成投资建议*",
    ]
    return "\n".join(L)


def _cli_error_brief(out):
    """把 lark-cli 的 JSON 错误压成一行可读摘要（鉴权/权限/参数）。

    历史教训（2026-10-25 K-ORG-20261025-DOCSYNC）：原来只打印 out[:120]
    原文，token_missing 与 app_scope_not_applied 都被截成看似同类的
    validation 报错，排查时白走一大圈。摘要只取 type/subtype/code/message，
    既短又保留病因。
    """
    text = (out or "").strip()
    try:
        payload = json.loads(text)
        err = payload.get("error") or {}
        kind = err.get("type") or "?"
        subtype = err.get("subtype") or "?"
        code = err.get("code")
        head = "%s/%s" % (kind, subtype)
        if code:
            head += "(%s)" % code
        return "%s %s" % (head, str(err.get("message") or "")[:160])
    except Exception:
        return text.replace("\n", " ")[:200]


def _doc_sync(md):
    """镜像报告到飞书文档：无则创建，有则整体覆盖；失败不影响推卡。"""
    exe = _lark_exe()
    if not exe:
        print("DOC_SYNC SKIP no lark-cli")
        return None
    # 2026-10-25 SEC 收口（EXEC-R16）：doc URL 读 env/本地私有覆盖，见 config_local.py
    from config_local import load_config, save_report_url

    cfg = load_config()
    doc = (cfg.get("report") or {}).get("premarket_doc_url", "")
    tmp = os.path.join(HERE, "_pre_doc.md")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(md)
        if doc:
            r = subprocess.run(
                [
                    exe,
                    "docs",
                    "+update",
                    "--as",
                    "user",
                    "--doc",
                    doc,
                    "--command",
                    "overwrite",
                    "--doc-format",
                    "markdown",
                    "--content",
                    "@./_pre_doc.md",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=HERE,
            )
            out = (r.stdout or "") + (r.stderr or "")
            if '"ok": true' in out or '"ok":true' in out:
                print("DOC_SYNC UPDATE OK")
                return doc
            # 兜底：更新瞬时失败（超时/抖动）时仍返回已知 URL——
            # 文档在、内容或旧一版，好过卡片丢掉唯一可达链接（10/39 实况）
            print("DOC_SYNC UPDATE FAIL, keep stored url:", _cli_error_brief(out))
            return doc
        r = subprocess.run(
            [
                exe,
                "docs",
                "+create",
                "--as",
                "user",
                "--title",
                "🌅 盘前分析（每日自动更新）",
                "--doc-format",
                "markdown",
                "--content",
                "@./_pre_doc.md",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=HERE,
        )
        out = (r.stdout or "") + (r.stderr or "")
        try:
            j = json.loads(r.stdout or "{}")
            doc = (j.get("data") or {}).get("document") or {}
            url = doc.get("url") or (j.get("data") or {}).get("url") or ""
        except Exception:
            url = ""
        if url:
            # 2026-10-25 SEC 收口（EXEC-R16）：写私有覆盖，不回写 tracked config.json
            save_report_url("premarket_doc_url", url)
            print("DOC_SYNC CREATE OK", url[:60])
            return url
        print("DOC_SYNC FAIL", _cli_error_brief(out))
        return None
    except Exception as e:
        print("DOC_SYNC ERR", str(e)[:120])
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "premarket.py", exc
            )


def push_card(data, doc_url=None):
    import runner

    d = data

    def bar(v):
        return ("%+d" % v) if v is not None else "—"

    l1 = " · ".join(
        "%s%+.2f%%" % (r["name"], r["pct"])
        for r in d["l1"]["rows"]
        if r.get("pct") is not None
    )
    j = d["l2"].get("jev")
    l5 = d["l5"]
    lines = [
        "**六层因子**",
        "· 外围联动 %s｜%s" % (bar(d["vals"]["外围"]), l1 or "—"),
        "· 流动性 %s｜两融 %s亿 环比%+.3f%% · 北向 %s亿 · Shibor O/N %s%%"
        % (
            bar(d["vals"]["流动性"]),
            d["l3"].get("margin_yi", "—"),
            d["l3"].get("margin_yoy", 0),
            d["l3"].get("north_yi", "—"),
            d["l3"].get("shibor_on", "—"),
        ),
        "· 政策敏感 %s%s"
        % (
            bar(d["vals"]["政策"]),
            "｜政策新闻 %d条 多%d/空%d" % (j["n"], j["bull"], j["bear"]) if j else "",
        ),
        "· 板块联动 %s｜%s"
        % (
            bar(d["vals"]["板块"]),
            " ".join("%s%+.1f%%" % (s["sector"], s["avg"]) for s in d["l4"][:3]) or "—",
        ),
        "· 市场情绪 %s｜3天 %d条 多%d/空%d · VIX %.1f⚡"
        % (
            bar(d["vals"]["情绪"]),
            l5.get("n", 0),
            l5.get("bull", 0),
            l5.get("bear", 0),
            (l5.get("vix") or {}).get("val", 0),
        ),
    ]
    if d["alerts"]:
        lines.append("**⚠ 预警**\n" + "\n".join("· " + a for a in d["alerts"]))
    # K42-c 判例：卡内不放内网链接（手机/外网不可达），仅放可达的
    # 飞书文档镜像链接；无 doc 链接则整行不出（数据自包含）
    links = []
    if doc_url:
        links.append("[📄 飞书镜像报告](%s)" % doc_url)
    md_els = [
        {
            "tag": "markdown",
            "content": "**综合开盘倾向 %s · %s**\n%s"
            % (d["overall"], d["verdict"], "\n".join(lines)),
        }
    ]
    if links:
        md_els.append({"tag": "markdown", "content": " · ".join(links)})
    md_els.append(
        {
            "tag": "markdown",
            "text_size": "notation",
            "content": "<font color='grey'>tushare+JEV+zhice-db · "
            "汇率/VIX 模拟源⚡ · %s</font>" % d["ts"],
        }
    )
    card = {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": "🌅 盘前分析 · A股开盘倾向"},
            "subtitle": {
                "tag": "plain_text",
                "content": "综合 %s · 溢价概率 %d%% · %s"
                % (d["overall"], d["prob"], d["verdict"][:18]),
            },
            "template": "orange" if d["alerts"] else "blue",
        },
        "body": {"elements": md_els},
    }
    ok = runner.push_feishu(json.dumps(card, ensure_ascii=False), card=True)
    print("PUSH", ok)


def _cal_open(day):
    """day(yyyymmdd) 是否 A 股开市日；日历读不到 fail-open 返回 True
    （判例同 K96：日历防线自身故障不拦正常跑）。"""
    try:
        import glob
        import pandas as pd

        files = sorted(
            glob.glob(os.path.join(HERE, "data", "tushare", "trade_cal", "*.parquet"))
        )
        cal = pd.concat([pd.read_parquet(f) for f in files]).drop_duplicates(
            subset=["cal_date"]
        )
        r = cal[cal["cal_date"] == day]
        return True if r.empty else bool(int(r.iloc[0]["is_open"]) == 1)
    except Exception:
        return True


def _next_open():
    """K97：未来 7 天内是否存在下一个 A 股交易日（盘前战报服务于下一开盘）。
    2026-10-23 修复（K-QUANT-20261023-PRE 根因二）：原实现只查"明天"——
    周五跑次日必周六 → is_open=0 → 永远 skip，周五盘前战报系统性缺产
    （10-23 判例：今日开市、下一交易日周一，旧逻辑误判休市）。
    改为扫描未来 7 天，任一开市即 True；日历防线异常时仍放行（判例同 K96）。"""
    for i in range(1, 8):
        d = (datetime.now() + timedelta(days=i)).strftime("%Y%m%d")
        if _cal_open(d):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument(
        "--force", action="store_true", help="跳过次日交易日门禁（手动验证用）"
    )
    a = ap.parse_args()
    # K266 P2-2：双门禁（今日+次日开市）。原只查次日——假期最后一天
    # （次日开市、当日休市）放行后当日行情全空，iloc[-1] 家族 IndexError
    # rc=1 误报（10-07 14:30 判例）。当日休市→行情必然缺失，干净 skip。
    today = datetime.now().strftime("%Y%m%d")
    if not a.force and not (_cal_open(today) and _next_open()):
        print("PREMARKET skip：今日或次日非 A 股交易日（休市，不产报不推送）")
        return
    try:
        run(push=a.push)
    except SystemExit:
        raise
    except BaseException:
        # OPS-1009b 防复发加固（2026-10-09）：10-09 07:05 任务层 rc=1 但脚本
        # 无重定向、print 不落盘，崩溃栈丢失无法归因（融合巡检定案：中期
        # 方案=此处落日志）。写完整栈后原样 re-raise，rc=1 语义不变。
        import traceback
        from pathlib import Path as _P

        log = _P(__file__).resolve().parent / "logs" / "premarket_last_error.log"
        try:
            log.parent.mkdir(exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(
                    "\n==== %s (push=%s) ====\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), a.push)
                )
                traceback.print_exc(file=f)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "premarket.py", exc
            )
        raise


def run_premarket_once(run_date: str, dry_run: bool = True) -> dict:
    """盘前产物单次门禁；dry_run 默认只检查新鲜度，不写报告或推送。"""
    latest = os.path.join(REPORTS, "premarket_latest.json")
    artifact_exists = os.path.exists(latest)
    age_hours = None
    if artifact_exists:
        age_hours = max(
            0.0,
            (
                datetime.now() - datetime.fromtimestamp(os.path.getmtime(latest))
            ).total_seconds()
            / 3600.0,
        )
    result = {
        "run_date": str(run_date),
        "dry_run": bool(dry_run),
        "production_write": False,
        "artifact": latest,
        "artifact_exists": artifact_exists,
        "age_hours": age_hours,
        "status": "ready"
        if artifact_exists and age_hours is not None and age_hours <= 26
        else "stale",
    }
    if dry_run:
        return result
    raise RuntimeError(
        "盘前正式运行必须由现有 main 门禁和调度器触发；先完成 dry_run 验收"
    )


if __name__ == "__main__":
    main()
