# -*- coding: utf-8 -*-
"""bt10m 1000 万执行模拟器——单一事实源（2026-11-10 审计后从 cand.sim /
monthly2.sim_m 两份拷贝合并而来，固化口径 v1 的执行内核）。

此前两份拷贝（bt10m_10m_cand.sim 与 bt10m_10m_monthly2.sim_m）逻辑同源、
各自维护，属漂移隐患；本模块合并为一，价格与成交额函数**可注入**，
便于单元测试（bt10m_selftest.py）。

内核语义（与合并前逐位一致，除一处显式修复）：
  日序 = 卖出尾巴(carry) → 卖出腿 → 买入 → 盯市；
  买入 fill=当日开盘（引擎信号次日开盘）；卖出腿 fill=触发日次日开盘，
  止盈族按限价（tp_ratio / rearm_mult）不计冲击；
  M1：单笔 ≤ 当日成交额 liq_cap（超出次日续卖）；冲击 = k_bp*sqrt(part)；
  股数 100 股取整；复权价记账、原始价现金。

审计修复（本模块唯一语义变更）：
  卖出尾巴当日成交额缺失时的回退值，原两份拷贝均写 `sh_left * xr * 100`
  （量纲错 100 倍 → part 被低估 → 冲击被低估）。改为 notional
  `sh_left * xr`（part=1.0，冲击按全额保守计）。仅在成交额字段缺失
  且价格存在时触发，对既有锚点 NAV 无影响（见 G 门复验）。

月度台账（months）恒开启：逐日累积平均仓位/最高仓/买卖笔数/卖出构成
（sell_cat 归类）/成交额/费用/续卖次数/缩量次数，供 monthly 系报表使用。
"""
import math

LOT_DEFAULT = 100
CAPITAL_DEFAULT = 10_000_000
LIQ_CAP_DEFAULT = 0.01
K_BP_DEFAULT = 100


def sell_cat(how):
    """卖出 how → 四类手法。stop_loss→止损；take_profit*→止盈；
    time_stop→到期；其余（trail_stop/ma_break/3b_fail/exit_open/dk_*）→规则。"""
    if how == "stop_loss":
        return "止损"
    if how.startswith("take_profit"):
        return "止盈"
    if how == "time_stop":
        return "到期"
    return "规则"


