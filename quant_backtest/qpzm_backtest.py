# -*- coding: utf-8 -*-
"""青萍之末策略回测锚（qpzm_backtest）—— S4 里程碑，只读历史数据。

目的：对已上产线、参数未经历史验证的 rps_resonance.py 打分体系建立回测锚。
本文件为独立新建文件，不 import、不修改 rps_resonance.py / portfolio.py /
sector_effect.py；打分公式按产线逐条复刻（见 DESIGN 注释与单元测试）。

回测口径（与产线对齐）:
  · RPS: 逐交易日截面。每股用自身 bar 序列算 N 日涨幅（N=50/120/250），
    全市场 rank(pct=True)*100，复合分 = Σ(w_p*rps_p)/Σ(可用周期权重)，
    现行权重 {50:0.4, 120:0.3, 250:0.3}（rps_resonance.RPS_W）。
    上市不足 250 日的次新股：不跳过（与产线一致），缺周期降权参与，
    并按可用周期数标记，报告提供按周期数的信号拆分。
  · 共振打分: tech/JEV/RPS/env 四维缺维降权（W4 tech.35/jev.25/rps.25/
    env.15）。JEV 依赖 news.db 近 7 天新闻，历史不可重放 → 回测口径即
    产线 JEV 缺维时的三维降权（tech/rps/env → 0.4667/0.3333/0.2），
    与 reports/rps_latest.json 的 dims="tech+rps+env" 完全一致。
    tech = 4策略多头数*25 ± 10(close vs MA20)，clip[0,100]；
    ≥买入阈值 → 买入信号，≥观察阈值 → 观察；env safe=False → 一票否决。
  · 环境闸门: breadth = 站上MA20占比（≥21 根 bar 才计入），med20 =
    20日涨幅中位数，upfrac = 20日上涨占比；score = 60*breadth + 40*upfrac；
    breadth<0.40 或 med20<-3% → safe=False（rps_resonance.market_env）。
  · 信号后验: T 日收盘产生信号 → T+1 开盘价买入，持有 N 个交易日
    （T+1 起第 N 根 bar 收盘卖出）；N∈{5,10,20}。成本 = 往返一次性，
    默认 0.30%（单边 15bp×2，从 config.trade.by_market.CN 推导）。
    K254：全价后复权（pre_close 链）；T+1 一字板（开=高=收
    且开盘涨幅达板）视为无法成交剔除；出场停牌按最后可得价结算并计数。
  · 参数网格: RPS 权重 3 组 × 买入/观察阈值 2 组 × 闸门开关 2 态。

产物: reports/qpzm_backtest_report.json（原子写）+
      reports/qpzm_backtest_summary.md（人类可读摘要）。

用法（产线解释器唯一合法路径，junction 路径避免中文路径问题）:
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/qpzm_backtest.py \
      --start 20251001 --end 20260930          # 近 1 年小样本
  ... --start 20240401                          # 数据允许的全量信号窗
  ... --stocks-limit 80                         # 冒烟：只取部分股票
"""

# ── 口径注记 v2（2026-11-07，QPZM_RD_PLAN.md v2，纯注释、不含任何逻辑）──
# 1) M1 三重修复已入本文件，历史数字一律以 v2 基线锁为准：
#    · 掩码：unfill.shift(-1).fillna(False) 在 pandas 3.x 退化为 object dtype，
#      `~` 按位取反恒真 → 一字板剔除在修复前从未生效；三处掩码链尾已加
#      .astype(bool)。贡献审计窗口 2.2~2.8bp（占三重修复 85%）。
#    · --limit-mode exact：走 data/tdx_api/ts_stk_limit/ 交易所精确涨跌停价，
#      缺失日自动回退 approx 并在 meta.limit_exact_meta.fallback_dates 留痕。
#    · 数据池含 M1 补入的 83 只退市股（只增不改），缓解幸存者偏差；
#      2020-2023 退市 127 只、2020 前 111 只仍未补齐，旧五窗口数字视为上界。
# 2) 本引擎是「逐笔独立模型」，其净值是每日信号平均收益的算术累乘，
#    12m 曾出现虚拟 −4.72% 而真组合 +14.21%（符号相反）。任何对外收益/
#    回撤结论必须以 portfolio_sim.py 的真组合净值为准，禁止引用本文件净值。
# 3) 红线：成本 30bp 往返、主持有期 hold10、审计窗口固定 20251015~20261014、
#    阈值 75/50 不动；JEV 历史恒缺维，实际三维归一 0.4667/0.3333/0.2，
#    对外称「多维加权（技术/RPS/环境）」，不称「四维共振」。
# 4) PIT：禁用 qfq 系追溯指标；一律 hfq 或 bfq+adj_factor。既有池 2025 年中
#    以前为前复权，同股收益链安全但跨股价格不可比（路径 B 另立项）。
# 5) 基线锁：本文件被 t7_lock/baseline_lock.json 按 md5 锚定，
#    **任何改动（包括只加注释）都必须重冻引擎指纹并跑 verify_baseline.py**，
#    且只动引擎项、绝不碰 data 指纹。

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime

import numpy as np
import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")

# ── 产线参数快照（rps_resonance.py / config.json，只读复刻） ──
PERIODS = (50, 120, 250)
RPS_W_CUR = {50: 0.4, 120: 0.3, 250: 0.3}  # 现行
RPS_W_EQ = {50: 1 / 3, 120: 1 / 3, 250: 1 / 3}  # 等权
RPS_W_ALT = {50: 0.5, 120: 0.3, 250: 0.2}  # 替代：短周期加权
W4 = {"tech": 0.35, "jev": 0.25, "rps": 0.25, "env": 0.15}
# 077k w015 态：JEV 以 50 中性分按 0.15 进权重的权重重组
W4_W015 = {"tech": 0.40, "jev": 0.15, "rps": 0.30, "env": 0.15}
JEV_REPLAY_PATH = os.path.join(HERE, "data", "jev_archive", "jev_replay_series.json")
JEV_MODES = ("missing", "neutral50", "w015", "replay")
TH_CUR = (75, 50)  # 现行 买入/观察
TH_ALT = (70, 50)
DEFAULT_COST_FALLBACK = 0.0030  # CN 实盘往返（单边 15bp × 2），读 config 失败时回退


def default_cost_roundtrip(market="CN"):
    """从 config.json 的 trade.by_market 推导往返成本比例。

    cost_bps / slippage_bps 为**单边**费率；回测的 cost 是逐笔往返扣一次
    （trade_returns 里 ret = exit/entry - 1 - cost），故往返 = 2×(cost+slippage)/10000。
    读不到 config 或市场缺失时回退 0.0030（CN 实盘口径），不静默用 0.0015。
    """
    try:
        import backtest as _bt  # 复用既有 config 读取逻辑，不重复造 JSON 解析

        cfg = _bt.load_trade_config(market)
        cost_bps = float(cfg["cost_bps"])
        slippage_bps = float(cfg["slippage_bps"])
        val = 2.0 * (cost_bps + slippage_bps) / 10000.0
        # load_trade_config 对未知市场静默回退 CN，这里探测并显式告警
        probe = _bt.load_trade_config("__t8_unknown_market__")
        if market != "CN" and (cost_bps, slippage_bps) == (
            float(probe["cost_bps"]),
            float(probe["slippage_bps"]),
        ):
            print(
                "[cost] 警告：market=%s 不在 config.trade.by_market 中，"
                "按 CN 保守口径推导为 %.6f（单边 %gbp×2）"
                % (market, val, cost_bps + slippage_bps)
            )
        return val
    except Exception as exc:
        print(
            "[cost] 警告：读取 config.trade.by_market[%s] 失败（%s: %s），"
            "回退默认往返成本 %.4f" % (market, type(exc).__name__, exc, DEFAULT_COST_FALLBACK)
        )
        return DEFAULT_COST_FALLBACK


MIN_BARS_ELIG = 55  # resonance_watchlist 最低 bar 数（缺则不参与打分）
MIN_BARS_ENV = 21  # market_env 计入宽度要求
STRAT_PARAMS = {  # config.json strategies（产线现行参数）
    "ma_cross": {"fast": 8, "slow": 20},
    "rsi_reversal": {"period": 6, "oversold": 30, "overbought": 65},
    "boll_breakout": {"period": 20, "k": 1.5},
    "trend_follow": {"n": 20, "slope_lookback": 3},
}

