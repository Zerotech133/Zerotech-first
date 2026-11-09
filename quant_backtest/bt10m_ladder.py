"""分层加仓对照（1000 万 · 1 年 · 缠论三买 · 老田 2026-11-09 方案）。

老田方案：总资金 1000 万，每日只选 5 只最看好的票，开盘各买 100 万（=500 万）；
之后单只波动 5% 盘中加 60 万、再波动 5% 加 40 万（每只满额 200 万，5 只恰好 1000 万）；
10 个交易日内没波动则该票后续档位作废。

关键歧义——「波动 5%」向上还是向下，决定这套规则是趋势跟随还是马丁格尔：
  up   = 浮盈加仓（正金字塔·顺势）     ← 按「择势」的说法应取此解
  down = 浮亏加仓（逆势摊平·等同马丁）   ← 一并跑，用数字说明为什么不能这么用

其余口径沿用已交付的最优组合：距中枢上沿 ≤3% 才买 + 关失效线 + 持有满 20 日强平
+ 移动止损 5%/3.5% + 单边成本 0.15%。

用法：python bt10m_ladder.py [job ...]
产出：reports/bt10m_ladder.json
"""
import json
import os
import statistics
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_engine_v2 as eng  # noqa: E402
import bt10m_sweep as SW  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports")
RESULT = os.path.join(OUT, "bt10m_ladder.json")

# 基线 = 降换手实验的最优组合（口径已固化），本次只改「怎么下注」
BASE_PARAMS = dict(SW.BASE_PARAMS)
BASE_PARAMS.update(
    {
        "max_ext_pct": 0.03,
        "three_fail_exit": False,
        "holding_max_days": 20,
        "trail_activate": 0.05,
        "trail_drawdown": 0.035,
        "light_meta": "1",
    }
)

# 老田方案的基础仓位口径：5 只 × 首笔 100 万、总额 1000 万（不再按 80/50/30 压帽）
FIVE_SLOT = {"top_n": 5, "slot_pct": 0.10, "slot_mode": "fixed", "cap_mode": "fixed",
             "cap_fixed": 1.0}

VARIANTS = [
    ("lad_up", "A 老田方案", "5只×100万 + 涨5%加60万 + 再涨5%加40万（10日窗）",
     dict(FIVE_SLOT, ladder_mode="up", ladder_step=0.05, ladder_window=10)),
    ("lad_dn", "B 反向对照", "同金额但「跌5%」才加 → 逆势摊平（马丁）",
     dict(FIVE_SLOT, ladder_mode="down", ladder_step=0.05, ladder_window=10)),
    ("lad_one", "C 只首笔", "5只×100万，永不加仓（50% 资金闲置）", dict(FIVE_SLOT)),
    ("lad_flat", "D 一次性满仓", "5只×200万 开盘一次打满（无分批）",
     dict(FIVE_SLOT, slot_pct=0.20)),
    ("lad_up_gate", "E 加择时", "老田方案 + regime 仓位帽 80/50/30（择时压满仓）",
     dict(FIVE_SLOT, cap_mode="regime_8050", ladder_mode="up", ladder_step=0.05,
          ladder_window=10)),
    ("lad_up_s3", "F 更密阶梯", "老田方案但每档 3%（更易触发）",
     dict(FIVE_SLOT, ladder_mode="up", ladder_step=0.03, ladder_window=10)),
    ("lad_up_w5", "G 更短窗口", "老田方案但 5 日内没动就作废",
     dict(FIVE_SLOT, ladder_mode="up", ladder_step=0.05, ladder_window=5)),
    ("lad_up_8", "H 集中 8 只", "8只×首笔12.5% 满仓 100%，涨5%加7.5%再涨加5%",
     dict(FIVE_SLOT, top_n=8, slot_pct=0.125, ladder_mode="up",
          ladder_amounts=(0.125, 0.075, 0.05), ladder_step=0.05, ladder_window=10)),
    # ---- I 止损配套：加仓阈值 5% 与移动止损启动 5% 撞在同一价位，加完就没缓冲 ----
    ("lad_up_t10", "I 宽止损配套", "老田方案 + 移动止损改启动10%/回撤7%（给加仓留空间）",
     dict(FIVE_SLOT, ladder_mode="up", ladder_step=0.05, ladder_window=10,
          trail_activate=0.10, trail_drawdown=0.07)),
    ("lad_up_notrail", "I 宽止损配套", "老田方案 + 关移动止损，只靠 20 日强平",
     dict(FIVE_SLOT, ladder_mode="up", ladder_step=0.05, ladder_window=10,
          trail_activate=0.0, trail_drawdown=0.0)),
    ("lad_one_notrail", "I 宽止损配套", "只首笔 + 关移动止损（配套对照）",
     dict(FIVE_SLOT, trail_activate=0.0, trail_drawdown=0.0)),
    ("lad_dn_t10", "I 宽止损配套", "向下加仓 + 宽止损 10%/7%（看摊平是否仍占优）",
     dict(FIVE_SLOT, ladder_mode="down", ladder_step=0.05, ladder_window=10,
          trail_activate=0.10, trail_drawdown=0.07)),
    # ---- J 不平均买卖：按评分加权（老田：不该每只一样多）----
    ("lad_score", "J 不平均", "5 只按评分加权分配（评分高多给），满仓 100%",
     dict(FIVE_SLOT, slot_mode="score")),
    ("lad_score_up", "J 不平均", "评分加权 + 向上加仓（组合老田两句话）",
     dict(FIVE_SLOT, slot_mode="score", ladder_mode="up", ladder_step=0.05,
          ladder_window=10)),
]


