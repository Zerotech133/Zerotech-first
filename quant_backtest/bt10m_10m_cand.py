# -*- coding: utf-8 -*-
"""候选池四腿 × 1000 万真实约束模拟（2026-11-10，老田「测试1年数据1000万看表现」）。

在 bt10m_10m_exec.py 的执行模拟器内核上扩展多腿卖出，把候选池全部灌进
同一账户约束（100 股取整 + 真实费率 + 平方根冲击 + M1 流动性帽）对比表现：

  V3  基线（现行固化口径）
  A3  slot_mode=score          合并 α +141bp t=1.91（非样本外，回撤代价）
  D1  slot_mode=score cap25    合并 α +127bp t=1.78
  P4  tp_partial 0.5 + trail 20%/5%（S-A 机构原版，弱市改善牛市略降）

口径：
  窗口 RY = 20251110→20261109（滚动年）；引擎侧另记 122bp 平摊 NAV 作参照。
  买入 fill=T+1 开盘；卖出腿 fill=触发日次日开盘；止盈族腿 fill=限价目标价
  （slip 档：entry×1.2×0.997，rearm 再 ×1.2×0.997），限价单不计冲击。
  执行手法两档对照：M0 开盘全量、M1 开盘+流动性帽 1%（单笔 ≤ 当日成交额 1%，
  卖出超出部分次日续卖）；冲击 k=100bp（敏感性见 bt10m_10m_exec.json）。
  价格收益用复权价（px），现金/手数用原始价（px_raw），分红送转由比价吸收。

自检门（「参数看着传了」家族防御）：
  G1  apply_preset 展开后四腿关键键必须原样在位；
  G2  V3 两档 NAV 必须复现 bt10m_10m_exec.json（M0 105.84 / M1 106.32，±0.15pp）；
  G3  P4 交易清单必须含 take_profit_part 腿（否则分批参数未生效）；
  G4  逐腿对账：重建毛收益 vs 引擎 pnl 最大偏差 < 0.05pp。

用法：python bt10m_10m_cand.py
产出：reports/bt10m_10m_cand.json + 终端对照表
"""
import json
import math
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_10m_cand.json")

CAPITAL = 10_000_000
D0, D1 = "20251110", "20261109"
COST_FLAT = 0.0061
TP_PCT, TP_SLIP = 0.20, 0.003
LOT = 100
LIQ_CAP = 0.01
K_BP = 100

LEGS = [
    ("V3", {}),
    ("A3_slotscore", {"slot_mode": "score"}),
    ("D1_score_cap25", {"slot_mode": "score", "slot_score_cap": 0.25}),
    ("P4_part50_trail", {"tp_partial": 0.5,
                         "trail_activate": 0.20, "trail_drawdown": 0.05}),
]
# G2 复现锚（取自 reports/bt10m_10m_exec.json，v3 @ k=100）
EXPECT_V3 = {"M0_open_full": 105.84, "M1_liqcap1": 106.32}


def fee_buy(amt):
    return max(5.0, amt * 0.00025) + amt * 0.00001


def fee_sell(amt):
    return max(5.0, amt * 0.00025) + amt * 0.0005 + amt * 0.00001


def impact_bp(k, part):
    return k * math.sqrt(max(part, 0.0))


def day_amount(s, d):
    b = eng._book(s)
    if b is None:
        return None
    row = b.loc[b["trade_date"] == d]
    if row.empty or not row["amount"].iloc[0]:
        return None
    return float(row["amount"].iloc[0]) * 1000  # 千元→元