DESIGN = {
    "replay": "逐交易日全 A 截面；每股用自身 bar 序列算涨幅（与产线 "
    "_load_market 只读自身 close 数组一致），停牌日不占窗口",
    "rps": "N 日涨幅截面 rank(pct=True)*100（pandas average 法，同产线）；"
    "复合分按可用周期权重归一；涨幅不可得(NaN)不参与排名",
    "sub_new": "上市不足 250 日不跳过（与产线一致），缺周期降权参与，按可用"
    "周期数(1/2/3)标记并给出信号拆分",
    "jev": "JEV 维历史不可重放（news.db 仅近 7 天），按产线缺维降权规则取 "
    "tech/rps/env 三维（归一权重 0.4667/0.3333/0.2），与 "
    "rps_latest.json 的 dims='tech+rps+env' 一致；"
    "077k 起可经 --jev-mode 注入四态：missing(默认，同上)/"
    "neutral50(50 中性分进权重)/w015(权重 tech.40/rps.30/jev.15/"
    "env.15，jev 按 50 中性进权重)/replay(消费 data/jev_archive/"
    "jev_replay_series.json 逐日截面，归档仅覆盖其日期范围——"
    "回测区间早于该范围时相关日期无 jev 数据自然缺维，预期行为)",
    "entry_exit": "T 日收盘信号 → T+1 开盘买入 → 持有 N 个交易日后收盘卖出；"
    "K254：出场日停牌按最后可得价结算（ffill）并计数"
    "(n_dropped 口径变更为 nobar+一字板)；T+1 一字板（开=高=收"
    "且开盘涨幅达板）视为无法成交剔除",
    "cost": "往返一次性成本（佣金双边+印花税+滑点），默认 0.0030"
    "（单边 12+3bp 由 config.trade.by_market.CN 推导 ×2；--cost 显式覆盖）",
    "price": "K254 起后复权口径：逐股 own-bar 因子链 "
    "f(t)=f(t-1)*close(t-1)/pre_close(t)，close/open 全乘因子；"
    "（K253 实证旧「未复权」口径近一年 2883 起除权、1131 起单次"
    "假收益>2pp、最大 -36.77%，远超旧文档所称「轻微失真」）",
    "excess": "超额 = 信号逐笔收益均值 − 基准（同样的 T+1 开盘进/持有 N 日"
    "收盘出、全体可交易股票等权）限制在同一批信号日内的逐笔均值",
    "gate_veto": "闸门开 = env safe=False 时买入/观察信号全部否决；"
    "闸门关 = 不否决（env 分仍计入打分），用于隔离否决贡献",
    "universe": "load_wide 按 os.listdir 全量读 data/ashare/，其中含 4 只指数"
    "（000001.SH/000688.SH/399001.SZ/399006.SZ，仓库既有约定：指数住 ashare，"
    "作大盘基准）；审计 T13 实测其占买入单元 0.128%，剔除后 n_buy −133、"
    "均值 Δ≤0.0015pp（噪声以下）——故不修，仅此注记（R3，2026-11-05 审计）",
}


# ── 纯计算层（可单测） ───────────────────────────────────────
def cross_rps(chg_by_period, weights):
    """截面 RPS 复合分（复刻 batch_rps）。

    chg_by_period: {p: DataFrame(dates × stocks) N日涨幅}
    weights: {p: w}
    返回 (mix DataFrame, n_avail DataFrame(float))。
    """
    num = None
    den = None
    n_avail = None
    for p, chg in chg_by_period.items():
        r = (chg.rank(axis=1, pct=True) * 100).round(1)  # 同产线 round(1)
        wp = float(weights.get(p, 0.0))
        rw = (r * wp).fillna(0.0)  # 产线 sum(axis=1) 跳 NaN 的等价写法
        num = rw if num is None else num + rw
        m = r.notna().astype(float)
        den = m * wp if den is None else den + m * wp
        n_avail = m if n_avail is None else n_avail + m
    mix = (num / den.replace(0.0, np.nan)).round(1)
    return mix, n_avail


def env_gate(above, ret20):
    """环境闸门（复刻 market_env，逐日向量化）。

    above/ret20: DataFrame(dates × stocks)，bar 数不足 MIN_BARS_ENV 处为 NaN。
    返回 DataFrame: breadth, med20, upfrac, score(float), safe(bool)。

    【2026-10-15 产线切换 K-STOCK-401】safe 判定委托 gatekeeper v3 policy
    （policies/v3_breadth40_med20.py，sha 冻结 c42cd48fab11ab57），逐日
    GateDecision 落 data/gate_decisions.jsonl（feeder=qpzm_wide_embedded，
    按 market+date 去重——回测重跑不产生重复行）。等价锁：黄金基线
    reports/_gatekeeper_golden_baseline.json 242 日逐值对照 + 等价性测试
    tools/gatekeeper/tests/test_equivalence_golden.py 全绿。
    口径保持：①safe 用 round 前的 raw breadth/med（与旧实现一致）；
    ②breadth 全 NaN 日（无票可评）不调 policy、不落账，safe=True 旧语义。
    QPZM_GATEKEEPER=0 回退旧内联判定（零账本写入）。policy sha 校验失败
    fail-closed：raise（判据被改必须人工介入，宁炸勿静默漂移）。
    """
    a = above.notna()
    breadth = above.where(a).mean(axis=1)
    upfrac = (ret20 > 0).where(a).mean(axis=1)
    med = ret20.where(a).median(axis=1)
    score = (60 * breadth + 40 * upfrac).clip(0, 100)
    if os.environ.get("QPZM_GATEKEEPER", "1") != "0":
        safe = _env_gate_via_gatekeeper(breadth, med, upfrac)
    else:
        safe = ~((breadth < 0.40) | (med < -0.03))
    return pd.DataFrame(
        {
            "breadth": breadth.round(3),
            "med20": med.round(4),
            "upfrac": upfrac.round(3),
            "score": score,
            "safe": safe,
        }
    )


def _env_gate_via_gatekeeper(breadth, med, upfrac, ledger_fp=None):
    """逐日经 gatekeeper v3 policy 判定并落判定账本（K-STOCK-401）。

    返回 safe Series（bool，索引同输入）；policy 判定与旧内联式
    not(breadth<0.40 or med<-0.03) 逐值等价（黄金基线锁定）。
    账本语义＝运行时**最新锚日**判定留痕（历史日判定随 env_df 进报告，
    不入账本——回测 wide 全历史 8132 日，全量落账会刷 8100+ 行历史噪声，
    2026-10-15 首跑实证后改为只落最后有效日；跨运行按 (market,date) 去重）。
    ledger_fp：账本路径注入（仅测试用，None=默认 data/gate_decisions.jsonl）。
    """
    import sys as _sys

    _gk = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "tools", "gatekeeper"
    )
    if _gk not in _sys.path:
        _sys.path.insert(0, _gk)
    from policies.v3_breadth40_med20 import V3Policy  # noqa: PLC0415
    from ledger import Ledger  # noqa: PLC0415

    policy = V3Policy()
    if not policy.intact:
        raise RuntimeError(
            "gatekeeper v3 policy sha 校验失败(%s)：判据疑似被改，"
            "env_gate fail-closed 拒绝判定" % policy.sha
        )
    ledger = Ledger(fp=ledger_fp)
    seen = {str(r.get("date")) for r in ledger.query("CN")}
    # 最新有效锚日（唯一落账日）
    valid = [
        d for d in breadth.index if not (pd.isna(breadth.get(d)) or pd.isna(med.get(d)))
    ]
    anchor = str(valid[-1]) if valid else None
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = {}
    for d in breadth.index:
        b, m = breadth.get(d), med.get(d)
        if pd.isna(b) or pd.isna(m):
            out[d] = True  # 无票可评：旧语义放行，不落账
            continue
        s, cond = policy.evaluate({"breadth": float(b), "med20": float(m)})
        out[d] = bool(s)
        if anchor is None or str(d) != anchor or str(d) in seen:
            continue  # 非锚日或已落账：只判定不追加
        try:
            ledger.append(
                {
                    "market": "CN",
                    "date": str(d),
                    "safe": bool(s),
                    "conditions": cond,
                    "features": {
                        "breadth": float(b),
                        "med20": float(m),
                        "upfrac": (
                            None if pd.isna(upfrac.get(d)) else float(upfrac.get(d))
                        ),
                    },
                    "policy_ver": policy.ver,
                    "policy_sha": policy.sha,
                    "feeder": "qpzm_wide_embedded",
                    "ts": ts,
                }
            )
            seen.add(str(d))
        except Exception as e:  # noqa: BLE001
            print("[env_gate] 判定账本写入失败 %s: %s" % (d, e), flush=True)
    return pd.Series(out, index=breadth.index, dtype=bool)


def tech_score(longs, above20):
    """技术维（复刻 resonance_watchlist: tech = longs*25 ± 10, clip[0,100]）。

    longs: 4 策略多头数 wide；above20: close>MA20 wide（NaN=数据不足→按否）。
    """
    bonus = np.where(above20.fillna(False).to_numpy(dtype=bool), 10.0, -10.0)
    return (longs * 25 + bonus).clip(0, 100)


def normalize_jev_key(code):
    """归档键归一化为 wide 同款全码形态，无法识别返回 None。

    W5c 键形态修复：sh688025 → 688025.SH，sz301047 → 301047.SZ，
    bj430047 → 430047.BJ；已是全码形态（688025.SH）幂等返回；
    其余（gb_nvda / BTC/USDT 等非 A 股键）→ None（跳过并计数）。
    """
    s = str(code).strip()
    m = re.match(r"^(sh|sz|bj)(\d{6})$", s, re.IGNORECASE)
    if m:
        return "%s.%s" % (m.group(2), m.group(1).upper())
    m = re.match(r"^(\d{6})\.(sh|sz|bj)$", s, re.IGNORECASE)
    if m:
        return "%s.%s" % (m.group(1), m.group(2).upper())
    return None


def load_jev_replay(path=JEV_REPLAY_PATH):
    """读 jev_replay_series.json → (score DataFrame dates×codes, meta)。

    日期键 'YYYY-MM-DD' 转成 int64 YYYYMMDD 与 wide 索引对齐；归档键经
    normalize_jev_key 归一化为 wide 同款全码形态（688025.SH），reindex
    才能命中；无法识别的键跳过并在 meta 计数。只有归档覆盖内的
    (date, code) 有分，其余为 NaN（自然缺维，预期行为）。
    """
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = {}
    n_skipped = 0
    skipped_keys = set()
    for d, by_code in payload.get("series", {}).items():
        d_int = int(d.replace("-", ""))
        for code, rec in by_code.items():
            wide_code = normalize_jev_key(code)
            if wide_code is None:
                n_skipped += 1
                skipped_keys.add(str(code))
                continue
            rows.setdefault(d_int, {})[wide_code] = float(rec.get("score", 50.0))
    mat = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    meta = payload.get("meta", {})
    if n_skipped:
        meta["unrecognized_keys_skipped"] = n_skipped
        meta["unrecognized_keys"] = sorted(skipped_keys)
    # 审查 P2-1（077l）：末日为重放时刻前部分数据，剔除防半日偏置
    if meta.get("partial_last_day") and len(mat) > 1:
        mat = mat.iloc[:-1]
        meta["partial_last_day_dropped"] = str(mat.index[-1])
    return mat, meta


