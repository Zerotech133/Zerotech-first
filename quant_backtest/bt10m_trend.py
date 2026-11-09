# -*- coding: utf-8 -*-
"""趋势退出 + 满仓 + 追高分档：三条改动各跑一年并排验证。

老田 2026-11-10：「用换成趋势还在就不砍（如跌破 20 日均线才走），这是牛市跑输的
直接来源。其次是仓位能升到 100%、距上沿≤3% 按 regime 分档而不是全局常数。再次跑1年回测。」

上一轮（bt10m_verify）的两条诊断：
  ① 样本外年（牛市，基准 +45.11%）D 的满仓等价只 34.67%，跑输 10.4pp；
     牛市 3 买事件 3924 只/年，但只成交 85 笔，多出来的被 top_n + 8% 槽位挡掉。
  ② 「距上沿≤3%」在样本外年笔均超额 t=−2.12 显著为负——它把仓位从强势股上挪开。

本轮三条改动，逐条单独开、再叠加：
  T1 趋势退出  收盘跌破个股自身 MA20 才走（替代 20 日强平 + 移动止损）
  T2 满仓      cap 100/100/100，10% 槽 × 10 槽 = 可到 100%
  T3 追高分档  high 10% / mid 5% / low 2%（牛市强势股本来就会远离中枢）

用法：
  python bt10m_trend.py            # 全部
  python bt10m_trend.py G H        # 只跑指定编号
产出：reports/bt10m_trend_1y.json
"""
import json
import os
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m_sweep as SW  # noqa: E402
import bt10m_verify as V  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(BASE, "reports", "bt10m_trend_1y.json")

COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]

# 买入口径与 D 相同（距上沿 3% / 关失效线 / 8% 槽 / regime 80-50-30），只换退出
D_LIKE = {"max_ext_pct": 0.03, "three_fail_exit": False}

# 满仓：10 槽 × 10% 可达 100%
FULL = {"top_n": 10, "slot_pct": 0.10, "slot_mode": "fixed",
        "cap_mode": "regime_8050", "cap_high": 1.0, "cap_mid": 1.0, "cap_low": 1.0}

# 追高分档：牛市 10%、震荡 5%、弱市 2%
EXT3 = {"max_ext_high": 0.10, "max_ext_mid": 0.05, "max_ext_low": 0.02}

MA20 = dict(D_LIKE, three_fail_exit=False, holding_max_days=0, exit_ma=20)

CONFIGS = [
    ("D", "旧头号（20日+止损5/3.5）",
     "上一轮结论 +11.45%/+14.56% 那个，本轮当基准对照",
     dict(D_LIKE, holding_max_days=20, trail_activate=0.05, trail_drawdown=0.035)),

    ("G", "T1 MA20退出（仓位不动）",
     "只换退出：跌破个股MA20才走，不设到期、不设移动止损；仓位/买入口径全部不动",
     dict(MA20)),

    ("H", "T1+T2 满仓",
     "G + cap 100/100/100、10% 槽×10 槽（仓位能到满）",
     dict(MA20, **FULL)),

    ("I", "T1+T2+T3 分档",
     "H + 追高门槛按 regime 分档（high 10/mid 5/low 2）",
     dict(MA20, **FULL, **EXT3)),

    ("J", "MA10（T1+T2+T3）",
     "均线换 MA10：更贴趋势、换手更高",
     dict(D_LIKE, holding_max_days=0, exit_ma=10, **FULL, **EXT3)),

    ("K", "MA60（T1+T2+T3）",
     "均线换 MA60：更慢，扛得住回踩",
     dict(D_LIKE, holding_max_days=0, exit_ma=60, **FULL, **EXT3)),

    ("L", "MA20+60日兜底",
     "I + 持有满 60 日强平（防均线永不击穿的死仓）",
     dict(MA20, holding_max_days=60, **FULL, **EXT3)),

    ("N", "入场MA20过滤+MA20退出",
     "I + 要求买入日开盘价 > 前一日MA20（趋势不起来不买）——本轮核心假设",
     dict(MA20, entry_ma=20, **FULL, **EXT3)),

    ("O", "MA20武装+止损8%+60日",
     "不做入场过滤，改为「站上均线后才算趋势单」；未武装靠 −8% 硬止损与 60 日兜底",
     dict(D_LIKE, exit_ma=20, exit_ma_mode="armed", stop_pct=-0.08,
          holding_max_days=60, **FULL, **EXT3)),

    ("P", "只加入场MA20过滤",
     "退出仍用 D 的「20日强平+移动止损」，只加趋势过滤——单独量入场过滤的贡献",
     dict(D_LIKE, holding_max_days=20, trail_activate=0.05, trail_drawdown=0.035,
          entry_ma=20, **FULL, **EXT3)),

    # ---- 2×2 消融：入场过滤 vs 满仓+分档，各单独开一次 ----
    ("Q", "D+入场过滤（不扩仓）",
     "2×2 消融①：D 的退出与仓位全不动，只加「开盘>前一日MA20」",
     dict(D_LIKE, holding_max_days=20, trail_activate=0.05, trail_drawdown=0.035,
          entry_ma=20)),

    ("R", "D+满仓+分档（不过滤）",
     "2×2 消融②：D 的退出不动，只放开仓位到 100% 并按 regime 分档追高",
     dict(D_LIKE, holding_max_days=20, trail_activate=0.05, trail_drawdown=0.035,
          **FULL, **EXT3)),
]


