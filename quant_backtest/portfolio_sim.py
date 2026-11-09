# -*- coding: utf-8 -*-
"""青萍之末 · 组合层裁判（portfolio_sim）。

引擎 qpzm_backtest.py 是**逐笔独立模型**：每个信号各自按「T+1 开盘进、
持有 N 日收盘出」算净收益，累计曲线是「每日信号平均收益」的算术累乘。
该曲线的致命缺陷是**重叠持仓被重复计入**——同一只票在相邻 5 个信号日各
算一次，等于同时满仓买了 5 份；也没有任何资金约束（可以同时持有全市场
900 只）。所以它给出的 −42.6% / −96.4% 只是"信号强度"的代理，不是组合。

本文件补上组合层：
  · 真资金账户（现金 + 持仓），single-name 上限 --max-weight；
  · T 日收盘信号 → T+1 开盘成交（与引擎一致），T+1 一字板（unfill）跳过；
  · 买入后第 N 个交易日收盘卖出（引擎口径：signal T → exit close(T+N)）；
  · 持仓用**单股收益率链**估值（买入金额 × close(t)/执行价），不做跨股
    绝对价比较（2025 年中以前是前复权口径，绝对价不可比）；
  · 成本 cost 往返：买扣 cost/2、卖扣 cost/2。

复用 qpzm_backtest 的 load_wide / env_gate / cross_rps / tech_score /
combine_score，保证打分与信号集与引擎**逐格一致**；本文件只做撮合与账户，
不修改引擎任何一行。

用法（单配置）:
  python portfolio_sim.py --start 20260806 --end 20261105 --hold 5 \
      --top-n 30 --capital 10000000 --cost 0.0030 --out out.json

用法（一次 load_wide 跑整张矩阵）:
  python portfolio_sim.py --batch m2 --out-dir _qpzm_rd_20261106/portfolio_sim
"""

# ── 口径注记 v2（2026-11-07，QPZM_RD_PLAN.md v2，纯注释、不含任何逻辑）──
# 1) 本文件是收益/回撤的唯一裁判：引擎虚拟净值连符号都可能错（12m −4.72%
#    vs 真组合 +14.21%，3m 虚拟回撤 −42.76% vs 真 −19.45%）。
# 2) 三重计数：3m 的 26246 个信号单元只能建仓 8267 笔（重叠 3.17x）——逐笔
#    均值把「相邻信号日重复买同一票」当独立样本，禁止与真组合混用。
# 3) 真回撤变浅 ≠ 更安全：cash 口径资金利用率仅 39%~46%（闸门 60 天关 31 天），
#    满仓折算后 3m/top30 仍约 −35%，报告须并列满仓折算口径。
# 4) 已验改造开关（默认全关、strict 路径逐位不变）：
#    · --weight-scheme equal|inv_vol：inv_vol 是唯一过五窗口判据的改造
#      （5/5 收益不降、10/10 回撤更浅）；
#    · --lot-fill strict|recycle：预算回收再分配，排序不变；
#    · 终裁主线 = inv_vol + recycle 叠加档（36m 117.71% vs equal 73.37%，
#      夏普 1.22 vs 0.80）。增益真因是填单通道（skip_lot 4794→2210、
#      填单率 55.9%→79.2%，36m 归因占 116%），「低波 alpha」已证伪。
#    · 已否决：--rank rps / score_resid、--dynamic-n env，勿重开。
# 5) 待办优先级：NA 桶核查（停牌复牌笔占 24m equal 盈亏 38%）> 五窗口全量
#    复跑+50bp 双成本终验 > 叠加档冲击成本压测（笔数 +41%，3bp 常数需替换）。
# 6) 已知高估方向：跌停无法卖出未建模、冲击成本按常数、集合竞价必成假设。

import argparse
import json
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import qpzm_backtest as qb  # noqa: E402  只读复用，不修改

DATA_DIR = os.path.join(HERE, "data", "ashare")
OUT_DIR = os.path.join(HERE, "_qpzm_rd_20261106", "portfolio_sim")
LOT = 100
TRADING_DAYS = 242
EPS = 1e-9