def combine_score(
    tech,
    rps_mix,
    env_df,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=True,
    eligible=None,
    jev_mode="missing",
    jev_mat=None,
):
    """共振打分 + 信号（复刻 resonance_watchlist 缺维降权）。

    jev_mode 四态（077k）:
      missing   JEV 缺维（默认，历史基准，与产线 JEV 缺维完全一致）
      neutral50 JEV 按 50 中性分进权重（等价 env_score 广播形式）
      w015      权重重组 tech.40/rps.30/jev.15/env.15，JEV 按 50 中性进权重
      replay    消费 jev_mat（逐日截面 replay 分）；能匹配的票该分作 jev 维，
                匹配不到的票保持缺维降权（replay 归档仅覆盖其日期范围）

    返回 (score DataFrame, buy bool, obs bool)。gate_on 且 env safe=False →
    买入/观察全部否决（与产线 action='回避（环境闸门）' 一致）。
    """
    if jev_mode not in JEV_MODES:
        raise ValueError("unknown jev_mode: %s" % jev_mode)
    if jev_mode == "replay" and jev_mat is None:
        raise ValueError("jev_mode=replay requires jev_mat")
    t = tech.to_numpy(dtype=float)
    rp = rps_mix.to_numpy(dtype=float)
    el = (
        tech.notna().to_numpy(dtype=bool)
        if eligible is None
        else (eligible & tech.notna()).to_numpy(dtype=bool)
    )
    hr = el & ~np.isnan(rp)
    env_score = env_df["score"].to_numpy(dtype=float)  # (D,)
    if jev_mode == "missing":
        wsum = (W4["tech"] + W4["env"]) * el + W4["rps"] * hr
        num = (
            t * W4["tech"]
            + env_score[:, None] * W4["env"]
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"])
        )
    elif jev_mode == "neutral50":
        wsum = (W4["tech"] + W4["env"] + W4["jev"]) * el + W4["rps"] * hr
        num = (
            t * W4["tech"]
            + env_score[:, None] * W4["env"]
            + 50.0 * W4["jev"] * el
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"])
        )
    elif jev_mode == "w015":
        wt = W4_W015["tech"]
        wr = W4_W015["rps"]
        wj = W4_W015["jev"]
        we = W4_W015["env"]
        wsum = (wt + we + wj) * el + wr * hr
        num = (
            t * wt
            + env_score[:, None] * we
            + 50.0 * wj * el
            + np.where(np.isnan(rp), 0.0, rp * wr)
        )
    else:  # replay
        jm = jev_mat.reindex(index=tech.index, columns=tech.columns).to_numpy(
            dtype=float
        )
        jv = el & ~np.isnan(jm)
        wsum = (W4["tech"] + W4["env"]) * el + W4["rps"] * hr + W4["jev"] * jv
        num = (
            t * W4["tech"]
            + env_score[:, None] * W4["env"]
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"])
            + np.where(np.isnan(jm), 0.0, jm * W4["jev"])
        )
    sc = np.divide(num, wsum, out=np.full(num.shape, np.nan), where=wsum > 0)
    gate = (
        env_df["safe"].to_numpy(dtype=bool)
        if gate_on
        else np.ones(len(env_df), dtype=bool)
    )
    gate_ok = np.broadcast_to(gate[:, None], sc.shape)
    ge = np.nan_to_num(sc, nan=-1e9)
    buy_m = el & hr & gate_ok & (ge >= buy_th)
    obs_m = el & hr & gate_ok & (ge >= obs_th) & (ge < buy_th)
    score = pd.DataFrame(sc, index=tech.index, columns=tech.columns)
    buy = pd.DataFrame(buy_m, index=tech.index, columns=tech.columns)
    obs = pd.DataFrame(obs_m, index=tech.index, columns=tech.columns)
    return score, buy, obs


def _flat_series(mask, values, index, columns):
    """bool 矩阵取值摊平为 MultiIndex(date, code) Series（跳过 False）。"""
    vals = np.where(mask.to_numpy(dtype=bool), values, np.nan).ravel()
    mi = pd.MultiIndex.from_product([index, columns], names=["date", "code"])
    return pd.Series(vals, index=mi).dropna()


def trade_returns(buy, open_px, close_px, n, cost, unfill=None):
    """T+1 开盘买入、持有 n 日收盘卖出的逐信号净收益（含成本）。

    K254：出场日停牌按最后可得价结算（close 先 ffill 再 shift）；
    T+1 一字板（unfill 矩阵）视为无法成交剔除。
    返回 (ret Series(date, code), n_dropped int)（n_dropped=nobar+一字板）。
    """
    entry = open_px.shift(-1).to_numpy(dtype=float)
    exit_px = close_px.ffill().shift(-n).to_numpy(dtype=float)
    ret_vals = exit_px / entry - 1.0 - cost
    ok = open_px.shift(-1).notna() & close_px.ffill().shift(-n).notna()
    if unfill is not None:
        # pandas 3.x：bool DF shift() 退化为 object，~ 会变成 int 按位取反
        # （~True=-2 恒真值）导致掩码失效——必须 astype(bool) 恢复 K254 语义
        unf = (
            unfill.shift(-1)
            .fillna(False)
            .reindex_like(open_px)
            .fillna(False)
            .astype(bool)
        )
        ok = ok & ~unf
    valid = buy & ok
    n_dropped = int((buy & ~ok).sum().sum())
    return _flat_series(valid, ret_vals, buy.index, buy.columns), n_dropped


def date_mean(ret_s):
    """按信号日聚合的均值收益序列。"""
    if len(ret_s) == 0:
        return pd.Series(dtype=float)
    return ret_s.groupby(level="date").mean()


def max_drawdown(daily_ret):
    """日频净值最大回撤（算术累乘净值）。"""
    if len(daily_ret) == 0:
        return 0.0
    eq = (1.0 + daily_ret.to_numpy(dtype=float)).cumprod()
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    return float(dd)


def bench_returns(eligible, open_px, close_px, n, cost, unfill=None):
    """基准逐笔收益：全体可交易股票同窗口（T+1 开盘进 / 持有 n 日收盘出）。

    K254：停牌 ffill 出场；entry 一字板剔除（与信号侧对称）。
    """
    entry = open_px.shift(-1)
    exit_px = close_px.ffill().shift(-n)
    valid = eligible & entry.notna() & exit_px.notna()
    if unfill is not None:
        # 同 trade_returns：astype(bool) 防 pandas 3.x object-shift 掩码失效
        unf = (
            unfill.shift(-1)
            .fillna(False)
            .reindex_like(open_px)
            .fillna(False)
            .astype(bool)
        )
        valid = valid & ~unf
    ret_vals = (exit_px / entry - 1.0 - cost).to_numpy(dtype=float)
    return _flat_series(valid, ret_vals, eligible.index, eligible.columns)


def eval_hold(
    buy, open_px, close_px, n, cost, bench_ret=None, eligible=None, unfill=None
):
    """一个 (信号, 持有期) 组合的后验指标。

    bench_ret 未给则现算（同一 hold 下与变体无关，可预计算传入复用）。
    超额 = 信号逐笔均值 − 基准限制在同一批信号日内的逐笔均值。
    """
    ret, n_drop = trade_returns(buy, open_px, close_px, n, cost, unfill=unfill)
    dm = date_mean(ret)
    if bench_ret is None:
        bench_ret = bench_returns(eligible, open_px, close_px, n, cost, unfill=unfill)
    if len(dm) and len(bench_ret):
        sig_dates = set(dm.index)
        b = bench_ret[bench_ret.index.get_level_values("date").isin(sig_dates)]
        bench_mean = float(b.mean()) if len(b) else None
    else:
        bench_mean = None
    stats = {
        "n_trades": int(len(ret)),
        "n_signal_dates": int(len(dm)),
        "n_dropped_nobar": n_drop,
        "mean": round(float(ret.mean()), 6) if len(ret) else None,
        "median": round(float(ret.median()), 6) if len(ret) else None,
        "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None,
        "std": round(float(ret.std()), 6) if len(ret) > 1 else None,
        "p10": round(float(ret.quantile(0.10)), 6) if len(ret) else None,
        "p90": round(float(ret.quantile(0.90)), 6) if len(ret) else None,
        "max_dd": round(max_drawdown(dm), 4),
        "bench_mean": round(bench_mean, 6) if bench_mean is not None else None,
        "excess_mean": round(float(ret.mean()) - bench_mean, 6)
        if len(ret) and bench_mean is not None
        else None,
    }
    return stats


