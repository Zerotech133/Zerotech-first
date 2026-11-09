"""降换手扫参（1000 万 · 1 年 · 缠论三买轮动）。

命题：基准跑出换手 60.6 倍本金、往返成本 18.2%，净值 −21.90%；而零成本仍 −6.28%。
换手为什么等于 60 倍：768 笔里 765 笔持仓恰好 1 个交易日——三买失效线（收盘跌破
中枢上沿）在买入次日几乎必然成立，持仓当天就被否掉。

本次扫两组杠杆：
  A 放松失效线：zg→zd（跌破中枢下沿才走）/ 加缓冲 / 关掉失效线只靠卖点+到期
  B 硬性持有期：min_holding_days=5/10（此前该参数在缠论分支是空转的，本次才接上）
     并配 holding_max_days 兜底（卖点一年只出现 2 次，不兜底会永久持仓）

目标线：换手 <10 倍本金 ⇔ 往返成本 <3%（10×2×0.15%）。

用法：
  python bt10m_sweep.py                # 跑全部变体
  python bt10m_sweep.py hold5 zd_hold5 # 只跑指定变体
产出：reports/bt10m_sweep.json（逐变体汇总，增量落盘）
"""
import json
import os
import re
import sys
import time
import traceback
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports")
RESULT = os.path.join(OUT, "bt10m_sweep.json")

CAPITAL = 10_000_000.0

# 基准参数 = 已交付的 bt10m1y（缠论等机会轮动 · 仅三买 · 8% 槽 · 千分之 1.5 单边）
BASE_PARAMS = {
    "strategy": "chanlun",
    "d0": "20251013",
    "d1": "20261012",
    "top_n": 10,
    "slot_pct": 0.08,
    "slot_mode": "fixed",
    "cap_mode": "regime_8050",
    "cost": 0.0015,
    "limit_check": True,
    "buy_filter": "3",
    "universe": "market",
    "hist_st": True,
    "three_fail_exit": True,
    "fail_level": "zg",
    "fail_buffer": 0.0,
    "min_holding_days": 0,
    "holding_max_days": 0,
}