# ── 数据准备（一次 load_wide，窗口复用）───────────────────────────
def build_matrices(wide, i0, i1, buy_th=75.0, obs_th=50.0, gate_on=True, log=print, want_vq=False):
    """从 wide 切出 [i0, i1] 行区间并算好打分矩阵，返回 numpy 视图字典。

    截面 rank / env 宽度 / tech 全为逐行运算，行切不影响取值，因此一个
    高分辨率切片可服务多个子窗口，无需重复 load_wide。

    【口径一致性】主口径 w_cur + th7550 + gate_on + jev_mode=missing。
    """
    dates = wide["dates"][i0 : i1 + 1]
    cols = wide["codes"]

    def take(k):
        return wide[k].iloc[i0 : i1 + 1]

    d = {
        "dates": dates,
        "codes": cols,
        "open": take("open"),
        "close": take("close"),
        "above20": take("above20"),
        "ret20": take("ret20"),
        "longs": take("longs"),
        "eligible": take("eligible"),
        "unfill": take("unfill"),
    }
    env_df = qb.env_gate(d["above20"], d["ret20"])
    chg = {p: take("chg%d" % p) for p in qb.PERIODS}
    mix, _n_avail = qb.cross_rps(chg, qb.RPS_W_CUR)
    tech = qb.tech_score(d["longs"], d["above20"])
    score, buy, _obs = qb.combine_score(
        tech,
        mix,
        env_df,
        buy_th=buy_th,
        obs_th=obs_th,
        gate_on=gate_on,
        eligible=d["eligible"],
        jev_mode="missing",
    )
    log(
        "  matrices %d dates x %d codes, buy cells %d, unsafe days %d"
        % (len(dates), len(cols), int(buy.to_numpy().sum()), int((~env_df["safe"]).sum()))
    )
    close = d["close"]
    out = {
        "dates": dates,
        "codes": cols,
        "O": d["open"].to_numpy(dtype="float32"),
        "C": close.to_numpy(dtype="float32"),
        # 出场结算用 ffill（K254：出场停牌按最后可得价），链锚在买入日
        "Cff": close.ffill().to_numpy(dtype="float32"),
        "UNF": d["unfill"].to_numpy(dtype=bool),
        "SC": score.to_numpy(dtype="float32"),
        "BUY": buy.to_numpy(dtype=bool),
        "RPS": mix.to_numpy(dtype="float32"),
        "env_safe": env_df["safe"].to_numpy(dtype=bool),
    }
    # ── 风险预算层附加矩阵（L2b，默认不参与任何计算路径）────────────
    # VOL20: 单股日收益 20 日滚动 std（逐日截面，收益链来自 K254 后复权 close，
    #        只做同日比值，跨股绝对价不可比问题不存在）
    cl = close.astype("float64")
    vol20 = (cl / cl.shift(1) - 1.0).rolling(20, min_periods=20).std(ddof=1)
    out["VOL20"] = vol20.to_numpy(dtype="float32")
    # RESID: score 对 vol20 的逐日截面一元 OLS 残差（候选池内）
    #        候选池 = BUY[t]；有效样本 <10 的当日退化为原始 score；
    #        vol 缺失/为 0 的票残差取当日残差中位数
    sc = score.to_numpy(dtype="float64")
    bu = out["BUY"]
    vo = out["VOL20"].astype("float64")
    resid = np.full(sc.shape, np.nan, dtype="float32")
    for t in range(sc.shape[0]):
        idx = np.flatnonzero(bu[t])
        if not idx.size:
            continue
        s = sc[t, idx]
        v = vo[t, idx]
        ok = np.isfinite(s) & np.isfinite(v) & (v > 0)
        if ok.sum() < 10:
            resid[t, idx] = s.astype("float32")  # 样本不足 → 不做中性化
            continue
        x, y = v[ok], s[ok]
        xm, ym = x.mean(), y.mean()
        den = float(((x - xm) ** 2).sum())
        b = float(((x - xm) * (y - ym)).sum()) / den if den > 0 else 0.0
        r = np.full(idx.size, np.nan)
        r[ok] = y - (ym - b * xm + b * x)
        r[~np.isfinite(r)] = float(np.nanmedian(r))
        resid[t, idx] = r.astype("float32")
    out["RESID"] = resid
    # ENV: 逐日 env score 与其 expanding 分位（只用截至当日的历史，无前视）
    es = env_df["score"].to_numpy(dtype="float64")
    envpct = np.full(len(es), 0.5)
    for t in range(len(es)):
        if t >= 20:
            h = es[: t + 1]
            envpct[t] = (float((h < es[t]).sum()) + 0.5 * float((h == es[t]).sum())) / len(h)
    out["ENVS"] = es
    out["ENVPCT"] = envpct
    # VQ: 当日**全市场截面**的 vol20 五分位标签（1=最低波 … 5=最高波，0=缺失）。
    #     仅用于逐笔归因打标（attr 批），默认不计算、不参与任何决策路径。
    if want_vq:
        vq = np.zeros(out["VOL20"].shape, dtype="uint8")
        for t in range(vq.shape[0]):
            v = vo[t]
            ok = np.isfinite(v) & (v > 0)
            n_ok = int(ok.sum())
            if n_ok < 200:  # 截面样本太少（早期）→ 不打标
                continue
            sv = np.sort(v[ok])
            cuts = sv[[int(0.2 * n_ok), int(0.4 * n_ok), int(0.6 * n_ok), int(0.8 * n_ok)]]
            lab = 1 + (v[:, None] > cuts[None, :]).sum(axis=1)
            vq[t] = np.where(ok, lab, 0).astype("uint8")
        out["VQ"] = vq
    log(
        "  risk-budget matrices: vol20 finite %.1f%%, resid days %d, env pct range %.2f~%.2f"
        % (
            100.0 * float(np.isfinite(out["VOL20"]).mean()),
            int(np.isfinite(resid).any(axis=1).sum()),
            float(envpct.min()),
            float(envpct.max()),
        )
    )
    return out


# ── 撮合与账户 ────────────────────────────────────────────────
def _inv_vol_weights(vol):
    """1/vol 归一权重；vol 缺失/为 0 的票回退为平均权重（等价等权档）。"""
    n = vol.size
    ok = np.isfinite(vol) & (vol > 0)
    if not ok.any():
        return np.full(n, 1.0 / max(n, 1))
    inv = 1.0 / vol[ok]
    w = np.full(n, float(inv.mean()))
    w[ok] = inv
    s = float(w.sum())
    return w / s if s > 0 else np.full(n, 1.0 / max(n, 1))


def _plan_shares(budget, px, half, lot_fill="strict", st=None, max_budget=None):
    """把「单票预算」换成「整手股数」。

    strict （默认，与改前逐位一致）：sh = floor(budget / (px*(1+half)*LOT)) * LOT，
             买不起 1 手 → 0（调用方记为 skip_lot，钱留现金）。
    recycle：预算不足 1 手的票把预算**回收**成当日资金池，先把最接近够 1 手的
             票补到刚好 1 手（按其 1 手成本升序，即「谁能成交就先救谁」），
             剩余池再均摊给当日其余可成交票；最多 3 轮直到无新增饿死票。
             选股池 / 排序 / 当日总预算三项完全不变（只做池内搬运，Σbudget 不变），
             唯一变化是「同样一笔钱怎么装进整手」。不使用任何 vol / 价格先验。
    """
    b = np.asarray(budget, dtype="float64").copy()
    px = np.asarray(px, dtype="float64")
    with np.errstate(invalid="ignore", divide="ignore"):
        lot_cost = px * (1.0 + half) * LOT
        ok_px = np.isfinite(px) & (px > 0) & np.isfinite(b)
        if lot_fill != "recycle":
            sh = np.floor(b / lot_cost) * LOT
            return np.nan_to_num(sh, nan=0.0, posinf=0.0, neginf=0.0).astype(np.int64)
        for rnd in range(3):
            good = ok_px & (b > 0)
            aff = good & (b >= lot_cost)
            starved = good & ~aff
            if not starved.any():
                break
            pool = float(b[starved].sum())
            if pool <= 0:
                break
            b[starved] = 0.0
            if st is not None:
                st["recycle_rounds"] += 1
                st["recycle_pool"] += pool
            # ① 先补最接近 1 手的票：1 手成本升序，够则补到刚好 1 手（多出的钱留给 ②）
            cand = np.flatnonzero(starved)
            cand = cand[np.argsort(lot_cost[cand], kind="stable")]
            for j in cand:
                c = float(lot_cost[j])
                if pool + 1e-9 >= c:
                    b[j] = c
                    pool -= c
                    if st is not None:
                        st["recycle_filled"] += 1
            # ② 剩余均摊给当日全部可成交票（价格有效），不给死票留残钱
            alive = ok_px & (b > 0)
            m = int(alive.sum())
            if m and pool > 0:
                b[alive] += pool / m
        if max_budget is not None:
            cap = np.minimum(b, np.asarray(max_budget, dtype="float64"))
            if st is not None:
                st["recycle_capped"] += int((cap < b - 1e-9).sum())
            b = cap
        sh = np.floor(b / lot_cost) * LOT
        return np.nan_to_num(sh, nan=0.0, posinf=0.0, neginf=0.0).astype(np.int64)