def skips(payload):
    """拒单归因：把当日 skip/note 的首段理由计数（看谁在真正拦住买入）。"""
    c = Counter()
    for rec in payload["days"].values():
        for a in rec.get("actions") or []:
            if a.get("type") != "skip":
                continue
            n = a.get("note") or ""
            for key in ("追高门槛", "槽位已满", "趋势过滤", "资金不足", "信号过期", "runup帽"):
                if n.startswith(key):
                    c[key] += 1
                    break
            else:
                c["其他"] += 1
    return dict(c)


def exits(payload):
    c = Counter(t["how"] for t in payload["trades"] if t["side"] == "SELL")
    return dict(c)


def main(argv):
    only = [a for a in argv[1:] if not a.startswith("--")]
    rows = {}
    if os.path.exists(RESULT):
        try:
            rows = json.load(open(RESULT, encoding="utf-8")).get("rows", {})
        except Exception:
            rows = {}

    for tag, d0, d1, desc in V.WINDOWS:
        print("\n########## %s %s → %s  %s" % (tag, d0, d1, desc), flush=True)
        nav, ks, nf = V.bench(d0, d1)
        bret = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
        peak, mdd = nav[ks[0]], 0.0
        for k in ks:
            peak = max(peak, nav[k])
            mdd = min(mdd, nav[k] / peak - 1)
        print("等权全A基准：%+.2f%%  回撤 %.2f%%  %d 日" % (bret, mdd * 100, len(ks)),
              flush=True)
        for cid, cname, note, over in CONFIGS:
            if only and cid not in only:
                continue
            for cost, ctag in COSTS:
                p = dict(SW.BASE_PARAMS)
                p.update(over)
                p["d0"], p["d1"] = d0, d1
                p["cost"] = cost
                job = "t%s_%s_%s" % (cid, tag, ctag)
                t0 = time.time()
                payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
                if err or not payload:
                    print("%-3s %-22s %-6s 失败：%s" % (cid, cname, ctag, err), flush=True)
                    continue
                m = V.metrics(payload, d0, d1, cost)
                m.update({"cid": cid, "name": cname, "note": note, "win_tag": tag,
                          "cost_tag": ctag, "d0": d0, "d1": d1,
                          "bench_ret": round(bret, 2), "bench_dd": round(mdd * 100, 2),
                          "ret_full": (round(m["ret"] / (m["avg_inv"] / 100.0), 2)
                                       if m["avg_inv"] else None),
                          "skip_why": skips(payload), "exit_why": exits(payload),
                          "secs": round(time.time() - t0, 1)})
                rows[job] = m
                print("%-3s %-22s %-6s 组合 %+7.2f%%  回撤 %6.2f%%  Calmar %5s  "
                      "笔数 %4d  胜率 %5s  PF %5s  换手 %5.2f  成本 %5.2f%%  "
                      "均持 %5s  均仓 %5s  笔均超额 %+7.1fbp t=%5s  满仓等价 %+7.2f%%"
                      % (cid, cname, ctag, m["ret"], m["dd"], m["calmar"], m["n"],
                         m["win_rate"], m["pf"], m["turnover"], m["cost_pct"],
                         m["avg_hold"], m["avg_inv"], m["ex_bp"] or 0, m["ex_t"],
                         m["ret_full"] or 0),
                      flush=True)
                json.dump({"rows": rows, "windows": V.WINDOWS},
                          open(RESULT, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)

    # ---------------- 并排报告 ----------------
    print("\n########## 三条改动 · 两年并排（30bp）")
    print("%-3s %-22s %9s %9s %9s %9s %8s %8s"
          % ("", "配置", "IN收益", "IN满仓等", "OOS收益", "OOS满仓等", "OOS基准", "均仓"))
    for cid, cname, _, _ in CONFIGS:
        a, b = rows.get("t%s_IN_30bp" % cid), rows.get("t%s_OOS_30bp" % cid)
        if not a or not b:
            continue
        print("%-3s %-22s %+8.2f%% %+8.2f%% %+8.2f%% %+8.2f%% %+7.2f%% %6.1f%%"
              % (cid, cname, a["ret"], a["ret_full"] or 0, b["ret"], b["ret_full"] or 0,
                 b["bench_ret"], b["avg_inv"] or 0))

    print("\n选股 alpha（笔均超额 · 唯一剔掉仓位与大盘的量）：")
    print("%-3s %-22s %9s %6s %9s %6s %8s" %
          ("", "配置", "IN bp", "t", "OOS bp", "t", "判定"))
    for cid, cname, _, _ in CONFIGS:
        a, b = rows.get("t%s_IN_30bp" % cid), rows.get("t%s_OOS_30bp" % cid)
        if not a or not b:
            continue
        sig = (abs(a["ex_t"]) >= 2) + (abs(b["ex_t"]) >= 2)
        if sig == 0:
            v = "两年都不显著"
        elif (a["ex_bp"] or 0) * (b["ex_bp"] or 0) > 0:
            v = "同向显著"
        else:
            v = "翻转"
        print("%-3s %-22s %+8.1f %6.2f %+8.1f %6.2f %8s"
              % (cid, cname, a["ex_bp"] or 0, a["ex_t"], b["ex_bp"] or 0, b["ex_t"], v))

    print("\n122bp 成本口径（真实资金那档）：")
    print("%-3s %-22s %10s %10s %10s" % ("", "配置", "IN 122bp", "OOS 122bp", "两年累计"))
    for cid, cname, _, _ in CONFIGS:
        a, b = rows.get("t%s_IN_122bp" % cid), rows.get("t%s_OOS_122bp" % cid)
        if not a or not b:
            continue
        cum = ((1 + a["ret"] / 100) * (1 + b["ret"] / 100) - 1) * 100
        print("%-3s %-22s %+9.2f%% %+9.2f%% %+9.2f%%"
              % (cid, cname, a["ret"], b["ret"], cum))

    print("\n拒单归因（OOS 年 · 谁在拦买入）：")
    for cid, cname, _, _ in CONFIGS:
        b = rows.get("t%s_OOS_30bp" % cid)
        if b:
            print("  %-3s %-22s 笔数%3d  %s" % (cid, cname, b["n"], b.get("skip_why")))

    print("\n离场原因分布（OOS 年）：")
    for cid, cname, _, _ in CONFIGS:
        b = rows.get("t%s_OOS_30bp" % cid)
        if b:
            print("  %-3s %-22s %s" % (cid, cname, b.get("exit_why")))

    print("\n产物：%s" % RESULT)


if __name__ == "__main__":
    main(sys.argv)
