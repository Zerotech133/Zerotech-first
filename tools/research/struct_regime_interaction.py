# -*- coding: utf-8 -*-
"""结构类因子 × 趋势状态：条件因子检验，并回答「分年翻转」的机制。

    ⚠️ 结论已于 2026-11-08 定案，**不要再往下挖**。封存文档：
    `C:/项目/deliverables/2026-11-08_择时研究收口_v1.md`

    定案：**机制成立（H_A），但不可交易。**
      - 成立：zt_dens 上行/放量 -0.165(p=0.003, 3/3 同号) / 上行/缩量 +0.174(p=0.050, 2/2)；
              zt_cnt 上行/放量 -0.228(p=0.000, 3/3)
      - 机制：涨停家数是情绪温度计，读数含义取决于量能。放量上行+涨停潮＝增量资金
              一次性涌入、接力盘耗尽（末期特征）→ 负；缩量上行+涨停潮＝存量抱团主线、
              涨停是主线确认 → 正。全样本 rho≈0 是正负相消，不是没信号。
      - 不可交易：转成 `hot` 规则后 trail +0.041pp(p=0.333) 不过闸；hold +0.298pp(p=0.025)
              过闸但增量与「少交易」不可区分(p=0.203)，且 81.5% 日数 × 2.327 = 1.897
              < R0 的 2.029 —— **降回撤不是增收益**。
      - board_max 的翻转是「2024 妖股特例」：任一层的 p 都不显著(0.423/0.219)，缩量层分年 0/2。

    若仍要继续结构类，唯一有资格的方向是**个股级横截面**（同一日哪些票在 hot 环境更该
    避开），需要个股层面的涨停/连板暴露——现有数据不够。日级开关这条路已经走完。

## 原始设计说明（保留，供复现）

为什么重开这一节

`entry_timing_3y` 已否决全部 6 个结构因子（|rho|≤0.111、随机对照 0/10、分年 6/6 翻转）。
但「分年翻转」本身有两种完全不同的解释，处置方式相反：

  H_A（机制成立、只是被状态掩盖）：结构因子是**投机资金参与度**，它的方向取决于
      市场是「增量资金」还是「存量博弈」。全样本正负相消 → rho≈0，分层后就该分化。
  H_B（无机制、纯拟合）：每年有效的方向不同，且**不能由任何事前可观测状态预测**，
      分层也分不出来，翻转是随机游走。

判别方法只有一个：**用事前可观测的分层变量**（t-1 收盘可知，不看未来），
看层内因子符号是否 (a) 显著偏离 0 且 (b) 在各年内部稳定。

  - 若 (a)(b) 都成立 → H_A，可做条件因子，能落地
  - 若 (a) 成立但 (b) 不成立 → 分层只是又一次曲线拟合，仍不可落地
  - 若 (a) 都不成立 → H_B，彻底放弃结构类

## 分层变量（全部 shift(1)，只用 t-1 收盘信息）

  trend : 指数收盘 相对 MA60 的位置（1=上方 上行 / 0=下方 下行）
  vol   : 全市场成交额分位（1=放量 ≥P50 / 0=缩量 <P50）

这两个直接对应「增量资金」假设：上行+放量才有接力盘，下行或缩量时高涨停数
更像存量抱团。若 H_A 为真，`zt_dens` 应在 (trend=1, vol=1) 层 rho>0、
在 (trend=0, vol=0) 层 rho<0。

**注意 amt_pct 不再作为因子**：它现在是分层变量之一，同时当因子会自我循环。
剩下 5 个结构因子：zt_cnt / zt_dens / dt_cnt / board_max / zt_dt。

## 判据（严到不可能靠运气过）

1. 层内 |rho| ≥ 0.15 且 n_days ≥ 80
2. 层内随机对照：层内打乱因子值 1000 次，真实 |rho| 要超过 95% 分位
3. **跨年符号一致**：该层内至少 3 个年份同号（含方向与主检验一致）
4. 层间符号必须**相反**（否则不叫交互，只是同一效应）

第 3 条是 H_A / H_B 的分水岭，也是这一节存在的唯一理由。
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entry_timing_3y.py")


def _load_mod():
    spec = importlib.util.spec_from_file_location("et3y", SPEC)
    m = importlib.util.module_from_spec(spec)
    sys.modules["et3y"] = m
    spec.loader.exec_module(m)
    return m


et = _load_mod()

TDX = et.TDX
POOL = et.POOL
OUT = os.path.join(ROOT, "portal", "struct_regime_evidence.json")
SEED = 20261108
FACTORS = ["zt_cnt", "zt_dens", "dt_cnt", "board_max", "zt_dt"]
MIN_DAYS = 80
MIN_RHO = 0.15
BOOT = 1000


def build_regime(axis, idx):
    """事前趋势/量能状态。返回 {name: pd.Series(0/1, index=axis)}，均已 shift(1)。"""
    s = idx.set_index("trade_date")["close"].astype(float).reindex(axis)
    ma60 = s.rolling(60).mean()
    trend = (s > ma60).astype(float)
    trend[ma60.isna()] = np.nan
    trend = trend.shift(1)                      # t-1 收盘可知

    # 量能：需全市场成交额，走 ts_daily（与 entry_timing 同源）
    amt = defaultdict(float)
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["trade_date", "amount"])
        d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        d = d[d["trade_date"].isin(set(axis))]
        if not len(d):
            continue
        for dt, v in d.groupby("trade_date")["amount"].sum().items():
            amt[dt] += float(v)
    a = pd.Series({d: amt.get(d, np.nan) for d in axis}, dtype=float)
    pct = a.rolling(61).apply(
        lambda w: float((w[:-1] <= w[-1]).mean()) if len(w) > 1 else np.nan, raw=True)
    vol = (pct >= 0.5).astype(float)
    vol[pct.isna()] = np.nan
    vol = vol.shift(1)

    return {"trend": trend, "vol": vol}


def layering(reg):
    """2×2 四层，返回 {label: set(dates)}。缺失状态的日期不进任何层。"""
    t, v = reg["trend"], reg["vol"]
    out = defaultdict(set)
    for d in t.index:
        a, b = t.get(d), v.get(d)
        if not np.isfinite(a) or not np.isfinite(b):
            continue
        out["%s/%s" % ("上行" if a == 1 else "下行", "放量" if b == 1 else "缩量")].add(d)
    return out


def spearman(x, y):
    if len(x) < 8:
        return None
    s = pd.Series(x).corr(pd.Series(y), method="spearman")
    return None if not np.isfinite(s) else float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--pool", default="")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    if a.pool:
        et.POOL = a.pool
    say = (lambda *x, **k: None) if a.json else print

    rows = et.load_pool(since=a.since.replace("-", "")[:8])
    idx = et.load_index()
    axis = et.build_axis(rows, idx)
    say("池 %s：%d 笔 / 日历 %d 日 %s~%s"
        % (os.path.basename(et.POOL), len(rows), len(axis), axis[0], axis[-1]))

    # 日组均（每个开仓日等权，再按日等权）
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r["ret"])
    day_mean = {d: float(np.mean(v)) for d, v in by.items()}
    say("有信号开仓日 %d" % len(day_mean))

    say("\n[1] 结构因子（同日、未前移，PIT 由调用方 shift）")
    f = et.load_market_struct(axis)
    date_ok = sorted(day_mean)
    # 因子统一在 t 日收盘可得 → 用于 t+1 决策，故 shift(1) 后对齐
    fac = {k: v.shift(1).reindex(date_ok) for k, v in f.items() if k in FACTORS}

    reg = build_regime(axis, idx)
    layers = layering(reg)
    say("\n[2] 事前分层（trend=MA60 上下 / vol=成交额分位 P50，均 shift(1)）")
    for k in ("上行/放量", "上行/缩量", "下行/放量", "下行/缩量"):
        ds = [d for d in layers[k] if d in day_mean]
        say("  %-9s %4d 个有信号日" % (k, len(ds)))

    y = np.array([day_mean[d] for d in date_ok], float)
    years = np.array([int(d[:4]) for d in date_ok])

    res = {
        "meta": {"pool": os.path.basename(et.POOL), "n_rows": len(rows),
                 "n_days": len(day_mean), "window": [date_ok[0], date_ok[-1]],
                 "layers": {k: len([d for d in v if d in day_mean]) for k, v in layers.items()},
                 "min_days": MIN_DAYS, "min_rho": MIN_RHO, "boot": BOOT,
                 "note": "结构因子已 shift(1)；amt_pct 改作分层变量，不作因子"},
        "factors": {},
    }

    rng = np.random.default_rng(SEED)
    say("\n[3] 分层内条件效应（rho vs 日组均）")

    for name in FACTORS:
        s = fac[name]
        v = np.array([s.get(d, np.nan) for d in date_ok], float)
        m = np.isfinite(v)
        rec = {"full": None, "layers": {}}
        full_rho = spearman(v[m], y[m])
        rec["full"] = {"n": int(m.sum()), "rho": None if full_rho is None else round(full_rho, 3)}
        say("\n  %s  全样本 n=%d rho=%s"
            % (name, int(m.sum()), "%.3f" % full_rho if full_rho is not None else "-"))

        for lab, ds in layers.items():
            sel = np.array([d in ds for d in date_ok]) & m
            n = int(sel.sum())
            if n < MIN_DAYS:
                rec["layers"][lab] = {"n": n, "skip": "样本 <%d" % MIN_DAYS}
                say("    %-9s n=%4d  样本不足，跳过" % (lab, n))
                continue
            r = spearman(v[sel], y[sel])
            # 层内随机对照：打乱因子值
            draws = []
            vv = v[sel]
            for _ in range(BOOT):
                rr = spearman(rng.permutation(vv), y[sel])
                if rr is not None:
                    draws.append(rr)
            draws = np.array(draws, float)
            p = float((np.abs(draws) >= abs(r)).mean()) if len(draws) else None
            # 跨年符号一致
            yr_sign = {}
            for yr in sorted(set(years[sel].tolist())):
                mm = sel & (years == yr)
                if mm.sum() >= 20:
                    ry = spearman(v[mm], y[mm])
                    if ry is not None:
                        yr_sign[str(yr)] = round(ry, 3)
            same = sum(1 for x in yr_sign.values() if np.sign(x) == np.sign(r))
            rec["layers"][lab] = {
                "n": n, "rho": round(r, 3), "p_null": None if p is None else round(p, 4),
                "by_year": yr_sign, "years_same_sign": same, "years_total": len(yr_sign),
            }
            say("    %-9s n=%4d rho=%+.3f  p_null=%.3f  分年同号 %d/%d %s"
                % (lab, n, r, p if p is not None else np.nan, same, len(yr_sign),
                   {k: "%+.2f" % x for k, x in yr_sign.items()}))
        res["factors"][name] = rec

    # ---------- 判定 ----------
    say("\n[4] 判定（4 条全过才算条件因子成立）")
    verdicts = {}
    for name, rec in res["factors"].items():
        layers_all = {lab: L for lab, L in rec["layers"].items() if not L.get("skip")}
        # 反号检查必须在**所有有效层之间**做，不能只在「通过判据的层」之间做：
        # 否则 p 稍差一点的那层会被剔出，剩下单层永远「同向」→ 把真交互误判成
        # 单层效应（zt_cnt 就踩了这个：放量层 −0.228 p=0.000、缩量层 +0.146
        # p=0.094，旧逻辑只留前者，判成「同向」）。
        strong = [lab for lab, L in layers_all.items()
                  if abs(L["rho"]) >= MIN_RHO and L["p_null"] is not None
                  and L["p_null"] <= 0.05
                  and L["years_total"] >= 2
                  and L["years_same_sign"] >= max(2, L["years_total"] - 0)]
        marginal = [lab for lab, L in layers_all.items()
                    if lab not in strong and abs(L["rho"]) >= MIN_RHO
                    and L["p_null"] is not None and L["p_null"] <= 0.10
                    and L["years_total"] >= 2
                    and L["years_same_sign"] >= max(2, L["years_total"] - 1)]
        sig_layers = {lab: np.sign(L["rho"]) for lab, L in layers_all.items()
                      if abs(L["rho"]) >= MIN_RHO}
        opp = len(set(sig_layers.values())) > 1 if len(sig_layers) >= 2 else False

        if strong and len(strong) >= 2 and opp:
            v = "条件因子成立（可落地）"
        elif strong and marginal and opp:
            v = "边缘成立：一层强、一层弱但反号，需前推验证"
        elif strong:
            v = "层内有效但有效层同向 → 不是交互，等同全样本"
        else:
            v = "否决：分层后无稳定符号"
        verdicts[name] = {"pass_layers": strong, "marginal_layers": marginal,
                          "layers_opposite_sign": opp, "verdict": v}
        say("  %-9s 强层=%s 边缘层=%s 全层反号=%s → %s"
            % (name, strong or "无", marginal or "无", "是" if opp else "否", v))
    res["verdicts"] = verdicts

    approved = [k for k, v in verdicts.items() if v["verdict"].startswith(("条件因子成立", "边缘成立"))]
    res["summary"] = {
        "approved": approved,
        "conclusion": ("H_A 成立：结构因子方向由事前趋势/量能状态决定" if approved else
                       "H_B 成立：分层后无稳定符号，分年翻转不能由事前状态解释 → "
                       "结构类不可落地，不再投入"),
        "next": ("对 %s 做条件回测（层内才启用该过滤）" % approved if approved else
                 "结构类收口；择时只保留 sigdens，并继续养前推样本"),
    }
    say("\n结论：%s" % res["summary"]["conclusion"])
    say("下一步：%s" % res["summary"]["next"])

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