def simulate(M, cfg, log=print):
    """在矩阵 M（已切到本次窗口）上跑一次组合模拟，返回 (metrics, curve, trades)。

    每日流程严格按序：
      ⓪ T+1 开盘执行昨日信号产生的买单（unfill / 无开盘价 → 放弃，钱留现金）
      ① 到期的（买入后第 N 个交易日）收盘卖出，扣单边 cost/2
      ② 收盘价给持仓估值，算当日净值
      ③ 当日收盘生成信号，按 rank_key 降序取 top-N（排除已持仓）
      ④ 按可用现金等权分配（受 --max-weight 与可选 slot 预算约束），下单 T+1
    """
    O, Cff, UNF = M["O"], M["Cff"], M["UNF"]
    SC, BUY, RPS = M["SC"], M["BUY"], M["RPS"]
    dates, codes = M["dates"], M["codes"]
    n_days, n_cols = O.shape

    cost = float(cfg["cost"])
    half = cost / 2.0
    capital = float(cfg["capital"])
    hold = int(cfg["hold"])
    top_n = int(cfg["top_n"])
    max_w = float(cfg["max_weight"])
    alloc = cfg.get("alloc", "cash")
    rank = cfg.get("rank", "score")
    weight_scheme = cfg.get("weight_scheme", "equal")
    dynamic_n = cfg.get("dynamic_n", "off")
    lot_fill = cfg.get("lot_fill", "strict")
    trade_diag = bool(cfg.get("trade_diag", False))
    RESID = M.get("RESID")
    VOL20 = M.get("VOL20")
    VQ = M.get("VQ")
    ENVPCT = M.get("ENVPCT")

    cash = capital
    pos = {}  # col -> dict
    by_exit = {}
    pending = []
    curve = []
    trades = []
    st = Counter()

    for t in range(n_days):
        # ⓪ 执行挂单（昨日收盘信号的 T+1 买入）
        for od in pending:
            j = od["j"]
            px = float(O[t, j])
            if not np.isfinite(px) or px <= 0:
                st["skip_nobar"] += 1
                continue
            if UNF[t, j]:
                st["skip_unfill"] += 1
                continue
            sh = od["shares"]
            gross = sh * px
            fee = gross * half
            if gross + fee > cash + 1e-6:  # 成本导致的现金缺口 → 按可用现金缩手
                sh = int(np.floor(cash / (px * (1.0 + half)) / LOT)) * LOT
                if sh <= 0:
                    st["skip_cash"] += 1
                    continue
                gross = sh * px
                fee = gross * half
            cash -= gross + fee
            exit_t = t + hold - 1  # 引擎口径：signal T → exit close(T+hold) = entry_t+hold-1
            pos[j] = {
                "exit_t": exit_t,
                "shares": sh,
                "basis": gross + fee,
                "entry_px": px,
                "entry_t": t,
            }
            if trade_diag:
                # 归因打标：买入日（成交日 t）与信号日（t-1）的 vol20 / 当日截面五分位
                pos[j]["v_e"] = float(VOL20[t, j]) if VOL20 is not None else np.nan
                pos[j]["q_e"] = int(VQ[t, j]) if VQ is not None else 0
                if t > 0:
                    pos[j]["v_s"] = float(VOL20[t - 1, j]) if VOL20 is not None else np.nan
                    pos[j]["q_s"] = int(VQ[t - 1, j]) if VQ is not None else 0
                else:
                    pos[j]["v_s"] = np.nan
                    pos[j]["q_s"] = 0
            by_exit.setdefault(exit_t, []).append(j)
            st["n_buy"] += 1
            st["buy_gross"] += gross
        pending = []

        # ① 到期卖出（收盘价；停牌按最后可得价）
        for j in by_exit.pop(t, []):
            p = pos.pop(j)
            px = float(Cff[t, j])
            if not np.isfinite(px) or px <= 0:
                px = p["entry_px"]
                st["settle_fallback"] += 1
            gross = p["shares"] * px
            fee = gross * half
            cash += gross - fee
            rec = {
                "code": codes[j],
                "entry_date": int(dates[p["entry_t"]]),
                "exit_date": int(dates[t]),
                "entry_px": round(p["entry_px"], 4),
                "exit_px": round(px, 4),
                "ret": round((gross - fee) / p["basis"] - 1.0, 6),
                "shares": p["shares"],
            }
            if trade_diag:
                rec["pnl"] = round(gross - fee - p["basis"], 2)
                rec["basis"] = round(p["basis"], 2)
                rec["vol20_entry"] = round(p["v_e"], 6) if np.isfinite(p["v_e"]) else None
                rec["volq_entry"] = int(p["q_e"])
                rec["vol20_sig"] = round(p["v_s"], 6) if np.isfinite(p["v_s"]) else None
                rec["volq_sig"] = int(p["q_s"])
            trades.append(rec)

        # ② 收盘估值
        mv = 0.0
        for j, p in pos.items():
            px = float(Cff[t, j])
            if not np.isfinite(px) or px <= 0:
                px = p["entry_px"]
            mv += p["shares"] * px
        eq = cash + mv
        curve.append(
            {
                "date": int(dates[t]),
                "equity": round(eq, 2),
                "cash": round(cash, 2),
                "mv": round(mv, 2),
                "n_pos": len(pos),
                "util": round(mv / eq, 6) if eq > 0 else 0.0,
            }
        )

        # ③④ 生成信号 → 分配现金 → 挂 T+1 单
        if t < n_days - 1:
            cand_all = np.flatnonzero(BUY[t])
            if len(cand_all):
                st["n_signal_cells_raw"] += len(cand_all)
            cand = cand_all
            if len(cand) and pos:
                held = np.fromiter(pos.keys(), dtype=np.int64, count=len(pos))
                cand = cand[~np.isin(cand, held)]
            if len(cand):
                st["n_signal_cells"] += len(cand)
                st["n_signal_days"] += 1
                if rank == "score":
                    key = SC[t, cand].astype(float)
                elif rank == "rps":
                    key = RPS[t, cand].astype(float)
                else:  # score_resid：score 对 vol20 的截面残差（build_matrices 预计算）
                    key = RESID[t, cand].astype(float)
                order = np.argsort(-np.nan_to_num(key, nan=-1e9), kind="stable")
                n_t = top_n
                if dynamic_n == "env":
                    p = float(ENVPCT[t])
                    n_t = 30 if p >= 0.70 else (200 if p <= 0.30 else top_n)
                    st["dyn_n_%d" % n_t] += 1
                sel = cand[order[:n_t]]
                n_sel = len(sel)
                st["n_selected"] += n_sel
                per = min(cash / n_sel, max_w * eq)
                if alloc == "slot":
                    # 稳态等权：每日 N 只 × 持有 hold 日 ⇒ 单票目标 1/(N*hold)
                    per = min(per, eq / float(max(n_t, 1) * max(hold, 1)))
                if per > 0:
                    nxt = t + 1
                    px_n = O[nxt, sel].astype(float)
                    if weight_scheme == "inv_vol":
                        # 1/vol20 加权，总预算与等权口径相同（n_sel×per），单票仍受 max_w 约束
                        w = _inv_vol_weights(VOL20[t, sel].astype(float))
                        budget = np.minimum(n_sel * per * w, max_w * eq)
                    else:
                        budget = np.full(n_sel, per)
                    sh = _plan_shares(
                        budget,
                        px_n,
                        half,
                        lot_fill=lot_fill,
                        st=st,
                        max_budget=np.full(n_sel, max_w * eq),
                    )
                    for j, s in zip(sel, sh):
                        s = int(s)
                        if s <= 0:
                            st["skip_lot"] += 1
                            continue
                        pending.append({"j": int(j), "shares": s})
                else:
                    st["skip_nocash"] += n_sel

    # ── 指标 ──
    eq = np.array([c["equity"] for c in curve], dtype=float)
    rets = eq[1:] / eq[:-1] - 1.0
    total = float(eq[-1] / capital - 1.0)
    n_per = max(len(eq) - 1, 1)
    ann = float((1.0 + total) ** (TRADING_DAYS / n_per) - 1.0) if total > -1 else -1.0
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1.0).min())
    sharpe = (
        float(rets.mean() / rets.std(ddof=1) * np.sqrt(TRADING_DAYS))
        if len(rets) > 1 and rets.std(ddof=1) > 0
        else None
    )
    tr = np.array([x["ret"] for x in trades], dtype=float)
    mean_eq = float(eq.mean())
    metrics = {
        "total_return": round(total, 6),
        "annualized": round(ann, 6),
        "max_drawdown": round(dd, 6),
        "daily_win_rate": round(float((rets > 0).mean()), 4) if len(rets) else None,
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "n_days": int(n_per),
        "avg_positions": round(float(np.mean([c["n_pos"] for c in curve])), 2),
        "max_positions": int(max(c["n_pos"] for c in curve)),
        "avg_utilization": round(float(np.mean([c["util"] for c in curve])), 4),
        "turnover": round(st["buy_gross"] / mean_eq, 4) if mean_eq > 0 else None,
        "n_buys": int(st["n_buy"]),
        "n_sells": int(len(trades)),
        "n_trades_closed": int(len(tr)),
        "avg_trade_return": round(float(tr.mean()), 6) if len(tr) else None,
        "median_trade_return": round(float(np.median(tr)), 6) if len(tr) else None,
        "trade_win_rate": round(float((tr > 0).mean()), 4) if len(tr) else None,
        "std_trade_return": round(float(tr.std(ddof=1)), 6) if len(tr) > 1 else None,
        "n_open_at_end": int(len(pos)),
        "n_signal_days": int(st["n_signal_days"]),
        "n_signal_cells": int(st["n_signal_cells"]),
        "n_signal_cells_raw": int(st["n_signal_cells_raw"]),
        "overlap_factor": round(
            st["n_signal_cells_raw"] / st["n_buy"], 3
        )
        if st["n_buy"]
        else None,
        "n_selected": int(st["n_selected"]),
        "skip_unfill": int(st["skip_unfill"]),
        "skip_nobar": int(st["skip_nobar"]),
        "skip_cash": int(st["skip_cash"]),
        "skip_lot": int(st["skip_lot"]),
        "skip_nocash": int(st["skip_nocash"]),
        "settle_fallback": int(st["settle_fallback"]),
        "final_equity": round(float(eq[-1]), 2),
        "capital": capital,
    }
    if weight_scheme != "equal":
        metrics["weight_scheme"] = weight_scheme
    if lot_fill != "strict":
        metrics["lot_fill"] = lot_fill
        metrics["recycle_pool"] = round(float(st["recycle_pool"]), 2)
        metrics["recycle_rounds"] = int(st["recycle_rounds"])
        metrics["recycle_filled"] = int(st["recycle_filled"])
        metrics["recycle_capped"] = int(st["recycle_capped"])
    if dynamic_n != "off":
        metrics["dynamic_n"] = dynamic_n
        metrics["dyn_n_days"] = {
            k[6:]: int(v) for k, v in sorted(st.items()) if k.startswith("dyn_n_")
        }
    return metrics, curve, trades


