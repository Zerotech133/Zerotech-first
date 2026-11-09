# -*- coding: utf-8 -*-
"""缠论结构池（Chan Pool）：走势类型 × 中枢 × 三类买卖点 × 背驰。

为什么重写（2026-11-09，老田：「记住你是缠论」）：
  v1（pool_state.py 首版，commit 4a8fb35d7）判的是「距 120 日高点 -15%、
  波动收缩、缩量、均线粘合、站上 MA60」——那是形态相似度打分，西方技术分析
  的路子，与缠论无关。它连一个缠论模块都没调用，却把结果叫「蓄势/突破/加速/
  衰竭」，名实不符。

  缠论不问这些。缠论只问三件事：
    1. 现在处在哪个级别的什么走势类型里（上涨 / 下跌 / 盘整）
    2. 相对最近一个中枢，在枢内、上沿外、还是下沿外
    3. 有没有买卖点成立、有没有背驰

  「走势终完美」——任何走势类型必然完成。所以池子不是「强势股清单」，而是
  「结构临界清单」：盯的是谁的中枢正在走向完成，谁的离开段已经背驰。

判据全部来自 chanlun_signal.analyze（权威口径），本文件只做「位置归纳」，
不自己造指标。严禁改用 chanlun_bi.find_signals——那是已知的
look-ahead 口径（截断重放 44% 信号消失，与权威口径一致率 5.4%）。

────────────────────────────────────────────────────────────
九态（全部由结构推出，一个均线都不看）
────────────────────────────────────────────────────────────

  三买待   收盘在 ZG 之上 8% 以内、最后一笔向上、回踩笔尚未出现、
           且无新鲜卖点 —— 三类买点的「将买未买」。
           这是缠论里唯一称得上「临近爆发」的位置：向上离开中枢已成，
           就差回踩不破上沿这一口气。超过 8% 归「离开段」，不叫待回踩。

  三买     回踩低点 > ZG 已确认，且收盘仍在 ZG 之上（chanlun_signal 的 3买）
           —— 趋势确认。仓位在此建，不在「三买待」建。

  二买     一类买点后首次回调不破极值（2买），且收盘仍守在信号价上方
           —— 转折后第二个上车点，位置高于一买。

  一买     向下线段终结 + 段内动量衰减（1买）
           —— 最左侧。缠论里买点全在下跌中产生，一买是跌出来的。

  背驰     顶背驰成立：趋势背驰 / MACD 背驰 / 一卖二卖兼力度衰竭
           —— 衰竭。这是「看到衰竭点」那一半。

  三卖     三类卖点新鲜成立（跌破 ZD 后反抽不过）
           —— 下跌延续，不是衰竭，别碰。下跌趋势里每个中枢都会出一次。

  离开段   收盘在 ZG 上方 8% 以外
           —— 已在趋势中段，追高区。买点早过了。

  中枢     收盘 ∈ [ZD, ZG]，中枢未终结；或中枢区间过宽（级别已升级、
           上下沿不可操作）—— 震荡，等它选方向。

  中枢下   收盘 < ZD，向下离开中枢 —— 等三卖成立，或等下一个一买。

优先级：背驰 > 三买 > 二买 > 一买 > 三买待 > 三卖 > 离开段 > 中枢 > 中枢下

判据上的两个刻意选择（都是被实测打回来的）：
  · 背驰只认真背驰，不认「有卖点」。首版把 3卖 也算背驰，47.7% 的票进了
    衰竭档——那是把下跌中继当见顶。3卖 在下跌趋势里遍地都是，不构成衰竭。
  · 买点必须仍站在其赖以成立的价位上。3买 守 ZG，1/2买 守信号价；跌破即
    失效。首版只看信号类型与新鲜度，把「3买 后已跌破 ZG 24%」的票仍列在
    买点档里，那是报表好看、实盘不能用。

────────────────────────────────────────────────────────────
口径与边界
────────────────────────────────────────────────────────────

· 操作级别 = 日线。analyze 喂固定 LOOKBACK=500 根（约两年），口径确定、
  可复现；窗口起点变化会让笔的划分变化，这是缠论本身的性质，不是 bug。
· 只用到 <= asof 当日的 K 线，PIT 安全。末笔可能仍在延伸（未完成笔），
  状态里用 bi_live 显式标出，不假装它已定。
· U0 可交易域（2026-11-09 审计：全样本 +195bp 里有 602 笔低成交额票撑起
  +3756bp，剔掉后变 -189bp）：
    剔 ST/退市整理、股价 < 2 元、20 日均成交额 < 门槛、上市不足 MIN_BARS、
    数据不新鲜（残档退市票）、指数（000xxx.SH / 399xxx.SZ）、北交所（可选）。
  不剔这些，池子会重新长出「买不到的票」。

用法:
  python pool_state.py --scan                 # 全市场扫，按六态分档
  python pool_state.py --scan --stage 三买待   # 只看某态
  python pool_state.py --scan --save          # 落 reports/pool_state_<asof>.json
  python pool_state.py --intraday             # 盘中：读池 + 实时价，看结构演化

盘中只比对中枢沿，不重算笔——笔的修正需要收盘价确认，盘中算出来的是假结构。
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import chanlun_signal  # noqa: E402  权威买卖点口径

MIN_BARS = 250      # 少于 250 根不判（次新结构不足）
LOOKBACK = 500      # 喂给 analyze 的 K 线数（约两年；固定窗口保证口径可复现）
MIN_PRICE = 2.0     # 面值退市风险
ST_MARKS = ("ST", "退")
COLS = ["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]

# 信号有效期（交易日）：买点成立太久就不算「当下可执行」
SIG_FRESH = 60
# 买点后已涨超此幅度，标注「已远离」，不再当买点候选
SIG_FAR = 0.15
# 中枢最少笔数（三笔成枢，延伸到 5 笔以上才算「扎得住」）
PIV_MIN_BI = 3
PIV_SOLID_BI = 5
# 中枢延伸上限：缠论「中枢延伸不超过 9 段，超过即升级」。超过后本级别的
# ZG/ZD 不再是可操作边界——实测依据：五洲交通末中枢标 19 笔、ZD=3.90
# ZG=4.09，而该区间实际最低 3.33、最高 4.46，现价 4.20 落在真实震荡的正
# 中间，却被判成「上沿外·三买待」。根因是笔中枢「有交集即延伸」：一笔从
# 3.92 起涨到 4.46，起点在区间内就算重叠，中枢一路延伸到 19 笔。
PIV_MAX_BI = 9
# 中枢区间宽度上限：超过则笔中枢已过宽，级别实际已升级
PIV_MAX_WIDTH = 0.30
# 三买待的「待回踩」窗口：已离开上沿但没走远。超过此距离属离开段中段（追高区）
B3P_MAX_DIST = 0.08

# 九态（顺序即优先级，判定从高到低）
# 映射用户要的三段：临界 = 三买待；爆发中 = 三买/二买/一买/离开段；
# 衰竭 = 背驰。三卖/中枢下 是下跌侧，不进买点池。
STAGES = ["背驰", "三买", "二买", "一买", "三买待", "三卖", "离开段", "中枢", "中枢下"]

# 池子只打印这六档。三卖/离开段/中枢下 是「不买」的一侧：
# 三卖在下跌趋势中每个中枢都会出一次（实测 1,625 只，占 41%），
# 那是趋势延续的常态，不是衰竭，列出来只会淹没真正的信号。
POOL_STAGES = ["三买待", "三买", "二买", "一买", "背驰", "中枢"]
OFF_STAGES = ["三卖", "离开段", "中枢下"]

# 指数：000xxx.SH 是上证系列，399xxx.SZ 是深证系列
def _is_index(code):
    num, _, mkt = code.partition(".")
    if num.startswith("399"):
        return True
    if mkt == "SH" and num.startswith("000"):
        return True
    return False


def _is_bj(code):
    return code.endswith(".BJ") or code.startswith("920") or code.startswith("8")


def _safe(x):
    """JSON 友好：NaN/inf → None。"""
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return round(v, 6)


def _bars(df):
    """DataFrame → analyze 需要的 [{dt,o,h,l,c,v}]（dt 字符串，字典序=时间序）。"""
    out = []
    has_v = "volume" in df.columns
    for r in df.itertuples(index=False):
        v = getattr(r, "volume", None) if has_v else None
        out.append({
            "dt": str(getattr(r, "trade_date")),
            "o": float(getattr(r, "open")),
            "h": float(getattr(r, "high")),
            "l": float(getattr(r, "low")),
            "c": float(getattr(r, "close")),
            "v": float(v) if v is not None and pd.notna(v) else 0.0,
        })
    return out


def _trend_of(pivots):
    """走势类型：末两个中枢同向不重叠 = 趋势，否则盘整（缠论 a+A+b+B+c）。"""
    if len(pivots) < 2:
        return "盘整", 0
    p1, p2 = pivots[-2], pivots[-1]
    if p2["zd"] > p1["zg"]:
        return "上涨", 1
    if p2["zg"] < p1["zd"]:
        return "下跌", -1
    return "盘整", 0


def _bei_flags(sig):
    """把信号上的背驰影子标注汇成一串，供页面直接显示。"""
    if not sig:
        return ""
    out = []
    if sig.get("tbe"):
        out.append("趋势背驰")
    if sig.get("mbei"):
        out.append("MACD背驰")
    if sig.get("bei2"):
        out.append("同向力度衰竭")
    if sig.get("bei"):
        out.append("盘整背驰")
    if sig.get("vv") is False:
        out.append("量能未配合")
    return "/".join(out)


def analyse(df, code=""):
    """单只票 → 结构字典；结构不足返回 None。

    只做「位置归纳」：调用 chanlun_signal.analyze 拿笔/线段/中枢/买卖点，
    然后回答「现在站在哪」。不自己发明指标。
    """
    if df is None or len(df) < MIN_BARS:
        return None
    df = df.sort_values("trade_date")
    win = df.tail(LOOKBACK)
    try:
        bars = _bars(win)
    except Exception:
        return None
    if len(bars) < MIN_BARS:
        return None

    try:
        res = chanlun_signal.analyze(bars)
    except Exception:
        return None

    bis = res.get("bis") or []
    segs = res.get("segments") or []
    pivs = res.get("pivots") or []
    sigs = res.get("signals") or []
    if not bis:
        return None

    close = bars[-1]["c"]
    last_bi = bis[-1]
    last_seg = segs[-1] if segs else None
    piv = pivs[-1] if pivs else None
    trend, trend_dir = _trend_of(pivs)

    # ── 相对中枢的位置 ────────────────────────────────
    if piv:
        zg, zd = piv["zg"], piv["zd"]
        if close > zg:
            pos = "上沿外"
        elif close < zd:
            pos = "下沿外"
        else:
            pos = "枢内"
        dist_zg = close / zg - 1.0 if zg else None
        dist_zd = close / zd - 1.0 if zd else None
    else:
        zg = zd = None
        pos = "无枢"
        dist_zg = dist_zd = None

    # ── 最近信号（取时间最晚的一条）────────────────────
    sig = sigs[-1] if sigs else None
    sig_dt = sig["dt"] if sig else None
    sig_type = sig.get("type") if sig else None
    sig_price = sig.get("price") if sig else None
    # 信号距今天数（用窗口内 K 线序号算，避免节假日问题）
    sig_age = None
    if sig_dt:
        dts = [b["dt"] for b in bars]
        try:
            sig_age = len(dts) - 1 - dts.index(sig_dt)
        except ValueError:
            sig_age = None
    sig_fresh = sig_age is not None and sig_age <= SIG_FRESH
    sig_gain = (close / sig_price - 1.0) if sig_price else None
    sig_far = bool(sig_gain is not None and sig_gain > SIG_FAR)

    # ── 九态判定（优先级从高到低）──────────────────────
    bei = _bei_flags(sig)
    is_sell = sig_type in ("1卖", "2卖", "3卖")
    # 顶背驰 = 真衰竭，按缠论只认 1卖/2卖：
    #   1卖 = 向上线段终结·动量衰减，本身就是顶背驰点；
    #   2卖 = 1卖后反弹不创新高，次高卖点，性质同为上涨结构结束。
    # 3卖 不进衰竭档：它是中枢下沿反抽不过，属下跌趋势确认（延续），
    #   而且它的 bei 字段恒为真（"反抽力度弱于离开笔"本就是 3卖 的构造），
    #   拿它判衰竭等于把整个下跌侧都报成见顶——首版踩过一次，1,367 只。
    # tbe/mbei 挂在买点上是「底背驰」，是最强买点确认，绝不能翻成衰竭。
    top_bei = bool(sig and sig_fresh and sig_type in ("1卖", "2卖"))

    # 买点有效性：信号须新鲜、仍站在其赖以成立的价位上、且尚未走远。
    # · 3买 守「信号成立时那个中枢」的上沿 sig_zg（信号自带 zg 字段），
    #   不是末中枢的 ZG——中信银行距末中枢 ZG 已 +17.6%，却仍被算作三买，
    #   就是因为拿错了参照物。
    # · 1买/2买 守信号价。
    # · 容错 1%：缠论里"回踩不破 ZG"是硬条件，破了就是破了。首版留 3%，
    #   结果新北洋当日跌 9%、早已跌破 ZG，仍挂在三买档。
    # · sig_far：买点后已涨超 SIG_FAR 即「已兑现」，不再是入场券。
    # · 下限 SIG_STOP：买点后跌超 5% 即认输。没有这条，3买 后跌 18.8% 的票
    #   （威龙股份）仍算有效——它守的旧中枢上沿早已远离现价，条件形同虚设。
    sig_zg = sig.get("zg") if sig else None
    tol = 0.99
    SIG_STOP = -0.05

    def _buy_ok(t, level):
        return (sig_type == t and sig_fresh and not sig_far
                and level is not None and close > level * tol
                and (sig_gain is None or sig_gain >= SIG_STOP))

    # 3买 额外要求：当前不能已跌破末中枢下沿。格灵深瞳 3买 后先涨出新中枢
    # （ZG 抬到 22.30），随后跌回 17.30、落在新中枢下沿 18.45 之下——相对
    # 旧买点没亏，但结构上早已不是三买。1买/2买 是下跌末端买点，允许在
    # 中枢下方，不受此约束。
    b3_ok = (_buy_ok("3买", (sig_zg or zg))
             and (zd is None or close >= zd * 0.99))
    b2_ok = _buy_ok("2买", sig_price)
    b1_ok = _buy_ok("1买", sig_price)

    # 中枢是否仍在本级别有效：延伸超 9 笔、或区间过宽 → 已升级。
    # 此时 ZG/ZD 不代表可操作边界，位置判定作废（宁可不判，也不拿假边界
    # 当买点条件）。
    n_piv_bi = piv["n_bi"] if piv else 0
    piv_wide = bool(zg and zd and (zg - zd) / zd > PIV_MAX_WIDTH)
    piv_over = bool(piv_wide or n_piv_bi > PIV_MAX_BI)

    # 中枢覆盖期内的「真实高低点」。ZG/ZD 按缠论定义取最初三笔的重叠区间，
    # 后续笔只延伸不修正区间，所以延伸后 ZG/ZD 会与真实震荡脱节——天津港
    # 标 ZG-ZD=4.33~4.35（宽 0.46%），9 笔却横跨更宽的价格带。把真实区间
    # 一并算出来作为证据，读者能自己看出边界可不可信。
    real_lo = real_hi = None
    if piv:
        seg = [b for b in bars if piv["s_dt"] <= b["dt"] <= piv["e_dt"]]
        if seg:
            real_lo = min(b["l"] for b in seg)
            real_hi = max(b["h"] for b in seg)

    if top_bei:
        stage = "背驰"
    elif b3_ok:
        stage = "三买"
    elif b2_ok:
        stage = "二买"
    elif b1_ok:
        stage = "一买"
    elif piv_over:
        # 枢已升级 → 本级别上/下沿不可操作，按中枢处理
        stage = "中枢"
    elif (pos == "上沿外" and last_bi["dir"] == 1
          and dist_zg is not None and 0.0 <= dist_zg <= B3P_MAX_DIST
          and trend_dir >= 0
          and not (is_sell and sig_fresh)):
        # 已离开上沿、还没走远、无新鲜卖点，且不在下跌趋势里 —— 这才是
        # 「将买未买」。trend_dir >= 0 这条不能省：下跌趋势中价格回到上沿
        # 之上是反弹，不是三买（三买的前提是离开方向向上）。首版漏了它，
        # 69 只三买待全是「下跌走势 + 过期 3卖」，即该中枢早已被向下离开。
        stage = "三买待"
    elif sig_type == "3卖" and sig_fresh:
        stage = "三卖"
    elif pos == "上沿外":
        stage = "离开段"
    elif pos == "枢内":
        stage = "中枢"
    else:
        stage = "中枢下"

    # ── 末笔状态：是否仍在延伸（未完成笔）──────────────
    # 末笔终点若就是最后一根 K 线，视为“正在生长的笔”，不假装已定
    bi_live = bool(bars and last_bi["e_dt"] == bars[-1]["dt"])

    # ── 结构强度分（排序用，非评分模型）──────────────
    # 缠论里中枢延伸越久，离开后力度越大；趋势中的买点强于盘整中的买点。
    # 这不是「打分选股」，只是同档内排序依据。
    score = 0.0
    if stage == "三买待":
        # 贴着上沿等突破：距 ZG 越近越优先（这是三买待的全部意义）。
        # 中枢成熟度只作微调，且 5~8 笔加分、9 笔（升级边缘）不加。
        score = 100.0 - abs(dist_zg or 0) * 1000.0
        if PIV_SOLID_BI <= n_piv_bi < PIV_MAX_BI:
            score += 5.0
    elif stage == "三买":
        score = float(n_piv_bi) * 2.0 + abs(trend_dir) * 3.0
        if sig_fresh and sig_gain is not None:
            score += max(0.0, 5.0 - abs(sig_gain) * 20.0)
    elif stage in ("二买", "一买"):
        score = float(n_piv_bi) + abs(trend_dir) * 2.0
        if sig_fresh and sig_gain is not None:
            score += max(0.0, 5.0 - abs(sig_gain) * 10.0)
    elif stage == "中枢":
        # 中枢越久（笔数越多），一旦离开越有力
        score = float(n_piv_bi) * 0.5
    elif stage == "背驰":
        score = -float(n_piv_bi)
    elif stage in ("三卖", "离开段", "中枢下"):
        score = -float(n_piv_bi) * 0.5
    if piv_over:
        # 枢已升级 = 当前处在大级别震荡里，本级别的买卖点信号不可靠。
        # 仍列出（信号本身是真的），但排到本档末尾。
        score -= 30.0

    return {
        "stage": stage,
        # 价格
        "close": _safe(close),
        "pct": _safe(bars[-1].get("c") / bars[-2]["c"] - 1.0) if len(bars) > 1 else None,
        # 中枢
        "zg": _safe(zg),
        "zd": _safe(zd),
        "pos": pos,
        "dist_zg": _safe(dist_zg),
        "dist_zd": _safe(dist_zd),
        "piv_n_bi": int(n_piv_bi),
        "piv_sdt": piv["s_dt"] if piv else None,
        "piv_edt": piv["e_dt"] if piv else None,
        "n_piv": len(pivs),
        "piv_solid": bool(n_piv_bi >= PIV_SOLID_BI),
        "piv_wide": piv_wide,
        "piv_over": piv_over,
        "real_lo": _safe(real_lo),
        "real_hi": _safe(real_hi),
        # 走势
        "trend": trend,
        "trend_dir": int(trend_dir),
        # 笔 / 线段
        "bi_dir": int(last_bi["dir"]),
        "bi_sdt": last_bi["s_dt"],
        "bi_edt": last_bi["e_dt"],
        "bi_amp": _safe(abs(last_bi["e_price"] - last_bi["s_price"])),
        "bi_live": bi_live,
        "n_bi": len(bis),
        "seg_dir": int(last_seg["dir"]) if last_seg else None,
        "n_seg": len(segs),
        # 信号
        "sig_type": sig_type,
        "sig_dt": sig_dt,
        "sig_price": _safe(sig_price),
        "sig_note": sig.get("note") if sig else None,
        "sig_age": sig_age,
        "sig_fresh": bool(sig_fresh),
        "sig_gain": _safe(sig_gain),
        "sig_far": sig_far,
        "bei": bei,
        "score": _safe(score),
        "asof": int(str(bars[-1]["dt"])[:8]) if bars[-1]["dt"] else None,
    }


def iter_files():
    for fn in os.listdir(ASHARE):
        if fn.endswith(".parquet"):
            yield fn


def load_names():
    """code2name 映射（data/stock_names.json，fetch_stock_names.py 生成）。"""
    p = os.path.join(HERE, "data", "stock_names.json")
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        return {str(k).upper(): str(v) for k, v in (j.get("code2name") or {}).items()}
    except Exception:
        return {}


def load_mv():
    """最新交易日流通市值 {code: 亿元}（ts_daily_basic，circ_mv 单位万元）。"""
    try:
        fs = sorted(glob.glob(os.path.join(HERE, "data", "tdx", "ts_daily_basic", "*.parquet")))
        if not fs:
            return {}
        dfs = [pd.read_parquet(f, columns=["ts_code", "trade_date", "circ_mv"]) for f in fs]
        df = pd.concat(dfs, ignore_index=True)
        d = df[df["trade_date"] == df["trade_date"].max()]
        return {str(c).upper(): float(v) / 1e4
                for c, v in zip(d["ts_code"], d["circ_mv"])
                if pd.notna(v)}
    except Exception:
        return {}


def mv_bucket(v):
    if v is None:
        return "未知"
    if v < 20:
        return "微盘<20"
    if v < 50:
        return "小盘20-50"
    if v < 200:
        return "中盘50-200"
    if v < 1000:
        return "大盘200-1k"
    return "超大盘>1k"


def _pad(s, w):
    """按显示宽度左对齐（中文占 2 列）。"""
    s = "" if s is None else str(s)
    n = sum(2 if ord(ch) > 127 else 1 for ch in s)
    return s + " " * max(0, w - n)


def scan(min_amt=0.5, stage_filter=None, top=30, verbose=True, keep_bj=False):
    t0 = time.time()
    rows = []
    n_st = n_low = n_amt = n_stale = n_idx = n_bj = n_bad = 0
    names = load_names()
    mvs = load_mv()
    files = list(iter_files())
    asof_seen = []

    for i, fn in enumerate(files, 1):
        path = os.path.join(ASHARE, fn)
        code = fn[:-8].replace("_", ".")
        if _is_index(code):
            n_idx += 1
            continue
        if not keep_bj and _is_bj(code):
            n_bj += 1
            continue
        nm = names.get(code, "")
        if any(k in nm for k in ST_MARKS):
            n_st += 1
            continue
        try:
            df = pd.read_parquet(path, columns=COLS)
        except Exception:
            n_bad += 1
            continue
        if len(df) < MIN_BARS:
            n_bad += 1
            continue
        # 数据不新鲜：残档（退市/长期停牌）最后一根远早于最新交易日
        last_dt = str(df["trade_date"].iloc[-1])
        asof_seen.append(last_dt)
        r = analyse(df, code)
        if r is None:
            n_bad += 1
            continue
        if (r["close"] or 0) < MIN_PRICE:
            n_low += 1
            continue
        # 20 日均额（amount 单位=千元 → 亿元）
        if "amount" in df.columns:
            amt20 = float(np.nan_to_num(df["amount"].to_numpy(float)[-20:]).mean()) / 1e5
        else:
            amt20 = 0.0
        if amt20 < min_amt:
            n_amt += 1
            continue
        r["code"] = code
        r["name"] = nm
        r["mv_yi"] = _safe(mvs.get(code))
        r["bucket"] = mv_bucket(r["mv_yi"])
        r["amt20_yi"] = _safe(amt20)
        rows.append(r)
        if verbose and i % 1500 == 0:
            print(f"  ...{i}/{len(files)}", file=sys.stderr)

    # 一次性剔掉「不新鲜」：以可判样本的众数交易日为准
    data_asof = ""
    if asof_seen:
        data_asof = max(asof_seen)
        fresh = [r for r in rows if str(r["asof"]) and str(r["asof"]) == data_asof[:8]]
        n_stale = len(rows) - len(fresh)
        rows = fresh

    buckets = {}
    for r in rows:
        buckets.setdefault(r["stage"], []).append(r)

    elapsed = time.time() - t0
    print(f"[chan_pool] 扫描 {len(files)} 文件 / 可判 {len(rows)} / {elapsed:.1f}s")
    print(f"[chan_pool] 剔除：指数 {n_idx} / 北交所 {n_bj} / ST退 {n_st} / "
          f"低价 {n_low} / 额<{min_amt}亿 {n_amt} / 不新鲜 {n_stale} / 次新或坏文件 {n_bad}")
    print(f"[chan_pool] 数据基准 {data_asof} · 操作级别=日线 · 窗口 {LOOKBACK} 根")
    for st in STAGES:
        n = len(buckets.get(st, []))
        bar = "█" * min(40, int(n / max(len(rows), 1) * 120))
        print(f"  {_pad(st, 8)}{n:>6}  {bar}")

    if rows:
        tre = {}
        for r in rows:
            tre[r["trend"]] = tre.get(r["trend"], 0) + 1
        print("[chan_pool] 走势类型分布: " + "  ".join(
            f"{k}={tre.get(k, 0)}" for k in ("上涨", "下跌", "盘整")))
        posd = {}
        for r in rows:
            posd[r["pos"]] = posd.get(r["pos"], 0) + 1
        print("[chan_pool] 相对中枢位置: " + "  ".join(
            f"{k}={posd.get(k, 0)}" for k in ("上沿外", "枢内", "下沿外", "无枢")))

    stages = [stage_filter] if stage_filter else POOL_STAGES
    out = {}
    for st in stages:
        lst = buckets.get(st, [])
        # 排序：结构强度 → 中枢笔数 → 信号新鲜
        lst.sort(key=lambda r: (-(r["score"] or 0), -(r["piv_n_bi"] or 0),
                                r["sig_age"] if r["sig_age"] is not None else 9999))
        out[st] = lst[:top]
        if not lst:
            continue
        print(f"\n===== {_pad(st, 8)} 共 {len(lst)} 只，列前 {min(top, len(lst))} =====")
        print(f"{_pad('代码', 11)}{_pad('名称', 11)}{'现价':>7}{'市值亿':>7}"
              f"{'ZD':>7}{'ZG':>7}{'距ZG':>7}{'枢笔':>5}{'走势':>5}"
              f"{'末笔':>5}{'信号':>6}{'龄':>4}  背驰/备注")
        for r in out[st]:
            d_zg = f"{(r['dist_zg'] or 0)*100:>6.1f}%" if r["dist_zg"] is not None else "     -"
            note = r["bei"] or ""
            if r["sig_type"] and not r["sig_fresh"]:
                note = (note + " 信号已过期").strip()
            if r["piv_over"]:
                note = (note + " 枢已升级").strip()
            if r["bi_live"]:
                note = (note + " 末笔未走完").strip()
            print(f"{_pad(r['code'], 11)}{_pad(r.get('name', ''), 11)}"
                  f"{(r['close'] or 0):>7.2f}{(r['mv_yi'] or 0):>7.1f}"
                  f"{(r['zd'] or 0):>7.2f}{(r['zg'] or 0):>7.2f}{d_zg}"
                  f"{r['piv_n_bi']:>5}{r['trend']:>5}"
                  f"{'↑' if r['bi_dir'] > 0 else '↓' if r['bi_dir'] < 0 else '-'}"
                  f"{('*' if r['bi_live'] else ' ')}"
                  f"{(r['sig_type'] or '-'):>6}"
                  f"{(r['sig_age'] if r['sig_age'] is not None else -1):>4}"
                  f"  {note}")

    return out, buckets, {
        "files": len(files), "judged": len(rows),
        # 各档真实总数（out 只留 top，页面显示「共 N 只」要靠这个）
        "stage_counts": {st: len(buckets.get(st, [])) for st in STAGES},
        # 全市场分布（页面顶部的走势/位置条形图必须用这个，
        # 若用 buckets 里的 top-N 汇总，条形图会变成「六档内部的分布」，
        # 与全市场无关，读起来是错的）
        "trend_dist": {k: sum(1 for r in rows if r["trend"] == k)
                       for k in ("上涨", "下跌", "盘整")},
        "pos_dist": {k: sum(1 for r in rows if r["pos"] == k)
                     for k in ("上沿外", "枢内", "下沿外", "无枢")},
        "ex_idx": n_idx, "ex_bj": n_bj, "ex_st": n_st, "ex_low": n_low,
        "ex_amt": n_amt, "ex_stale": n_stale, "ex_bad": n_bad,
        "elapsed": round(elapsed, 1), "min_amt": min_amt,
        "lookback": LOOKBACK, "min_bars": MIN_BARS,
        "data_asof": data_asof,
        "sig_fresh": SIG_FRESH, "sig_far": SIG_FAR,
    }


def _fetch_quotes(codes):
    """新浪实时价（与 pool_watch / watch_positions 同一接口）。"""
    root = os.path.dirname(HERE)
    p = os.path.join(root, "news_collector")
    if p not in sys.path:
        sys.path.insert(0, p)
    from quote_snapshot import fetch_quotes

    return fetch_quotes(codes)


def _from_sina(c):
    c = str(c).lower()
    for pre, suf in (("sh", ".SH"), ("sz", ".SZ"), ("bj", ".BJ")):
        if c.startswith(pre):
            return c[2:] + suf
    return str(c).upper()


def intraday(top=None):
    """盘中巡检：读最近一份池文件，只比对中枢沿，不重算笔。

    缠论的理由：笔的成立要靠收盘价确认，盘中用现价重算会造出假笔、假中枢，
    进而造出假三类买卖点。所以盘中只做一件事——看价格有没有越过中枢沿，
    把「结构可能演化成什么」标出来，收盘后再重算定论。

      中枢    现价 > ZG      → 向上离开，转三买待（等回踩）
      三买待  现价 < ZG      → 回踩破了上沿，三买不成立，退回枢内
      中枢    现价 < ZD      → 向下离开，转中枢下
      三买    现价 < ZG      → 买点失效（中枢上沿失守）
      二买/一买 现价 < 信号价 → 买点极值失守
    """
    files = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
    if not files:
        print("[intraday] 无池文件，先跑 --scan --save")
        return 1
    src = files[-1]
    with open(src, encoding="utf-8") as f:
        j = json.load(f)

    rows = []
    for st in ("三买待", "中枢", "三买", "二买", "一买"):
        for r in j["buckets"].get(st, []):
            rows.append((st, r))
    if not rows:
        print("[intraday] 池为空")
        return 1

    codes = [r["code"] for _, r in rows]
    try:
        qs = _fetch_quotes(codes)
    except Exception as e:
        print(f"[intraday] 取价失败: {e}")
        return 2
    idx = {_from_sina(q.get("code", "")): q for q in qs}

    print(f"[intraday] 池文件 {os.path.basename(src)}（结构基准 {j.get('data_asof')}）"
          f" / 监控 {len(codes)} 只 / 取到报价 {len(idx)} 只")
    print("[intraday] 只比对中枢沿，不重算笔——收盘后跑 --scan 才是定论\n")
    print(f"{_pad('代码', 11)}{_pad('名称', 11)}{'原态':>7}{'现价':>8}{'涨幅':>7}"
          f"{'ZD':>7}{'ZG':>7}{'vsZG':>8}   结构演化")

    alerts, shown = [], 0
    for st, r in rows:
        q = idx.get(r["code"])
        if not q or not q.get("price"):
            continue
        px = float(q["price"])
        pct = q.get("pct")
        zg = r.get("zg") or 0
        zd = r.get("zd") or 0
        d_zg = px / zg - 1 if zg else 0

        note = ""
        if st == "中枢" and zg and px > zg:
            note = "上离枢→三买待（等回踩不破 ZG）"
        elif st == "中枢" and zd and px < zd:
            note = "下离枢→中枢下（等三卖或新一买）"
        elif st == "三买待" and zg and px < zg:
            note = "回踩破 ZG→三买不成立，退枢内"
        elif st == "三买" and zg and px < zg:
            note = "ZG 失守→三买失效"
        elif st in ("二买", "一买"):
            sp = r.get("sig_price") or 0
            if sp and px < sp:
                note = f"{st}极值失守（信号价 {sp:.2f}）"
        if note:
            alerts.append(f"{r['code']} {r.get('name', '')} {note} 现价 {px}")

        print(f"{_pad(r['code'], 11)}{_pad(r.get('name', ''), 11)}{st:>7}"
              f"{px:>8.2f}{(pct if pct is not None else 0):>6.2f}%"
              f"{zd:>7.2f}{zg:>7.2f}{d_zg*100:>7.1f}%   {note}")
        shown += 1

    print(f"\n[intraday] 共 {shown} 只 / 结构演化 {len(alerts)} 条")
    for a in alerts:
        print("  ⚑ " + a)
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="全市场扫描")
    ap.add_argument("--intraday", action="store_true", help="盘中巡检：读池 + 实时价，比中枢沿")
    ap.add_argument("--min-amt", type=float, default=0.5, help="20 日均成交额下限（亿元）")
    ap.add_argument("--stage", default=None, help="只看某态：" + "/".join(STAGES))
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--keep-bj", action="store_true", help="保留北交所（默认剔除）")
    args = ap.parse_args()

    if args.intraday:
        return intraday()

    if not args.scan:
        ap.print_help()
        return 0

    out, buckets, meta = scan(args.min_amt, args.stage, args.top, keep_bj=args.keep_bj)
    if args.save:
        os.makedirs(REPORTS, exist_ok=True)
        asof = meta.get("data_asof") or datetime.now().strftime("%Y%m%d")
        p = os.path.join(REPORTS, f"pool_state_{asof}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "data_asof": asof,
                       "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "buckets": out}, f, ensure_ascii=False, indent=1)
        print(f"\n[saved] {p}  (结构基准 {asof})")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