def simulate(days, legq, buy_recs, *, px, px_raw, day_amount,
             capital=CAPITAL_DEFAULT, liq_cap=LIQ_CAP_DEFAULT,
             k_bp=K_BP_DEFAULT, lot=LOT_DEFAULT,
             tp_ratio=(1 + 0.20) * (1 - 0.003),
             rearm_mult=(1 + 0.20) * (1 - 0.003),
             method="M1_liqcap1"):
    """单腿 × 单手法 1000 万账户模拟。

    days:       交易日列表（引擎 equity_curve 顺序）
    legq:       (stock, bday) -> [ {sday, how, frac} ] 按日序 FIFO 多腿
    buy_recs:   [ {day, stock, wgt, entry_adj, open_raw, close_raw} ]
    px/px_raw:  fn(stock, day, kind) -> float|None（kind: open/close/high/low）
    day_amount: fn(stock, day) -> 成交额(元) 或 None
    tp_ratio:   止盈限价成交比（引擎 tp_pct/tp_slip 对应）
    rearm_mult: 再触发清仓在 tp_ratio 基础上再乘的系数
    返回 stat：eq/impact_yuan/fees/min_cash/turnover/n_buy/n_downsize/
              n_sell_carry/deploy_sum/max_deploy/part_ratios/months
    """
    cash = float(capital)
    pos = {}          # stock -> [ {sh, entry_raw, entry_adj} ]
    last_ratio = {}
    eq_curve = []
    stat = {"impact_yuan": 0.0, "fees": 0.0, "turnover": 0.0,
            "n_buy": 0, "n_downsize": 0, "n_sell_carry": 0,
            "deploy_sum": 0.0, "part_ratios": [],
            "min_cash": capital, "max_deploy": 0.0, "months": {}}
    carry = []        # (stock, sh_left, entry_raw, entry_adj)
    buys_by_day = {}
    for b in buy_recs:
        buys_by_day.setdefault(b["day"], []).append(b)
    legs_by_day = {}
    for (s, bd), legs in legq.items():
        for lg in legs:
            legs_by_day.setdefault(lg["sday"], []).append((s, bd, lg))

    def new_m(mk):
        return {"deploy_sum": 0.0, "days": 0, "max_deploy": 0.0,
                "n_buy": 0, "sells": {"止损": 0, "止盈": 0, "到期": 0, "规则": 0},
                "turnover": 0.0, "fees": 0.0, "n_carry": 0, "n_downsize": 0,
                "holdings_eom": 0, "deploy_eom": 0.0}

    cur_mk = None
    m = None

    for d in days:
        mk = d[:6]
        if mk != cur_mk:
            if m is not None:
                m["holdings_eom"] = len(pos)
            cur_mk, m = mk, new_m(mk)
            stat["months"][mk] = m

        # ---- 卖出尾巴（昨日没卖完，今日续卖）----
        still = []
        for (s, sh_left, eraw, eadj) in carry:
            xa, xr = px(s, d, "open"), px_raw(s, d, "open")
            if not (xa and xr):
                still.append((s, sh_left, eraw, eadj))
                continue
            # 审计修复：成交额缺失回退 notional（原 *100 量纲错，冲击被低估）
            da = day_amount(s, d) or (sh_left * xr)
            sh_now = sh_left
            if method == "M1_liqcap1":
                sh_now = math.floor(min(sh_left * xr, liq_cap * da) / xr / lot) * lot
            if sh_now <= 0:
                still.append((s, sh_left, eraw, eadj))
                continue
            part = sh_now * xr / da
            imp = k_bp * math.sqrt(max(part, 0.0)) / 10000.0
            gross = sh_now * eraw * (xa / eadj)
            cash += gross * (1 - imp) - _fee_sell(gross)
            stat["impact_yuan"] += gross * imp
            stat["fees"] += _fee_sell(gross)
            stat["turnover"] += gross
            stat["part_ratios"].append(part)
            m["turnover"] += gross
            m["fees"] += _fee_sell(gross)
            if sh_now < sh_left:
                still.append((s, sh_left - sh_now, eraw, eadj))
                stat["n_sell_carry"] += 1
                m["n_carry"] += 1
        carry = still

        # ---- 卖出腿 ----
        for (s, bd, lg) in legs_by_day.get(d, []):
            if s not in pos:
                continue
            sh_total = sum(t["sh"] for t in pos[s])
            if sh_total <= 0:
                continue
            eadj = pos[s][0]["entry_adj"]
            cat = sell_cat(lg["how"])
            if lg["how"].startswith("take_profit"):
                ratio = tp_ratio
                if lg["how"] == "take_profit_rearm":
                    ratio *= rearm_mult
                sh_sell = int(round(lg["frac"] * sh_total / lot) * lot)
                sh_sell = min(sh_sell, sh_total)
                if sh_sell <= 0:
                    continue
                _, raw_avg, _ = _take_shares(pos, s, sh_sell)
                gross = sh_sell * raw_avg * ratio
                cash += gross - _fee_sell(gross)   # 限价单不计冲击
                stat["fees"] += _fee_sell(gross)
                stat["turnover"] += gross
                m["turnover"] += gross
                m["fees"] += _fee_sell(gross)
            else:
                xa, xr = px(s, d, "open"), px_raw(s, d, "open")
                if not (xa and xr):
                    continue  # 停牌顺延（腿留队列，次日不再触发——极罕见）
                da = day_amount(s, d) or 1e12
                is_final = lg is legq[(s, bd)][-1]
                if is_final:
                    sh_sell = sh_total  # 收尾腿清掉全部剩余（含取整零头）
                else:
                    sh_sell = int(round(lg["frac"] * sh_total / lot) * lot)
                    sh_sell = min(sh_sell, sh_total)
                if sh_sell <= 0:
                    continue
                # 整腿股数先全部划走（防「留 pos 盯市 + 进 carry 再卖」双计）
                sh_taken, raw_avg, _ = _take_shares(pos, s, sh_sell)
                if method == "M1_liqcap1":
                    sh_now = math.floor(min(sh_taken * xr, liq_cap * da) / xr / lot) * lot
                    sh_now = min(sh_now, sh_taken)
                else:
                    sh_now = sh_taken
                if sh_now <= 0:
                    carry.append((s, sh_taken, raw_avg, eadj))
                    stat["n_sell_carry"] += 1
                    m["n_carry"] += 1
                    continue
                part = sh_now * xr / da
                imp = k_bp * math.sqrt(max(part, 0.0)) / 10000.0
                gross = sh_now * raw_avg * (xa / eadj)
                cash += gross * (1 - imp) - _fee_sell(gross)
                stat["impact_yuan"] += gross * imp
                stat["fees"] += _fee_sell(gross)
                stat["turnover"] += gross
                stat["part_ratios"].append(part)
                m["turnover"] += gross
                m["fees"] += _fee_sell(gross)
                if sh_now < sh_taken:
                    carry.append((s, sh_taken - sh_now, raw_avg, eadj))
                    stat["n_sell_carry"] += 1
                    m["n_carry"] += 1
            m["sells"][cat] += 1

        # ---- 买入 ----
        for b in buys_by_day.get(d, []):
            s = b["stock"]
            if s in pos:
                continue
            da = day_amount(s, d)
            if not da:
                continue
            eq_prev = eq_curve[-1] if eq_curve else capital
            amt = b["wgt"] / 100.0 * eq_prev
            if method == "M1_liqcap1":
                amt = min(amt, liq_cap * da)
            xr = b["open_raw"]
            sh = math.floor(amt / xr / lot) * lot
            if sh <= 0:
                continue
            spend = sh * xr
            part = spend / da
            imp = k_bp * math.sqrt(max(part, 0.0)) / 10000.0
            cost = spend * (1 + imp) + _fee_buy(spend)
            if cash < cost:
                sh = math.floor(cash * 0.999 / xr / lot) * lot
                if sh <= 0:
                    continue
                spend = sh * xr
                part = spend / da
                imp = k_bp * math.sqrt(max(part, 0.0)) / 10000.0
                cost = spend * (1 + imp) + _fee_buy(spend)
                stat["n_downsize"] += 1
                m["n_downsize"] += 1
            cash -= cost
            stat["impact_yuan"] += spend * imp
            stat["fees"] += _fee_buy(spend)
            stat["turnover"] += spend
            stat["part_ratios"].append(part)
            m["turnover"] += spend
            m["fees"] += _fee_buy(spend)
            pos[s] = [{"sh": sh, "entry_raw": xr, "entry_adj": b["entry_adj"]}]
            stat["n_buy"] += 1
            m["n_buy"] += 1

        # ---- 盯市 ----
        mv = 0.0
        for s, ts in pos.items():
            ca = px(s, d, "close")
            if ca:
                for t in ts:
                    last_ratio[s] = ca / t["entry_adj"]
            for t in ts:
                mv += t["sh"] * t["entry_raw"] * last_ratio.get(s, 1.0)
        eq_now = cash + mv
        eq_curve.append(eq_now)
        stat["min_cash"] = min(stat["min_cash"], cash)
        if eq_now > 0:
            dep = mv / eq_now * 100
            stat["deploy_sum"] += dep
            stat["max_deploy"] = max(stat["max_deploy"], dep)
            m["days"] += 1
            m["deploy_sum"] += dep
            m["max_deploy"] = max(m["max_deploy"], dep)
            m["deploy_eom"] = dep
    if m is not None:
        m["holdings_eom"] = len(pos)
    stat["eq"] = eq_curve
    return stat