# ── 单窗口运行（在已切好的大矩阵上再切子窗口）──────────────���──
def run_window(M, start, end, cfg, log=print):
    dates = M["dates"]
    i0 = int(np.searchsorted(dates, int(start), side="left"))
    i1 = int(np.searchsorted(dates, int(end), side="right")) - 1
    if i1 <= i0:
        raise RuntimeError("window empty: %s~%s (%d..%d)" % (start, end, i0, i1))
    sub = {k: (v[i0 : i1 + 1] if isinstance(v, np.ndarray) else v) for k, v in M.items()}
    sub["dates"] = dates[i0 : i1 + 1]
    return simulate(sub, cfg, log=log)


# ── 实验矩阵定义 ──────────────────────────────────────────────
W3M = ("20260806", "20261105")
W12M = ("20251105", "20261105")
# 五窗口（末端统一 20261105，L2b 风险预算层验证用）
W5 = [
    ("3m", "20260806", "20261105"),
    ("6m", "20260506", "20261105"),
    ("12m", "20251105", "20261105"),
    ("24m", "20241105", "20261105"),
    ("36m", "20231105", "20261105"),
]
CAP = 10_000_000.0
COST = 0.0030
RISK_DIR = os.path.join(HERE, "_qpzm_rd_20261106", "risk_budget")


def _cfg(
    tag,
    win,
    hold,
    top_n,
    capital=CAP,
    max_w=0.05,
    alloc="cash",
    rank="score",
    note="",
    weight_scheme="equal",
    dynamic_n="off",
    lot_fill="strict",
    trade_diag=False,
):
    return {
        "tag": tag,
        "start": win[0],
        "end": win[1],
        "hold": hold,
        "top_n": top_n,
        "capital": capital,
        "cost": COST,
        "max_weight": max_w,
        "alloc": alloc,
        "rank": rank,
        "note": note,
        "weight_scheme": weight_scheme,
        "dynamic_n": dynamic_n,
        "lot_fill": lot_fill,
        "trade_diag": trade_diag,
    }