def build_legs(tag, extra):
    """run_job → (days, engine_nav, buy_recs, legq, recon_dev)

    legq: (stock, bday) -> [ {sday, how, frac} ] 按日序 FIFO 多腿。
    frac: take_profit_part 用记录的 tp_frac，其余腿 = 剩余比例。
    """
    p = {"preset": "v3", "d0": D0, "d1": D1, "cost": COST_FLAT}
    p.update(extra)
    payload, err = eng.run_job(p, "cand10m_%s" % tag,
                               progress=lambda *a, **k: None)
    assert not err, "%s: %s" % (tag, err)
    trades = payload["trades"]
    days = [e["day"] for e in payload["equity_curve"]]
    engine_nav = payload["equity_curve"][-1]["equity"] * 100
    hmap = {}
    for d, plist in (payload.get("holdings") or {}).items():
        for h in plist:
            hmap[(d, h["stock"])] = h.get("wgt_pct")

    q = {}   # stock -> [ {bday, legs, cum} ]
    legq = {}
    for t in sorted(trades, key=lambda x: x["day"]):
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(
                {"bday": t["day"], "legs": [], "cum": 0.0})
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if not lst:
                continue
            cur = lst[0]
            f = t.get("tp_frac")
            f = float(f) if f is not None else max(0.0, 1.0 - cur["cum"])
            cur["legs"].append({"sday": t["day"], "how": t["how"],
                                "frac": f, "pnl_engine": float(t["pnl"])})
            cur["cum"] += f
            if cur["cum"] >= 0.999:
                lst.pop(0)
                legq[(t["stock"], cur["bday"])] = cur["legs"]

    # 对账（G4）：各腿重建毛收益 vs 引擎 pnl
    recon_dev = []
    for (s, bd), legs in legq.items():
        ea = eng.px(s, bd, "open")
        if not ea:
            continue
        for lg in legs:
            if lg["how"].startswith("take_profit"):
                ratio = (1 + TP_PCT) * (1 - TP_SLIP)
                if lg["how"] == "take_profit_rearm":
                    ratio *= (1 + 0.20) * (1 - TP_SLIP)
            else:
                xa = eng.px(s, lg["sday"], "open")
                if not xa:
                    continue
                ratio = xa / ea
            recon_dev.append(abs((ratio - 1) * 100 - lg["pnl_engine"]))

    buy_recs = []
    for t in trades:
        if t["side"] != "BUY":
            continue
        d, s = t["day"], t["stock"]
        ea, er = eng.px(s, d, "open"), eng.px_raw(s, d, "open")
        cr = eng.px_raw(s, d, "close")
        wgt = hmap.get((d, s))
        if not (ea and er and cr and wgt):
            continue
        buy_recs.append({"day": d, "stock": s, "wgt": wgt,
                         "entry_adj": ea, "open_raw": er, "close_raw": cr})
    return days, engine_nav, buy_recs, legq, recon_dev


def _take_shares(pos, s, n):
    """从 tranches 按比例取走 n 股，返回 (实际股数, 均价元, 均价adj)。"""
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
    # 取整差额补齐：从后往前再扣
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