def _take_shares(pos, s, n):
    """从 tranches 按比例取走 n 股，返回 (实际股数, 原始均价, adj)。"""
    ts = pos.get(s)
    if not ts:
        return 0, 0.0, 1.0
    total = sum(t["sh"] for t in ts)
    n = min(n, total)
    if n <= 0:
        return 0, 0.0, 1.0
    raw = sum(t["sh"] * t["entry_raw"] for t in ts) / total
    adj = ts[0]["entry_adj"]
    frac = n / total
    rest = []
    taken_sh = 0
    for t in ts:
        take = int(round(t["sh"] * frac))
        take = max(0, min(take, t["sh"]))
        taken_sh += take
        if t["sh"] - take > 0:
            rest.append({"sh": t["sh"] - take, "entry_raw": t["entry_raw"],
                         "entry_adj": t["entry_adj"]})
    diff = n - taken_sh
    for t in reversed(rest):
        if diff <= 0:
            break
        cut = min(diff, t["sh"])
        t["sh"] -= cut
        diff -= cut
    pos[s] = [t for t in rest if t["sh"] > 0]
    if not pos[s]:
        pos.pop(s, None)
    return n, raw, adj


def _fee_buy(amt):
    return max(5.0, amt * 0.00025) + amt * 0.00001


def _fee_sell(amt):
    return max(5.0, amt * 0.00025) + amt * 0.0005 + amt * 0.00001
