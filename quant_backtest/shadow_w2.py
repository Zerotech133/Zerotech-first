# -*- coding: utf-8 -*-
"""T3 · 影子第 7 腿 W2：DIF 三态仓位（研发计划 2026-11-11 R2）。

《交易思路 v3.7》第 29 页仓位规则，叠加在 v3 主腿持仓上（只记账不交易）：
    m = 1.0   DIF > 0（全仓）
    m = 0.5   DIF < 0 且 MACD柱 > 0 且 MA55 斜率 > −15°（半仓·疑似筑底）
    m = 0.0   其余（空仓·强制离场记账）

实现 = v3 逐日持仓权重 × m（收盘重定价、免成本、影子口径）：
    W2 日收益[t] = Σ_i w_i,t−1 × m_i,t−1 × r_i,t   （w 来自 v3 holdings[t−1].wgt_pct）

口径注记（影子期评审时要看的假设）：
- DIF/DEA/柱 用后复权价（pct_chg 累乘）算 EMA12/26/9——除权跳空不污染均线
- MA55 斜率 = (MA55[t]/MA55[t−5])^(1/5) − 1（几何日均）；角度换算约定
  **45° ≙ 日均 ±1%**：deg = degrees(atan(slope_daily / 0.01))，故
  「斜率 > −15°」 ⇔ 日均斜率 > −0.2678%（约月度 −5.3% 内不算陡跌）。
  PPT 未给图表标定，此约定写进 assumptions，中期评审可换标定重跑。
- m 用 t 日收盘数据决策、作用于 t→t+1 收益——PIT 安全（决策在收盘后）
- m≡1 跑同一公式 = v3 近似日收益，与 v3 真实逐日 pnl 对比作为叠加层
  保真度检验（T+1_open 成交导致近似有残差，报 max|diff| 与相关系数）

用法：python shadow_w2.py（写 data/shadow/w2_YYYYMMDD.json，供 pipeline_health 第 9 项检查）
"""
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data", "ashare")
SHADOW_DIR = os.path.join(BASE, "data", "shadow")

HALF_DEG = -15.0
SLOPE_UNIT = 0.01  # 45° ≙ 日均 1%


def _regime_series(code, days_needed):
    """个股 DIF 三态乘数序列：{day: m}。数据不足返回 {}。"""
    fp = os.path.join(DATA, code.replace(".", "_") + ".parquet")
    if not os.path.exists(fp):
        return {}
    df = pd.read_parquet(fp, columns=["trade_date", "pct_chg"])
    if "pct_chg" not in df.columns:
        return {}
    df = df.sort_values("trade_date").reset_index(drop=True)
    adj = (1.0 + df["pct_chg"].fillna(0.0) / 100.0).cumprod()
    ema12 = adj.ewm(span=12, adjust=False).mean()
    ema26 = adj.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    ma55 = adj.rolling(55).mean()
    slope = (ma55 / ma55.shift(5)) ** (1.0 / 5.0) - 1.0
    deg = np.degrees(np.arctan(slope / SLOPE_UNIT))
    out = {}
    d2 = dict(zip(df["trade_date"].astype(str), range(len(df))))
    for day in days_needed:
        i = d2.get(str(day))
        if i is None or i < 55 or np.isnan(ma55.iloc[i]):
            continue
        if dif.iloc[i] > 0:
            m = 1.0
        elif hist.iloc[i] > 0 and deg.iloc[i] > HALF_DEG:
            m = 0.5
        else:
            m = 0.0
        out[str(day)] = m
    return out


