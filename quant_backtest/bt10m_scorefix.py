# -*- coding: utf-8 -*-
"""修 score_3buy 位置分量纲 bug 之后：评分修复效果 + 「反抽」退出网格。

老田 2026-11-10 指令（第二条）：
  1) 修 score_3buy 位置分量纲 bug（adj_close 比 zg → 位置分恒满分，40% 权重=常数）
  2) 按「反抽」重设退出：固定目标位止盈 + 时间衰减，而不是均线跟随
  3) 两件配着一起验证

隔离设计（关键）：
  修法只改 bars 的收盘价量纲（复权→原始），结构层只用 h/l，所以
  **信号（日期/类型/zg/zd）逐条不变，只有 score 变**。
  → 拿「同一套退出规则 D」在旧评分/新评分各跑一次，差值就是评分修复的净效果。

基线链（逐层加东西，每层只改一件事）：
  D   20日强平 + 移动止损5%/3.5%      （上一轮结论，旧评分口径 +11.45%/ +14.56%）
  Dn  同 D，但用新评分                → 隔离评分修复
  R0  关移动止损，改 −8% 硬止损 + 20日  → TP 的干净基线
  S~W 在 R0 上加固定目标位止盈，扫 tp_pct × tp_decay
  Z   同 V 但无到期（纯止盈 + 止损）  → 分离「衰减」与「到期」
  X   最优 TP + 满仓 100% + 追高分档

用法：python bt10m_scorefix.py [Dn S T ...]
产出：reports/bt10m_scorefix_1y.json
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m_sweep as SW  # noqa: E402
import bt10m_verify as V  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(BASE, "reports", "bt10m_scorefix_1y.json")
COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]

WINDOWS = list(V.WINDOWS) + [
    ("PRE", "20231010", "20240930", "第三个窗口（从未参与挑参·240 日·零重叠）"),
]

# D 的退出：20 日强平 + 移动止损（上一轮头号）
D_EXIT = {"max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20,
          "trail_activate": 0.05, "trail_drawdown": 0.035}
# R0：关移动止损，改硬止损 −8%（缠论分支此前 stop_pct 是空转的，本轮才接上）
R0 = {"max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20,
      "stop_pct": -0.08}
FULL = {"top_n": 10, "slot_pct": 0.10, "slot_mode": "fixed", "cap_mode": "regime_8050",
        "cap_high": 1.0, "cap_mid": 1.0, "cap_low": 1.0}
EXT3 = {"max_ext_high": 0.10, "max_ext_mid": 0.05, "max_ext_low": 0.02}


def tp(p, decay=0.0, lo=None):
    """固定目标位止盈：目标 = p − decay×持有日数，下限 max(lo, 2%) 防降到 0。"""
    return {"tp_pct": p, "tp_decay": decay, "tp_min": max(lo if lo is not None else 0.0, 0.02)}


CONFIGS = [
    ("Dn", "D 退出·新评分", "同 D，只把评分从「恒 40 位置分」换成真位置分", dict(D_EXIT)),
    ("R0", "硬止损8%+20日（无TP）", "TP 的干净基线：关移动止损，亏 8% 走、20 日到期", dict(R0)),
    ("S", "TP 8% 无衰减", "R0 + 目标位止盈 8%（纯固定目标）", dict(R0, **tp(0.08))),
    ("T", "TP 12% 无衰减", "R0 + 目标位止盈 12%", dict(R0, **tp(0.12))),
    ("U", "TP 15% 无衰减", "R0 + 目标位止盈 15%", dict(R0, **tp(0.15))),
    ("V", "TP 12% 衰减0.2pp/日", "目标从 12% 起，每持有一日降 0.2pp（下限 3%）", dict(R0, **tp(0.12, 0.002, 0.03))),
    ("W", "TP 10% 衰减0.3pp/日", "目标从 10% 起，每日降 0.3pp（下限 2%）", dict(R0, **tp(0.10, 0.003, 0.02))),
    ("Z", "TP 12%衰减·无到期", "同 V 但取消 20 日强平 → 分离「衰减」与「到期」各自的作用",
     dict(R0, holding_max_days=0, **tp(0.12, 0.002, 0.03))),
    ("X", "TP12%衰减+满仓+分档", "V + cap 100/100/100、10% 槽×10 槽、追高按 regime 分档",
     dict(R0, **FULL, **EXT3, **tp(0.12, 0.002, 0.03))),
    ("Y", "TP12%衰减+满仓(不分档)", "X 去掉分档，看仓位与分档各自贡献", dict(R0, **FULL, **tp(0.12, 0.002, 0.03))),
    # ---- 第二批：目标位单调往上扫（第一批里 8%<12%<15%，还没见顶）----
    ("U1", "TP 18% 无衰减", "U 的延长：看拐点在哪", dict(R0, **tp(0.18))),
    ("U2", "TP 20% 无衰减", "继续往上", dict(R0, **tp(0.20))),
    ("U3", "TP 25% 无衰减", "继续往上", dict(R0, **tp(0.25))),
    ("U4", "TP 30% 无衰减", "接近「不止盈，只靠止损+到期」", dict(R0, **tp(0.30))),
    ("U5", "TP 15%·30日到期", "U 把到期从 20 日放到 30 日（是不是被 20 日砍了）",
     dict(R0, holding_max_days=30, **tp(0.15))),
    ("U6", "TP 20%·30日到期", "U2 配 30 日到期", dict(R0, holding_max_days=30, **tp(0.20))),
    # ---- 评分修复真正能起作用的地方：候选数 > 槽位时排序才触发 ----
    # 同一次「满仓+分档」跑两遍：R（trend 那轮，旧评分）vs Rn（本轮，新评分）。
    ("Rn", "满仓+分档·新评分(D退出)", "与上一轮 R（旧评分）逐项对照 → 隔离位置排序的价值",
     dict(D_EXIT, **FULL, **EXT3)),
    ("Un", "TP15%+满仓+分档·新评分", "把最优 TP 装上满仓+分档（候选多、排序生效）",
     dict(R0, **FULL, **EXT3, **tp(0.15))),
    # ---- 第三批：U6 邻域（判「稳健」还是「拟合」——只看一个点必是拟合）----
    ("V1", "TP20%·40日", "到期再放长", dict(R0, holding_max_days=40, **tp(0.20))),
    ("V2", "TP20%·60日", "到期放到 60 日", dict(R0, holding_max_days=60, **tp(0.20))),
    ("V3", "TP18%·30日", "目标左邻", dict(R0, holding_max_days=30, **tp(0.18))),
    ("V4", "TP25%·30日", "目标右邻", dict(R0, holding_max_days=30, **tp(0.25))),
    ("V5", "TP15%·40日", "目标 15 + 40 日", dict(R0, holding_max_days=40, **tp(0.15))),
    ("V6", "TP12%·30日", "目标 12 + 30 日", dict(R0, holding_max_days=30, **tp(0.12))),
    # ---- 第四批：评分排序能不能替代「距上沿≤3%」这道硬门槛 ----
    ("W1", "TP20%·40日·不设追高门槛", "max_ext=0，买点位置完全交给评分排序去挑",
     dict(R0, holding_max_days=40, max_ext_pct=0.0, **tp(0.20))),
    ("W2", "TP20%·40日·门槛2%", "门槛更紧（2%），与 V1 的 3% 对照", 
     dict(R0, holding_max_days=40, max_ext_pct=0.02, **tp(0.20))),
    ("W3", "V1+评分门槛70", "只做评分≥70 的机会（评分从排序变成硬筛选）",
     dict(R0, holding_max_days=40, min_score=70, **tp(0.20))),
]


def main(argv):
    only = [a for a in argv[1:] if not a.startswith("--")]
    win_only = []
    for i, a in enumerate(argv):
        if a == "--win" and i + 1 < len(argv):
            win_only = argv[i + 1].split(",")
    rows = {}
    if os.path.exists(RESULT):
        try:
            rows = json.load(open(RESULT, encoding="utf-8")).get("rows", {})
        except Exception:
            rows = {}

    # PRE = 第三个窗口（20231010→20240930，240 日）：完全没参与过任何参数挑选，
    # 用于回答「V1/W2 那一族是真的还是一次网格搜索的运气」。
    for tag, d0, d1, desc in WINDOWS:
        if win_only and tag not in win_only:
            continue
        print("\n########## %s %s → %s  %s" % (tag, d0, d1, desc), flush=True)
        nav, ks, _ = V.bench(d0, d1)
        bret = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
        peak, mdd = nav[ks[0]], 0.0
        for k in ks:
            peak = max(peak, nav[k])
            mdd = min(mdd, nav[k] / peak - 1)
        print("等权全A基准：%+.2f%%  回撤 %.2f%%" % (bret, mdd * 100), flush=True)
        for cid, cname, note, over in CONFIGS:
            if only and cid not in only:
                continue
            for cost, ctag in COSTS:
                p = dict(SW.BASE_PARAMS)
                p.update(over)
                p["d0"], p["d1"] = d0, d1
                p["cost"] = cost
                job = "s%s_%s_%s" % (cid, tag, ctag)
                t0 = time.time()
                payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
                if err or not payload:
                    print("%-3s %-24s %-6s 失败：%s" % (cid, cname, ctag, err), flush=True)
                    continue
                m = V.metrics(payload, d0, d1, cost)
                m.update({"cid": cid, "name": cname, "note": note, "win_tag": tag,
                          "cost_tag": ctag, "d0": d0, "d1": d1,
                          "bench_ret": round(bret, 2), "bench_dd": round(mdd * 100, 2),
                          "ret_full": (round(m["ret"] / (m["avg_inv"] / 100.0), 2)
                                       if m["avg_inv"] else None),
                          "n_tp": payload["summary"].get("n_take_profit") or 0,
                          "n_time_stop": payload["summary"].get("n_time_stop") or 0,
                          "exit_why": {},
                          "secs": round(time.time() - t0, 1)})
                cnt = {}
                for t in payload["trades"]:
                    if t["side"] == "SELL":
                        cnt[t["how"]] = cnt.get(t["how"], 0) + 1
                m["exit_why"] = cnt
                rows[job] = m
                print("%-3s %-24s %-6s 组合 %+7.2f%%  回撤 %6.2f%%  Calmar %5s  "
                      "笔数 %4d  胜率 %5s  PF %5s  换手 %5.2f  均持 %5s  均仓 %5s  "
                      "笔均超额 %+7.1fbp t=%5s  TP %3d"
                      % (cid, cname, ctag, m["ret"], m["dd"], m["calmar"], m["n"],
                         m["win_rate"], m["pf"], m["turnover"], m["avg_hold"],
                         m["avg_inv"], m["ex_bp"] or 0, m["ex_t"], m["n_tp"]),
                      flush=True)
                json.dump({"rows": rows, "windows": V.WINDOWS},
                          open(RESULT, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)

    # ---------------- 并排 ----------------
    print("\n########## 评分修复 + 反抽退出（30bp）")
    print("%-3s %-24s %9s %9s %8s %8s %7s"
          % ("", "配置", "IN", "OOS", "IN笔超", "OOS笔超", "均持"))
    for cid, cname, _, _ in CONFIGS:
        a, b = rows.get("s%s_IN_30bp" % cid), rows.get("s%s_OOS_30bp" % cid)
        if not a or not b:
            continue
        print("%-3s %-24s %+8.2f%% %+8.2f%% %+7.1f %+8.1f %7s"
              % (cid, cname, a["ret"], b["ret"], a["ex_bp"] or 0, b["ex_bp"] or 0,
                 b["avg_hold"]))

    print("\n122bp（真实资金）：")
    print("%-3s %-24s %9s %9s %9s" % ("", "配置", "IN", "OOS", "两年累计"))
    for cid, cname, _, _ in CONFIGS:
        a, b = rows.get("s%s_IN_122bp" % cid), rows.get("s%s_OOS_122bp" % cid)
        if not a or not b:
            continue
        cum = ((1 + a["ret"] / 100) * (1 + b["ret"] / 100) - 1) * 100
        print("%-3s %-24s %+8.2f%% %+8.2f%% %+8.2f%%"
              % (cid, cname, a["ret"], b["ret"], cum))

    print("\n离场原因（OOS 30bp）：")
    for cid, cname, _, _ in CONFIGS:
        b = rows.get("s%s_OOS_30bp" % cid)
        if b:
            print("  %-3s %-24s %s" % (cid, cname, b["exit_why"]))

    print("\n产物：%s" % RESULT)


if __name__ == "__main__":
    main(sys.argv)