def batch_configs(name):
    if name == "m1":  # 量级校验：买全部信号（资金上限压到 1e-4 使现金不成为约束）
        return [
            _cfg(
                "validate_3m_all_cashmode",
                W3M,
                5,
                999999,
                capital=1e10,
                max_w=1e-4,
                alloc="cash",
                note="买入全部信号，用于与引擎逐笔均值对账",
            )
        ]
    if name == "m2":
        cfgs = []
        for n in (30, 50, 100, 200):
            cfgs.append(_cfg("3m_h5_top%d" % n, W3M, 5, n, note="主口径 score 排序"))
        for n in (30, 50, 100, 200):
            cfgs.append(
                _cfg("3m_h5_top%d_slot" % n, W3M, 5, n, alloc="slot", note="稳态等权预算 1/(N*hold)")
            )
        cfgs.append(_cfg("3m_h5_top30_rps", W3M, 5, 30, rank="rps", note="同信号集，按 RPS 排序"))
        cfgs.append(
            _cfg("3m_h5_top30_rps_slot", W3M, 5, 30, rank="rps", alloc="slot", note="RPS 排序 + slot")
        )
        for n in (30, 100):
            cfgs.append(_cfg("12m_h5_top%d" % n, W12M, 5, n, note="12m 主口径"))
        cfgs.append(_cfg("12m_h5_top30_rps", W12M, 5, 30, rank="rps", note="12m RPS 排序"))
        return cfgs
    if name == "m3b":  # 风险预算层：5 窗 × {A 加权 20 + B 中性 5 + C 动态N 5}
        cfgs = []
        for win, s, e in W5:
            w = (s, e)
            for n in (30, 100):
                for ws in ("equal", "inv_vol"):
                    cfgs.append(
                        _cfg(
                            "m3b_%s_h5_top%d_%s" % (win, n, ws),
                            w,
                            5,
                            n,
                            weight_scheme=ws,
                            note="A 组：%s 加权（%s）" % (ws, "等权基线" if ws == "equal" else "1/vol20 加权"),
                        )
                    )
            cfgs.append(
                _cfg(
                    "m3b_%s_h5_top30_score_resid" % win,
                    w,
                    5,
                    30,
                    rank="score_resid",
                    note="B 组：score 对 vol20 截面回归残差排序（equal 加权）",
                )
            )
            cfgs.append(
                _cfg(
                    "m3b_%s_h5_top100_dynenv" % win,
                    w,
                    5,
                    100,
                    dynamic_n="env",
                    note="C 组：按 env score expanding 分位动态 N（≥0.7→30，≤0.3→200，中档 100）",
                )
            )
        return cfgs
    if name == "attr":  # 归因批：5 窗 × {equal, inv_vol, equal_fixlot}，全部带逐笔明细
        cfgs = []
        for win, s, e in W5:
            w = (s, e)
            cfgs.append(
                _cfg(
                    "attr_%s_h5_top30_equal" % win,
                    w,
                    5,
                    30,
                    weight_scheme="equal",
                    lot_fill="strict",
                    trade_diag=True,
                    note="归因基线：等权 + 严格整手（与 m3b 同配置）",
                )
            )
            cfgs.append(
                _cfg(
                    "attr_%s_h5_top30_inv_vol" % win,
                    w,
                    5,
                    30,
                    weight_scheme="inv_vol",
                    lot_fill="strict",
                    trade_diag=True,
                    note="归因对照：1/vol20 加权 + 严格整手（与 m3b 同配置）",
                )
            )
            cfgs.append(
                _cfg(
                    "attr_%s_h5_top30_equal_fixlot" % win,
                    w,
                    5,
                    30,
                    weight_scheme="equal",
                    lot_fill="recycle",
                    trade_diag=True,
                    note="干净对照：等权 + 回收预算修复填单（选股/排序/总预算不变）",
                )
            )
            cfgs.append(
                _cfg(
                    "attr_%s_h5_top30_inv_vol_fixlot" % win,
                    w,
                    5,
                    30,
                    weight_scheme="inv_vol",
                    lot_fill="recycle",
                    trade_diag=True,
                    note="叠加档：1/vol20 加权 + 回收预算（检验两条通道是否互斥）",
                )
            )
        return cfgs
    raise ValueError("unknown batch: %s" % name)


# ── CLI ──────────────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="青萍之末组合层模拟器（真资金 top-N）")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--hold", type=int, default=5)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--capital", type=float, default=CAP)
    p.add_argument("--cost", type=float, default=None, help="往返成本，缺省从 config 推导")
    p.add_argument("--max-weight", type=float, default=0.05)
    p.add_argument("--alloc", choices=["cash", "slot"], default="cash")
    p.add_argument("--rank", choices=["score", "rps", "score_resid"], default="score")
    p.add_argument("--weight-scheme", choices=["equal", "inv_vol"], default="equal")
    p.add_argument("--dynamic-n", choices=["off", "env"], default="off")
    p.add_argument(
        "--lot-fill",
        choices=["strict", "recycle"],
        default="strict",
        help="strict=现状（买不起 1 手即跳过）；recycle=回收不足 1 手的预算按当日候选均摊后重判（最多 3 轮）",
    )
    p.add_argument("--limit-mode", choices=["exact", "approx"], default="exact")
    p.add_argument("--stocks-limit", type=int, default=0, help="调试：只读前 N 只")
    p.add_argument("--out", default=None)
    p.add_argument("--out-dir", default=OUT_DIR)
    p.add_argument("--batch", default=None, choices=["m1", "m2", "m3b", "attr"])
    p.add_argument("--tag", default=None)
    p.add_argument("--no-trades", action="store_true", help="JSON 不写逐笔明细")
    p.add_argument(
        "--trade-diag",
        action="store_true",
        help="逐笔明细追加 vol20/volq/pnl/basis 归因字段（默认关闭，保持输出逐位不变）",
    )
    return p.parse_args(argv)


