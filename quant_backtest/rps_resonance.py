# -*- coding: utf-8 -*-
"""RPS 相对强度 + 多维加权打分（借鉴 D:\\青萍之末2 方法论，数据独立部署）。

方法论来源（青萍之末2，只读借鉴未改动其系统）:
  · RPS: src/indicators/rps.py —— 全市场 N 日涨幅百分位(0-100)
  · 共振: src/screeners/resonance_screener.py —— 外部来源原口径为技术0.35/
    板块0.30/RPS0.20/环境0.15 四因子加权，缺维自动降权重分配；环境可一票否决

口径澄清（2026-11-05 审计，勿再称「四维共振」）:
  · JEV 维依赖历史新闻，回测不可重放 → 全窗口恒缺维；实际计分只有
    tech/rps/env 三维，缺维归一后权重 = tech .4667 / rps .3333 / env .2
    （0.35/0.75、0.25/0.75、0.15/0.75，与 qpzm_backtest.py:61 的 W4 同源）
  · 四维加权排序的增量不显著且跨年不稳：W1 +0.0985pp(p=0.54)、
    W2 −0.1637pp、W3 −0.0908pp(p=0.33)，Cochran Q p=0.005（符号会翻）
  · 真正扛住收益的是 tech≥75 门槛与环境闸门，不是维度数量

本模块实现（四市场全市场数据）:
  · A 股全市场: 首次拷贝 D:\\青萍之末2\\data_cache\\daily Parquet，之后
    tushare pro 按交易日增量（token 读其 .env，只读）；RPS 50/120/250
  · 美股/港股/加密全市场: zhice-db v2_bar_daily（同事维护的全量库）
    — 美股/加密历史约半年 → RPS50；港股 → RPS50+120 复合
  · 环境闸门: 全市场宽度（站上MA20占比 + 20日中位涨幅），四市场各一道；
    池内宽度仅作 DB 不可用时兜底
  · 自选池多维加权: 技术(4策略多头占比) + RPS(全市场) + 环境(对应市场闸门)，
    缺维降权；闸门收紧一票否决

主口径约定（T10 落地；全文见 _qpzm_audit_20261105/t10_wording/t10_main_caliber.md）:
  · 持有期 = hold10（T1 平衡点：零成本基准下 hold5 净 −11.5bp、hold10 +24.3bp；
    T5：hold10 排序增量 +0.1973pp > hold5 +0.0815pp，且单调性最好）
  · 成本 = 往返 30bp（CN 单边 cost_bps 12 + slippage_bps 3，买卖各计一次）
  · 默认窗口 = 20251015~20261014，不切全窗口（T9：全窗口差值 −0.0908pp、
    p=0.33，Q 检验 p=0.005 符号不稳）
  · 对外口径：多维加权相对同笔数随机名单「方向为正但统计不显著，且跨年
    不稳定」——禁止引用「+0.0815pp 即有增量」

用法:
  python rps_resonance.py --sync     # 同步 A 股日线（首次拷贝+增量）
  python rps_resonance.py --daily    # 全量：同步+四市场RPS/闸门+自选池打分+页面+推卡
  python rps_resonance.py --dry      # 只算不推卡（调试）
  python rps_resonance.py --shadow-tech   # 影子：技术维×缠论信号打分对比
                                          # （独立入口，不推送不影响 --daily）
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

QP_DAILY = r"D:\青萍之末2\data_cache\daily"  # 青萍数据（只读源）
QP_ENV = r"D:\青萍之末2\.env"  # tushare token
LOCAL = os.path.join(HERE, "data", "ashare")  # 工作站自有副本
REPORTS = os.path.join(HERE, "reports")
NAMES_CACHE = os.path.join(LOCAL, "_names.json")
PORTAL = (
    json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    .get("report", {})
    .get("portal_url", "")
)
PERIODS = (50, 120, 250)  # A 股 RPS 周期（tushare 历史充足）
RPS_W = {50: 0.4, 120: 0.3, 250: 0.3}  # 复合分权重（按可用周期归一化）
POOL_GATE_MIN = 5  # 兜底池内闸门最低样本数
DB_MARKETS = ("US", "HK", "CRYPTO")  # zhice-db 全市场闸门/选股市场
DB_PERIODS = {"US": (50,), "HK": (50, 120), "CRYPTO": (50,)}
DB_LOOKBACK = {"US": 200, "HK": 300, "CRYPTO": 200}  # 拉取自然日窗口
NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")

# 选股中心市场分域：key → 页面/榜单/情绪映射
MARKETS = {
    "cn": {
        "label": "🇨🇳 A股",
        "file": "bt_m_cn.html",
        "tops": "CN",
        "mkt": "CN",
        "scope": "cn",
        "top_n": 30,
    },
    "us": {
        "label": "🇺🇸 美股",
        "file": "bt_m_us.html",
        "tops": "US",
        "mkt": "US",
        "scope": "us",
        "top_n": 20,
    },
    "hk": {
        "label": "🇭🇰 港股",
        "file": "bt_m_hk.html",
        "tops": "HK",
        "mkt": "HK",
        "scope": "hk",
        "top_n": 20,
    },
    "crypto": {
        "label": "🪙 加密",
        "file": "bt_m_crypto.html",
        "tops": "CRYPTO",
        "mkt": "CRYPTO",
        "scope": None,
        "top_n": 10,
    },
}
STRAT_CN = {
    "trend_follow": "趋势跟随",
    "ma_cross": "均线交叉",
    "rsi_reversal": "RSI反转",
    "boll_breakout": "布林突破",
}
STRAT_LOGIC = {
    "trend_follow": "收盘价在 N 日均线之上且均线斜率为正 → 做多",
    "ma_cross": "快均线上穿慢均线做多，下穿空仓",
    "rsi_reversal": "RSI 跌破超卖线做多，升破超买线空仓",
    "boll_breakout": "收盘突破布林上轨做多，跌破中轨空仓",
}


def _strat_pos_wide(sn, wide, params):
    """宽表向量化复刻 strategies.REGISTRY（策略仅用 close，逐列数学一致）。"""
    import numpy as np

    if sn == "ma_cross":
        fa = wide.rolling(int(params.get("fast", 5)), min_periods=1).mean()
        sl = wide.rolling(int(params.get("slow", 20)), min_periods=1).mean()
        return (fa > sl).fillna(False).astype(int)
    if sn == "trend_follow":
        m = wide.rolling(int(params.get("n", 20)), min_periods=1).mean()
        slope = m.diff(int(params.get("slope_lookback", 5)))
        return ((wide > m) & (slope > 0)).fillna(False).astype(int)
    if sn == "rsi_reversal":
        n = int(params.get("period", 14))
        delta = wide.diff()
        gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(n, min_periods=1).mean()
        rs = gain / loss.replace(0, np.nan)
        r = (100 - 100 / (1 + rs)).fillna(50)
        # 事件帧：NA=未触发（空仓），超卖进/超买出，与 REGISTRY 新版等价
        val = wide * np.nan
        val = val.mask(r < float(params.get("oversold", 30)), 1)
        val = val.mask(r > float(params.get("overbought", 70)), 0)
        return val.ffill().fillna(0).astype(int)
    if sn == "boll_breakout":
        n, k = int(params.get("period", 20)), float(params.get("k", 2))
        mid = wide.rolling(n, min_periods=1).mean()
        std = wide.rolling(n, min_periods=1).std(ddof=0)
        upper = mid + k * std
        val = wide.gt(upper).astype(float).replace(0, np.nan)
        val = val.ffill().fillna(0)
        return val.mask(wide < mid, 0).astype(int)
    raise ValueError(sn)


def _strat_market_stats(closes, params_all, name_map):
    """一个市场 → 各策略 {n, long, pct, new_long[近5日翻多 top8]}。"""
    import numpy as np
    import pandas as pd

    if not closes:
        return {}
    cols = {}
    for s, v in closes.items():
        a = np.asarray(v, dtype=float)
        if len(a) < 30:  # 不足慢线窗口，不参与
            continue
        cols[s] = a
    if not cols:
        return {}
    wide = pd.DataFrame({k: pd.Series(v) for k, v in cols.items()})
    chg = wide.iloc[-1] / wide.iloc[-21] - 1 if len(wide) >= 21 else None
    out = {}
    for sn, params in params_all.items():
        try:
            pos = _strat_pos_wide(sn, wide, dict(params))
            last = pos.iloc[-1]
            prev = pos.iloc[-6] if len(pos) >= 6 else pos.iloc[0]
            longs = int((last > 0).sum())
            newly = [
                s for s in wide.columns if last.get(s, 0) > 0 and prev.get(s, 0) <= 0
            ]
            newly.sort(
                key=lambda s: (
                    -(chg.get(s) if chg is not None and pd.notna(chg.get(s)) else -9)
                )
            )
            out[sn] = {
                "n": int(len(wide.columns)),
                "long": longs,
                "pct": round(longs * 100 / max(len(wide.columns), 1)),
                "new_long": [
                    {
                        "sym": s,
                        "name": (name_map.get(s) or "")[:16],
                        "chg20": round(float(chg.get(s)) * 100, 1)
                        if chg is not None and pd.notna(chg.get(s))
                        else None,
                    }
                    for s in newly[:8]
                ],
            }
        except Exception:
            continue
    return out


def _bt_agg(sn):
    """backtest_results.jsonl 按策略聚合绩效（全史，含重复跑）。"""
    runs = []
    try:
        with open(os.path.join(HERE, "backtest_results.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("strategy") == sn:
                    runs.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/rps_resonance.py", exc)
    if not runs:
        return {"n": 0}

    def mean(k):
        v = [x.get(k) for x in runs if x.get(k) is not None]
        return sum(v) / len(v) if v else 0

    pm = {}
    for x in runs:
        pm.setdefault(x.get("market", "?"), []).append(x)
    return {
        "n": len(runs),
        "sharpe": round(mean("sharpe"), 2),
        "ret": round(mean("total_return") * 100, 1),
        "dd": round(mean("max_drawdown") * 100, 1),
        "win": round(mean("win_rate") * 100),
        "trades": int(mean("trades")),
        "per_mkt": {
            mk: {
                "n": len(v),
                "sharpe": round(sum(y.get("sharpe") or 0 for y in v) / len(v), 2),
                "ret": round(
                    sum(y.get("total_return") or 0 for y in v) / len(v) * 100, 1
                ),
            }
            for mk, v in pm.items()
        },
        "window": runs[-1].get("window", ""),
    }


# ── tushare ────────────────────────────────────────────────
_NO_TOKEN_WARNED = False


def _token():
    """token 解析：env TUSHARE_TOKEN → 同目录 .env.local（XT_DATA_KEY，
    与 tushare_sync.py 同源，SYSTEM 任务可读）→ QP_ENV .env（旧源，兼容）。

    2026-10-10 审计修复：QP_ENV 目录已删，SYSTEM 任务两路皆空返回空串，
    导致 fina/forecast/index 三接口静默断供 9 天（九维 0 出分）。"""
    global _NO_TOKEN_WARNED
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    for path, key in (
        (os.path.join(HERE, ".env.local"), "XT_DATA_KEY="),
        (QP_ENV, "TUSHARE_TOKEN="),
    ):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith(key):
                        v = line.strip().split("=", 1)[1]
                        if v:
                            return v
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/rps_resonance.py", exc)
    if not _NO_TOKEN_WARNED:
        _NO_TOKEN_WARNED = True
        print(
            "[WARN] TUSHARE_TOKEN 不可得（env 未设 / .env.local 缺 "
            "XT_DATA_KEY / QP_ENV 不可读），tushare 接口将全部失败",
            file=sys.stderr,
        )
    return ""


def _ts(api, **params):
    """tushare POST；失败重试 1 次。返回 (rows, fields) 或 raise。"""
    import requests

    for attempt in (0, 1):
        try:
            r = requests.post(
                "http://api.tushare.pro",
                json={
                    "api_name": api,
                    "token": _token(),
                    "params": params,
                    "fields": "",
                },
                timeout=25,
            )
            j = r.json()
            if j.get("code") == 0:
                d = j["data"]
                return d["items"], d["fields"]
            raise RuntimeError(str(j.get("message"))[:100])
        except Exception:
            if attempt:
                raise
            time.sleep(1.5)


def _names():
    """A 股 ts_code → 股票名（stock_basic 缓存 30 天）。"""
    if (
        os.path.exists(NAMES_CACHE)
        and time.time() - os.path.getmtime(NAMES_CACHE) < 86400 * 30
    ):
        return json.load(open(NAMES_CACHE, encoding="utf-8"))
    rows, fields = _ts("stock_basic", list_status="L", fields="ts_code,name")
    i, n = fields.index("ts_code"), fields.index("name")
    m = {r[i]: r[n] for r in rows}
    os.makedirs(LOCAL, exist_ok=True)
    with open(NAMES_CACHE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    return m


def _names_safe():
    try:
        return _names()
    except Exception:
        return {}


# ── zhice-db 全市场 ────────────────────────────────────────
def _db_query(sql, params):
    """zhice-db 直连优先，docker psql 兜底。返回 rows 元组列表。"""
    import data_loader as dl

    try:
        _, rows = dl._query_direct(sql, params)
        return rows
    except Exception:
        _, rows = dl._query_docker(sql, params)
        return rows


def _db_closes(market, days):
    """v2_bar_daily → {symbol: close数组}（近 days 自然日，升序）。"""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = _db_query(
        "SELECT symbol, trade_date, close FROM v2_bar_daily "
        "WHERE market=%s AND trade_date >= %s AND close IS NOT NULL "
        "ORDER BY symbol, trade_date",
        [market, start],
    )
    closes = {}
    for sym, _td, c in rows:
        closes.setdefault(sym, []).append(float(c))
    return {s: pd.Series(v).to_numpy() for s, v in closes.items()}


def _db_names():
    """v2_instrument → {(market, symbol): name}。"""
    rows = _db_query("SELECT market, symbol, name FROM v2_instrument", [])
    return {(m, s): n for m, s, n in rows}


# ── 同步（A 股 tushare） ───────────────────────────────────
def sync_ashare():
    """首次: 拷贝青萍 Parquet；增量: tushare 按交易日补齐到最新。"""
    os.makedirs(LOCAL, exist_ok=True)
    first = not any(f.endswith(".parquet") for f in os.listdir(LOCAL))
    if first:
        n = 0
        for f in os.listdir(QP_DAILY):
            if f.endswith(".parquet"):
                shutil.copy2(os.path.join(QP_DAILY, f), os.path.join(LOCAL, f))
                n += 1
        print("SYNC copy %d files from qingping" % n)

    # 本地最新交易日（抽样 30 个文件取最大）
    import random

    fs = [f for f in os.listdir(LOCAL) if f.endswith(".parquet")]
    sample = random.sample(fs, min(30, len(fs)))
    max_d = ""
    for f in sample:
        try:
            df = pd.read_parquet(os.path.join(LOCAL, f), columns=["trade_date"])
            if len(df):
                max_d = max(max_d, str(df["trade_date"].max()))
        except Exception:
            continue
    if not max_d:
        print("SYNC no local data")
        return
    rows, fields = _ts(
        "trade_cal",
        exchange="SSE",
        start_date=max_d,
        end_date=datetime.now().strftime("%Y%m%d"),
    )
    i_o = fields.index("is_open")
    i_c = fields.index("cal_date")
    dates = sorted(r[i_c] for r in rows if r[i_o] == 1 and r[i_c] > max_d)
    if not dates:
        print("SYNC up-to-date %s" % max_d)
        return
    print("SYNC %d trade days to fetch (%s..%s)" % (len(dates), dates[0], dates[-1]))

    # 全部载入内存 → 合并 → 一次性写回
    frames = {}
    for f in fs:
        code = f[:-8].replace("_", ".")  # 600519_SH → 600519.SH
        try:
            frames[code] = pd.read_parquet(os.path.join(LOCAL, f))
        except Exception:
            continue
    for d in dates:
        got, off = [], 0
        while True:
            rows, fields = _ts("daily", trade_date=d, offset=off, limit=6000)
            if not rows:
                break
            got.extend(rows)
            if len(rows) < 6000:
                break
            off += 6000
        if not got:
            continue
        dfd = pd.DataFrame(
            got,
            columns=[
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "volume",
                "amount",
            ],
        )
        for code, g in dfd.groupby("ts_code"):
            if code in frames:
                frames[code] = pd.concat([frames[code], g], ignore_index=True)
            else:  # 新上市
                frames[code] = g
        time.sleep(0.3)
    n = 0
    for code, df in frames.items():
        df = (
            df.drop_duplicates("trade_date", keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
        f = code.replace(".", "_") + ".parquet"
        df.to_parquet(os.path.join(LOCAL, f))
        n += 1
    print("SYNC done %d files -> %s" % (n, dates[-1]))


# ── RPS + 环境 ─────────────────────────────────────────────
def _load_market():
    """A 股 {code: close数组} + 最新交易日。只读 close 一列。"""
    closes, latest = {}, ""
    for f in os.listdir(LOCAL):
        if not f.endswith(".parquet") or f.startswith("_"):
            continue
        try:
            df = pd.read_parquet(
                os.path.join(LOCAL, f), columns=["trade_date", "close"]
            )
            if len(df) < 30:
                continue
            closes[f[:-8].replace("_", ".")] = df["close"].to_numpy()
            latest = max(latest, str(df["trade_date"].max()))
        except Exception:
            continue
    return closes, latest


def batch_rps(closes, periods=None):
    """全市场 RPS 百分位（向量化 rank）→ 按可用周期加权的复合分。
    rps_mix = Σ(w_p × rps_p) / Σ(w_p of 可用周期)。
    new_flag（S5 影子字段，纯标记不改打分/过滤）: 可观测 K 线 <250 根
    → True。A 股源为全量历史（首次拷贝青萍全量 + tushare 增量），根数
    ≈ 上市以来交易日数，故等价「上市不足 250 个交易日」；美股/港股/
    加密 zhice-db 拉取窗口仅 200/300 自然日，其 new_flag 只代表
    「拉取窗口内可观测历史不足」，不可作次新口径。"""
    periods = periods or PERIODS
    data = {}
    for p in periods:
        chg = {}
        for code, c in closes.items():
            if len(c) >= p + 1 and c[-(p + 1)] > 0:
                chg[code] = c[-1] / c[-(p + 1)] - 1
        s = pd.Series(chg)
        data["rps_%d" % p] = (s.rank(pct=True) * 100).round(1)
    df = pd.DataFrame(data)
    if df.empty:
        return df
    cols = list(df.columns)
    w = pd.Series({c: RPS_W.get(int(c.split("_")[1]), 1.0 / len(cols)) for c in cols})
    df["rps_mix"] = (
        df.mul(w, axis=1).sum(axis=1) / df.notna().mul(w, axis=1).sum(axis=1)
    ).round(1)
    df["new_flag"] = pd.Series(
        {c: bool(len(closes.get(c, ())) < 250) for c in df.index}
    )
    return df.sort_values("rps_mix", ascending=False)


def market_env(closes):
    """宽度法环境闸门: 站上MA20占比 + 20日中位涨幅。
    safe 三态（S6 修正）: True=放行 / False=一票否决 / None=数据缺失
    —— 有效样本 < POOL_GATE_MIN 时宽度无统计意义，返回 None 让自选池
    打分对该市场环境维走缺维降权，不再误触发一票否决。"""
    above, ret20 = [], []
    for c in closes.values():
        if len(c) >= 21:
            above.append(c[-1] > c[-20:].mean())
            ret20.append(c[-1] / c[-21] - 1)
    if len(above) < POOL_GATE_MIN:
        return {
            "score": 50,
            "breadth": 0,
            "ret20": 0,
            "upfrac": None,
            "safe": None,
            "state": "数据缺失（有效样本 %d <%d，环境维缺维降权）"
            % (len(above), POOL_GATE_MIN),
        }
    breadth = sum(above) / len(above)
    med = float(pd.Series(ret20).median())
    upfrac = sum(r > 0 for r in ret20) / len(ret20)
    score = round(min(100, max(0, 60 * breadth + 40 * upfrac)))
    if breadth >= 0.55 and med > 0:
        state = "偏暖（宽度 %.0f%%，20日中位 %+.1f%%）" % (breadth * 100, med * 100)
        safe = True
    elif breadth < 0.40 or med < -0.03:
        state = "偏冷（宽度 %.0f%%，20日中位 %+.1f%%）— 闸门收紧" % (
            breadth * 100,
            med * 100,
        )
        safe = False
    else:
        state = "中性（宽度 %.0f%%，20日中位 %+.1f%%）" % (breadth * 100, med * 100)
        safe = True
    return {
        "score": score,
        "breadth": round(breadth, 3),
        "ret20": round(med, 4),
        # [20261014_m metrics-dev] upfrac 落快照：三档影子对照
        # gate_shadow_collect.read_qpzm 读此字段；纯增量、不影响打分
        "upfrac": round(upfrac, 4),
        "safe": safe,
        "state": state,
    }


def _jev_market(entries, scope):
    """近7天该市场关联新闻的 JEV 情绪聚合（news.db，含逐条下钻）。"""
    import sqlite3

    kws = set()
    for e in entries:
        if e.get("name"):
            kws.add(e["name"])
        for a in e.get("aliases") or []:
            if len(str(a)) >= 2:
                kws.add(str(a))
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        rows = db.execute(
            "SELECT ts, title, jev FROM news WHERE jev IS NOT NULL "
            "AND created_at>=? ORDER BY id DESC LIMIT 2000",
            (since,),
        ).fetchall()
        db.close()
    except Exception:
        return {"n": 0, "bull": 0, "bear": 0, "neu": 0, "items": []}
    bull = bear = neu = 0
    items = []
    for ts, title, j in rows:
        if not title:
            continue
        try:
            jv = json.loads(j)
        except Exception:
            continue
        hit = any(k in title for k in kws)
        if not hit and scope and jv.get("scope") == scope:
            hit = True
        if not hit:
            continue
        b = jv.get("bias")
        if b == "bull":
            bull += 1
        elif b == "bear":
            bear += 1
        else:
            neu += 1
        items.append(
            {
                "ts": (ts or "")[:16],
                "title": title,
                "bias_cn": jv.get("bias_cn", "?"),
                "conf": round((jv.get("conf") or 0) * 100),
                "reason": jv.get("reason", ""),
            }
        )
    return {
        "n": bull + bear + neu,
        "bull": bull,
        "bear": bear,
        "neu": neu,
        "items": items[:6],
    }


def _jev_pool_scores(wl):
    """自选池逐股 JEV 消息面分（近7天已判断新闻，单次读库批量匹配）。
    score = 50 + 平均净多强度/2（50中性，利多逼近100，利空逼近0）。"""
    import sqlite3

    kws = {}  # 关键词 → watchlist code
    for e in wl:
        code = e.get("code", "")
        if not code:
            continue
        ks = {e.get("name") or ""}
        for a in e.get("aliases") or []:
            if len(str(a)) >= 2:
                ks.add(str(a))
        ks.discard("")
        for k in ks:
            kws.setdefault(k, code)
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        rows = db.execute(
            "SELECT title, jev FROM news WHERE jev IS NOT NULL "
            "AND created_at>=? ORDER BY id DESC LIMIT 3000",
            (since,),
        ).fetchall()
        db.close()
    except Exception:
        return {}
    agg = {}
    for title, j in rows:
        if not title:
            continue
        try:
            jv = json.loads(j)
        except Exception:
            continue
        b = jv.get("bias")
        if b not in ("bull", "bear"):  # 中性不计方向强度
            continue
        cf = (jv.get("conf") or 0) * 100
        for k, code in kws.items():
            if k in title:
                a = agg.setdefault(code, {"net": 0.0, "n": 0, "hits": []})
                a["net"] += cf if b == "bull" else -cf
                a["n"] += 1
                # 溯源存证（077j）：命中明细逐条留痕，供未来重放/审计
                if len(a["hits"]) < 20:
                    a["hits"].append(
                        {"kw": k, "title": title[:80], "bias": b, "conf": round(cf, 2)}
                    )
                break
    _archive_jev(since, agg)
    return {
        code: {
            "score": round(max(0, min(100, 50 + (a["net"] / a["n"]) / 2))),
            "n": a["n"],
        }
        for code, a in agg.items()
        if a["n"]
    }


def _archive_jev(since, agg):
    """JEV 打分快照归档（077j 老田令：精准溯源）。

    每次 _jev_pool_scores 落 data/jev_archive/jev_YYYYMMDD.json：
    当日 7 天窗逐股命中明细（关键词/标题/bias/conf）+ 聚合分。
    回测重放直接消费档案，不再受 news.db 查询窗与 watchlist 漂移限制。
    原子写；失败只留痕不阻断产线。"""
    try:
        adir = os.path.join(HERE, "data", "jev_archive")
        os.makedirs(adir, exist_ok=True)
        fp = os.path.join(adir, "jev_%s.json" % datetime.now().strftime("%Y%m%d"))
        doc = {
            "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window_since": since,
            "pool": {
                code: {
                    "score": round(max(0, min(100, 50 + (a["net"] / a["n"]) / 2))),
                    "n": a["n"],
                    "hits": a.get("hits", []),
                }
                for code, a in agg.items()
                if a["n"]
            },
        }
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, fp)
    except Exception as e:
        sys.stderr.write(" ! jev 归档失败（不阻断产线）: %s\n" % e)


def _link_code(code):
    """榜单/自选池行 → /kg/stock/ 的入参（watchlist 代码形态）。"""
    from urllib.parse import quote

    code = str(code)
    if len(code) == 9 and code[6] == ".":  # 600519.SH → sh600519
        code = code[7:].lower() + code[:6]
    return quote(code, safe="")


# ── 自选池多维加权（缺维降权，规则照搬青萍 resonance_screener） ──
def resonance_watchlist(rps_df, env, full_envs=None, full_rps=None):
    """watchlist 逐股多维加权打分（实际三维：tech/rps/env）。
    技术 = 4策略多头占比*100 ± MA20修正；JEV = 近7天关联新闻消息面；
    RPS = 全市场（A股 tushare / 美港加密 zhice-db，DB 缺符号时兜底池内
    百分位）；环境 = 对应市场全市场闸门（DB 失败兜底池内闸门）。
    权重 技术.35/JEV.25/RPS.25/环境.15（青萍规则），缺维按比例降权。
    JEV 依赖历史新闻、回测不可重放 → 全窗口恒缺维，实际归一为
    tech .4667 / rps .3333 / env .2。"""
    from data_loader import load_bars
    import strategies as strat
    import runner

    full_envs, full_rps = full_envs or {}, full_rps or {}
    wl = json.load(
        open(os.path.join(runner.NEWS_DIR, "news_config.json"), encoding="utf-8")
    )["watchlist"]
    wl_added = {e.get("code"): e.get("added") for e in wl if e.get("added")}
    jev_scores = _jev_pool_scores(wl)
    entries = runner.resolve_watchlist(wl)
    by_mkt = {}
    for mkt, sym, nm in entries:
        by_mkt.setdefault(mkt, []).append((sym, nm))

    # 池内 50 日涨幅（兜底 RPS）+ 池内闸门（兜底环境）
    start = (datetime.now() - pd.Timedelta(days=130)).strftime("%Y-%m-%d")
    bars, pool_chg = {}, {}
    for mkt, pairs in by_mkt.items():
        got = load_bars(mkt, [s for s, _ in pairs], start, None)
        chg = {}
        for sym, nm in pairs:
            df = got.get(sym)
            if df is None or len(df) < 55:
                continue
            bars[(mkt, sym)] = df
            chg[sym] = float(df["close"].iloc[-1] / df["close"].iloc[-51] - 1)
        s = pd.Series(chg)
        # R3 修复(077e 选股审计): 兜底池内 RPS 需池量门槛(与池内闸门同
        # POOL_GATE_MIN)——小池 rank 百分位是纯噪声, 不足门槛记缺维降权,
        # 不硬凑百分位
        pool_chg[mkt] = (
            ((s.rank(pct=True) * 100).round(1)).to_dict()
            if len(s) >= POOL_GATE_MIN
            else {}
        )

    pool_envs = {}
    for mkt, pairs in by_mkt.items():
        if mkt == "CN" or len(pairs) < POOL_GATE_MIN:
            continue
        cs = {
            sym: bars[(mkt, sym)]["close"].to_numpy()
            for sym, _ in pairs
            if (mkt, sym) in bars
        }
        if len(cs) >= POOL_GATE_MIN:
            pool_envs[mkt] = market_env(cs)

    rows = []
    for e in wl:
        code, nm = e.get("code", ""), e.get("name", "")
        mkt, sym = _to_market(code, bars)
        df = bars.get((mkt, sym))
        if df is None or len(df) < 55:
            continue
        # 技术分（逐策略信号留档，供市场页信号矩阵用）
        longs, sigs = 0, {}
        for sn in ("trend_follow", "ma_cross", "rsi_reversal", "boll_breakout"):
            try:
                sig = strat.REGISTRY[sn](df, dict(runner.CFG["strategies"].get(sn, {})))
                sv = int(sig.values[-1])
                sigs[sn] = sv
                longs += int(sv > 0)
            except Exception as e:
                # R2 修复(077e 选股审计): 策略异常留痕——tech 分参与统计
                # (A5 判据), 全炸时退化纯 MA 修正仍在出数, 静默即漏网
                sys.stderr.write("    ! %s %s 策略 %s 异常: %s\n" % (mkt, sym, sn, e))
        tech = longs * 25
        ma20 = df["close"].iloc[-20:].mean()
        tech = min(100, max(0, tech + (10 if df["close"].iloc[-1] > ma20 else -10)))
        # RPS 分（全市场优先，池内兜底）
        rps_label, rps = "—", None
        if mkt == "CN":
            ts_code = _to_ts(code)  # sh600519 → 600519.SH
            if ts_code in rps_df.index:
                rps = float(rps_df.loc[ts_code, "rps_mix"])
                rps_label = "全市场"
        elif mkt in full_rps and sym in full_rps[mkt].index:
            rps = float(full_rps[mkt].loc[sym, "rps_mix"])
            rps_label = "全市场"
        if rps is None and sym in pool_chg.get(mkt, {}):
            rps = float(pool_chg[mkt][sym])
            rps_label = "池内"
        # 环境分（对应市场全市场闸门，池内兜底）；safe=None（缺数据）→
        # 环境维缺维降权，不把缺数据分数混入加权
        env_ref = env if mkt == "CN" else (full_envs.get(mkt) or pool_envs.get(mkt))
        env_s = (
            env_ref["score"] if env_ref and env_ref.get("safe") is not None else None
        )
        # JEV 消息面分（近7天该股关联已判断新闻，无判断则缺维）
        js = jev_scores.get(code)
        jev = js["score"] if js else None

        # 多维权重（技术/消息面JEV/RPS/环境，青萍结构）；缺维按比例降权
        # （JEV 依赖历史新闻、回测不可重放 → 恒缺维，实际按 tech/rps/env 归一）
        W4 = {"tech": 0.35, "jev": 0.25, "rps": 0.25, "env": 0.15}
        parts = {"tech": tech}
        if jev is not None:
            parts["jev"] = jev
        if rps is not None:
            parts["rps"] = rps
        if env_s is not None:
            parts["env"] = env_s
        tot = sum(W4[k] for k in parts)
        score = sum(parts[k] * W4[k] / tot for k in parts)
        # K233 调校修正：先取整再进阈值——判定与展示同一口径
        # （修正前 action 用原始浮点分、页面显示 round 分，边界票出现
        #  「75 分·观察」自相矛盾；四法门审计 gb_nvda 案发）
        score = int(round(score))
        w = {k: round(W4[k] / tot, 3) for k in parts}
        # S6: 仅「数据齐且触发否决」才一票否决；None（缺数据）不回避
        if env_ref and env_ref.get("safe") is False:
            action = "回避（环境闸门）"
        else:
            action = "买入" if score >= 75 else ("观察" if score >= 50 else "回避")
        # S5 落地(K230 回测实证): A 股次新(可观测K线<250)不产生买入信号，
        # 过线降级「观察（次新）」。仅限 CN——美/港/加密拉取窗口短，
        # new_flag 系统性 True 不具次新语义，消费即误杀。
        is_subnew = False
        if mkt == "CN" and ts_code in rps_df.index:
            is_subnew = bool(rps_df.loc[ts_code].get("new_flag", False))
        if is_subnew and action == "买入":
            action = "观察（次新）"
        dims = list(parts)
        rows.append(
            {
                "name": nm,
                "market": mkt,
                "code": code,
                "px": round(float(df["close"].iloc[-1]), 2),
                "tech": tech,
                "jev": jev,
                "jev_n": (js or {}).get("n"),
                "rps": rps,
                "rps_label": rps_label,
                "env": env_s,
                "score": score,
                "new_flag": is_subnew,
                "added": wl_added.get(code),
                "action": action,
                "dims": "+".join(dims),
                "w": w,
                "longs": longs,
                "sigs": sigs,
                "date": df.index[-1].strftime("%m-%d"),
            }
        )
    rows.sort(key=lambda x: -x["score"])
    return rows, pool_envs


def _to_market(code, bars=None):
    """watchlist 代码 → (market, db_symbol)。
    US/CRYPTO 双注册时（resolve_watchlist 的兜底机制），取 bars 里有数据的市场。"""
    import runner

    cands = runner.resolve_watchlist([{"code": code, "name": ""}])
    for mkt, sym, _nm in cands:
        if bars is None or (mkt, sym) in bars:
            return mkt, sym
    return cands[0][0], cands[0][1]


def _to_ts(code):
    """sh600519 → 600519.SH；sz/bj 同理。"""
    if code.startswith(("sh", "sz", "bj")):
        return code[2:] + "." + code[:2].upper()
    return code


# ── 页面 + 卡片 ────────────────────────────────────────────
def _top_rows(rps_df, closes, names, n=30):
    """RPS 榜行：sym/name/px/chg20/r50/(r120)/mix。"""
    out = []
    for sym, r in rps_df.head(n).iterrows():
        c = closes.get(sym, [])
        chg20 = round((c[-1] / c[-21] - 1) * 100, 1) if len(c) >= 21 else None
        row = {
            "sym": sym,
            "name": (names.get(sym, "") or "")[:18],
            "px": round(float(c[-1]), 2) if len(c) else None,
            "chg20": chg20,
            "r50": r.get("rps_50"),
            "r120": r.get("rps_120"),
            "mix": r.rps_mix,
        }
        out.append(row)
    return out


_CSS = """<style>
body{background:#0d1117;color:#c9d1d9;font:14px/1.6 system-ui;
margin:0;padding:24px}
h1{font-size:20px;color:#58a6ff;margin:0 0 6px}
h2{font-size:15px;color:#3fd6c5;margin:26px 0 8px}
table{border-collapse:collapse;width:100%;margin-top:8px}
td,th{border:1px solid #30363d;padding:6px 10px;font-size:13px;
text-align:left}
th{background:#161b22;color:#8b949e}
.env{border:1px solid #30363d;border-radius:8px;padding:12px 16px;
background:#161b22;margin-top:8px}
.env-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.dim{font-size:11px;color:#8b949e}
.buy{color:#f85149}.watch{color:#d29922}.avoid{color:#3fb950}
a{color:#58a6ff;text-decoration:none}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
.nav a,.nav span{display:inline-block;padding:6px 13px;border:1px solid
#30363d;border-radius:16px;color:#58a6ff;text-decoration:none;
font-size:12.5px;background:#161b22}
.nav span{color:#e6edf3;border-color:#58a6ff}
.jev-item{border:1px solid #30363d;border-radius:8px;padding:8px 12px;
background:#161b22;margin-top:6px;font-size:13px}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;
font-size:11.5px;margin-right:8px}
.t-bull{background:#3a1219;color:#f85149}
.t-bear{background:#0f2e1b;color:#3fb950}
.t-neu{background:#2b2f1a;color:#d29922}
.b-long{color:#f85149;font-weight:600}
.b-flat{color:#8b949e}
.b-short{color:#3fb950;font-weight:600}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;
margin:8px 0 4px;background:#21262d;max-width:420px}
.bar i{display:block;height:100%}
.module{border:1px solid #30363d;border-radius:10px;background:#161b22;
margin:14px 0;overflow:hidden}
.mod-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
padding:10px 16px;border-bottom:1px solid #30363d;background:#10151c}
.mod-head b{font-size:15px}
.mod-head .enter{margin-left:auto;font-size:12.5px;white-space:nowrap}
.mod-env{padding:6px 16px 0;font-size:12px}
.badge{display:inline-block;padding:1px 9px;border-radius:10px;
font-size:11.5px}
.bd-buy{background:#3a1219;color:#f85149}
.bd-watch{background:#2b2f1a;color:#d29922}
.bd-avoid{background:#0f2e1b;color:#3fb950}
.bd-none{background:#21262d;color:#8b949e}
.module table{margin-top:4px}
.module td,.module th{font-size:12.5px;padding:5px 9px}
</style>"""


def _page(title, body):
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,"
        "initial-scale=1'><title>"
        + title
        + "</title>"
        + _CSS
        + "</head><body>"
        + body
        + "</body></html>"
    )


def _top_nav(key="bt"):
    """K39 面包屑：与门户 SSR 同源——portal_api.pill_nav 单一数据源
    （首页 › 域 › 当前页 + ☰ 域内下拉）。废除旧五板块胶囊（金融/效率
    为 K37 已废板块名）。portal 不可导入时回退极简面包屑，
    生成任务永不因门户故障断页。"""
    try:
        import sys

        _p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"
        )
        if _p not in sys.path:
            sys.path.insert(0, _p)
        from portal_api import pill_nav

        return pill_nav(key)
    except Exception:
        return (
            "<div class='nav'><a href='/'>🏠 首页</a>"
            "<a href='/quant'>📈 量化</a>"
            "<a href='/bt'>🎯 选股中心</a></div>"
        )


def _mkt_nav(cur):
    parts = [
        _top_nav(),
        "<div class='nav'><a href='/fin'>🏠 金融驾驶舱</a>"
        "<a href='/bt'>🎯 选股中心</a>",
    ]
    for k, m in MARKETS.items():
        if k == cur:
            parts.append("<span>%s</span>" % m["label"])
        else:
            parts.append("<a href='/bt/m/%s'>%s</a>" % (k, m["label"]))
    parts.append("</div>")
    return "".join(parts)


def _env_card(label, e):
    sv = e.get("safe")  # S6 三态: True/False/None
    if sv is None:
        cls, st = "watch", "缺维降权（数据缺失）"
    elif sv:
        cls, st = "buy", "放行"
    else:
        cls, st = "avoid", "一票否决"
    return (
        "<div class='env'><b>%s</b> · 综合分 <b class='%s'>%d</b>"
        " · 闸门 %s<div class='dim' style='margin-top:4px'>%s</div>"
        "</div>" % (label, cls, e["score"], st, e["state"])
    )


def _sig_badge(v):
    if v is None:
        return "<td class='b-flat'>—</td>"
    if v > 0:
        return "<td class='b-long'>多</td>"
    if v < 0:
        return "<td class='b-short'>空</td>"
    return "<td class='b-flat'>平</td>"


def write_pages(env_by_key, res, latest, tops, wl_by_mkt):
    """选股中心：hub + 四市场页（五块结构）+ 快照。"""
    os.makedirs(REPORTS, exist_ok=True)
    foot = (
        "<div class='dim' style='margin-top:26px'>数据截至 %s · 共振四维"
        "=技术/JEV消息面/RPS/环境（缺维降权，青萍结构）· 环境闸门="
        "全市场宽度 · A股=tushare 美港加密=zhice-db · JEV 情绪来自 "
        "news.db 近7天已判断新闻 · 纸面研究不构成投资建议</div>" % latest
    )

    for k, meta in MARKETS.items():
        parts = [_mkt_nav(k), "<h1>%s · 市场全景</h1>" % meta["label"]]
        lb, e = env_by_key.get(k, ("—", {}))
        parts.append("<h2>① 环境闸门（全市场宽度）</h2>")
        parts.append(_env_card(lb, e) if e else "<div class='env'>暂无数据</div>")

        # ② 全市场 RPS 榜（行 → 个股全景页）
        rows = tops.get(meta["tops"], [])[: meta["top_n"]]
        if rows:
            trs = []
            for t in rows:
                trs.append(
                    "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td>%s</td><td>%s</td><td>%.0f</td>%s"
                    "<td><b>%.1f</b></td></tr>"
                    % (
                        _link_code(t["sym"]),
                        t["sym"],
                        _link_code(t["sym"]),
                        (t["name"] or t["sym"])[:18],
                        ("%.2f" % t["px"]) if t.get("px") else "—",
                        ("%+.1f%%" % t["chg20"]) if t.get("chg20") is not None else "—",
                        t.get("r50") or 0,
                        ("<td>%.0f</td>" % t["r120"])
                        if t.get("r120") is not None
                        else "",
                        t["mix"],
                    )
                )
            has120 = rows[0].get("r120") is not None
            parts.append(
                "<h2>② 全市场 RPS 榜（点标的看个股全景）</h2>"
                "<table><tr><th>代码</th><th>名称</th><th>现价"
                "</th><th>近20日</th><th>RPS50</th>%s<th>复合</th>"
                "</tr>%s</table>" % ("<th>RPS120</th>" if has120 else "", "".join(trs))
            )

        # ③ 自选池打分（本市场子集）
        mres = [r for r in res if r["market"] == meta["mkt"]]
        if mres:
            trs = "".join(
                "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                "<td>%.2f</td><td>%d</td><td>%s</td><td>%s%.0f</td>"
                "<td>%s</td><td><b>%d</b></td><td class='%s'>%s</td>"
                "<td>%s</td><td class='dim'>%s</td></tr>"
                % (
                    _link_code(r["code"]),
                    r["name"]
                    + (" <span class='dim'>[次新]</span>" if r.get("new_flag") else ""),
                    r["px"],
                    r["tech"],
                    ("%d<span class='dim'>(%d条)</span>" % (r["jev"], r["jev_n"]))
                    if r["jev"] is not None
                    else "—",
                    ("池内 " if r["rps_label"] == "池内" else ""),
                    r["rps"] if r["rps"] is not None else 0,
                    r["env"] if r["env"] is not None else "—",
                    r["score"],
                    "buy"
                    if "买入" in r["action"]
                    else ("watch" if "观察" in r["action"] else "avoid"),
                    r["action"],
                    r["dims"],
                    r.get("added") or "—",
                )
                for r in mres
            )
            parts.append(
                "<h2>③ 自选池共振（%d 只 · 四维：技术/JEV/RPS/"
                "环境，缺维降权）</h2>"
                "<table><tr><th>标的</th><th>现价</th><th>技术"
                "</th><th>JEV</th><th>RPS</th><th>环境</th>"
                "<th>综合</th><th>建议</th><th>维度</th>"
                "<th>入选</th></tr>%s"
                "</table>" % (len(mres), trs)
            )

        # ④ JEV 市场情绪（近7天已判断新闻）
        jev = _jev_market(wl_by_mkt.get(k, []), meta["scope"])
        n = max(jev["n"], 1)
        parts.append("<h2>④ JEV 市场情绪（近7天 %d 条已判断）</h2>" % jev["n"])
        if jev["n"]:
            parts.append(
                "<div class='bar'>"
                "<i style='width:%d%%;background:#238636'></i>"
                "<i style='width:%d%%;background:#da3633'></i>"
                "<i style='width:%d%%;background:#9e6a03'></i></div>"
                "<div class='dim'>利多 %d · 利空 %d · 中性 %d</div>"
                % (
                    jev["bull"] * 100 // n,
                    jev["bear"] * 100 // n,
                    jev["neu"] * 100 // n,
                    jev["bull"],
                    jev["bear"],
                    jev["neu"],
                )
            )
            for it in jev["items"]:
                tc = (
                    "t-bull"
                    if it["bias_cn"] == "利多"
                    else "t-bear"
                    if it["bias_cn"] == "利空"
                    else "t-neu"
                )
                parts.append(
                    "<div class='jev-item'><span class='tag %s'>%s %d%%"
                    "</span>%s<div class='dim'>%s</div></div>"
                    % (
                        tc,
                        it["bias_cn"],
                        it["conf"],
                        it["title"],
                        it["reason"] or it["ts"],
                    )
                )
            parts.append(
                "<div class='dim' style='margin-top:6px'>"
                "<a href='/bt-jev'>查看全部 JEV 判断 →</a></div>"
            )
        else:
            parts.append("<div class='env'>近7天无该市场关联的已判断新闻</div>")

        # ⑤ 策略信号矩阵（4 策略 × 本市场自选）
        if mres:
            trs = "".join(
                "<tr><td><a href='/kg/stock/%s'>%s</a></td>%s<td><b>%d/4"
                "</b></td></tr>"
                % (
                    _link_code(r["code"]),
                    r["name"],
                    "".join(
                        _sig_badge((r.get("sigs") or {}).get(sn)) for sn in STRAT_CN
                    ),
                    r["longs"],
                )
                for r in mres
            )
            head = "".join(
                "<th><a href='/bt/strategy/%s'>%s</a></th>" % (sn, v)
                for sn, v in STRAT_CN.items()
            )
            parts.append(
                "<h2>⑤ 策略信号矩阵（当持仓状态）</h2>"
                "<table><tr><th>标的</th>%s<th>多头数</th></tr>%s"
                "</table>" % (head, trs)
            )
        parts.append(foot)
        with open(os.path.join(REPORTS, meta["file"]), "w", encoding="utf-8") as f:
            f.write(_page(meta["label"] + " · 选股中心", "".join(parts)))

    # ── hub ── K233 四市场模块化：每市场一个模块卡
    # （头部=旗标+闸门徽标+计数+进入链接；体内=env 详情行 + 自选池打分表
    #  Top8；空市场保留模块壳不消失——四模块恒在）
    hub = [
        _mkt_nav(None),
        "<h1>🎯 选股中心</h1>",
        "<h2>四市场模块 · 自选池共振（点「进入模块」看全量与五块全景）</h2>",
    ]
    for k, meta in MARKETS.items():
        lb, e = env_by_key.get(k, ("—", {}))
        mres = sorted(
            [r for r in res if r["market"] == meta["mkt"]], key=lambda r: -r["score"]
        )
        nb = sum(1 for r in mres if "买入" in r["action"])
        nw = sum(1 for r in mres if r["action"].startswith("观察"))
        na = sum(1 for r in mres if r["action"].startswith("回避"))
        if not e:
            bcls, btxt = "bd-none", "闸门无数据"
        elif e.get("safe") is None:
            bcls, btxt = "bd-watch", "闸门缺维"
        elif e.get("safe"):
            bcls, btxt = "bd-buy", "闸门放行 %d" % e["score"]
        else:
            bcls, btxt = "bd-avoid", "闸门否决 %d" % e["score"]
        head = (
            "<div class='mod-head'><b>%s 模块</b>"
            "<span class='badge %s'>%s</span>"
            "<span class='dim'>%d 只 · 买入 %d · 观察 %d · 回避 %d</span>"
            "<a class='enter' href='/bt/m/%s'>进入模块 →</a></div>"
            % (meta["label"], bcls, btxt, len(mres), nb, nw, na, k)
        )
        if e:
            envline = "<div class='mod-env dim'>%s · %s</div>" % (lb, e["state"])
        else:
            envline = "<div class='mod-env dim'>%s · 暂无全市场闸门数据</div>" % lb
        if mres:
            show = mres[:8]
            mrows = "".join(
                "<tr><td><a href='/kg/stock/%s'>%s%s</a></td>"
                "<td><b>%d</b></td><td class='%s'>%s</td>"
                "<td>%s</td><td class='dim'>%s</td></tr>"
                % (
                    _link_code(r["code"]),
                    r["name"]
                    + (" <span class='dim'>[次新]</span>" if r.get("new_flag") else ""),
                    "",
                    r["score"],
                    "buy"
                    if "买入" in r["action"]
                    else ("watch" if "观察" in r["action"] else "avoid"),
                    r["action"],
                    r["dims"],
                    ("入选 " + r["added"]) if r.get("added") else "—",
                )
                for r in show
            )
            tbl = (
                "<table><tr><th>标的</th><th>综合</th><th>建议</th>"
                "<th>维度</th><th>入选</th></tr>%s</table>" % mrows
            )
            if len(mres) > len(show):
                tbl += (
                    "<div class='dim' style='padding:4px 2px'>"
                    "<a href='/bt/m/%s'>↓ 完整 %d 只见模块页</a></div>" % (k, len(mres))
                )
        else:
            tbl = "<div class='mod-env dim'>自选池暂无标的</div>"
        hub.append("<div class='module'>%s%s%s</div>" % (head, envline, tbl))

    cross = []
    for k, meta in MARKETS.items():
        for t in tops.get(meta["tops"], [])[:3]:
            cross.append(
                "<tr><td>%s</td><td><a href='/kg/stock/%s'>%s"
                "</a></td><td>%s</td><td><b>%.1f</b></td></tr>"
                % (
                    meta["label"],
                    _link_code(t["sym"]),
                    (t["name"] or t["sym"])[:18],
                    t["sym"],
                    t["mix"],
                )
            )
    hub.append(
        "<h2>跨市场 RPS 最强</h2><table><tr><th>市场</th><th>名称"
        "</th><th>代码</th><th>复合RPS</th></tr>%s</table>" % "".join(cross)
    )

    hub.append(
        "<h2>策略库（纵切）</h2><div class='nav'>"
        + "".join(
            "<a href='/bt/strategy/%s'>🧪 %s</a>" % (sn, cn)
            for sn, cn in STRAT_CN.items()
        )
        + "</div>"
    )
    hub.append(
        "<h2>策略与工具</h2><div class='nav'>"
        "<a href='/bt-report'>📄 回测报告</a>"
        "<a href='/bt-signals'>🚦 明日信号</a>"
        "<a href='/bt-positions'>💼 持仓跟踪</a>"
        "<a href='/bt-opt'>📊 寻优榜单</a>"
        "<a href='/bt-sectors'>🔥 板块热力</a>"
        "<a href='/bt-jev'>⚖️ JEV判断</a>"
        "<a href='/bt-brief'>🤖 AI 晨报</a></div>"
    )
    hub.append(foot)
    with open(os.path.join(REPORTS, "bt_hub.html"), "w", encoding="utf-8") as f:
        f.write(_page("选股中心", "".join(hub)))

    snap = {
        "latest": latest,
        "envs": [{"label": lb, **e} for lb, e in env_by_key.values() if e],
        "tops": tops,
        "res": res,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    tmp = os.path.join(REPORTS, "rps_latest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(REPORTS, "rps_latest.json"))
    print(
        "PAGES hub+%d markets, res=%d, top1=%s"
        % (len(MARKETS), len(res), (tops.get("CN") or [{}])[0].get("name", "-"))
    )


def _strat_nav(cur):
    parts = [
        _top_nav(),
        "<div class='nav'><a href='/fin'>🏠 金融驾驶舱</a>"
        "<a href='/bt'>🎯 选股中心</a>",
    ]
    for sn, cn in STRAT_CN.items():
        if sn == cur:
            parts.append("<span>🧪 %s</span>" % cn)
        else:
            parts.append("<a href='/bt/strategy/%s'>🧪 %s</a>" % (sn, cn))
    return "".join(parts) + "</div>"


_MKT_CN = {"CN": "A股", "US": "美股", "HK": "港股", "CRYPTO": "加密"}


def write_strategy_pages(strat_data, res, latest):
    """策略库纵切页 ×4：宽度/自选状态/回测绩效/参数寻优。"""
    import runner

    params_cfg = runner.CFG.get("strategies", {})
    grids = runner.CFG.get("opt_grids", {})
    foot = (
        "<div class='dim' style='margin-top:26px'>数据截至 %s · 宽度为"
        "宽表向量化逐列复刻 strategies.REGISTRY（数学等价）· 回测来自"
        "backtest_results.jsonl 全史 · 参数季度寻优后自动写回 config"
        " · 纸面研究不构成投资建议</div>" % latest
    )
    for sn, cn in STRAT_CN.items():
        parts = [
            _strat_nav(sn),
            "<h1>🧪 %s <span class='dim'>(%s)</span></h1>" % (cn, sn),
            "<div class='env'>%s<div class='dim' style='margin-top:4px'"
            ">当前参数 %s%s</div></div>"
            % (
                STRAT_LOGIC[sn],
                json.dumps(params_cfg.get(sn, {}), ensure_ascii=False),
                (" · 寻优网格 %d 组" % len(grids.get(sn, {})))
                if sn in grids
                else " · 未配置寻优网格",
            ),
        ]
        # ① 全市场持仓宽度
        parts.append("<h2>① 全市场持仓宽度</h2>")
        trs = []
        for mkt in ("CN", "US", "HK", "CRYPTO"):
            st = strat_data.get(mkt, {}).get(sn)
            if not st:
                continue
            cls = "buy" if st["pct"] >= 55 else ("avoid" if st["pct"] < 35 else "watch")
            trs.append(
                "<tr><td><a href='/bt/m/%s'>%s</a></td><td>%d</td>"
                "<td><b class='%s'>%d（%d%%）</b></td><td>%d</td></tr>"
                % (
                    {"CN": "cn", "US": "us", "HK": "hk", "CRYPTO": "crypto"}[mkt],
                    _MKT_CN[mkt],
                    st["n"],
                    cls,
                    st["long"],
                    st["pct"],
                    len(st["new_long"]),
                )
            )
        parts.append(
            "<table><tr><th>市场</th><th>样本</th><th>当前多头"
            "</th><th>近5日翻多</th></tr>%s</table>" % "".join(trs)
        )
        for mkt in ("CN", "US", "HK", "CRYPTO"):
            st = strat_data.get(mkt, {}).get(sn)
            if st and st["new_long"]:
                items = " · ".join(
                    "<a href='/kg/stock/%s'>%s</a>%s"
                    % (
                        _link_code(t["sym"]),
                        t["name"] or t["sym"],
                        ("(%+.1f%%)" % t["chg20"]) if t["chg20"] is not None else "",
                    )
                    for t in st["new_long"][:8]
                )
                parts.append(
                    "<div class='dim' style='margin-top:6px'>"
                    "%s 近5日翻多：%s</div>" % (_MKT_CN[mkt], items)
                )

        # ② 自选池该策略状态
        rows = [r for r in res if (r.get("sigs") or {}).get(sn) is not None]
        rows.sort(key=lambda r: (-(r["sigs"].get(sn) or 0), -r["score"]))
        trs = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td>%.2f</td><td>%s</td><td>%d</td><td class='%s'>%s</td></tr>"
            % (
                _link_code(r["code"]),
                r["name"],
                r["market"],
                r["px"],
                "<b class='b-long'>多头</b>"
                if r["sigs"].get(sn, 0) > 0
                else "<span class='b-flat'>空仓</span>",
                r["score"],
                "buy"
                if "买入" in r["action"]
                else ("watch" if "观察" in r["action"] else "avoid"),
                r["action"],
            )
            for r in rows
        )
        parts.append(
            "<h2>② 自选池状态（%d 只，按信号+共振排序）</h2>"
            "<table><tr><th>标的</th><th>市场</th><th>现价</th>"
            "<th>本策略</th><th>共振分</th><th>共振建议</th></tr>"
            "%s</table>" % (len(rows), trs)
        )

        # ③ 回测绩效（全史聚合）
        agg = _bt_agg(sn)
        if agg.get("n"):
            pm = " · ".join(
                "%s %d次 夏普%.2f 收益%+.1f%%"
                % (_MKT_CN.get(mk, mk), v["n"], v["sharpe"], v["ret"])
                for mk, v in sorted(agg["per_mkt"].items())
            )
            parts.append(
                "<h2>③ 回测绩效（%d 次全史聚合）</h2><div class='env'>"
                "平均夏普 <b>%s</b> · 平均收益 <b>%+.1f%%</b> · 平均回撤 "
                "%.1f%% · 平均胜率 %d%% · 平均交易 %.1f 次<div class='dim'"
                " style='margin-top:4px'>最近窗口 %s</div><div class='dim'"
                " style='margin-top:4px'>%s</div><div class='dim' "
                "style='margin-top:4px'><a href='/bt-report'>完整回测报告"
                "</a> · <a href='/bt-opt'>寻优榜单</a></div></div>"
                % (
                    agg["n"],
                    ("%+.2f" % agg["sharpe"]) if agg["sharpe"] else "—",
                    agg["ret"],
                    agg["dd"],
                    agg["win"],
                    agg["trades"],
                    agg["window"],
                    pm or "—",
                )
            )
        else:
            parts.append(
                "<h2>③ 回测绩效</h2><div class='env dim'>暂无"
                "回测记录 · <a href='/bt-report'>跑一次回测"
                "</a></div>"
            )

        # ④ 参数寻优
        if sn in grids:
            parts.append(
                "<h2>④ 参数寻优</h2><div class='env'>网格 %d 组 · "
                "当前生效 %s<div class='dim' style='margin-top:4px'"
                ">季度寻优（opt_defaults --apply）取全池平均夏普"
                "最高组合自动写回</div></div>"
                % (
                    len(grids[sn]),
                    json.dumps(params_cfg.get(sn, {}), ensure_ascii=False),
                )
            )
        parts.append(foot)
        with open(
            os.path.join(REPORTS, "bt_strat_%s.html" % sn), "w", encoding="utf-8"
        ) as f:
            f.write(_page("策略库 · " + cn, "".join(parts)))
    print("STRAT pages x%d" % len(STRAT_CN))


# ── W3-4 分位数口径：原始多维加权分强右偏（中位数全档为负），分高≠能买， ──
#    要看分位。历史分布基准 = 日批归档（rps_daily_batches.jsonl）live 行
#    滚动 60 交易日的候选分集合；replay 行 score 恒 None 不参与。
def load_hist_scores(batches_path, window=60, exclude_date=None):
    """日批归档 → 滚动 window 个交易日的 live 打分集合（历史分布基准）。

    口径（判例式）：
      · 只取 source=live 行（replay 行 score=None，纯三维复算不进基准）；
      · 按日期升序取「最后 window 个交易日」，窗口内全部候选分入池
        （不足 window 日则全部可得——回退路径，上线初期归档仅数日）；
      · exclude_date 排除当日（今日批次在推送之后才归档，双保险防自含）；
      · 无历史 → 空列表，调用方不加 percentile 字段（fail-open 不炸卡）。
    """
    rows = []
    try:
        with open(batches_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("source") != "live":
                    continue
                d = str(r.get("date") or "")
                if not d or d == str(exclude_date or ""):
                    continue
                rows.append((d, r.get("picks") or []))
    except OSError:
        return []
    dates = sorted({d for d, _ in rows})[-int(window) :]  # 滚动窗口
    dset = set(dates)
    out = []
    for d, picks in rows:
        if d not in dset:
            continue
        for p in picks:
            s = p.get("score")
            if s is not None:
                try:
                    out.append(float(s))
                except (TypeError, ValueError):
                    continue
    return out


def score_percentile(score, hist):
    """单票原始分 → 当日全市场分位 P0~P100（一位小数）。

    定义 = 严格低于该分的历史分占比×100（并列计高位，判例：分位看
    「比过去多少日子里的票强」，同分不抬轿）。hist 空 → None。
    """
    if not hist:
        return None
    pct = 100.0 * sum(1 for h in hist if h < float(score)) / len(hist)
    return round(min(100.0, max(0.0, pct)), 1)


def annotate_percentile(res, batches_path=None, cfg=None):
    """打分产物逐候选注入 percentile 字段（W3-4 推送口径）。

    开关 config.json qpzm.score_percentile（缺省 true，判例：口径提示
    类开关默认开、关掉即回退原卡片）；无历史基准或单票算不出分位时不
    加字段（不硬凑），页面/卡片渲染按无分位处理。
    """
    cfg = (
        cfg
        if cfg is not None
        else json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    )
    if not (cfg.get("qpzm") or {}).get("score_percentile", True):
        return res
    batches_path = batches_path or os.path.join(REPORTS, "rps_daily_batches.jsonl")
    hist = load_hist_scores(batches_path)
    if not hist:
        print("PERCENTILE: 无 live 历史基准，跳过分位注入")
        return res
    n = 0
    for r in res:
        p = score_percentile(r.get("score"), hist)
        if p is not None:
            r["percentile"] = p
            n += 1
    print("PERCENTILE: %d/%d 注入（基准 %d 分/滚动60日）" % (n, len(res), len(hist)))
    return res


def push_daily_card(snap):
    top, res = snap["tops"].get("CN", []), snap["res"]
    env_lines = "\n".join(
        "· %s：%s（分 %d）" % (e["label"], e["state"], e["score"])
        for e in snap.get("envs", [])
    )
    if not env_lines:
        env_lines = "· 暂无环境数据"
    gates_closed = sum(
        1 for e in snap.get("envs", []) if e.get("safe") is False
    )  # S6: None(缺数据)不计收紧
    jn = sum(1 for r in res if r.get("jev") is not None)
    jev_line = "· JEV 消息面：%d/%d 只进入四维打分（无判断自动缺维降权）" % (
        jn,
        len(res),
    )
    top_lines = "\n".join(
        "%d. %s %s — RPS%.0f（50日 %.0f）"
        % (i + 1, t["name"] or t["sym"], t["sym"], t["mix"], t["r50"])
        for i, t in enumerate(top[:8])
    )
    cross = " · ".join(
        "%s#1 %s(%.0f)" % (lb, (t[0]["name"] or t[0]["sym"])[:10], t[0]["mix"])
        for lb, t in (
            ("美股", snap["tops"].get("US", [])),
            ("港股", snap["tops"].get("HK", [])),
            ("加密", snap["tops"].get("CRYPTO", [])),
        )
        if t
    )
    hot = [r for r in res if r["score"] >= 50][:6]
    # W3-4：分旁挂当日全市场分位（有字段才挂，开关关/无基准时卡片同旧版）
    res_lines = (
        "\n".join(
            "· %s %d分%s %s［%s］"
            % (
                r["name"],
                r["score"],
                ("(P%.1f)" % r["percentile"])
                if r.get("percentile") is not None
                else "",
                r["action"],
                r["dims"],
            )
            for r in hot
        )
        or "（暂无 ≥50 分标的）"
    )
    # W3-4 头部说明行：口径提示（仅当本次卡片带分位时出现，避免口径与
    # 内容脱节——开关关掉后提示同步消失，不留「有说明无数据」的孤儿文案）
    has_pct = any(r.get("percentile") is not None for r in res)
    caliber = (
        [
            {
                "tag": "markdown",
                "text_size": "notation",
                "content": "<font color='grey'>分数为原始共振分（右偏分布），"
                "括号内为当日全市场分位</font>",
            }
        ]
        if has_pct
        else []
    )
    card = {
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 RPS 强度榜 × 共振（四市场）"},
            "subtitle": {
                "tag": "plain_text",
                "content": "环境闸门 %d/%d 开启 · %s"
                % (
                    len(snap.get("envs", [])) - gates_closed,
                    max(len(snap.get("envs", [])), 1),
                    snap["latest"],
                ),
            },
            "template": "blue" if gates_closed == 0 else "grey",
        },
        "elements": caliber
        + [
            {
                "tag": "markdown",
                "content": "**环境闸门（全市场宽度）**\n%s\n%s" % (env_lines, jev_line),
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": "**A 股 RPS Top8**\n%s" % top_lines},
            {"tag": "hr"},
            {"tag": "markdown", "content": "**跨市场最强**\n%s" % cross},
            {"tag": "hr"},
            {"tag": "markdown", "content": "**自选池共振 ≥50**\n%s" % res_lines},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📊 完整榜单"},
                        "type": "primary",
                        "url": PORTAL + "bt",
                    }
                ],
            },
            {
                "tag": "markdown",
                "text_size": "notation",
                "content": "<font color='grey'>借鉴青萍之末2方法论 · "
                "纸面研究不构成投资建议</font>",
            },
        ],
    }
    import runner

    print("CARD", runner.push_feishu(json.dumps(card, ensure_ascii=False), card=True))


# ── 影子预研：技术维 × 缠论结构信号（独立入口，只落盘不推送） ──
CHAN_QK = ("mbei", "bei", "bei2", "vv", "tbe", "mt")  # 质量布尔标记（pool 实际字段）


def _load_chan_pool(pool_path):
    """chanlun_pool.jsonl → ({ts_code: [sig,...按时间升序]}, {detect_date}, 坏行数)。
    BOM 判例：逐行 lstrip("\\ufeff")；json.loads 失败跳过并计数。"""
    by_code, dates, bad = {}, set(), 0
    try:
        with open(pool_path, encoding="utf-8") as f:
            for line in f:
                line = line.lstrip("\ufeff").strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except Exception:
                    bad += 1
                    continue
                c = p.get("code") or ""
                if not c:
                    continue
                by_code.setdefault(c, []).append(p)
                if p.get("detect_date"):
                    dates.add(p["detect_date"])
    except OSError:
        return {}, set(), 0
    return by_code, dates, bad


def _chan_match_score(ts_code, by_code, recent):
    """pool 信号 → 缠论分：窗口内最近一条 买类+25/卖类-25，
    质量标记（CHAN_QK 中值为 True）每 +5 同向叠加、上限 +15；
    窗口内无信号 → chan_score=0 且 no_signal=True。"""
    sigs = [
        s for s in by_code.get(ts_code, []) if (s.get("detect_date") or "") in recent
    ]
    if not sigs:
        return {
            "chan_score": 0,
            "dir": None,
            "no_signal": True,
            "evidence": [],
            "quality_n": 0,
            "latest_type": None,
            "latest_date": None,
        }
    sigs.sort(key=lambda s: (s.get("detect_date") or "", s.get("first_seen") or ""))
    latest = sigs[-1]
    typ = latest.get("type") or ""
    qmarks = sorted(k for k in CHAN_QK if latest.get(k) is True)
    bonus = min(15, 5 * len(qmarks))
    if "买" in typ:
        base, direction = 25, "buy"
    elif "卖" in typ:
        base, direction = -25, "sell"
    else:
        base, direction = 0, "flat"
    evidence = [
        {
            "detect_date": s.get("detect_date"),
            "type": s.get("type"),
            "quality": sorted(k for k in CHAN_QK if s.get(k) is True),
            "trend": s.get("trend"),
        }
        for s in sigs
    ]
    return {
        "chan_score": base + (bonus if base > 0 else (-bonus if base < 0 else 0)),
        "dir": direction,
        "no_signal": False,
        "evidence": evidence,
        "quality_n": len(qmarks),
        "latest_type": typ,
        "latest_date": latest.get("detect_date"),
    }


def _chan_blend(tech_old, chan_score):
    """建议合成口径：0.6*tech_old + 0.4*(50+chan_score)，clip [0,100]。"""
    v = 0.6 * tech_old + 0.4 * (50 + chan_score)
    return round(min(100.0, max(0.0, v)), 1)


def _atomic_write_json(path, payload):
    """原子写：tempfile + os.replace（防半截文件）。"""
    import tempfile

    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/rps_resonance.py", exc)
        raise


def _shadow_rows(entries, bars, by_code, recent, cfg_strats):
    """纯打分核（可单测）：entries=[(code,name,mkt,sym)]，
    bars={(mkt,sym):df}。tech_old 照抄 resonance_watchlist 口径
    （4策略多头×25 ± MA20修正，L589-603 不动），仅独立重算。"""
    import strategies as strat

    rows = []
    for code, nm, mkt, sym in entries:
        df = bars.get((mkt, sym))
        if df is None or len(df) < 55:
            continue
        longs, sigs = 0, {}
        for sn in ("trend_follow", "ma_cross", "rsi_reversal", "boll_breakout"):
            try:
                sig = strat.REGISTRY[sn](df, dict(cfg_strats.get(sn, {})))
                sv = int(sig.values[-1])
                sigs[sn] = sv
                longs += int(sv > 0)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/rps_resonance.py", exc)
        tech_old = longs * 25
        ma20 = df["close"].iloc[-20:].mean()
        tech_old = min(
            100, max(0, tech_old + (10 if df["close"].iloc[-1] > ma20 else -10))
        )
        m = _chan_match_score(_to_ts(code), by_code, recent)
        tech_new = _chan_blend(tech_old, m["chan_score"])
        rows.append(
            {
                "code": code,
                "name": nm,
                "market": mkt,
                "tech_old": int(tech_old),
                "longs": longs,
                "sigs": sigs,
                "chan_score": m["chan_score"],
                "chan_dir": m["dir"],
                "chan_latest": m["latest_type"],
                "chan_date": m["latest_date"],
                "quality_n": m["quality_n"],
                "tech_new_proposed": tech_new,
                "delta": round(tech_new - tech_old, 1),
                "no_signal": m["no_signal"],
                "evidence": m["evidence"],
            }
        )
    rows.sort(key=lambda r: -abs(r["delta"]))
    return rows


def shadow_tech_upgrade(pool_path=None, out_path=None):
    """影子入口：缠论结构信号纳入技术维打分对比（预研，不合龙）。
    影子纪律（chanlun_mtf 同款声明）：只落盘 data/qpzm_tech_shadow.json
    供人工评审，不推送、不注册任务、不改变 --daily/--dry 现有行为。
    口径（如实声明）：
    · 匹配窗 = pool 内 detect_date 去重降序前 10 个（近似最近 10 个交易日）
    · 缠论分 = 窗口内最近信号 买类+25/卖类-25，质量标记每 +5 同向叠加
      上限 +15；无信号票 chan_score=0 标 no_signal
    · tech_old 按 4 策略口径独立重算（与 resonance_watchlist 同规则）
    · tech_new_proposed = 0.6*tech_old + 0.4*(50+chan_score)，clip [0,100]"""
    import runner
    from data_loader import load_bars

    pool_path = pool_path or os.path.join(HERE, "data", "chanlun_pool.jsonl")
    out_path = out_path or os.path.join(HERE, "data", "qpzm_tech_shadow.json")
    wl = json.load(
        open(os.path.join(runner.NEWS_DIR, "news_config.json"), encoding="utf-8")
    )["watchlist"]
    by_code, dates, bad = _load_chan_pool(pool_path)
    recent = set(sorted(dates)[-10:])

    entries = []
    for e in wl:
        code = e.get("code", "")
        if not code:
            continue
        try:
            mkt, sym = _to_market(code)
        except Exception:
            continue
        entries.append((code, e.get("name", ""), mkt, sym))

    start = (datetime.now() - pd.Timedelta(days=130)).strftime("%Y-%m-%d")
    by_mkt = {}
    for _code, _nm, mkt, sym in entries:
        by_mkt.setdefault(mkt, set()).add(sym)
    bars = {}
    for mkt, syms in by_mkt.items():
        got = load_bars(mkt, sorted(syms), start, None)
        for sym, df in (got or {}).items():
            if df is not None and len(df) >= 55:
                bars[(mkt, sym)] = df

    cfg_strats = runner.CFG.get("strategies", {})
    rows = _shadow_rows(entries, bars, by_code, recent, cfg_strats)
    with_chan = sum(1 for r in rows if not r["no_signal"])
    deltas = [r["delta"] for r in rows]
    payload = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows": rows,
        "summary": {
            "n": len(rows),
            "watchlist": len(wl),
            "with_chan": with_chan,
            "pool_bad_lines": bad,
            "pool_dates_covered": sorted(dates)[-10:],
            "avg_delta": round(sum(deltas) / len(deltas), 1) if deltas else 0.0,
        },
    }
    _atomic_write_json(out_path, payload)
    print(
        "SHADOW tech rows=%d with_chan=%d avg_delta=%+.1f -> %s"
        % (len(rows), with_chan, payload["summary"]["avg_delta"], out_path)
    )
    return payload


# ── main ───────────────────────────────────────────────────
def run_daily(push=True):
    # A 股：tushare 全市场
    sync_ashare()
    closes, latest = _load_market()
    if not closes:
        print("RPS no data")
        return
    env = market_env(closes)
    rps_df = batch_rps(closes)

    # 美股/港股/加密：zhice-db 全市场
    full_rps, full_envs, tops = {}, {}, {"CN": []}
    a_names = _names_safe()
    for sym, r in rps_df.head(30).iterrows():
        c = closes.get(sym, [])
        tops["CN"].append(
            {
                "sym": sym,
                "name": a_names.get(sym, "")[:18],
                "px": round(float(c[-1]), 2) if len(c) else None,
                "chg20": round((c[-1] / c[-21] - 1) * 100, 1) if len(c) >= 21 else None,
                "r50": r.rps_50,
                "r120": r.rps_120,
                "r250": r.rps_250,
                "mix": r.rps_mix,
                "new_flag": bool(getattr(r, "new_flag", False)),
            }
        )
    # 策略全市场宽度（A股）
    import runner as _R2

    _params = _R2.CFG.get("strategies", {})
    strat_data = {}
    try:
        strat_data["CN"] = _strat_market_stats(
            closes, _params, {s: a_names.get(s, "") for s in closes}
        )
    except Exception as ex:
        print("STRAT CN fail: %s" % str(ex)[:80])
    try:
        db_names = _db_names()
    except Exception:
        db_names = {}
    for mkt in DB_MARKETS:
        try:
            cs = _db_closes(mkt, DB_LOOKBACK[mkt])
            if len(cs) < POOL_GATE_MIN:
                print("FULL %s only %d syms, skip" % (mkt, len(cs)))
                continue
            full_rps[mkt] = batch_rps(cs, DB_PERIODS[mkt])
            full_envs[mkt] = market_env(cs)
            nm_mkt = {s: db_names.get((mkt, s), "") for s in full_rps[mkt].index}
            tops[mkt] = _top_rows(
                full_rps[mkt], cs, nm_mkt, n=20 if mkt != "CRYPTO" else 10
            )
            strat_data[mkt] = _strat_market_stats(
                cs, _params, {s: db_names.get((mkt, s), "") for s in cs}
            )
            del cs
            print(
                "FULL %s %d syms env=%d safe=%s top1=%s"
                % (
                    mkt,
                    len(full_rps[mkt]),
                    full_envs[mkt]["score"],
                    full_envs[mkt]["safe"],
                    tops[mkt][0]["name"] or tops[mkt][0]["sym"],
                )
            )
        except Exception as ex:
            print("FULL %s fail: %s" % (mkt, str(ex)[:100]))
    del closes

    print(
        "RPS A股 %d stocks, env=%d safe=%s" % (len(rps_df), env["score"], env["safe"])
    )
    res, pool_envs = resonance_watchlist(rps_df, env, full_envs, full_rps)
    # W3-4：分位注入须在 write_pages 之前——percentile 随 res 落
    # rps_latest.json，推卡/日批归档读同一份快照，口径单一来源
    try:
        annotate_percentile(res)
    except Exception as _ex:
        # fail-open：分位算不出不影响主链，卡片按无分位渲染
        print("PERCENTILE fail: %s" % str(_ex)[:120])
    env_by_key = {"cn": ("A股全市场(%d只)" % len(rps_df), env)}
    for k, m in (("us", "US"), ("hk", "HK"), ("crypto", "CRYPTO")):
        if m in full_envs:
            lbl = {"US": "美股", "HK": "港股", "CRYPTO": "加密"}[m]
            env_by_key[k] = (
                "%s全市场(%d只)" % (lbl, len(full_rps.get(m, []))),
                full_envs[m],
            )
    # watchlist 按市场分组（JEV 情绪关键词用）
    import runner as _runner

    wl = json.load(
        open(os.path.join(_runner.NEWS_DIR, "news_config.json"), encoding="utf-8")
    )["watchlist"]
    wl_by_mkt = {}
    for e in wl:
        try:
            mkt, _sym = _to_market(e.get("code", ""))
            key = {"CN": "cn", "US": "us", "HK": "hk", "CRYPTO": "crypto"}.get(mkt)
            if key:
                wl_by_mkt.setdefault(key, []).append(e)
        except Exception:
            continue
    write_pages(env_by_key, res, latest, tops, wl_by_mkt)
    write_strategy_pages(strat_data, res, latest)
    if push:
        push_daily_card(
            json.load(open(os.path.join(REPORTS, "rps_latest.json"), encoding="utf-8"))
        )
    # K238 每日批次：归档当日批次 + 子进程批次回测（异常隔离不阻塞主链）
    try:
        import qpzm_daily_batch as _qdb

        _qdb.archive_daily_batch()
        _qdb.spawn_backtest()
    except Exception as _ex:
        print("BATCH fail: %s" % str(_ex)[:120])
    # 2026-10-14 源缺失告警（alert-loop P1）：cross 四源健康挂每日批
    # 产出口之后——任一源 stale/missing 不再静默（复用韧性改造写好的
    # source_health/push_source_health_alert，此前仅门户请求可见）。
    # 去重复用 guard_alert 12h 状态（键=坏源清单，状态变化即新告警，
    # 键前缀 cross_src_ 与守卫拦截告警区分）；巡检异常不反噬日批 rc。
    try:
        import cross_poll as _cp
        import guard_alert as _ga

        _h = _cp.source_health()
        print("源健康巡检: %s" % _cp.health_summary_line(_h), flush=True)
        _sent_probe, _txt = _cp.push_source_health_alert(_h, send=False)
        if _txt:  # 有坏源（全 ok 时 _txt 为空不告警）
            _key = "cross_src_" + ",".join(
                sorted(
                    "%s:%s" % (_sid, _v["status"])
                    for _sid, _v in _h.items()
                    if _v["status"] != "ok"
                )
            )
            _ga.alert_once(_key, _txt, send=True)
    except Exception as _ex:
        print("SRC-HEALTH alert fail: %s" % str(_ex)[:120])


if __name__ == "__main__":
    try:
        if "--sync" in sys.argv:
            sync_ashare()
        elif "--shadow-tech" in sys.argv:
            shadow_tech_upgrade()
        elif "--dry" in sys.argv:
            run_daily(push=False)
        else:
            run_daily()
    except SystemExit:
        raise
    except Exception:
        # OPS-0930c：cmd /c 直调无 ps launcher，python 侧兜底告警
        # （任务 rc 由 cmd 天然透传，这里只补"飞书直达"缺口）
        try:
            wrapper = os.path.abspath(
                os.path.join(HERE, "..", "news_collector", "alert_fail_wrap.py")
            )
            subprocess.run(
                [
                    sys.executable,
                    wrapper,
                    "rps_resonance",
                    "1",
                    os.path.join(HERE, "logs", "rps_task.out"),
                ],
                timeout=120,
                check=False,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/rps_resonance.py", exc)
        raise
