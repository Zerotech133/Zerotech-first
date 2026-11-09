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
  python pool_state.py --live                 # 全市场叠实时价：谁在离开中枢
  python pool_state.py --judge --save         # 盘中一条命令出两个产物（推荐）

--live 与 --judge 的分工：前者只答「相对中枢跑到哪了」，把「越过上沿」和
「三买刚成立」混在一格，操作含义相反；后者在同一份数据上再叠可操作性判据，
分成 A 突破 / B 三买 / C 持有 / D 衰竭 / E 贴沿五层，并顺手落 pool_live。
两者共用一次全市场扫描（_snapshot）——原来各扫一遍，等于把 4,000 个 parquet
读两次。

盘中只比对中枢沿，不重算笔——笔的修正需要收盘价确认，盘中算出来的是假结构。
"""

import argparse
import contextlib
import glob
import io
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
        # 信号成立时那个中枢的上沿：3买 的守位是它，不是末中枢的 ZG。
        # 两者常常不是同一个（3买 后可能已长出新中枢），混用会把「守得住」
        # 判成「失守」，也会把「早已破位」判成「还守着」。
        "sig_zg": _safe(sig_zg),
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
    """新浪实时价（与 pool_watch / watch_positions 同一接口）。

    分批取：`hq.sinajs.cn/list=` 是 GET，4,000 只塞一个 URL 会超长被截断，
    返回里少掉的 key 不会报错，只会静默丢票——盘中巡检最怕这个。
    """
    root = os.path.dirname(HERE)
    p = os.path.join(root, "news_collector")
    if p not in sys.path:
        sys.path.insert(0, p)
    from quote_snapshot import fetch_quotes

    uniq = list(dict.fromkeys(codes))
    out, seen = [], set()
    CH = 200
    for i in range(0, len(uniq), CH):
        part = uniq[i:i + CH]
        try:
            got = fetch_quotes(part)
        except Exception as e:  # 单批失败不拖垮全轮
            print(f"[live] 批次 {i // CH + 1} 取价失败: {str(e)[:80]}", file=sys.stderr)
            got = []
        for q in got:
            k = q.get("code")
            if k and k not in seen:
                seen.add(k)
                out.append(q)
        if i + CH < len(uniq):
            time.sleep(0.2)
    return out


def _from_sina(c):
    c = str(c).lower()
    for pre, suf in (("sh", ".SH"), ("sz", ".SZ"), ("bj", ".BJ")):
        if c.startswith(pre):
            return c[2:] + suf
    return str(c).upper()


def _lpos(px, zd, zg, tol=0.003):
    """实时价相对中枢的位置。

    结构日的 `pos` 是按当日收盘算的，盘中拿它显示位置会与分档矛盾：
    南矿集团实时 19.05 已越过 ZG 18.27，JSON 里 `pos` 仍写「枢内」，
    页面读它就和「上离枢」标签打架。所以另算一份 `lpos`，两者并存——
    `pos` 回答「11-06 收在哪里」，`lpos` 回答「现在在哪里」。
    """
    if not zd or not zg:
        return "无枢"
    if px > zg * (1 + tol):
        return "上沿外"
    if px < zd * (1 - tol):
        return "下沿外"
    return "枢内"


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
    # buckets 现在落的是全量（中枢 1,005 / 三买 82），巡检只盯每档前 N 只，
    # 否则一次要取 1,182 只价、打印 1,182 行——那是全市场扫描，不是巡检。
    INTRA_TOP = 50
    for st in ("三买待", "中枢", "三买", "二买", "一买"):
        for r in j["buckets"].get(st, [])[:INTRA_TOP]:
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


def _px_of(idx, code):
    q = idx.get(code)
    if not q:
        return None, None
    p = q.get("price")
    if not p:
        return None, None
    return float(p), q.get("pct")


def _mins_of(live_at):
    """已交易分钟数——把「当日累计成交额」折算成量比要用它。

    首版（tmp/judge_pool.py）写的是 (hh-9)*60+mm-30：14:00 会算成 270 分钟，
    分母虚大 2 倍多，量比被压掉一半，午后的突破全被判成「无量」。这里按
    真实交易分钟算：上午 ≤120，午休记 120，下午接着累加，上限 240。
    """
    hm = (live_at or "")[11:16]
    if len(hm) < 5:
        return 0
    try:
        hh, mm = int(hm[:2]), int(hm[3:5])
    except ValueError:
        return 0
    t = hh * 60 + mm
    if t < 570:                        # 09:30 之前
        return 1
    if t <= 690:                       # 上午 09:30~11:30
        return max(t - 570, 1)
    if t < 780:                        # 午休
        return 120
    return min(120 + (t - 780), 240)   # 下午 13:00~15:00


def _snapshot(min_amt=0.5, top=40, verbose=False, quiet=False):
    """一次全市场扫描 + 一次取价，供 --live / --judge 共用。

    --live 和 --judge 原来各自 scan 一遍全市场（每遍约 50s），盘中两件事都要
    看的时候等于把 4,000 个 parquet 读两遍、把 4,000 只票的价格取两遍。
    这里只扫一次，两件事吃同一份数据。

    quiet=True 时只留 scan 的三行摘要——--judge 自己有 A~E 五张表，再把九态
    大表打在前面就是噪声（盘中每 15 分钟一条推送，前面挂 180 行谁都不看）。
    """
    t0 = time.time()
    if quiet:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _out, buckets, meta = scan(min_amt, None, top, verbose=False)
        for ln in buf.getvalue().splitlines():
            if ln.startswith(("[chan_pool] 扫描", "[chan_pool] 剔除",
                              "[chan_pool] 数据基准")):
                print(ln)
    else:
        _out, buckets, meta = scan(min_amt, None, top, verbose=verbose)
    rows = [r for st in STAGES for r in buckets.get(st, [])]
    if not rows:
        return None
    try:
        qs = _fetch_quotes([r["code"] for r in rows])
    except Exception as e:                    # noqa: BLE001 —— 网络失败要显式报错
        raise RuntimeError(f"取价失败: {e}") from e
    idx = {_from_sina(q.get("code", "")): q for q in qs}
    ts_seen = [q.get("ts") for q in qs if q.get("ts")]
    live_at = max(ts_seen) if ts_seen else ""

    hit = up = dn = flat = n_lim = 0
    for r in rows:
        px, pct = _px_of(idx, r["code"])
        if px is None:
            continue
        hit += 1
        pct = pct or 0.0
        if pct > 0:
            up += 1
        elif pct < 0:
            dn += 1
        else:
            flat += 1
        if pct >= 9.8:
            n_lim += 1

    return {
        "rows": rows, "buckets": buckets, "meta": meta, "idx": idx,
        "live_at": live_at, "struct_asof": meta.get("data_asof") or "",
        "mins": _mins_of(live_at), "elapsed": time.time() - t0,
        "hit": hit, "up": up, "down": dn, "flat": flat, "limit_up": n_lim,
    }


def _build_evo(rows, idx):
    """结构演化：现价对结构日（最近收盘日）的中枢沿。

    与 --judge 的分工：这里只回答「相对那个中枢跑到哪了」，不带任何可操作性
    判据；--judge 才在上面加「宽度/笔数/量比/涨跌停」这层筛子。
    """
    evo = {"上离枢": [], "下离枢": [], "三买待确认": [], "三买待破位": [],
           "三买守稳": [], "三买失守": [], "买点失守": []}
    for r in rows:
        if r.get("piv_over"):
            continue          # 级别已升级，ZG/ZD 不是操作边界
        px, pct = _px_of(idx, r["code"])
        if px is None:
            continue
        st = r["stage"]
        zg = r.get("zg") or 0
        zd = r.get("zd") or 0
        item = dict(r)
        item["lpx"] = px
        item["lpct"] = pct if pct is not None else 0.0
        item["ldzg"] = (px / zg - 1) if zg else None
        item["lpos"] = _lpos(px, zd, zg)

        if st == "中枢" and zg and px > zg * 1.003:
            evo["上离枢"].append(item)
        elif st == "中枢" and zd and px < zd * 0.997:
            evo["下离枢"].append(item)
        elif st == "三买待" and zg:
            (evo["三买待确认"] if px > zg else evo["三买待破位"]).append(item)
        elif st == "三买":
            # 守位是信号成立时那个中枢的上沿，不是末中枢的 ZG
            g3 = r.get("sig_zg") or zg
            if g3:
                item["guard"] = g3
                item["ldzg"] = px / g3 - 1
                (evo["三买守稳"] if px > g3 else evo["三买失守"]).append(item)
        elif st in ("二买", "一买"):
            sp = r.get("sig_price") or 0
            if sp and px < sp:
                evo["买点失守"].append(item)
    return evo


def _save_live(evo, snap, top):
    """落盘 pool_live_*.json（全量，不按 top 裁）。"""
    os.makedirs(REPORTS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    # 落盘前排序：突破档按越沿幅度降序（谁离得最远最先看），守位档按
    # 距守位升序（谁最贴止损线最先看）。终端只印前 top 个，JSON 是全量。
    for k, v in evo.items():
        if k in ("上离枢", "下离枢"):
            v.sort(key=lambda r: -abs(r.get("ldzg") or 0))
        else:
            v.sort(key=lambda r: (r.get("ldzg") if r.get("ldzg") is not None else 9))
    p = os.path.join(REPORTS, f"pool_live_{stamp}.json")
    payload = {
        "struct_asof": snap["struct_asof"],
        "live_at": snap["live_at"],
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "judged": len(snap["rows"]), "quoted": snap["hit"],
        "up": snap["up"], "down": snap["down"], "flat": snap["flat"],
        "limit_up": snap["limit_up"],
        "counts": {k: len(v) for k, v in evo.items()},
        # 存全量，不按 top 裁。原来的 `v[:top]` 让「三买守稳」69 只只落
        # 30 条，页面「下钻看完整证据」时后 39 只查不到——计数与明细对不上。
        "groups": evo,
        "print_top": top,     # 终端只印前 N 个，JSON 是全量
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {p}")
    return p


def live(min_amt=0.5, top=40, save=False):
    """全市场实时叠加：结构取最近数据日的日线，价格取当下。

    与 --intraday 的分工：
      --intraday  读池文件，只看已落盘的 top-N（中枢档 1,005 只只留 50 只，
                  等于对 95% 的市场失明），适合盯已持仓那几十只；
      --live      复用 scan 的 buckets（全量行，未裁剪），给每只叠实时价，
                  覆盖全部可判标的，回答「今天全市场谁在离开中枢」。

    不做的事：不重算笔。盘中价格未收盘，用它造 K 线会造出假笔、假中枢，
    进而造出假买卖点。这里只把「现价 vs 结构日的中枢沿」摆出来。
    """
    try:
        snap = _snapshot(min_amt, top)
    except RuntimeError as e:
        print(f"[live] {e}")
        return 2
    if snap is None:
        print("[live] 无可判标的")
        return 1

    rows, idx = snap["rows"], snap["idx"]
    print(f"[live] 结构基准 {snap['struct_asof']} · 实时 {snap['live_at']}")
    print(f"[live] 可判 {len(rows)} / 取到报价 {snap['hit']} "
          f"({snap['hit'] / max(len(rows), 1) * 100:.0f}%) · "
          f"扫描 {snap['elapsed']:.0f}s")
    print(f"[live] 实时涨跌：上涨 {snap['up']} / 下跌 {snap['down']} / "
          f"平 {snap['flat']} · 涨停(≥9.8%) {snap['limit_up']}")

    evo = _build_evo(rows, idx)

    def _show(title, lst, hint, key=None, guard=False):
        if not lst:
            print(f"\n===== {title}  0 只 =====")
            return
        print(f"\n===== {title}  共 {len(lst)} 只"
              + (f"，列前 {min(top, len(lst))} =====" if len(lst) > top else " ====="))
        if guard:
            print(f"{_pad('代码', 11)}{_pad('名称', 11)}{'现价':>8}{'涨幅':>8}"
                  f"{'守位':>7}{'距守位':>9}{'市值亿':>8}{'枢笔':>5}{'位':>5}  {hint}")
        else:
            print(f"{_pad('代码', 11)}{_pad('名称', 11)}{'现价':>8}{'涨幅':>8}"
                  f"{'ZD':>7}{'ZG':>7}{'距ZG':>8}{'市值亿':>8}{'枢笔':>5}{'位':>5}  {hint}")
        srt = sorted(lst, key=key or (lambda r: -(r.get("ldzg") or -9)))
        for r in srt[:top]:
            mid = (f"{(r['guard'] or 0):>7.2f}{(r['ldzg'] or 0) * 100:>8.1f}%"
                   if guard else
                   f"{(r['zd'] or 0):>7.2f}{(r['zg'] or 0):>7.2f}"
                   f"{(r['ldzg'] or 0) * 100:>7.1f}%")
            print(f"{_pad(r['code'], 11)}{_pad(r.get('name', ''), 11)}"
                  f"{r['lpx']:>8.2f}{r['lpct']:>7.2f}%{mid}"
                  f"{(r['mv_yi'] or 0):>8.1f}{r['piv_n_bi']:>5}"
                  f"{(r.get('lpos') or '-'):>5}")

    _show("中枢 → 上离枢（今日真突破，收盘才算确认）", evo["上离枢"],
          "突破幅度", key=lambda r: -(r["ldzg"] or -9))
    _show("三买待 → 仍在上沿上方（等回踩不破 ZG）", evo["三买待确认"],
          "距ZG", key=lambda r: (r["ldzg"] or 9))
    _show("三买待 → 破 ZG（回踩失守，转枢内）", evo["三买待破位"],
          "距ZG", key=lambda r: (r["ldzg"] or 9))
    _show("三买 → 仍守 ZG（守位=信号中枢上沿）", evo["三买守稳"], "距守位",
          key=lambda r: (r["ldzg"] or 9), guard=True)
    _show("三买 → 守位失守（买点失效）", evo["三买失守"], "距守位",
          key=lambda r: (r["ldzg"] or 9), guard=True)
    _show("中枢 → 下离枢（向下离开，转中枢下）", evo["下离枢"],
          "跌破幅度", key=lambda r: (r["ldzg"] or 9))
    if evo["买点失守"]:
        print(f"\n[live] 另有一买/二买极值失守 {len(evo['买点失守'])} 只")

    if save:
        _save_live(evo, snap, top)
    return 0


# ── 判据：把「结构位置」重新分成可操作的五层 ─────────────────────
# 为什么另起一层：--live 只回答「相对中枢跑到哪了」，它把「今日越过上沿」与
# 「刚刚成立的三买」混在一个格子里——前者是等回踩，后者是可介入，操作含义
# 相反。这一层不新增任何指标，只把结构位置 + 量能 + 可操作性分开说：
#
#   A 今日突破   越过末中枢 ZG，但已走远——等回踩，不是现在买
#   B 新鲜三买   信号 ≤25 日 + 仍守在守位之上 + 距守位 ≤8%——可介入区
#   C 持有区     守住守位但已回末中枢上沿之下——只跟踪不加仓
#   D 衰竭       新鲜背驰（12 日内）——减仓预警，不是买点
#   E 贴沿未过   距上沿 1.5% 以内、还没过——「临近爆发」的本义，埋伏不是买
#
# 判据边界是 2026-11-09 盘中现定的，没有回测背书。124 个截面回放的结论是
# 三买 ② 列 −172bp(t=−3.70)、三买待 −284bp(t=−3.67)，所以这一层的用途是
# 「知道自己在什么位置、什么时候该走」，不是「买入信号」。
J_W_MIN, J_W_MAX = 0.03, 0.30      # 中枢宽度：<3% 退化，>30% 级别已升级
J_BI_MIN, J_BI_MAX = 3, 7          # 8~9 笔属延伸晚期，突破多为最后一冲
J_BRK_MIN, J_BRK_MAX = 0.003, 0.025  # 突破幅度：贴沿不算，走太远不追
J_VOL_MORNING = 1.8                # 早盘量能折算系数（前 30 分钟约占全天 1/1.8）
J_VOL_MIN = 1.0                    # 校准后量比 ≥1.0 才算有量
J_AGE_MAX = 25                     # 三买信号有效期
J_AGE_WAIT = 45                    # 三买待可放宽到 45 日，再老就是长磨不是新结构
J_NEAR = 0.08                      # 距守位 ≤8% 算「守位还在手边」
J_FAR = 0.15                       # >15% 已走远，连跟踪都不必
J_TOUCH = 0.015                    # 距上沿 1.5% 以内视为「门口」
J_MV_MIN, J_MV_MAX = 20.0, 2000.0  # 微盘流动性差，超大盘无弹性
J_SELL_SIGS = ("1卖", "2卖", "3卖")
# 游资过滤：近 20 日出现 ≥2 次单日 |涨跌|≥9.5%，说明这是资金博弈盘，不是
# 中枢震荡。实测依据：三峡新材/高争民爆 的一字板拼出「中枢」，ZG/ZD 毫无
# 结构含义；大连圣亚 10-19 跌停后在枢内反弹也被算成「贴沿爆发」。
J_LIMIT_N = 9.5
J_LIMIT_MAX = 1


def _ampl(code):
    """近 20 日涨跌停次数 + 近 5 日最高价。只对候选调用，几十次读盘可接受。"""
    num, _, mkt = code.partition(".")
    p = os.path.join(ASHARE, f"{num}_{mkt}.parquet")
    if not os.path.exists(p):
        return None, None
    try:
        df = pd.read_parquet(p).sort_values("trade_date")
        t = df.tail(20)
        n_lim = int((t["pct_chg"].abs() >= J_LIMIT_N).sum())
        hi5 = float(df.tail(5)["high"].max())
        return n_lim, hi5
    except Exception:                  # noqa: BLE001 —— 单只坏文件不该中断整轮
        return None, None


def _judge_layers(rows, idx, mins):
    """把 scan 的行 + 实时价切成 A/B/C/D/E 五层，并记下每个否决理由。"""
    A, B, C, D, E, drop = [], [], [], [], [], []
    for r in rows:
        px, pct = _px_of(idx, r["code"])
        if px is None:
            continue
        zg, zd = r.get("zg"), r.get("zd")
        if not zg or not zd:
            continue
        w = zg / zd - 1
        q = idx.get(r["code"]) or {}
        amt = q.get("amount") or 0
        volr = None
        if r.get("amt20_yi") and amt and mins:
            volr = amt / 1e8 / (r["amt20_yi"] * mins / 240.0) / J_VOL_MORNING
        sig = r.get("sig_type") or ""
        st = r["stage"]
        g3 = r.get("sig_zg") or zg

        it = dict(r)
        it.update(lpx=px, lpct=(pct or 0.0), w=w, volr=volr, mins=mins,
                  brk=px / zg - 1, dguard=px / g3 - 1,
                  lpos=_lpos(px, zd, zg))

        base_ok = ((not r.get("piv_over")) and (not r.get("piv_wide"))
                   and J_W_MIN <= w <= J_W_MAX
                   and J_BI_MIN <= (r.get("piv_n_bi") or 0) <= J_BI_MAX
                   and J_MV_MIN <= (r.get("mv_yi") or 0) <= J_MV_MAX
                   and (r.get("trend_dir") or 0) >= 0)

        # ── A：今日越过末中枢上沿（原「中枢」档）──────────────
        if st == "中枢" and px > zg * (1 + J_BRK_MIN):
            if not base_ok:
                drop.append((it, "A 结构不合格"))
            elif sig in J_SELL_SIGS:
                drop.append((it, f"A 信号方向={sig}（下跌侧）"))
            elif it["brk"] > J_BRK_MAX:
                drop.append((it, f"A 已越沿 {it['brk']:.1%}（走远）"))
            elif volr is None or volr < J_VOL_MIN:
                drop.append((it, f"A 量比 {volr or 0:.2f}（突破无量）"))
            else:
                A.append(it)

        # ── E：贴着上沿，还没过（「临近爆发」的本义）──────────
        elif st == "中枢" and px >= zg * (1 - J_TOUCH):
            if not base_ok:
                drop.append((it, "E 结构不合格"))
            elif sig in J_SELL_SIGS:
                drop.append((it, f"E 信号方向={sig}（下跌侧）"))
            elif volr is None or volr < J_VOL_MIN:
                drop.append((it, f"E 量比 {volr or 0:.2f}（门口无量）"))
            else:
                E.append(it)

        # ── B/C：三买（已成立）──────────────────────────
        elif st == "三买":
            if px <= g3:
                drop.append((it, "B 守位已破"))
            elif not base_ok:
                drop.append((it, "B 结构不合格"))
            elif (r.get("sig_age") or 999) > J_AGE_MAX:
                drop.append((it, f"B 信号 {r.get('sig_age')} 日（过期）"))
            elif it["dguard"] > J_FAR:
                drop.append((it, f"B 距守位 {it['dguard']:.0%}（已走远）"))
            elif px < zg:
                C.append(it)          # 守位在，但已回末中枢上沿之下
            elif it["dguard"] > J_NEAR:
                C.append(it)          # 守住了、还没走远，但离守位 8%+
            else:
                B.append(it)

        # ── 三买待：上沿外等回踩 ────────────────────────
        elif st == "三买待":
            if px < zg:
                drop.append((it, "待 已回落枢内"))
            elif not base_ok:
                drop.append((it, "待 结构不合格"))
            elif sig in J_SELL_SIGS:
                drop.append((it, f"待 信号方向={sig}（下跌侧）"))
            elif (r.get("sig_age") or 999) > J_AGE_WAIT:
                drop.append((it, f"待 信号 {r.get('sig_age')} 日（长磨非新结构）"))
            elif it["brk"] > J_NEAR:
                drop.append((it, f"待 距上沿 {it['brk']:.0%}（已走远）"))
            else:
                it["guard"] = zg
                B.append(it)

        # ── D：衰竭（新鲜背驰）──────────────────────────
        elif st == "背驰":
            if (r.get("sig_age") or 999) <= 15 and not r.get("piv_over") \
                    and (r.get("mv_yi") or 0) >= J_MV_MIN:
                it["dguard"] = None
                D.append(it)

    def _scrub(lst):
        keep = []
        for it in lst:
            n_lim, hi5 = _ampl(it["code"])
            it["n_lim"] = n_lim
            it["pullback"] = (it["lpx"] / hi5 - 1) if hi5 else None
            if n_lim is not None and n_lim > J_LIMIT_MAX:
                drop.append((it, f"近20日{n_lim}次涨跌停（游资盘）"))
                continue
            keep.append(it)
        return keep

    return (_scrub(A), _scrub(B), C, D, _scrub(E), drop)


def judge(min_amt=0.5, top=30, save=False):
    """盘中可操作分层（A/B/C/D/E）+ 结构演化，一次扫描两产物。

    与 --live 的关系：--live 只给「谁在离开中枢」；--judge 在同一份数据上
    再叠一层可操作性判据，并同时把 pool_live_*.json 落盘。所以盘中一条命令
    就够：`--judge --save`。
    """
    try:
        snap = _snapshot(min_amt, top, quiet=True)
    except RuntimeError as e:
        print(f"[judge] {e}")
        return 2
    if snap is None:
        print("[judge] 无可判标的")
        return 1

    rows, idx, mins = snap["rows"], snap["idx"], snap["mins"]
    A, B, C, D, E, drop = _judge_layers(rows, idx, mins)

    print(f"[judge] 结构基准 {snap['struct_asof']} · 实时 {snap['live_at']} · "
          f"已交易 {mins} 分钟")
    print(f"[judge] 可判 {len(rows)} / 取价 {snap['hit']} · 扫描 {snap['elapsed']:.0f}s · "
          f"上涨 {snap['up']} / 下跌 {snap['down']} / 涨停 {snap['limit_up']}")

    def _head(t, n, note=""):
        print("\n" + "─" * 118)
        print(f"{t}   共 {n} 只" + (f"   {note}" if note else ""))
        print("─" * 118)
        print(f"{'代码':<11}{'名称':<9}{'现价':>8}{'涨幅':>8}{'ZD':>8}{'ZG':>8}"
              f"{'越沿/距守位':>12}{'量比':>6}{'市值':>8}{'笔':>3}{'信号':>5}{'龄':>4}   备注")

    def _row(it, dguard=True):
        g = it.get("guard") or it["zg"]
        dg = (it["lpx"] / g - 1) if dguard else 0
        note = it.get("bei") or ""
        if it.get("bi_live"):
            note = (note + " 末笔未走完").strip()
        if it.get("n_lim"):
            note = (note + f" 含{it['n_lim']}次涨跌停").strip()
        if it.get("pullback") is not None and it["pullback"] < -0.05:
            note = (note + f" 距5日高{it['pullback']:.0%}").strip()
        print(f"{it['code']:<11}{it['name'][:6]:<7}{it['lpx']:>8.2f}{it['lpct']:>7.2f}%"
              f"{it['zd']:>8.2f}{it['zg']:>8.2f}{dg:>11.1%}"
              f"{(it['volr'] or 0):>6.2f}{it['mv_yi']:>8.1f}{it['piv_n_bi']:>3}"
              f"{it.get('sig_type') or '-':>5}{(it.get('sig_age') or 0):>4}   {note}")

    _head("A · 今日突破末中枢上沿（等回踩不破，不追）", len(A),
          f"越沿 {J_BRK_MIN:.1%}~{J_BRK_MAX:.1%} · 量比>{J_VOL_MIN}")
    for it in sorted(A, key=lambda x: -(x["volr"] or 0)):
        _row(it)

    _head("B · 新鲜三买（守位在手边 ≤8%，末中枢上沿之上）", len(B), "可介入区，止损=守位")
    for it in sorted(B, key=lambda x: x["dguard"]):
        _row(it)

    _head("E · 贴着上沿未过（临近爆发位，埋伏不是买）", len(E),
          f"距上沿 0~{J_TOUCH:.1%} · 量比>{J_VOL_MIN}")
    for it in sorted(E, key=lambda x: -(x["volr"] or 0)):
        _row(it)

    _head("C · 守位未破但已回末中枢上沿之下（持有跟踪，不加仓）", len(C), "再破守位即出")
    for it in sorted(C, key=lambda x: x["dguard"])[:15]:
        _row(it)

    _head("D · 新鲜背驰（12 日内，衰竭侧，只作减仓预警）", len(D))
    for it in sorted(D, key=lambda x: -(x.get("score") or 0))[:12]:
        _row(it, dguard=False)

    print("\n" + "─" * 118)
    print(f"被否 {len(drop)} 只。理由分布：")
    cnt = {}
    for _, why in drop:
        k = why.split("（")[0].split(" 距")[0]
        cnt[k] = cnt.get(k, 0) + 1
    for k, n in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  {k}")

    if save:
        os.makedirs(REPORTS, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        p = os.path.join(REPORTS, f"pool_judge_{stamp}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"live_at": snap["live_at"], "struct_asof": snap["struct_asof"],
                       "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "mins": mins, "judged": len(rows), "quoted": snap["hit"],
                       "up": snap["up"], "down": snap["down"],
                       "limit_up": snap["limit_up"],
                       "counts": {"A": len(A), "B": len(B), "C": len(C),
                                  "D": len(D), "E": len(E), "drop": len(drop)},
                       "print_top": top,
                       "A": A, "B": B, "C": C, "D": D, "E": E,
                       "drop": [{"code": i["code"], "name": i["name"], "why": w}
                                for i, w in drop]}, f, ensure_ascii=False, indent=1)
        print(f"\n[saved] {p}")
        # 同一次扫描顺手把结构演化也落盘，省掉再跑一遍 --live
        _save_live(_build_evo(rows, idx), snap, top)
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="全市场扫描")
    ap.add_argument("--live", action="store_true",
                    help="全市场实时叠加：日线结构 + 当下价，看谁在离开中枢")
    ap.add_argument("--judge", action="store_true",
                    help="盘中可操作分层（A突破/B三买/C持有/D衰竭/E贴沿）+ 结构演化，"
                         "一次扫描出两个产物")
    ap.add_argument("--intraday", action="store_true", help="盘中巡检：读池 + 实时价，比中枢沿")
    ap.add_argument("--min-amt", type=float, default=0.5, help="20 日均成交额下限（亿元）")
    ap.add_argument("--stage", default=None, help="只看某态：" + "/".join(STAGES))
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--keep-bj", action="store_true", help="保留北交所（默认剔除）")
    args = ap.parse_args()

    if args.intraday:
        return intraday()

    if args.live:
        return live(args.min_amt, args.top, args.save)

    if args.judge:
        return judge(args.min_amt, args.top, args.save)

    if not args.scan:
        ap.print_help()
        return 0

    out, buckets, meta = scan(args.min_amt, args.stage, args.top, keep_bj=args.keep_bj)
    if args.save:
        os.makedirs(REPORTS, exist_ok=True)
        asof = meta.get("data_asof") or datetime.now().strftime("%Y%m%d")
        p = os.path.join(REPORTS, f"pool_state_{asof}.json")
        # 存全量 buckets，不存裁剪后的 out。原来存 out（每档前 top 个），
        # 导致「中枢」1,005 只只落 50 条，页面点「看全部」时明细只有 50，
        # 与卡片上的 1,005 对不上。out 只用于终端打印。
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "data_asof": asof,
                       "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "print_top": args.top,
                       "buckets": buckets}, f, ensure_ascii=False, indent=1)
        print(f"\n[saved] {p}  (结构基准 {asof} / 全量 "
              f"{sum(len(v) for v in buckets.values())} 条 / 终端只印前 {args.top})")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