def _atomic_write(path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    for k in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows 上目标被扫描/占用时短暂重试
            time.sleep(0.4 * (k + 1))
    with open(path, "w", encoding="utf-8") as f:  # 兜底：直接覆盖
        json.dump(payload, f, ensure_ascii=False, indent=1)
    try:
        os.remove(tmp)
    except OSError:
        pass


# ── beta / 残差诊断（L2b）─────────────────────────────────────
def market_daily_returns(M):
    """全市场等权日收益（逐日对有效个股日收益取均值），首个元素为 NaN。"""
    C = M["C"].astype("float64")
    R = C[1:] / C[:-1] - 1.0
    R = np.where(np.isfinite(R) & (np.abs(R) <= 0.5), R, np.nan)  # 剔除复权/复牌异常跳变
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mkt = np.nanmean(R, axis=1)
    return np.concatenate([[np.nan], mkt])


def window_idx(dates, start, end):
    i0 = int(np.searchsorted(dates, int(start), side="left"))
    i1 = int(np.searchsorted(dates, int(end), side="right")) - 1
    return i0, i1


def beta_corr(mkt, M, start, end, curve):
    """组合日收益 vs 全市场等权日收益的 Pearson 相关。"""
    i0, i1 = window_idx(M["dates"], start, end)
    eq = np.array([c["equity"] for c in curve], dtype=float)
    if len(eq) < 3:
        return None, 0
    pr = eq[1:] / eq[:-1] - 1.0
    mr = mkt[i0 + 1 : i1 + 1]
    n = min(len(pr), len(mr))
    pr, mr = pr[:n], mr[:n]
    ok = np.isfinite(pr) & np.isfinite(mr)
    if ok.sum() < 10 or pr[ok].std() == 0 or mr[ok].std() == 0:
        return None, int(ok.sum())
    return float(np.corrcoef(pr[ok], mr[ok])[0, 1]), int(ok.sum())


def resid_overlap(M, start, end, top=30):
    """score_resid 与原始 score 的 top-N 重合度（候选池内，逐信号日平均）。"""
    i0, i1 = window_idx(M["dates"], start, end)
    SCb, RS, BU = M["SC"][i0 : i1 + 1], M["RESID"][i0 : i1 + 1], M["BUY"][i0 : i1 + 1]
    ov, nd = [], 0
    for t in range(SCb.shape[0]):
        idx = np.flatnonzero(BU[t])
        if not idx.size:
            continue
        if idx.size <= top:
            ov.append(1.0)
            nd += 1
            continue
        s = np.nan_to_num(SCb[t, idx].astype(float), nan=-1e9)
        r = np.nan_to_num(RS[t, idx].astype(float), nan=-1e9)
        o1 = idx[np.argsort(-s, kind="stable")[:top]]
        o2 = idx[np.argsort(-r, kind="stable")[:top]]
        ov.append(len(set(o1.tolist()) & set(o2.tolist())) / float(top))
        nd += 1
    return (float(np.mean(ov)) if ov else None), nd


def _metrics_delta(new, old):
    """逐指标差值（只比 old 里存在的键），返回 (deltas, max_abs_float_delta, n_mismatch)。"""
    deltas, mx, bad = {}, 0.0, []
    for k, v in old.items():
        nv = new.get(k, "__missing__")
        if isinstance(v, bool) or isinstance(v, (int, np.integer)):
            try:
                same = int(nv) == int(v)
                d = int(nv) - int(v)
            except (TypeError, ValueError):
                same, d = False, None
        elif isinstance(v, float) or isinstance(v, np.floating):
            if nv in (None, "__missing__") or isinstance(nv, str):
                same, d = False, None
            else:
                d = round(float(nv) - float(v), 9)
                same = d == 0.0
                mx = max(mx, abs(d))
        else:
            same, d = (nv == v), None
        deltas[k] = d
        if not same:
            bad.append(k)
    return deltas, mx, bad


def _write_m3b_outputs(out_dir, rows, results, args, t0, cost, limit_meta, overlap_rows):
    import csv

    mat = os.path.join(out_dir, "m3b_matrix.csv")
    head = [
        "window",
        "group",
        "scheme",
        "top_n",
        "rank_key",
        "weight_scheme",
        "dynamic_n",
        "beta_corr",
        "n_obs",
        "n_signal_days",
        "dyn_n_mix",
        "d_ret_pp",
        "d_dd_pp",
        "d_sharpe",
        "d_trade_bp",
        "sample_ok",
    ]
    rest = [k for k in sorted(rows[0].keys()) if k not in head]
    cols = head + rest
    with open(mat, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("[m3b] wrote %s (%d rows)" % (mat, len(rows)), flush=True)

    bc = os.path.join(out_dir, "beta_corr.csv")
    with open(bc, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["window", "scheme", "top_n", "beta_corr", "n_obs", "pass_lt_0.3"])
        for r in rows:
            w.writerow(
                [
                    r["window"],
                    r["scheme"],
                    r["top_n"],
                    "" if r["beta_corr"] in (None, "") else "%.4f" % r["beta_corr"],
                    r["n_obs"],
                    "" if r["beta_corr"] in (None, "") else int(abs(r["beta_corr"]) < 0.3),
                ]
            )
    print("[m3b] wrote %s" % bc, flush=True)

    if overlap_rows:
        ov = os.path.join(out_dir, "resid_overlap.csv")
        with open(ov, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["window", "top_n", "overlap_score_resid_vs_score", "n_signal_days"])
            w.writeheader()
            for r in overlap_rows:
                w.writerow(r)

    # ── 回归校验：3m×top30×score×cash 与既有 JSON 逐指标对照 ──
    tag = "m3b_3m_h5_top30_equal"
    new_m = next((m for c, m, _ in results if c["tag"] == tag), None)
    checks = []
    for fn, ref in (
        ("3m_h5_top30.json", os.path.join(HERE, "_qpzm_rd_20261106", "portfolio_sim")),
        ("m3_3m_h5_top30_score.json", os.path.join(HERE, "_qpzm_rd_20261106", "portfolio_sim")),
    ):
        p = os.path.join(ref, fn)
        if not os.path.exists(p) or new_m is None:
            checks.append({"reference": fn, "status": "missing"})
            continue
        old = json.load(open(p, encoding="utf-8"))["metrics"]
        d, mx, bad = _metrics_delta(new_m, old)
        checks.append(
            {
                "reference": fn,
                "status": "identical" if not bad else "MISMATCH",
                "max_abs_delta": mx,
                "mismatched_keys": bad,
                "delta_new_minus_old": {k: v for k, v in d.items() if v not in (0, 0.0, None)},
                "n_keys_compared": len(old),
            }
        )
    ok = all(c.get("status") == "identical" for c in checks)
    _atomic_write(
        os.path.join(out_dir, "regression_check.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "config": tag,
            "criteria": "与既有 JSON 的 metrics 全部键逐值一致（Δ=0）",
            "passed": bool(ok),
            "checks": checks,
        },
    )
    print("[m3b] regression_check passed=%s" % ok, flush=True)

    _atomic_write(
        os.path.join(out_dir, "m3b_index.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch": "m3b",
            "cost_roundtrip": cost,
            "alloc": "cash",
            "hold": 5,
            "capital": CAP,
            "limit_meta": limit_meta,
            "elapsed_sec": round(time.time() - t0, 1),
            "regression_passed": bool(ok),
            "rows": rows,
            "resid_overlap": overlap_rows,
        },
    )
    if not ok:
        raise SystemExit("[m3b] 回归校验未通过，已停止（见 regression_check.json）")


def _write_attr_outputs(out_dir, rows, results, args, t0, cost, limit_meta):
    """attr 批产物：attr_matrix.csv / attr_index.json / attr_regression_check.json。"""
    import csv

    mat = os.path.join(out_dir, "attr_matrix.csv")
    head = [
        "window",
        "scheme",
        "lot_fill",
        "weight_scheme",
        "top_n",
        "d_ret_pp",
        "d_dd_pp",
        "d_sharpe",
        "d_trade_bp",
        "d_fill_pp",
    ]
    rest = sorted({k for r in rows for k in r.keys()} - set(head))
    with open(mat, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=head + rest, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("[attr] wrote %s (%d rows)" % (mat, len(rows)), flush=True)

    # ── 回归校验 1：strict 路径 vs m3b 逐键 Δ=0（10 个 equal/inv_vol 格子）──
    ref_dir = os.path.join(HERE, "_qpzm_rd_20261106", "risk_budget")
    checks = []
    for c, m, _ in results:
        win = next((w for w, s, e in W5 if s == c["start"] and e == c["end"]), None)
        if win is None or c["lot_fill"] != "strict":
            continue
        fn = "m3b_%s_h5_top30_%s.json" % (win, c["weight_scheme"])
        p = os.path.join(ref_dir, fn)
        if not os.path.exists(p):
            checks.append({"reference": fn, "status": "missing", "tag": c["tag"]})
            continue
        old = json.load(open(p, encoding="utf-8"))["metrics"]
        d, mx, bad = _metrics_delta(m, old)
        checks.append(
            {
                "tag": c["tag"],
                "reference": fn,
                "status": "identical" if not bad else "MISMATCH",
                "max_abs_delta": mx,
                "mismatched_keys": bad,
                "delta_new_minus_old": {k: v for k, v in d.items() if v not in (0, 0.0, None)},
                "n_keys_compared": len(old),
            }
        )
    ok = bool(checks) and all(x.get("status") == "identical" for x in checks)
    _atomic_write(
        os.path.join(out_dir, "attr_regression_check.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "criterion": "lot_fill=strict 的 equal/inv_vol 五窗口 metrics 与 m3b_*.json 逐键 Δ=0",
            "passed": ok,
            "n_compared": len(checks),
            "checks": checks,
        },
    )
    print("[attr] strict-parity vs m3b: %d cells, passed=%s" % (len(checks), ok), flush=True)

    _atomic_write(
        os.path.join(out_dir, "attr_index.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch": "attr",
            "cost_roundtrip": cost,
            "alloc": "cash",
            "hold": 5,
            "top_n": 30,
            "capital": CAP,
            "limit_meta": limit_meta,
            "elapsed_sec": round(time.time() - t0, 1),
            "strict_parity_passed": ok,
            "runs": [
                {"tag": c["tag"], "scheme": c["weight_scheme"], "lot_fill": c["lot_fill"], "file": os.path.basename(f)}
                for c, m, f in results
            ],
        },
    )
    if not ok:
        raise SystemExit("[attr] strict 回归校验未通过，已停止（见 attr_regression_check.json）")


def main(argv=None):
    args = parse_args(argv)
    t0 = time.time()
    cost = args.cost if args.cost is not None else qb.default_cost_roundtrip("CN")
    cfgs = batch_configs(args.batch) if args.batch else []
    if not cfgs:
        if not (args.start and args.end):
            raise SystemExit("需要 --start/--end 或 --batch")
        cfgs = [
            {
                "tag": args.tag or "run",
                "start": args.start,
                "end": args.end,
                "hold": args.hold,
                "top_n": args.top_n,
                "capital": args.capital,
                "cost": cost,
                "max_weight": args.max_weight,
                "alloc": args.alloc,
                "rank": args.rank,
                "weight_scheme": args.weight_scheme,
                "dynamic_n": args.dynamic_n,
                "lot_fill": args.lot_fill,
                "trade_diag": args.trade_diag,
                "note": "",
            }
        ]
    for c in cfgs:
        c["cost"] = cost

    is_m3b = args.batch == "m3b"
    is_attr = args.batch == "attr"
    if (is_m3b or is_attr) and args.out_dir == OUT_DIR:
        args.out_dir = RISK_DIR
    os.makedirs(args.out_dir, exist_ok=True)

    lo = min(int(c["start"]) for c in cfgs)
    hi = max(int(c["end"]) for c in cfgs)

    print("[sim] load_wide %s" % DATA_DIR, flush=True)
    wide = qb.load_wide(
        DATA_DIR,
        args.stocks_limit,
        keep_raw=(args.limit_mode == "exact"),
    )
    limit_meta = {}
    if args.limit_mode == "exact":
        try:
            limit_meta = qb.apply_exact_unfill(wide, lo, hi)
        except Exception as exc:  # noqa: BLE001
            print("[sim] exact unfill 失败，回退 approx：%s" % exc, flush=True)
            limit_meta = {"mode": "approx_fallback", "error": str(exc)}

    all_d = wide["dates"]
    pos_lo = max(int(np.searchsorted(all_d, lo, side="left")) - 30, 0)
    pos_hi = int(np.searchsorted(all_d, hi, side="right")) - 1
    print("[sim] slice rows %d..%d (%d dates), t=%.0fs" % (pos_lo, pos_hi, pos_hi - pos_lo + 1, time.time() - t0), flush=True)
    M = build_matrices(wide, pos_lo, pos_hi, want_vq=is_attr)
    del wide

    results = []
    MKT = market_daily_returns(M) if is_m3b else None
    rows, overlap_rows, run_meta = [], [], {}
    for c in cfgs:
        t1 = time.time()
        m, curve, trades = run_window(M, c["start"], c["end"], c)
        beta, n_obs = (beta_corr(MKT, M, c["start"], c["end"], curve) if is_m3b else (None, 0))
        payload = {
            "meta": {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "engine": "qpzm_backtest.py (reused: load_wide/env_gate/cross_rps/"
                "tech_score/combine_score)",
                "window": {"start": c["start"], "end": c["end"]},
                "hold": c["hold"],
                "top_n": c["top_n"],
                "capital": c["capital"],
                "cost_roundtrip": c["cost"],
                "max_weight": c["max_weight"],
                "alloc_mode": c["alloc"],
                "rank_key": c["rank"],
                "limit_mode": limit_meta.get("mode", "approx"),
                "limit_exact_meta": limit_meta,
                "note": c.get("note", ""),
                "n_codes": int(len(M["codes"])),
                "semantics": {
                    "entry": "T 日收盘信号 → T+1 开盘价成交",
                    "exit": "买入后第 N 个交易日收盘卖出（signal T → close(T+hold)）",
                    "unfill": "T+1 一字板跳过，资金留现金",
                    "cost": "买扣 cost/2，卖扣 cost/2",
                    "valuation": "单股收益链 市值(t)=买入金额×close(t)/执行价(open@T+1)",
                    "sizing": "现金等权，单票 ≤ max_weight×总资产，整手 100 股，允许留现金",
                },
                "elapsed_sec": round(time.time() - t1, 1),
            },
            "metrics": m,
            "equity_curve": curve,
        }
        if c.get("weight_scheme", "equal") != "equal":
            payload["meta"]["weight_scheme"] = c["weight_scheme"]
            payload["meta"]["semantics"]["sizing"] = (
                "1/vol20 倒数加权（总预算与等权同额），vol 缺失/0 回退平均权重，单票 ≤ max_weight×总资产"
            )
        if c.get("dynamic_n", "off") != "off":
            payload["meta"]["dynamic_n"] = c["dynamic_n"]
            payload["meta"]["dynamic_n_rule"] = (
                "env score expanding 分位（截至当日，min 20 obs）：≥0.7→30，≤0.3→200，其余→top_n"
            )
        if c.get("lot_fill", "strict") != "strict":
            payload["meta"]["lot_fill"] = c["lot_fill"]
            payload["meta"]["semantics"]["sizing"] = (
                "现金等权，整手 100 股；预算不足 1 手的票回收预算、当日候选均摊后重判（≤3 轮），"
                "选股池/排序/总预算不变"
            )
        if c.get("trade_diag"):
            payload["meta"]["trade_diag"] = True
            payload["meta"]["trade_diag_fields"] = (
                "pnl/basis/vol20_entry/volq_entry（买入日 t）/vol20_sig/volq_sig（信号日 t-1）；"
                "volq = 当日全市场截面 vol20 五分位，1=最低波"
            )
        if is_m3b:
            payload["meta"]["beta_corr_vs_mkt"] = None if beta is None else round(beta, 4)
            payload["meta"]["beta_corr_n_obs"] = int(n_obs)
        if not args.no_trades:
            payload["trades"] = trades
        out = args.out or os.path.join(args.out_dir, "%s.json" % c["tag"])
        if args.out and len(cfgs) > 1:
            out = os.path.join(args.out_dir, "%s.json" % c["tag"])
        _atomic_write(out, payload)
        results.append((c, m, out))
        run_meta[c["tag"]] = {"beta_corr": beta, "n_obs": n_obs}
        print(
            "%-30s top%-7s %-6s ret=%8.2f%% ann=%8.2f%% dd=%7.2f%% pos=%6.1f util=%5.1f%% "
            "turn=%6.1fx trade=%7.3f%% n=%6d beta=%s %.0fs"
            % (
                c["tag"],
                c["top_n"],
                c["alloc"],
                m["total_return"] * 100,
                m["annualized"] * 100,
                m["max_drawdown"] * 100,
                m["avg_positions"],
                m["avg_utilization"] * 100,
                m["turnover"] or 0,
                (m["avg_trade_return"] or 0) * 100,
                m["n_trades_closed"],
                "-" if beta is None else "%.3f" % beta,
                time.time() - t1,
            ),
            flush=True,
        )

    if is_attr:
        base = {}
        for c, m, _ in results:
            if c["weight_scheme"] == "equal" and c["lot_fill"] == "strict":
                base[(c["start"], c["end"])] = m
        for c, m, f in results:
            win = next((w for w, s, e in W5 if s == c["start"] and e == c["end"]), "?")
            scheme = (
                "equal_fixlot"
                if c["lot_fill"] == "recycle"
                else ("inv_vol" if c["weight_scheme"] == "inv_vol" else "equal")
            )
            bm = base.get((c["start"], c["end"]))
            d_ret = d_dd = d_shp = d_tr = d_fill = None
            if bm is not None:
                d_ret = round((m["total_return"] - bm["total_return"]) * 100, 3)
                d_dd = round((m["max_drawdown"] - bm["max_drawdown"]) * 100, 3)
                d_shp = (
                    round(m["sharpe"] - bm["sharpe"], 3)
                    if m["sharpe"] is not None and bm["sharpe"] is not None
                    else None
                )
                d_tr = (
                    round((m["avg_trade_return"] - bm["avg_trade_return"]) * 1e4, 2)
                    if m["avg_trade_return"] is not None and bm["avg_trade_return"] is not None
                    else None
                )
                d_fill = round(
                    100.0
                    * (
                        m["n_buys"] / max(m["n_selected"], 1)
                        - bm["n_buys"] / max(bm["n_selected"], 1)
                    ),
                    3,
                )
            row = dict(m)
            row.pop("dyn_n_days", None)
            row.update(
                {
                    "window": win,
                    "scheme": scheme,
                    "lot_fill": c["lot_fill"],
                    "weight_scheme": c["weight_scheme"],
                    "top_n": c["top_n"],
                    "d_ret_pp": "" if d_ret is None else d_ret,
                    "d_dd_pp": "" if d_dd is None else d_dd,
                    "d_sharpe": "" if d_shp is None else d_shp,
                    "d_trade_bp": "" if d_tr is None else d_tr,
                    "d_fill_pp": "" if d_fill is None else d_fill,
                }
            )
            rows.append(row)
        _write_attr_outputs(args.out_dir, rows, results, args, t0, cost, limit_meta)
        print("[sim] done %.0fs" % (time.time() - t0), flush=True)
        return 0

    if is_m3b:
        # 相对同窗同 N 的 equal 基线差值
        base = {}
        for c, m, _ in results:
            if c["weight_scheme"] == "equal" and c["rank"] == "score" and c["dynamic_n"] == "off":
                base[(c["start"], c["top_n"])] = m
        for c, m, f in results:
            win = next((w for w, s, e in W5 if s == c["start"] and e == c["end"]), "?")
            if c["rank"] == "score_resid":
                grp, scheme = "B_resid", "score_resid"
            elif c["dynamic_n"] == "env":
                grp, scheme = "C_dynN", "dynenv"
            elif c["weight_scheme"] == "inv_vol":
                grp, scheme = "A_inv_vol", "inv_vol"
            else:
                grp, scheme = "A_equal", "equal"
            bm = base.get((c["start"], c["top_n"]))
            d_ret = d_dd = d_shp = d_tr = None
            if bm is not None:
                d_ret = round((m["total_return"] - bm["total_return"]) * 100, 3)
                d_dd = round((m["max_drawdown"] - bm["max_drawdown"]) * 100, 3)
                d_shp = (
                    round(m["sharpe"] - bm["sharpe"], 3)
                    if m["sharpe"] is not None and bm["sharpe"] is not None
                    else None
                )
                d_tr = (
                    round((m["avg_trade_return"] - bm["avg_trade_return"]) * 1e4, 2)
                    if m["avg_trade_return"] is not None and bm["avg_trade_return"] is not None
                    else None
                )
            row = {k: v for k, v in m.items() if k != "dyn_n_days"}
            row.update(
                {
                    "window": win,
                    "group": grp,
                    "scheme": scheme,
                    "top_n": c["top_n"],
                    "rank_key": c["rank"],
                    "weight_scheme": c["weight_scheme"],
                    "dynamic_n": c["dynamic_n"],
                    "beta_corr": run_meta[c["tag"]]["beta_corr"],
                    "n_obs": run_meta[c["tag"]]["n_obs"],
                    "n_signal_days": m["n_signal_days"],
                    "dyn_n_mix": "|".join(
                        "%s:%s" % (k, v) for k, v in sorted(m.get("dyn_n_days", {}).items())
                    ),
                    "d_ret_pp": "" if d_ret is None else d_ret,
                    "d_dd_pp": "" if d_dd is None else d_dd,
                    "d_sharpe": "" if d_shp is None else d_shp,
                    "d_trade_bp": "" if d_tr is None else d_tr,
                    "sample_ok": int(m["n_trades_closed"] >= 800),
                }
            )
            rows.append(row)
        for win, s, e in W5:
            if not any(c["start"] == s for c, _, _ in results):
                continue
            ov, nd = resid_overlap(M, s, e, top=30)
            overlap_rows.append(
                {
                    "window": win,
                    "top_n": 30,
                    "overlap_score_resid_vs_score": None if ov is None else round(ov, 4),
                    "n_signal_days": nd,
                }
            )
        _write_m3b_outputs(args.out_dir, rows, results, args, t0, cost, limit_meta, overlap_rows)
        print("[sim] done %.0fs" % (time.time() - t0), flush=True)
        return 0

    _atomic_write(
        os.path.join(args.out_dir, "_index.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch": args.batch,
            "cost": cost,
            "elapsed_sec": round(time.time() - t0, 1),
            "runs": [
                {"tag": c["tag"], "params": {k: c[k] for k in ("start", "end", "hold", "top_n", "capital", "max_weight", "alloc", "rank")}, "metrics": m, "file": os.path.basename(f)}
                for c, m, f in results
            ],
        },
    )
    print("[sim] done %.0fs" % (time.time() - t0), flush=True)
    return 0

    _atomic_write(
        os.path.join(args.out_dir, "_index.json"),
        {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch": args.batch,
            "cost": cost,
            "elapsed_sec": round(time.time() - t0, 1),
            "runs": [
                {"tag": c["tag"], "params": {k: c[k] for k in ("start", "end", "hold", "top_n", "capital", "max_weight", "alloc", "rank")}, "metrics": m, "file": os.path.basename(f)}
                for c, m, f in results
            ],
        },
    )
    print("[sim] done %.0fs" % (time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