# (job_id, 分组, 一句话说明, 覆盖参数)
VARIANTS = [
    ("base", "基准", "现状：跌破中枢上沿次日即走·无最短持有期", {}),
    # ---- A 只放松失效线 ----
    ("zd", "A 放松失效线", "跌破中枢下沿才走（理论内最松口径）", {"fail_level": "zd"}),
    (
        "zg_b3",
        "A 放松失效线",
        "仍看上沿，但需再跌 3% 才算失效",
        {"fail_level": "zg", "fail_buffer": 0.03},
    ),
    (
        "no3b_m20",
        "A 放松失效线",
        "关掉失效线，只靠卖点确认+持有满20日强平",
        {"three_fail_exit": False, "holding_max_days": 20},
    ),
    # ---- B 硬性持有期 ----
    ("hold5", "B 最短持有期", "持有满5日前不许卖（失效线仍看上沿）", {"min_holding_days": 5}),
    ("hold10", "B 最短持有期", "持有满10日前不许卖", {"min_holding_days": 10}),
    (
        "hold5_mx20",
        "B 最短持有期",
        "最短5日 + 最长20日强平",
        {"min_holding_days": 5, "holding_max_days": 20},
    ),
    (
        "hold10_mx30",
        "B 最短持有期",
        "最短10日 + 最长30日强平",
        {"min_holding_days": 10, "holding_max_days": 30},
    ),
    (
        "hold10_mx60",
        "B 最短持有期",
        "最短10日 + 最长60日强平",
        {"min_holding_days": 10, "holding_max_days": 60},
    ),
    # ---- C 组合 ----
    (
        "zd_hold5",
        "C 组合",
        "跌破下沿才走 + 最短持有5日",
        {"fail_level": "zd", "min_holding_days": 5},
    ),
    (
        "zd_mx20",
        "C 组合",
        "跌破下沿才走 + 最长持有20日",
        {"fail_level": "zd", "holding_max_days": 20},
    ),
    # ---- D 纯到期族（彻底不要失效线）----
    (
        "no3b_m10",
        "D 纯到期",
        "关失效线 + 持有满10日平仓",
        {"three_fail_exit": False, "holding_max_days": 10},
    ),
    (
        "no3b_m30",
        "D 纯到期",
        "关失效线 + 持有满30日平仓",
        {"three_fail_exit": False, "holding_max_days": 30},
    ),
    (
        "no3b_free",
        "D 纯到期",
        "关失效线 + 不设强平（只靠卖点确认，一年仅 2 次）",
        {"three_fail_exit": False},
    ),
    # ---- E 用移动止损替代失效线 ----
    (
        "no3b_tr53_m20",
        "E 止损替代",
        "关失效线 + 浮盈5%布防/回撤3.5%离场 + 20日强平",
        {
            "three_fail_exit": False,
            "holding_max_days": 20,
            "trail_activate": 0.05,
            "trail_drawdown": 0.035,
        },
    ),
    (
        "no3b_tr53",
        "E 止损替代",
        "关失效线 + 只靠移动止损（5%/3.5%）",
        {"three_fail_exit": False, "trail_activate": 0.05, "trail_drawdown": 0.035},
    ),
    # ---- F 追高门槛（证明亏损来自「买在上沿上方 15%」）----
    (
        "ext05_zg",
        "F 追高门槛",
        "只买距上沿≤5% + 跌破上沿离场（最接近缠论三买原意）",
        {"max_ext_pct": 0.05},
    ),
    (
        "ext05_zg_mx30",
        "F 追高门槛",
        "只买距上沿≤5% + 跌破上沿离场 + 30日兜底",
        {"max_ext_pct": 0.05, "holding_max_days": 30},
    ),
    (
        "ext05_m20",
        "F 追高门槛",
        "只买距上沿≤5% + 关失效线 + 20日强平",
        {"max_ext_pct": 0.05, "three_fail_exit": False, "holding_max_days": 20},
    ),
    (
        "ext03_m20",
        "F 追高门槛",
        "只买距上沿≤3% + 关失效线 + 20日强平",
        {"max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20},
    ),
    (
        "ext05_m20_tr",
        "F 追高门槛",
        "只买距上沿≤5% + 关失效线 + 20日 + 移动止损5%/3.5%",
        {
            "max_ext_pct": 0.05,
            "three_fail_exit": False,
            "holding_max_days": 20,
            "trail_activate": 0.05,
            "trail_drawdown": 0.035,
        },
    ),
    # ---- G 门槛收紧 / 参数微调 ----
    (
        "ext03_m20_tr",
        "G 参数微调",
        "距上沿≤3% + 关失效线 + 20日 + 移动止损",
        {
            "max_ext_pct": 0.03,
            "three_fail_exit": False,
            "holding_max_days": 20,
            "trail_activate": 0.05,
            "trail_drawdown": 0.035,
        },
    ),
    (
        "ext02_m20",
        "G 参数微调",
        "距上沿≤2% + 关失效线 + 20日",
        {"max_ext_pct": 0.02, "three_fail_exit": False, "holding_max_days": 20},
    ),
    (
        "ext03_m30",
        "G 参数微调",
        "距上沿≤3% + 关失效线 + 30日",
        {"max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 30},
    ),
    (
        "ext03_tr53",
        "G 参数微调",
        "距上沿≤3% + 关失效线 + 只靠移动止损（无强平）",
        {
            "max_ext_pct": 0.03,
            "three_fail_exit": False,
            "trail_activate": 0.05,
            "trail_drawdown": 0.035,
        },
    ),
]


def _hold_days(trades, idx):
    """FIFO 配对买卖，返回持仓交易日数列表。"""
    q, out = {}, []
    for t in trades:
        s = t["stock"]
        if t["side"] == "BUY":
            q.setdefault(s, []).append(idx.get(t["day"]))
        elif t["side"] == "SELL":
            lst = q.get(s)
            if lst:
                b = lst.pop(0)
                if b is not None and t["day"] in idx:
                    out.append(idx[t["day"]] - b)
    return out


def _metrics(payload, job, label, note, over):
    summ = payload["summary"]
    days = payload["days"]
    trades = payload["trades"]
    eq = payload["equity_curve"]
    idx = {e["day"]: i for i, e in enumerate(eq)}

    # 换手：与 bt10m.py 同口径（Σ 买入金额占比）
    turnover = 0.0
    n_buy = 0
    for rec in days.values():
        for a in rec.get("actions") or []:
            if a.get("type") == "buy":
                n_buy += 1
                m = re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                if m:
                    turnover += float(m.group(1)) / 100.0

    cost_one_way = BASE_PARAMS["cost"]
    cost_total = turnover * cost_one_way * 2

    pnls = [t["pnl"] for t in trades if t["side"] == "SELL" and t.get("pnl") is not None]
    holds = _hold_days(trades, idx)
    how = Counter(t.get("how") for t in trades if t["side"] == "SELL")

    end_asset = CAPITAL * eq[-1]["equity"]
    ret = (end_asset / CAPITAL - 1) * 100

    # 成本完全去掉后还剩多少（用实测单笔 pnl 反推零成本组合收益的近似）
    # Σ(pnl × slot) 为「扣过买入费的相对收益」，与 bt10m.py 同法
    zero_approx = sum(pnls) / 100.0 * BASE_PARAMS["slot_pct"] * 100

    return {
        "job": job,
        "label": label,
        "note": note,
        "over": over,
        "total_return_pct": round(ret, 2),
        "end_asset": round(end_asset, 2),
        "pnl_amount": round(end_asset - CAPITAL, 2),
        "max_drawdown_pct": summ["max_drawdown_pct"],
        "annualized_pct": summ["annualized_pct"],
        "n_trades": summ["n_trades"],
        "n_buy": n_buy,
        "win_rate_pct": summ["win_rate_pct"],
        "profit_factor": summ["profit_factor"],
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "median_pnl_pct": round(sorted(pnls)[len(pnls) // 2], 2) if pnls else None,
        "avg_inv_pct": summ["avg_invested_pct"],
        "turnover_x": round(turnover, 2),
        "cost_total_pct": round(cost_total * 100, 2),
        "cost_total_amount": round(cost_total * CAPITAL, 0),
        "zero_cost_approx_pct": round(zero_approx, 2),
        "avg_hold_days": round(sum(holds) / len(holds), 2) if holds else None,
        "median_hold_days": sorted(holds)[len(holds) // 2] if holds else None,
        "max_hold_days": max(holds) if holds else None,
        "hold_dist": dict(sorted(Counter(holds).items())[:12]),
        "exit_why": dict(how),
        "n_min_hold_blocked": summ.get("n_min_hold_blocked"),
        "n_time_stop": summ.get("n_time_stop"),
        "n_3b_same_day": summ.get("n_3b_same_day"),
        "days_with_position": sum(1 for v in days.values() if v.get("n_pos", 0) > 0),
        "trading_days": len(eq),
    }


def main():
    want = set(sys.argv[1:])
    import backtest_engine_v2 as eng

    done = {}
    if os.path.exists(RESULT):
        try:
            prev = json.load(open(RESULT, encoding="utf-8"))
            done = {r["job"]: r for r in prev.get("variants", [])}
        except Exception:
            done = {}

    logs = []
    for job, group, note, over in VARIANTS:
        if want and job not in want:
            continue
        t0 = time.time()
        print("\n=== [%s] %s · %s" % (group, job, note), flush=True)
        params = dict(BASE_PARAMS)
        params.update(over)
        try:
            payload, err = eng.run_job(
                params, job, progress=lambda *a, **k: None
            )
        except Exception:
            print("  EXC\n" + traceback.format_exc(), flush=True)
            continue
        if err or payload is None:
            print("  失败：%s" % err, flush=True)
            continue
        m = _metrics(payload, job, group, note, over)
        m["group"] = group
        m["secs"] = round(time.time() - t0, 1)
        done[job] = m
        logs.append(m)
        print(
            "  收益 %+.2f%%  回撤 %.2f%%  笔数 %d  胜率 %s  PF %s"
            % (
                m["total_return_pct"],
                m["max_drawdown_pct"],
                m["n_trades"],
                m["win_rate_pct"],
                m["profit_factor"],
            ),
            flush=True,
        )
        print(
            "  换手 %.2f 倍  成本 %.2f%%（%.0f 元）  均持 %s 日  仓位 %.1f%%  用时 %ss"
            % (
                m["turnover_x"],
                m["cost_total_pct"],
                m["cost_total_amount"],
                m["avg_hold_days"],
                m["avg_inv_pct"],
                m["secs"],
            ),
            flush=True,
        )
        # 增量落盘：中途断掉也不丢已完成变体
        order = [v[0] for v in VARIANTS]
        rows = [done[k] for k in order if k in done]
        os.makedirs(OUT, exist_ok=True)
        json.dump(
            {"base_params": BASE_PARAMS, "variants": rows},
            open(RESULT, "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=1,
        )

    print("\n汇总（%d 个变体）→ %s" % (len(done), RESULT), flush=True)


if __name__ == "__main__":
    main()