def sim(method, k, days, legq, buy_recs):
    """单腿 × 单手法的 1000 万账户模拟（多腿卖出版）。"""
    cash = float(CAPITAL)
    pos = {}
    last_ratio = {}
    stat = {"impact_yuan": 0.0, "fees": 0.0, "turnover": 0.0,
            "n_buy": 0, "n_downsize": 0, "n_sell_carry": 0,
            "deploy_sum": 0.0, "eq": [], "part_ratios": [],
            "min_cash": CAPITAL, "max_deploy": 0.0}
    carry = []          # (stock, sh_left, entry_raw, entry_adj)
    buys_by_day = {}
    for b in buy_recs:
        buys_by_day.setdefault(b["day"], []).append(b)
    # 腿按日索引
    legs_by_day = {}
    for (s, bd), legs in legq.items():
        for lg in legs:
            legs_by_day.setdefault(lg["sday"], []).append((s, bd, lg))

    for d in days:
        # ---- 卖出尾巴（昨日没卖完，今日续卖）----
        still = []
        for (s, sh_left, eraw, eadj) in carry:
            xa, xr = eng.px(s, d, "open"), eng.px_raw(s, d, "open")
            if not (xa and xr):
                still.append((s, sh_left, eraw, eadj))
                continue
            da = day_amount(s, d) or sh_left * xr * 100
            sh_now = sh_left
            if method == "M1_liqcap1":
                sh_now = math.floor(min(sh_left * xr, LIQ_CAP * da) / xr / LOT) * LOT
            if sh_now <= 0:
                still.append((s, sh_left, eraw, eadj))
                continue
            part = sh_now * xr / da
            imp = impact_bp(k, part) / 10000.0
            gross = sh_now * eraw * (xa / eadj)
            cash += gross * (1 - imp) - fee_sell(gross)
            stat["impact_yuan"] += gross * imp
            stat["fees"] += fee_sell(gross)
            stat["turnover"] += gross
            stat["part_ratios"].append(part)
            if sh_now < sh_left:
                still.append((s, sh_left - sh_now, eraw, eadj))
                stat["n_sell_carry"] += 1
        carry = still

        # ---- 卖出腿 ----
        for (s, bd, lg) in legs_by_day.get(d, []):
            if s not in pos:
                continue
            sh_total = sum(t["sh"] for t in pos[s])
            if sh_total <= 0:
                continue
            eadj = pos[s][0]["entry_adj"]
            if lg["how"].startswith("take_profit"):
                ratio = (1 + TP_PCT) * (1 - TP_SLIP)
                if lg["how"] == "take_profit_rearm":
                    ratio *= (1 + 0.20) * (1 - TP_SLIP)
                sh_sell = int(round(lg["frac"] * sh_total / LOT) * LOT)
                sh_sell = min(sh_sell, sh_total)
                if sh_sell <= 0:
                    continue
                _, raw_avg, _ = _take_shares(pos, s, sh_sell)
                gross = sh_sell * raw_avg * ratio
                cash += gross - fee_sell(gross)   # 限价单不计冲击
                stat["fees"] += fee_sell(gross)
                stat["turnover"] += gross
            else:
                xa, xr = eng.px(s, d, "open"), eng.px_raw(s, d, "open")
                if not (xa and xr):
                    continue  # 停牌顺延（腿留在队列，次日不再触发——极罕见，计缺席）
                da = day_amount(s, d) or 1e12
                is_final = lg is legq[(s, bd)][-1]
                if is_final:
                    sh_sell = sh_total  # 收尾腿清掉全部剩余（含此前取整零头）
                else:
                    sh_sell = int(round(lg["frac"] * sh_total / LOT) * LOT)
                    sh_sell = min(sh_sell, sh_total)
                if sh_sell <= 0:
                    continue
                # 整腿股数先全部从持仓划走（防止「留在 pos 盯市 + 进 carry 再卖」双计）
                sh_taken, raw_avg, _ = _take_shares(pos, s, sh_sell)
                if method == "M1_liqcap1":
                    sh_now = math.floor(min(sh_taken * xr, LIQ_CAP * da) / xr / LOT) * LOT
                    sh_now = min(sh_now, sh_taken)
                else:
                    sh_now = sh_taken
                if sh_now <= 0:
                    carry.append((s, sh_taken, raw_avg, eadj))
                    stat["n_sell_carry"] += 1
                    continue
                part = sh_now * xr / da
                imp = impact_bp(k, part) / 10000.0
                gross = sh_now * raw_avg * (xa / eadj)
                cash += gross * (1 - imp) - fee_sell(gross)
                stat["impact_yuan"] += gross * imp
                stat["fees"] += fee_sell(gross)
                stat["turnover"] += gross
                stat["part_ratios"].append(part)
                if sh_now < sh_taken:
                    carry.append((s, sh_taken - sh_now, raw_avg, eadj))
                    stat["n_sell_carry"] += 1

        # ---- 买入 ----
        for b in buys_by_day.get(d, []):
            s = b["stock"]
            if s in pos:
                continue
            da = day_amount(s, d)
            if not da:
                continue
            eq_prev = stat["eq"][-1] if stat["eq"] else CAPITAL
            amt = b["wgt"] / 100.0 * eq_prev
            if method == "M1_liqcap1":
                amt = min(amt, LIQ_CAP * da)
            xr = b["open_raw"]
            sh = math.floor(amt / xr / LOT) * LOT
            if sh <= 0:
                continue
            spend = sh * xr
            part = spend / da
            imp = impact_bp(k, part) / 10000.0
            cost = spend * (1 + imp) + fee_buy(spend)
            if cash < cost:
                sh = math.floor(cash * 0.999 / xr / LOT) * LOT
                if sh <= 0:
                    continue
                spend = sh * xr
                part = spend / da
                imp = impact_bp(k, part) / 10000.0
                cost = spend * (1 + imp) + fee_buy(spend)
                stat["n_downsize"] += 1
            cash -= cost
            stat["impact_yuan"] += spend * imp
            stat["fees"] += fee_buy(spend)
            stat["turnover"] += spend
            stat["part_ratios"].append(part)
            pos[s] = [{"sh": sh, "entry_raw": xr, "entry_adj": b["entry_adj"]}]
            stat["n_buy"] += 1

        # ---- 盯市 ----
        mv = 0.0
        for s, ts in pos.items():
            ca = eng.px(s, d, "close")
            if ca:
                for t in ts:
                    last_ratio[s] = ca / t["entry_adj"]
            for t in ts:
                mv += t["sh"] * t["entry_raw"] * last_ratio.get(s, 1.0)
        eq_now = cash + mv
        stat["eq"].append(eq_now)
        stat["min_cash"] = min(stat["min_cash"], cash)
        if eq_now > 0:
            dep = mv / eq_now * 100
            stat["deploy_sum"] += dep
            stat["max_deploy"] = max(stat["max_deploy"], dep)
    return stat