def daily_w2(today=None, d0=None, d1=None):
    """W2 叠加层当日台账：跑 v3（缓存命中，秒级）→ 后处理。soft 调用方包 try。"""
    import backtest_engine_v2 as eng
    from shadow_ab import ensure_sig_cache

    today = today or time.strftime("%Y%m%d")
    d1 = d1 or today
    d0 = d0 or (str(int(today[:4]) - 1) + today[4:])  # 滚动一年，与 shadow_ab 一致

    # 日期滚动自愈（与六腿同源）：d1 滚动后缓存缺失自动重扫，失败照常抛给 soft 调用方
    # 坑（2026-11-11 凌晨首跑实锤）：数据未同步时（日线 16:47 更新）引擎会把
    # d1 收敛到日历最后交易日（run_job 2133-2141 行），缓存名随之变化；
    # 自愈若按原始 d1 建缓存，名字对不上引擎仍报缺失。W2 侧先收敛再自愈。
    idx_fp = os.path.join(DATA, "000001_SZ.parquet")
    if os.path.exists(idx_fp):
        last = pd.read_parquet(idx_fp, columns=["trade_date"])["trade_date"].astype(str).max()
        if d1 > last:
            print("[w2] d1 %s > 数据最后日 %s，收敛" % (d1, last), flush=True)
            d1 = last
    ensure_sig_cache(d0, d1)

    p = {"preset": "v3", "d0": d0, "d1": d1, "cost": 0.0061}
    ap = eng.apply_preset(p)
    for k, v in p.items():
        if k not in ("d0", "d1") and ap.get(k) != v:
            raise SystemExit("W2 自检失败：%s 传 %r 实际 %r" % (k, v, ap.get(k)))
    payload, err = eng.run_job(p, "shadow_w2_v3_%s" % today,
                               progress=lambda *a, **k: None)
    if err or not payload:
        raise RuntimeError("v3 底仓跑失败：%r" % (err,))

    days = payload["days"]                      # {day: {pnl_pct, ...}}
    holdings = payload["holdings"]              # {day: [{stock, wgt_pct, ...}]}
    win = sorted(days)
    need_codes = sorted({h["stock"] for hs in holdings.values() for h in hs})
    mult = {}
    for code in need_codes:
        mult[code] = _regime_series(code, win)
    loaded = {c: m for c, m in mult.items() if m}
    if len(loaded) < len(need_codes):
        print("[w2][warn] %d/%d 只票无行情（乘数缺失按 1.0 记）"
              % (len(loaded), len(need_codes)), flush=True)

    # 个股日收益矩阵（只用涉及的票）
    ret = {}
    for code in need_codes:
        fp = os.path.join(DATA, code.replace(".", "_") + ".parquet")
        if not os.path.exists(fp):
            continue
        df = pd.read_parquet(fp, columns=["trade_date", "pct_chg"])
        if "pct_chg" not in df.columns:
            continue
        s = df["pct_chg"].fillna(0.0) / 100.0
        ret[code] = dict(zip(df["trade_date"].astype(str), s))

    # 逐日叠加：W2_ret[t] = Σ w_i,t−1 × m_i,t−1 × r_i,t；v3 近似同式 m≡1
    w2_rets, v3_rets, act_rets = [], [], []
    expo = {"full": 0, "half": 0, "flat": 0, "partial": 0}
    for ti in range(1, len(win)):
        day, prev = win[ti], win[ti - 1]
        hs = holdings.get(prev) or []
        s_w2 = s_v3 = 0.0
        raw = []
        for h in hs:
            code = h["stock"]
            w = (h.get("wgt_pct") or 0.0) / 100.0
            m = mult.get(code, {}).get(prev, 1.0)
            r = ret.get(code, {}).get(day)
            if r is None:
                # 停牌顺延：收益 0，权重保留（与引擎停牌顺延口径一致）
                r = 0.0
            s_v3 += w * r
            s_w2 += w * m * r
            raw.append((code, w, m, r))
        w2_rets.append(s_w2)
        v3_rets.append(s_v3)
        act_rets.append((days[day]["pnl_pct"]) / 100.0)
        # 敞口统计（按前一日决策）
        ms = [m for _, _, m, _ in raw] or [0.0]
        if all(m == 1.0 for m in ms):
            expo["full"] += 1
        elif all(m == 0.0 for m in ms):
            expo["flat"] += 1
        elif any(m == 0.5 for m in ms):
            expo["half"] += 1
        else:
            expo["partial"] += 1

    def _curve(rets):
        eq, peak, mdd = 1.0, 1.0, 0.0
        for r in rets:
            eq *= (1.0 + r)
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak)
        return {"total_pct": round((eq - 1) * 100, 2), "max_dd_pct": round(mdd * 100, 2)}

    # 保真度检验：v3 近似 vs v3 真实
    va = np.array(v3_rets) * 100.0
    vb = np.array(act_rets) * 100.0
    corr = float(np.corrcoef(va, vb)[0, 1]) if len(va) > 5 and np.std(va) > 0 else None
    max_diff = float(np.max(np.abs(va - vb))) if len(va) else None

    out = {
        "date": today, "d0": d0, "d1": d1, "leg": "W2_dif_regime",
        "base_leg": "v3", "n_days": len(w2_rets), "exposure_days": expo,
        "assumptions": {
            "half_rule": "DIF<0 且 MACD柱>0 且 MA55斜率>-15°",
            "slope_conv": "deg=atan(日均斜率/0.01)，45°≙1%/日；>-15° ⇔ 日均>-0.2678%",
            "rebalance": "t 收盘决策 → t→t+1 收益，免成本影子口径",
            "price_basis": "后复权（pct_chg 累乘）算 EMA/MA55",
        },
        "v3_actual": _curve(act_rets),
        "v3_overlay_approx": _curve(v3_rets),
        "w2": _curve(w2_rets),
        "fidelity": {"corr": corr, "max_daily_diff_pct": round(max_diff, 3) if max_diff else None},
        "n_codes": len(need_codes),
    }
    os.makedirs(SHADOW_DIR, exist_ok=True)
    fp = os.path.join(SHADOW_DIR, "w2_%s.json" % today)
    json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("W2 台账：%s" % fp)
    print("  v3 真实     累计 %+.2f%% 回撤 %.2f%%" % (
        out["v3_actual"]["total_pct"], out["v3_actual"]["max_dd_pct"]))
    print("  v3 叠加近似 累计 %+.2f%% 回撤 %.2f%%（corr=%.3f max日差=%.3f%%）" % (
        out["v3_overlay_approx"]["total_pct"], out["v3_overlay_approx"]["max_dd_pct"],
        corr if corr is not None else -1, max_diff or -1))
    print("  W2 三态    累计 %+.2f%% 回撤 %.2f%%" % (
        out["w2"]["total_pct"], out["w2"]["max_dd_pct"]))
    print("  敞口天数   全仓 %d / 半仓 %d / 空仓 %d / 混合 %d / 总 %d" % (
        expo["full"], expo["half"], expo["flat"], expo["partial"], len(w2_rets)))
    return out


if __name__ == "__main__":
    daily_w2()