def hold_stats(payload):
    """逐笔配对算持仓天数（含加仓笔会被配对成多笔，按首笔算）。"""
    eq = [e["day"] for e in payload["equity_curve"]]
    idx = {x: i for i, x in enumerate(eq)}
    q, pairs = {}, []
    for t in payload["trades"]:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t)
        else:
            l = q.get(t["stock"])
            if l:
                b = l.pop(0)
                pairs.append(idx[t["day"]] - idx[b["day"]])
                q[t["stock"]] = l
    if not pairs:
        return {}
    return {
        "avg_hold": round(statistics.mean(pairs), 2),
        "med_hold": statistics.median(pairs),
        "max_hold": max(pairs),
    }


def main(argv):
    want = set(argv[1:])
    rows = []
    if os.path.exists(RESULT):
        try:
            rows = json.load(open(RESULT, encoding="utf-8")).get("variants", [])
        except Exception:
            rows = []
    done = {r["job"] for r in rows}

    for job, group, note, over in VARIANTS:
        if want and job not in want:
            continue
        if job in done and not want:
            print("跳过（已有）%s" % job, flush=True)
            continue
        p = dict(BASE_PARAMS)
        p.update(over)
        t0 = time.time()
        payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
        if err or not payload:
            print("%-12s 失败：%s" % (job, err), flush=True)
            continue
        s = payload["summary"]
        m = SW._metrics(payload, job, group, note, over)
        m.update(hold_stats(payload))
        m["n_add2"] = s.get("n_add2")
        m["n_add3"] = s.get("n_add3")
        m["avg_inv"] = s.get("avg_invested_pct")
        m["secs"] = round(time.time() - t0, 1)
        rows = [r for r in rows if r["job"] != job] + [m]
        rows.sort(key=lambda r: [v[0] for v in VARIANTS].index(r["job"]) if r["job"] in
                  [v[0] for v in VARIANTS] else 99)
        print("%-12s %+7.2f%%  DD %6.2f%%  n=%3d  win=%5s  PF %5s  换手 %5.2f  "
              "均仓 %5.1f%%  加仓 %2d+%2d  均持 %5s  %.1fs"
              % (job, m["total_return_pct"], m["max_drawdown_pct"], m["n_trades"],
                 m["win_rate_pct"], m["profit_factor"], m["turnover_x"],
                 m["avg_inv"] or 0, m.get("n_add2") or 0, m.get("n_add3") or 0,
                 m.get("avg_hold"), m["secs"]), flush=True)
        json.dump({"base_params": BASE_PARAMS, "variants": rows},
                  open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv)