# ── 数据层 ──────────────────────────────────────────────────
def stock_positions(close):
    """4 策略持仓数（复刻 strategies.REGISTRY + config.json 参数；
    指标口径同 indicators.py：rolling min_periods=1、RSI fillna(50)、
    boll 突破持有至跌破中轨、RSI 事件帧）。返回 int Series(0-4)。"""
    c = close
    p = STRAT_PARAMS
    fa = c.rolling(int(p["ma_cross"]["fast"]), min_periods=1).mean()
    sl = c.rolling(int(p["ma_cross"]["slow"]), min_periods=1).mean()
    pos_ma = (fa > sl).astype(int)
    n = int(p["rsi_reversal"]["period"])
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(n, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    pos_rsi = pd.Series(np.nan, index=c.index)
    pos_rsi[rsi < float(p["rsi_reversal"]["oversold"])] = 1
    pos_rsi[rsi > float(p["rsi_reversal"]["overbought"])] = 0
    pos_rsi = pos_rsi.ffill().fillna(0).astype(int)
    k = float(p["boll_breakout"]["k"])
    nb = int(p["boll_breakout"]["period"])
    mid = c.rolling(nb, min_periods=1).mean()
    std = c.rolling(nb, min_periods=1).std(ddof=0)
    upper = mid + k * std
    pos_boll = (c > upper).astype(float).replace(0.0, np.nan)
    pos_boll = pos_boll.ffill().fillna(0).mask(c < mid, 0.0).astype(int)
    m = c.rolling(int(p["trend_follow"]["n"]), min_periods=1).mean()
    slope = m.diff(int(p["trend_follow"]["slope_lookback"]))
    pos_tf = ((c > m) & (slope > 0)).astype(int)
    return pos_ma + pos_rsi + pos_boll + pos_tf


def load_wide(data_dir, stocks_limit=0, log_every=1000, keep_raw=False):
    """读全部 parquet → union 交易日索引的 wide 矩阵集合。

    逐股在自身 bar 序列上算指标（与产线 own-bar 语义一致），再 scatter。
    返回 dict: dates(np int64), codes, close/open/chg{p}/ret20/longs/
    n_bars(float32 DataFrame), above20(计闸门槛 ≥MIN_BARS_ENV),
    eligible(≥MIN_BARS_ELIG bool), unfill(K254 一字板 bool)；
    K254 起 close/open 为后复权口径（own-bar pre_close 链因子）。
    keep_raw=True 时额外保留原始价 _raw_open/_raw_high/_raw_close
    （--limit-mode exact 精确一字板判定用；默认 False，行为不变）。
    """
    files = sorted(
        f
        for f in os.listdir(data_dir)
        if f.endswith(".parquet") and not f.startswith("_")
    )
    if stocks_limit:
        files = files[: int(stocks_limit)]
    codes, per = [], []
    t0 = time.time()
    for i, f in enumerate(files):
        try:
            df = pd.read_parquet(
                os.path.join(data_dir, f),
                columns=["trade_date", "open", "high", "low", "close", "pre_close"],
            )
        except Exception as ex:  # A5: 坏样本静默消失会让回测样本集悄悄缩水——留痕
            print("[qpzm] 样本读取失败跳过 %s：%s" % (f, str(ex)[:60]))
            continue
        if len(df) < 2:
            continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        codes.append(f[:-8].replace("_", "."))
        per.append(df)
        if (i + 1) % log_every == 0:
            print(
                "  loaded %d/%d (%.0fs)" % (i + 1, len(files), time.time() - t0),
                flush=True,
            )
    if not per:
        raise RuntimeError("no parquet data in %s" % data_dir)
    union = np.unique(np.concatenate([d["trade_date"].to_numpy() for d in per])).astype(
        "int64"
    )
    dpos = {int(d): i for i, d in enumerate(union)}
    S, D = len(per), len(union)
    print(
        "universe %d stocks, union %d dates (%s ~ %s)" % (S, D, union[0], union[-1]),
        flush=True,
    )

    def blank():
        return np.full((D, S), np.nan, dtype="float32")

    arr = {
        k: blank()
        for k in (
            "close",
            "open",
            "chg50",
            "chg120",
            "chg250",
            "ret20",
            "longs",
            "n_bars",
        )
    }
    above_buf = np.zeros((D, S), dtype=bool)
    unfill_buf = np.zeros((D, S), dtype=bool)  # K254 一字板(T+1 无法成交)
    raw_buf = {k: blank() for k in ("open", "high", "low", "close")} if keep_raw else None
    for j, df in enumerate(per):
        rows = np.array([dpos[int(d)] for d in df["trade_date"].to_numpy()])
        c = df["close"].astype(float)
        o = df["open"].astype(float)
        # K254 后复权因子链（own-bar）：f(t)=f(t-1)*close(t-1)/pre_close(t)
        pc = pd.to_numeric(df["pre_close"], errors="coerce")
        ratio = (c.shift(1) / pc).astype(float)
        ratio = ratio.where(ratio.between(0.05, 20.0), 1.0).fillna(1.0)
        fct = ratio.cumprod()
        c = c * fct
        o = o * fct
        arr["close"][rows, j] = c.to_numpy()
        arr["open"][rows, j] = o.to_numpy()
        arr["n_bars"][rows, j] = np.arange(1, len(df) + 1)
        for p in PERIODS:
            arr["chg%d" % p][rows, j] = (c / c.shift(p) - 1).to_numpy()
        arr["ret20"][rows, j] = (c / c.shift(20) - 1).to_numpy()
        ma20 = c.rolling(20, min_periods=1).mean()
        above_buf[rows, j] = (c > ma20).to_numpy()
        arr["longs"][rows, j] = stock_positions(c).to_numpy()
        # K254 一字板（原始价判定）：开=高=收 且 开盘涨幅达板
        o_raw = df["open"].astype(float)
        h_raw = df["high"].astype(float)
        rg = o_raw / pc.replace(0.0, np.nan) - 1.0
        lim = (
            0.295
            if codes[j].endswith(".BJ")
            # 2026-11-10 审计修复：原 codes[j][2:4] 取第 3-4 位（301313.SZ→"13"），
            # 创业板/科创板 20cm 阈值永不命中、主板 003016 类反被误判 20cm。
            # 板块前缀在代码头两位：30/68 → 0.195。
            else (0.195 if codes[j][:2] in ("30", "68") else 0.095)
        )
        unfill_buf[rows, j] = (
            (o_raw >= h_raw - 1e-9)
            & ((o_raw - df["close"].astype(float)).abs() <= 1e-6)
            & (rg >= lim - 0.005)
            & rg.notna()
        ).to_numpy()
        if keep_raw:
            raw_buf["open"][rows, j] = o_raw.to_numpy()
            raw_buf["high"][rows, j] = h_raw.to_numpy()
            raw_buf["low"][rows, j] = df["low"].astype(float).to_numpy()
            raw_buf["close"][rows, j] = df["close"].astype(float).to_numpy()

    wide = {"dates": union, "codes": codes}
    for k, a in arr.items():
        wide[k] = pd.DataFrame(a, index=union, columns=codes)
    # 闸门只计入 ≥21 根自身 bar（market_env: len(c)>=21）
    wide["above20"] = pd.DataFrame(
        np.where(arr["n_bars"] >= MIN_BARS_ENV, above_buf.astype(float), np.nan),
        index=union,
        columns=codes,
    )
    wide["eligible"] = pd.DataFrame(
        arr["n_bars"] >= MIN_BARS_ELIG, index=union, columns=codes
    )
    wide["unfill"] = pd.DataFrame(unfill_buf, index=union, columns=codes)
    if keep_raw:
        for k in ("open", "high", "low", "close"):
            wide["_raw_%s" % k] = pd.DataFrame(raw_buf[k], index=union, columns=codes)
    return wide


def apply_exact_unfill(wide, start=None, end=None):
    """--limit-mode exact：用交易所精确涨跌停价覆盖一字板判定（p0_limit 20261106）。

    精确判定：当日 open==high==close 且 open>=up_limit（一字封死涨停，
    T+1 无法买入）。缓存缺失的日期保留 approx 值（自动回退）。
    就地替换 wide["unfill"]，返回 meta dict（覆盖/回退统计）。
    """
    import tdx_api_client as tdx

    union, codes = wide["dates"], wide["codes"]
    raw_o, raw_h, raw_c = wide["_raw_open"], wide["_raw_high"], wide["_raw_close"]
    cache = tdx.load_cache(union[0], union[-1])
    unf = wide["unfill"].copy()
    meta = {
        "mode": "exact",
        "cache_dir": tdx.CACHE_DIR,
        "cache_dates": 0,
        "exact_dates": 0,
        "fallback_dates": 0,
        "fallback_dates_in_window": 0,
        "exact_flips_vs_approx": None,
    }
    if cache is None or not len(cache):
        meta["fallback_dates"] = int(len(union))
        if start:
            meta["fallback_dates_in_window"] = int(((union >= int(start)) & (union <= int(end or union[-1]))).sum())
        print("[exact-unfill] cache empty, all %d dates fallback approx" % meta["fallback_dates"], flush=True)
        wide["unfill"] = unf
        return meta

    cache_by_date = {int(d): g.set_index("ts_code")["up_limit"] for d, g in cache.groupby("trade_date")}
    approx_vals = unf.to_numpy().copy()
    flips = 0
    n_exact = 0
    win_mask = np.ones(len(union), dtype=bool)
    if start:
        win_mask &= union >= int(start)
    if end:
        win_mask &= union <= int(end)
    for t, d in enumerate(union):
        lim = cache_by_date.get(int(d))
        if lim is None:
            meta["fallback_dates"] += 1
            if win_mask[t]:
                meta["fallback_dates_in_window"] += 1
            continue
        meta["cache_dates"] += 1
        # 只覆盖窗口内日期：窗外历史 K254 语义保留 approx（买入侧 shift(-1) 只关心窗内 T+1）
        if not win_mask[t]:
            continue
        meta["exact_dates"] += 1
        lim_v = lim.reindex(codes).to_numpy(dtype=float)
        o = raw_o.iloc[t].to_numpy(dtype=float)
        h = raw_h.iloc[t].to_numpy(dtype=float)
        c = raw_c.iloc[t].to_numpy(dtype=float)
        exact = (
            (np.abs(o - h) <= 1e-6)
            & (np.abs(o - c) <= 1e-6)
            & (o >= lim_v - 1e-6)
            & np.isfinite(lim_v)
            & np.isfinite(o)
        )
        has_lim = np.isfinite(lim_v)
        new_vals = np.where(has_lim, exact, unf.iloc[t].to_numpy(dtype=bool))
        flips += int((new_vals.astype(bool) != unf.iloc[t].to_numpy(dtype=bool)).sum())
        n_exact += int(has_lim.sum())
        unf.iloc[t] = new_vals
    meta["exact_flips_vs_approx"] = int(flips)
    meta["exact_overridden_cells"] = int(n_exact)
    print(
        "[exact-unfill] cache_dates %d, exact %d, fallback %d (in-window %d), flips %d"
        % (
            meta["cache_dates"],
            meta["exact_dates"],
            meta["fallback_dates"],
            meta["fallback_dates_in_window"],
            flips,
        ),
        flush=True,
    )
    wide["unfill"] = unf
    return meta


# ── 网格与报告 ──────────────────────────────────────────────
GRID_W = [("w_cur", RPS_W_CUR), ("w_eq", RPS_W_EQ), ("w_alt", RPS_W_ALT)]
GRID_TH = [("th7550", TH_CUR), ("th7050", TH_ALT)]
GRID_GATE = [("gate_on", True), ("gate_off", False)]


def run_backtest(args):
    t0 = time.time()
    wide = load_wide(
        args.data_dir, args.stocks_limit, keep_raw=(getattr(args, "limit_mode", "approx") == "exact")
    )
    union, codes = wide["dates"], wide["codes"]
    limit_exact_meta = {}
    if getattr(args, "limit_mode", "approx") == "exact":
        limit_exact_meta = apply_exact_unfill(wide, args.start, args.end)

    chg = {p: wide["chg%d" % p] for p in PERIODS}
    env_df = env_gate(wide["above20"], wide["ret20"])
    print(
        "env computed, unsafe days %d/%d" % (int((~env_df["safe"]).sum()), len(env_df)),
        flush=True,
    )

    rps_by_w = {}
    for wname, w in GRID_W:
        rps_by_w[wname] = cross_rps(chg, w)
        print("rps mixed [%s]" % wname, flush=True)

    jev_mat = None
    jev_meta = {}
    if args.jev_mode == "replay":
        jev_mat, jev_meta = load_jev_replay()
        print(
            "jev replay loaded: %s ~ %s (%d codes, %d days)"
            % (
                jev_meta.get("start"),
                jev_meta.get("end"),
                jev_mat.shape[1],
                jev_mat.shape[0],
            ),
            flush=True,
        )
    else:
        print("jev_mode=%s" % args.jev_mode, flush=True)

    in_win = np.ones(len(union), dtype=bool)
    if args.start:
        in_win &= union >= int(args.start)
    if args.end:
        in_win &= union <= int(args.end)
    holds = args.holds
    eligible = wide["eligible"]

    # 预计算每档持有期的基准逐笔收益（与变体无关）
    bench_by_hold = {}
    for n in holds:
        bench_by_hold[n] = bench_returns(
            eligible.loc[in_win],
            wide["open"].loc[in_win],
            wide["close"].loc[in_win],
            n,
            args.cost,
            unfill=wide.get("unfill"),
        )

    variants = []
    tech_w = tech_score(wide["longs"], wide["above20"])
    for wname, w in GRID_W:
        mix, _n_avail = rps_by_w[wname]
        for thname, (bth, oth) in GRID_TH:
            for gname, g in GRID_GATE:
                _score, buy, _obs = combine_score(
                    tech_w,
                    mix,
                    env_df,
                    buy_th=bth,
                    obs_th=oth,
                    gate_on=g,
                    eligible=eligible,
                    jev_mode=args.jev_mode,
                    jev_mat=jev_mat,
                )
                buy_w = buy.loc[in_win]
                v = {
                    "label": "%s_%s_%s" % (wname, thname, gname),
                    "rps_w": {str(k): round(vv, 4) for k, vv in w.items()},
                    "buy_th": bth,
                    "obs_th": oth,
                    "gate": g,
                    "n_buy_in_window": int(buy_w.sum().sum()),
                    "holds": {},
                }
                for n in holds:
                    v["holds"][str(n)] = eval_hold(
                        buy_w,
                        wide["open"].loc[in_win],
                        wide["close"].loc[in_win],
                        n,
                        args.cost,
                        bench_ret=bench_by_hold[n],
                        unfill=wide.get("unfill"),
                    )
                variants.append(v)
                h0 = v["holds"][str(holds[0])]
                print(
                    "%-24s buy=%6d hold%d mean=%s win=%s"
                    % (
                        v["label"],
                        v["n_buy_in_window"],
                        holds[0],
                        h0["mean"],
                        h0["win_rate"],
                    ),
                    flush=True,
                )

    # 次新拆分：买入信号按 RPS 可用周期数（现行权重+闸门开，全窗口）
    mix_cur, n_avail_cur = rps_by_w["w_cur"]
    _, buy_cur, _ = combine_score(
        tech_w,
        mix_cur,
        env_df,
        gate_on=True,
        eligible=eligible,
        jev_mode=args.jev_mode,
        jev_mat=jev_mat,
    )
    sig_n = n_avail_cur.where(buy_cur).to_numpy().ravel()
    sig_n = sig_n[~np.isnan(sig_n)]
    subnew = (
        {str(int(k)): int(v) for k, v in zip(*np.unique(sig_n, return_counts=True))}
        if len(sig_n)
        else {}
    )
    # S5 决策数据：按可用周期数分组的后验收益（全窗口，绝对口径组间可比；
    # 不带基准超额——全窗口基准序列内存不可控，组间对比用绝对收益即可）
    subnew_holds = {}
    for k in sorted(subnew, key=int):
        mask = (n_avail_cur == int(k)) & buy_cur
        subnew_holds[k] = {}
        for n in holds:
            ret, _drop = trade_returns(
                mask,
                wide["open"],
                wide["close"],
                n,
                args.cost,
                unfill=wide.get("unfill"),
            )
            subnew_holds[k][str(n)] = {
                "n_trades": int(len(ret)),
                "mean": round(float(ret.mean()), 6) if len(ret) else None,
                "median": round(float(ret.median()), 6) if len(ret) else None,
                "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None,
            }
    # ---- P60 RPS 分层（2026-11-10 老田批第 2 项）----
    # 目的：回答「RPS 该做硬门槛，还是继续当 0.25 分量」。
    #   holds_buy  —— 现行体系（w_cur + 闸门开 + TH_CUR）已买入样本内按 RPS 分档，
    #                 看低分档是否拖累整体；若后验随 RPS 单调，剔低档（门控）即可提升
    #   holds_all  —— 全可交易样本按 RPS 分档，纯截面预测力，不含打分与阈值影响
    # 窗口与 variants 一致（in_win），绝对口径，组间可直接比。
    RPS_EDGES = ((0.0, 50.0, "rps<50"), (50.0, 70.0, "rps50_70"),
                 (70.0, 90.0, "rps70_90"), (90.0, 101.0, "rps>=90"))
    mix_in = mix_cur.loc[in_win]
    buy_in = buy_cur.loc[in_win]
    elig_in = eligible.loc[in_win]
    open_in = wide["open"].loc[in_win]
    close_in = wide["close"].loc[in_win]
    unfill_in = wide["unfill"].loc[in_win] if wide.get("unfill") is not None else None
    rps_split = {
        "note": "RPS 分层后验（50/120/250 加权混合分，0-100）；"
        "holds_buy=已买入样本内，holds_all=全可交易样本；绝对口径",
        "buckets": {},
    }
    for _lo, _hi, _lb in RPS_EDGES:
        _band = (mix_in >= _lo) & (mix_in < _hi)
        _row = {
            "n_buy": int((_band & buy_in).sum().sum()),
            "n_all": int((_band & elig_in).sum().sum()),
            "holds_buy": {},
            "holds_all": {},
        }
        for _n in holds:
            for _key, _mask in (("holds_buy", _band & buy_in), ("holds_all", _band & elig_in)):
                _ret, _drop = trade_returns(
                    _mask, open_in, close_in, _n, args.cost, unfill=unfill_in
                )
                _row[_key][str(_n)] = {
                    "n_trades": int(len(_ret)),
                    "mean": round(float(_ret.mean()), 6) if len(_ret) else None,
                    "median": round(float(_ret.median()), 6) if len(_ret) else None,
                    "win_rate": round(float((_ret > 0).mean()), 4) if len(_ret) else None,
                }
        rps_split["buckets"][_lb] = _row
    rps_split["n_nan_rps"] = int(mix_in.isna().sum().sum())

    # ---- P61 加法 vs 门控 A/B（2026-11-10 老田批第 3 项）----
    # 加法（现行）：score = 各维加权平均 >= 75 → 强项可补偿弱项
    # 门控（共振）  ：tech>=T1 且 rps>=T2 且 env_safe 且 RPS 有值 → 买入，score 不参与判定
    # 依据：原型「共振定乾坤」= 各维必须同时成立，不是加权求和。
    #       P60 分层已证：RPS 全样本单调递减（反转），买入样本单调递增——单看 RPS 会错，
    #       必须与 tech 联合，故网格取 tech×rps 双阈值。
    GATE_AB_GRID = [(t1, t2) for t1 in (50, 60, 70, 80) for t2 in (0, 50, 70, 90)]
    tech_in = tech_w.loc[in_win]
    _safe_row = env_df.loc[in_win, "safe"].to_numpy(dtype=bool)
    safe_mat = pd.DataFrame(
        np.broadcast_to(_safe_row[:, None], tech_in.shape),
        index=tech_in.index,
        columns=tech_in.columns,
    )
    gate_ab = {
        "note": "门控变体：tech>=T1 且 rps>=T2 且 env_safe 且 RPS 有值；"
        "对照组 = variants 里的 w_cur_th7550_gate_on（加权平均口径）；"
        "绝对口径，窗口与 variants 一致",
        "variants": [],
    }
    for _t1, _t2 in GATE_AB_GRID:
        _cond = (tech_in >= _t1) & (mix_in >= _t2) & elig_in & safe_mat
        _gv = {
            "label": "gate_t%d_r%d" % (_t1, _t2),
            "tech_th": _t1,
            "rps_th": _t2,
            "n_buy": int(_cond.sum().sum()),
            "holds": {},
        }
        for _n in holds:
            _r, _d = trade_returns(
                _cond, open_in, close_in, _n, args.cost, unfill=unfill_in
            )
            _gv["holds"][str(_n)] = {
                "n_trades": int(len(_r)),
                "mean": round(float(_r.mean()), 6) if len(_r) else None,
                "median": round(float(_r.median()), 6) if len(_r) else None,
                "win_rate": round(float((_r > 0).mean()), 4) if len(_r) else None,
            }
        gate_ab["variants"].append(_gv)

    # ---- P63 「硬地板 + 加权排序」：在现行买入上叠加 RPS 地板（2026-11-11）----
    # 与上面的门控变体不是同一件事：门控变体放弃 score 阈值（纯双阈值判定），
    # 本组保留 score>=buy_th 的排序，只额外要求 RPS 不低于地板值。
    # 这才是「RPS 由权重分量改为硬地板」的真实形态 —— P61 网格从未覆盖它。
    # floor=0 必须与 base 完全一致（自带验证）。
    FLOOR_GRID = (0, 50, 70, 90)
    floor_ab = {
        "note": "地板变体：buy = buy_in & (RPS >= floor)；"
        "buy_in 已含 score>=buy_th 与 env_safe，故 floor=0 应与 base 完全一致；"
        "对照组 = variants 里的 w_cur_th7550_gate_on",
        "variants": [],
    }
    for _f in FLOOR_GRID:
        _fm = buy_in if _f <= 0 else (buy_in & (mix_in >= _f))
        _fv = {
            "label": "floor_r%d" % _f,
            "rps_floor": _f,
            "n_buy": int(_fm.sum().sum()),
            "holds": {},
        }
        for _n in holds:
            _fr, _ = trade_returns(
                _fm, open_in, close_in, _n, args.cost, unfill=unfill_in
            )
            _fv["holds"][str(_n)] = {
                "n_trades": int(len(_fr)),
                "mean": round(float(_fr.mean()), 6) if len(_fr) else None,
                "median": round(float(_fr.median()), 6) if len(_fr) else None,
                "win_rate": round(float((_fr > 0).mean()), 4) if len(_fr) else None,
            }
        floor_ab["variants"].append(_fv)

    # ---- P61 时间稳定性：逐月 T+5/T+10（防单期行情主导结论）----
    # 关键：buy 掩码用全窗口 open/close 计算出场，月掩码只作用于「信号日」，
    # 避免月末 5 日内的信号因切片而丢失出场价（否则每月末被系统性剔除）。
    _dates = tech_in.index
    # 索引可能是 int(YYYYMMDD) 也可能是 Timestamp，两种都要能取年月
    def _ym(_d):
        if hasattr(_d, "year"):
            return int(_d.year), int(_d.month)
        _i = int(_d)
        return _i // 10000, (_i // 100) % 100

    _YM = [_ym(_d) for _d in _dates]
    _MONTHS = sorted(set(_YM))
    _CFG = [
        ("base_w_cur_gate_on", buy_in),
        ("gate_t70_r70", (tech_in >= 70) & (mix_in >= 70) & elig_in & safe_mat),
        ("gate_t70_r90", (tech_in >= 70) & (mix_in >= 90) & elig_in & safe_mat),
        ("gate_t50_r90", (tech_in >= 50) & (mix_in >= 90) & elig_in & safe_mat),
        # P63：硬地板形态（保留 score 排序，只加 RPS 下限）
        ("floor_r50", buy_in & (mix_in >= 50)),
        ("floor_r70", buy_in & (mix_in >= 70)),
        ("floor_r90", buy_in & (mix_in >= 90)),
    ]
    gate_ab["monthly"] = {
        "note": "逐月按信号日分组；出场用全窗口价格，月末信号不丢出场价；"
        "同一成本口径（args.cost）",
        "holds": [int(h) for h in holds if int(h) <= 10],
        "rows": [],
    }
    for _lab, _mask in _CFG:
        for _y, _m in _MONTHS:
            _mm = np.array([_y == _a and _m == _b for _a, _b in _YM])[:, None]
            _sel = _mask & pd.DataFrame(
                np.broadcast_to(_mm, _mask.shape), index=_mask.index, columns=_mask.columns
            )
            _row = {
                "cfg": _lab,
                "month": "%04d%02d" % (_y, _m),
                "n_buy": int(_sel.sum().sum()),
            }
            for _n in gate_ab["monthly"]["holds"]:
                _r, _ = trade_returns(
                    _sel, open_in, close_in, _n, args.cost, unfill=unfill_in
                )
                _row["t%d" % _n] = round(float(_r.mean()), 6) if len(_r) else None
            gate_ab["monthly"]["rows"].append(_row)

    all_ret_hold = {}
    for n in holds:
        ret, _drop = trade_returns(
            buy_cur,
            wide["open"],
            wide["close"],
            n,
            args.cost,
            unfill=wide.get("unfill"),
        )
        all_ret_hold[str(n)] = {
            "n_trades": int(len(ret)),
            "mean": round(float(ret.mean()), 6) if len(ret) else None,
            "median": round(float(ret.median()), 6) if len(ret) else None,
            "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None,
        }

    env_rows = []
    for d, r in env_df.loc[in_win].iterrows():
        if pd.isna(r["score"]):
            continue
        env_rows.append(
            {
                "date": str(int(d)),
                "breadth": float(r["breadth"]),
                "med20": float(r["med20"]),
                "upfrac": float(r["upfrac"]),
                "score": round(float(r["score"])),
                "safe": bool(r["safe"]),
            }
        )

    report = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "interpreter": sys.executable,
            "data_dir": args.data_dir,
            "n_stocks": len(codes),
            "n_dates_union": int(len(union)),
            "window": {
                "start": args.start,
                "end": args.end,
                "signal_days": int(in_win.sum()),
            },
            "holds": holds,
            "cost_roundtrip": args.cost,
            "cost_source": getattr(args, "cost_source", "unknown"),
            "jev_mode": args.jev_mode,
            "jev_replay_meta": jev_meta,
            "limit_mode": getattr(args, "limit_mode", "approx"),
            "limit_exact_meta": limit_exact_meta,
            "sample_mode": bool(args.stocks_limit),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "design": DESIGN,
        "params_snapshot": {
            "periods": list(PERIODS),
            "w4": W4,
            "strategies": STRAT_PARAMS,
            "thresholds_current": list(TH_CUR),
            "min_bars_eligible": MIN_BARS_ELIG,
            "min_bars_env": MIN_BARS_ENV,
        },
        "env_daily": env_rows,
        "variants": variants,
        "subnew_buy_split": {
            "note": "买入信号按 RPS 可用周期数(1/2/3)拆分，现行权重+闸门开，"
            "全窗口（不受 --start 限制）；holds_by_period=分组后验"
            "（绝对口径），all=全体对照",
            "counts": subnew,
            "holds_by_period": subnew_holds,
            "all": all_ret_hold,
        },
        "rps_split": rps_split,
        "gate_ab": gate_ab,
        "floor_ab": floor_ab,
        "summary": {},
    }
    report["summary"] = build_summary(report, env_df.loc[in_win])
    if args.killed_details:
        payload = build_killed_payload(
            args, wide, env_df, tech_w, mix_cur, in_win, bench_by_hold, jev_mat
        )
        payload["meta"]["holds"] = holds
        payload["meta"]["anchor_check"] = {
            v["label"]: v["n_buy_in_window"]
            for v in variants
            if v["label"] in ("w_cur_th7550_gate_on", "w_cur_th7550_gate_off")
        }
        atomic_write_json(args.killed_out, payload)
        md_path = os.path.splitext(args.killed_out)[0] + "_summary.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(build_killed_md(payload) + "\n")
        print("killed -> %s" % args.killed_out, flush=True)
    return report