def main():
    t0 = time.time()
    ap = eng.apply_preset({"preset": "v3", "slot_mode": "score",
                           "slot_score_cap": 0.25, "tp_partial": 0.5,
                           "trail_activate": 0.20, "trail_drawdown": 0.05})
    # G1：四腿关键键必须原样在位
    for key, want in (("slot_mode", "score"), ("slot_score_cap", 0.25),
                      ("tp_partial", 0.5), ("trail_activate", 0.20),
                      ("trail_drawdown", 0.05)):
        assert ap.get(key) == want, "G1 失败：%s=%r（期望 %r）" % (key, ap.get(key), want)
    print("G1 通过：apply_preset 四腿关键键全部在位", flush=True)

    out = {"window": "%s-%s" % (D0, D1), "capital": CAPITAL,
           "k_bp": K_BP, "liq_cap": LIQ_CAP, "legs": {}}
    v3_check = {}
    for tag, extra in LEGS:
        days, engine_nav, buy_recs, legq, recon = build_legs(tag, extra)
        # G4 对账门
        if recon:
            dev = max(recon)
            assert dev < 0.05, "G4 失败：%s 对账偏差 %.4fpp" % (tag, dev)
        # G3 分批腿存在性
        n_part = sum(1 for legs in legq.values() for lg in legs
                     if lg["how"] == "take_profit_part")
        if tag.startswith("P4") and n_part == 0:
            raise SystemExit("G3 失败：P4 无 take_profit_part 腿，参数未生效")
        rows = []
        for method in ("M0_open_full", "M1_liqcap1"):
            st = sim(method, K_BP, days,
                     dict((kk, [dict(l) for l in vv]) for kk, vv in legq.items()),
                     buy_recs)
            nav = st["eq"][-1] / CAPITAL * 100
            peak, mdd = st["eq"][0], 0.0
            for v in st["eq"]:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            npr = len(st["part_ratios"])
            row = {"method": method, "k_bp": K_BP, "nav": round(nav, 2),
                   "mdd": round(mdd * 100, 2),
                   "impact_yuan_wan": round(st["impact_yuan"] / 10000, 1),
                   "impact_bp_of_capital": round(st["impact_yuan"] / CAPITAL * 10000, 1),
                   "part_median_pct": round(statistics.median(st["part_ratios"]) * 100, 2) if npr else None,
                   "part_p90_pct": round(sorted(st["part_ratios"])[int(npr * 0.9)] * 100, 2) if npr else None,
                   "n_buy": st["n_buy"], "n_downsize": st["n_downsize"],
                   "n_sell_carry": st["n_sell_carry"], "n_part_legs": n_part,
                   "min_cash_yuan": round(st["min_cash"]),
                   "avg_deploy_pct": round(st["deploy_sum"] / len(days), 1),
                   "max_deploy_pct": round(st["max_deploy"], 1),
                   "fees_yuan_wan": round(st["fees"] / 10000, 1)}
            rows.append(row)
            if tag == "V3":
                v3_check[method] = row["nav"]
            print("%-16s %-12s k=%3d  NAV %7.2f%%  回撤 %6.2f%%  冲击 %5.1fbp  "
                  "参与率中位 %5.2f%%  买 %d  缩量 %d  卖尾 %d  part腿 %d  均仓 %.1f%%"
                  % (tag, method, K_BP, nav, mdd * 100,
                     row["impact_bp_of_capital"], row["part_median_pct"] or 0,
                     st["n_buy"], st["n_downsize"], st["n_sell_carry"],
                     n_part, row["avg_deploy_pct"]), flush=True)
        out["legs"][tag] = {"engine_nav_flat122": round(engine_nav, 2),
                            "n_buys": len(buy_recs), "n_part_legs": n_part,
                            "recon_max_dev_pp": round(max(recon), 4) if recon else None,
                            "rows": rows}
        print("%-16s 引擎122bp平摊 NAV %7.2f%%   对账最大偏差 %.4fpp"
              % (tag, engine_nav, max(recon) if recon else 0.0), flush=True)

    # G2：V3 必须复现上轮
    for m, want in EXPECT_V3.items():
        got = v3_check.get(m)
        assert got is not None and abs(got - want) <= 0.15, \
            "G2 失败：V3 %s NAV %s vs 期望 %s" % (m, got, want)
    print("G2 通过：V3 复现上轮（M0 %.2f / M1 %.2f）"
          % (v3_check["M0_open_full"], v3_check["M1_liqcap1"]), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("产物：%s（%.1fs）" % (OUT, time.time() - t0))
    return out


if __name__ == "__main__":
    main()