def build_summary(report, env_win):
    """人类可读摘要（同结构写入 md）。"""
    w = report["meta"]["window"]
    unsafe = int((~env_win["safe"]).sum()) if len(env_win) else 0
    h0 = str(report["meta"]["holds"][0])
    lines = [
        "# 青萍之末回测锚（%s）"
        % ("小样本" if report["meta"]["sample_mode"] else "全量"),
        "",
    ]
    lines.append(
        "- 股票数: %d；信号窗: %s ~ %s（%d 个交易日，闸门收紧 %d 天）"
        % (report["meta"]["n_stocks"], w["start"], w["end"], w["signal_days"], unsafe)
    )
    lines.append(
        "- 成本: 往返 %.2f%%（来源 %s）；口径: T+1 开盘买入，持有 N 日收盘卖出；"
        "超额基准 = 同窗口全体可交易股票等权"
        % (
            report["meta"]["cost_roundtrip"] * 100,
            report["meta"].get("cost_source", "unknown"),
        )
    )
    lines.append("")
    lines.append("## 参数网格（hold=%s 日）" % h0)
    lines.append("")
    lines.append("| 变体 | 买入信号 | 均值 | 中位 | 胜率 | 超额 | 净值回撤 |")
    lines.append("|---|---|---|---|---|---|---|")
    for v in report["variants"]:
        s = v["holds"][h0]
        lines.append(
            "| %s | %d | %s | %s | %s | %s | %s |"
            % (
                v["label"],
                v["n_buy_in_window"],
                _pct(s["mean"]),
                _pct(s["median"]),
                _pct(s["win_rate"], True),
                _pct(s["excess_mean"]),
                _pct(s["max_dd"]),
            )
        )
    lines.append("")
    lines.append("## 多持有期（现行权重 75/50 闸门开）")
    lines.append("")
    lines.append("| hold | 笔数 | 均值 | 中位 | 胜率 | 最大回撤 |")
    lines.append("|---|---|---|---|---|---|")
    for v in report["variants"]:
        if v["label"] == "w_cur_th7550_gate_on":
            for n, s in sorted(v["holds"].items(), key=lambda kv: int(kv[0])):
                lines.append(
                    "| %s | %d | %s | %s | %s | %s |"
                    % (
                        n,
                        s["n_trades"],
                        _pct(s["mean"]),
                        _pct(s["median"]),
                        _pct(s["win_rate"], True),
                        _pct(s["max_dd"]),
                    )
                )
    return {"headline_md": "\n".join(lines)}


def _pct(x, is_ratio=False):
    if x is None:
        return "—"
    return ("%+.2f%%" if not is_ratio else "%.1f%%") % (x * 100)


# ── 错杀清单导出（20261014 追加：仅 --killed-details 启用，默认关闭，
#    不触碰上面任何既有计算路径） ─────────────────────────────
KILLED_GROUP_ORDER = (
    "gate_breadth",
    "gate_med20",
    "gate_both",
    "gate_other",
    "unfill",
    "entry_nobar",
    "rps_missing",
    "min_bars",
)
KILLED_GROUP_NOTE = {
    "gate_breadth": "环境闸门否决·仅 breadth<0.40 触发",
    "gate_med20": "环境闸门否决·仅 med20<-3% 触发",
    "gate_both": "环境闸门否决·双条件同时触发",
    "gate_other": "环境闸门否决·条件字段异常（预期 0 条）",
    "unfill": "闸门放行但 T+1 一字板无法成交（K254 剔除）",
    "entry_nobar": "闸门放行但 T+1 无价可成交（停牌/未上市）",
    "rps_missing": "RPS 缺维（renorm 打分过买阈但 hr 不成立，不产生信号）",
    "min_bars": "bar 数<55 不入池（假想口径 tech+env 归一打分，非产线行为）",
}


def _fwd_ret(open_px, close_px, n, cost):
    """T+1 开盘买（假想可成交）、持有 n 日收盘卖的前向净收益矩阵。"""
    entry = open_px.shift(-1)
    return close_px.ffill().shift(-n) / entry - 1.0 - cost


def _killed_group_stats(rows, def_th, ref_n):
    """按剔除原因分组统计（ref_n=错杀判定持有期）。"""
    rk = "r%d" % ref_n
    out = {}
    for g in KILLED_GROUP_ORDER:
        sub = [r for r in rows if r["reason"] == g]
        if not sub:
            out[g] = {"n_killed": 0}
            continue
        vals = [r[rk] for r in sub if r[rk] is not None]
        mis = [v for v in vals if v >= def_th]
        r5 = [r["r5"] for r in sub if r.get("r5") is not None]
        r20 = [r["r20"] for r in sub if r.get("r20") is not None]
        out[g] = {
            "n_killed": len(sub),
            "n_with_fwd": len(vals),
            "mean_%s" % rk: round(float(np.mean(vals)), 6) if vals else None,
            "median_%s" % rk: round(float(np.median(vals)), 6) if vals else None,
            "win_%s" % rk: round(float(np.mean([v > 0 for v in vals])), 4)
            if vals
            else None,
            "n_miskill": len(mis),
            "miskill_rate": round(len(mis) / len(vals), 4) if vals else None,
            "miskill_mean_%s" % rk: round(float(np.mean(mis)), 6) if mis else None,
            "total_%s" % rk: round(float(np.sum(vals)), 4) if vals else None,
            "mean_r5": round(float(np.mean(r5)), 6) if r5 else None,
            "mean_r20": round(float(np.mean(r20)), 6) if r20 else None,
        }
    return out


def gate_counterfactual(
    args, wide, env_df, tech_w, mix_cur, in_win, bench_by_hold, jev_mat
):
    """闸门三条件逐条放宽/收紧/移除的反事实回放（hold 网格口径）。"""
    br, md, uf = env_df["breadth"], env_df["med20"], env_df["upfrac"]
    bth, oth = TH_CUR
    configs = [
        ("base_b40_m-3", 0.40, -0.03, None),
        ("breadth_30", 0.30, -0.03, None),
        ("breadth_35", 0.35, -0.03, None),
        ("breadth_45", 0.45, -0.03, None),
        ("breadth_50", 0.50, -0.03, None),
        ("med20_m5", 0.40, -0.05, None),
        ("med20_m4", 0.40, -0.04, None),
        ("med20_m2", 0.40, -0.02, None),
        ("med20_0", 0.40, 0.00, None),
        ("drop_breadth", None, -0.03, None),
        ("drop_med20", 0.40, None, None),
        ("drop_both", None, None, None),
        ("add_upfrac30", 0.40, -0.03, 0.30),
        ("add_upfrac40", 0.40, -0.03, 0.40),
    ]
    rows = []
    for label, b_cf, m_cf, u_cf in configs:
        if b_cf is None and m_cf is None:
            safe = pd.Series(True, index=env_df.index)
        elif b_cf is None:
            safe = ~(md < m_cf)
        elif m_cf is None:
            safe = ~(br < b_cf)
        else:
            safe = ~((br < b_cf) | (md < m_cf))
        if u_cf is not None:
            safe = safe & ~(uf < u_cf)
        env_cf = env_df.copy()
        env_cf["safe"] = safe
        _sc, buy, _ob = combine_score(
            tech_w,
            mix_cur,
            env_cf,
            buy_th=bth,
            obs_th=oth,
            gate_on=True,
            eligible=wide["eligible"],
            jev_mode=args.jev_mode,
            jev_mat=jev_mat,
        )
        buy_w = buy.loc[in_win]
        v = {
            "label": label,
            "breadth_th": b_cf,
            "med20_th": m_cf,
            "upfrac_veto": u_cf,
            "n_buy": int(buy_w.sum().sum()),
            "unsafe_days": int((~safe.loc[in_win]).sum()),
            "holds": {},
        }
        for n in args.holds:
            v["holds"][str(n)] = eval_hold(
                buy_w,
                wide["open"].loc[in_win],
                wide["close"].loc[in_win],
                n,
                args.cost,
                bench_ret=bench_by_hold[n],
                unfill=wide.get("unfill"),
            )
        rows.append(v)
        h0 = v["holds"][str(args.holds[0])]
        print(
            "  cf %-14s buy=%6d hold%d mean=%s dd=%s"
            % (label, v["n_buy"], args.holds[0], h0["mean"], h0["max_dd"]),
            flush=True,
        )
    return rows


def build_killed_payload(
    args, wide, env_df, tech_w, mix_cur, in_win, bench_by_hold, jev_mat
):
    """错杀清单 + 分组统计 + 闸门反事实（只读，全部在窗口内）。

    错杀定义：被剔除信号的 r10 ≥ max(5%, 2×放行成交样本 r10 均值)
    （ref_n=10；若 --hold 未含 10 则退化为第一个持有期）。
    """
    cost = args.cost
    holds = args.holds
    ref_n = 10 if 10 in holds else holds[0]
    bth, oth = TH_CUR
    open_px, close_px = wide["open"], wide["close"]
    eligible = wide["eligible"]

    score, buy_on, _ = combine_score(
        tech_w,
        mix_cur,
        env_df,
        buy_th=bth,
        obs_th=oth,
        gate_on=True,
        eligible=eligible,
        jev_mode=args.jev_mode,
        jev_mat=jev_mat,
    )
    _s2, buy_off, _ = combine_score(
        tech_w,
        mix_cur,
        env_df,
        buy_th=bth,
        obs_th=oth,
        gate_on=False,
        eligible=eligible,
        jev_mode=args.jev_mode,
        jev_mat=jev_mat,
    )

    # 错杀判定阈值（放行成交样本 r10 均值）
    adm_ret, _drop = trade_returns(
        buy_on.loc[in_win],
        open_px.loc[in_win],
        close_px.loc[in_win],
        ref_n,
        cost,
        unfill=wide.get("unfill"),
    )
    adm_mean = float(adm_ret.mean()) if len(adm_ret) else 0.0
    def_th = max(0.05, 2.0 * adm_mean)

    br, md, _uf = env_df["breadth"], env_df["med20"], env_df["upfrac"]
    veto_w = (buy_off & ~buy_on).loc[in_win]
    on_w = buy_on.loc[in_win]
    unf_t1 = (
        wide["unfill"].shift(-1).fillna(False).loc[in_win].astype(bool)
    )  # astype(bool) 防 pandas 3.x object-shift 掩码失效（同 trade_returns）
    entry_na = open_px.shift(-1).isna().loc[in_win]
    safe_w = env_df["safe"].loc[in_win]
    hr_ok = mix_cur.notna().loc[in_win]

    def _bcast(s_bool):
        """日频布尔 Series 广播到 (dates×stocks)（df&series 会错位到列名）。"""
        if isinstance(s_bool, pd.DataFrame):
            return s_bool
        return pd.DataFrame(
            np.broadcast_to(s_bool.to_numpy(dtype=bool)[:, None], veto_w.shape),
            index=veto_w.index,
            columns=veto_w.columns,
        )

    is_br_b = _bcast(br.loc[in_win] < 0.40)
    is_md_b = _bcast(md.loc[in_win] < -0.03)
    safe_b, hr_b = _bcast(safe_w), _bcast(hr_ok)
    # min_bars 假想口径：tech+env 归一（显式非产线行为）
    env_sc = pd.DataFrame(
        np.broadcast_to(env_df["score"].to_numpy(dtype=float)[:, None], tech_w.shape),
        index=tech_w.index,
        columns=tech_w.columns,
    )
    pseudo = (tech_w * W4["tech"] + env_sc * W4["env"]) / (W4["tech"] + W4["env"])
    masks = {
        "gate_both": veto_w & is_br_b & is_md_b,
        "gate_breadth": veto_w & is_br_b & ~is_md_b,
        "gate_med20": veto_w & ~is_br_b & is_md_b,
        "gate_other": veto_w & ~(is_br_b | is_md_b),
        "unfill": on_w & unf_t1,
        "entry_nobar": on_w & entry_na,
        "rps_missing": (score.loc[in_win] >= bth)
        & eligible.loc[in_win]
        & ~hr_b
        & safe_b,
        "min_bars": (pseudo.loc[in_win] >= bth) & ~eligible.loc[in_win] & safe_b,
    }

    r_win = {n: _fwd_ret(open_px, close_px, n, cost).loc[in_win] for n in holds}
    r_arr = {n: r_win[n].to_numpy(dtype=float) for n in holds}
    ind = {
        k: env_df[k].loc[in_win].to_numpy(dtype=float)
        for k in ("breadth", "med20", "upfrac")
    }
    sc_a = score.loc[in_win].to_numpy(dtype=float)
    win_dates = [str(int(d)) for d in wide["dates"][in_win]]
    codes = wide["codes"]

    def _v(x, nd=6):
        return None if (x is None or np.isnan(x)) else round(float(x), nd)

    rows = []
    for g in KILLED_GROUP_ORDER:
        a = masks[g].to_numpy(dtype=bool)
        for d_i, s_i in np.argwhere(a):
            row = {
                "date": win_dates[d_i],
                "code": codes[s_i],
                "reason": g,
                "breadth": _v(ind["breadth"][d_i], 4),
                "med20": _v(ind["med20"][d_i], 4),
                "upfrac": _v(ind["upfrac"][d_i], 4),
                "score": _v(sc_a[d_i, s_i], 1),
            }
            for n in holds:
                row["r%d" % n] = _v(r_arr[n][d_i, s_i])
            rows.append(row)

    cf = gate_counterfactual(
        args, wide, env_df, tech_w, mix_cur, in_win, bench_by_hold, jev_mat
    )
    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window": {
                "start": args.start,
                "end": args.end,
                "signal_days": int(in_win.sum()),
            },
            "n_stocks": wide["close"].shape[1],
            "cost_roundtrip": cost,
            "cost_source": getattr(args, "cost_source", "unknown"),
            "jev_mode": args.jev_mode,
            "weights": "w_cur",
            "thresholds": list(TH_CUR),
            "group_note": KILLED_GROUP_NOTE,
        },
        "killed_definition": {
            "rule": "r%d >= max(5%%, 2×放行成交样本 r%d 均值)" % (ref_n, ref_n),
            "abs_th": 0.05,
            "rel_multiple": 2.0,
            "admitted_mean_r%d" % ref_n: round(adm_mean, 6),
            "threshold_used": round(def_th, 6),
        },
        "group_stats": _killed_group_stats(rows, def_th, ref_n),
        "counterfactual": cf,
        "rows": rows,
    }
    return payload


def build_killed_md(payload):
    """错杀清单人类可读摘要。"""
    m = payload["meta"]
    d = payload["killed_definition"]
    ref_n = 10 if 10 in m.get("holds", [5, 10, 20]) else 5
    lines = ["# 青萍之末错杀清单摘要", ""]
    lines.append(
        "- 窗口: %s ~ %s（%d 信号日，%d 股）；成本往返 %.2f%%"
        % (
            m["window"]["start"],
            m["window"]["end"] or "数据尾",
            m["window"]["signal_days"],
            m["n_stocks"],
            m["cost_roundtrip"] * 100,
        )
    )
    lines.append(
        "- 错杀定义: %s（本窗阈值 %+.2f%%）" % (d["rule"], d["threshold_used"] * 100)
    )
    lines.append("")
    lines.append("## 按剔除原因分组")
    lines.append("")
    lines.append(
        "| 剔除原因 | 被剔除数 | 有前向收益 | r10均值 | r10胜率 "
        "| 错杀数 | 错杀率 | 总r10代价 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    rk = "mean_r%d" % ref_n
    wk = "win_r%d" % ref_n
    for g, s in payload["group_stats"].items():
        if not s.get("n_killed"):
            continue
        lines.append(
            "| %s | %d | %s | %s | %s | %s | %s | %s |"
            % (
                g,
                s["n_killed"],
                s.get("n_with_fwd"),
                _pct(s.get(rk)),
                _pct(s.get(wk), True),
                s.get("n_miskill"),
                _pct(s.get("miskill_rate"), True),
                _pct(s.get("total_r%d" % ref_n)),
            )
        )
    lines.append("")
    lines.append("## 闸门反事实（hold5/10/20 均值与最大回撤）")
    lines.append("")
    lines.append(
        "| 配置 | breadth阈 | med20阈 | 收紧天数 | 买入信号 "
        "| h5均值 | h10均值 | h20均值 | h10回撤 | h20回撤 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for v in payload["counterfactual"]:
        h = v["holds"]
        lines.append(
            "| %s | %s | %s | %d | %d | %s | %s | %s | %s | %s |"
            % (
                v["label"],
                "—" if v["breadth_th"] is None else "%d%%" % (v["breadth_th"] * 100),
                "—" if v["med20_th"] is None else "%d%%" % (v["med20_th"] * 100),
                v["unsafe_days"],
                v["n_buy"],
                _pct(h.get("5", {}).get("mean")),
                _pct(h.get("10", {}).get("mean")),
                _pct(h.get("20", {}).get("mean")),
                _pct(h.get("10", {}).get("max_dd")),
                _pct(h.get("20", {}).get("max_dd")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def atomic_write_json(path, payload):
    """tempfile + os.replace 原子写（同产线 _atomic_write_json 风格）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="青萍之末策略回测锚")
    ap.add_argument("--start", default="20251001", help="信号窗起 YYYYMMDD")
    ap.add_argument("--end", default=None, help="信号窗止 YYYYMMDD（默认数据尾）")
    ap.add_argument("--hold", default="5,10,20", help="持有交易日，逗号分隔")
    ap.add_argument(
        "--cost",
        type=float,
        default=None,
        help="往返成本比例；默认从 config.trade.by_market.CN 推导为 0.0030"
        "（单边15bp×2）",
    )
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--out", default=os.path.join(REPORTS, "qpzm_backtest_report.json"))
    ap.add_argument(
        "--stocks-limit", type=int, default=0, help=">0 只取前 N 只股票（冒烟用）"
    )
    ap.add_argument(
        "--jev-mode",
        default="missing",
        choices=list(JEV_MODES),
        help="JEV 维注入态：missing=缺维降权（默认，历史基准）；"
        "neutral50=50 中性分进权重；w015=权重重组 "
        "tech.40/rps.30/jev.15/env.15；replay=消费 "
        "jev_replay_series.json 逐日截面（覆盖外日期自然缺维）",
    )
    ap.add_argument(
        "--limit-mode",
        default="approx",
        choices=("approx", "exact"),
        help="一字板判定口径：approx=K254 近似（开=高=收触板，默认，"
        "行为不变）；exact=交易所精确涨跌停价（tdx_api 缓存，缺失日期"
        "自动回退 approx 并记录 meta.limit_exact_meta.fallback_dates）",
    )
    ap.add_argument(
        "--killed-details",
        action="store_true",
        help="追加导出被剔除信号错杀清单（闸门否决/一字板/缺维"
        "分组 + 前向收益 + 闸门反事实网格），默认关闭，"
        "不改既有行为",
    )
    ap.add_argument(
        "--killed-out",
        default=os.path.join(REPORTS, "qpzm_killed_signals.json"),
        help="错杀清单 JSON 输出路径（--killed-details 时生效）",
    )
    args = ap.parse_args(argv)
    if args.cost is None:
        args.cost = default_cost_roundtrip("CN")
        args.cost_source = "config:CN(往返)"
    else:
        args.cost_source = "cli(显式)"
    args.holds = sorted({int(x) for x in str(args.hold).split(",") if x})
    return args


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/qpzm_backtest.py", exc)
    args = parse_args(argv)
    report = run_backtest(args)
    atomic_write_json(args.out, report)
    md_path = os.path.splitext(args.out)[0] + "_summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report["summary"]["headline_md"] + "\n")
    print("report -> %s" % args.out)
    print("summary -> %s" % md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
