# -*- coding: utf-8 -*-
"""V2 固化之后的三个问题：门槛还要多严、high 档能提多高、限价单假设有多乐观。

老田 2026-11-10 第三条指令：
  1) 位置权重 40/30/30 → 55/25/20，在放开门槛的设定下再测一次
  2) 提高 high 档仓位（X/Y 组失败是因为弱市也满仓，需要更细分档）
  3) 确认限价单实盘成交价与回测无系统性偏差（影子对照的可量化前置）

三组正交，各自只动一件事：
  W 组：门槛网格（0 / 2% / 3% / 5% / 8%）——位置分吃饱权重后，硬门槛还需不需要
  B 组：三档仓位（high 80/90/100 × mid 50/60/70 × low 20/30），slot 同步放到 10%
        否则 cap 提高是空转（10 槽 × 8% = 80% 封顶，cap=100% 永远撞不到）
  T 组：五档仓位（cap_mode=regime_tiers），high 内部按「超 MA20 多少」再切三档
  F 组：成交口径（limit / close / gap_only / slip 30bp）——同参数只换止盈成交假设

窗口三个：PRE(2023-10→2024-09) / IN(2025-10→2026-10) / OOS(2024-10→2025-10)。
PRE 至今没参与过任何挑参，是本轮唯一诚实的裁判。

用法：python bt10m_v3.py [W1 B100 ...] [--win PRE,OOS]
产出：reports/bt10m_v3.json（增量落盘）
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
RESULT = os.path.join(BASE, "reports", "bt10m_v3.json")
COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]

WINDOWS = list(V.WINDOWS) + [
    ("PRE", "20231010", "20240930", "第三窗口（从未参与挑参·240 日·零重叠）"),
]

# 所有组都从 V2 预设起——这就是「固化」的直接体现：
# 脚本里不再手抄 tp_pct / holding_max_days / stop_pct，全从引擎预设继承。
#
# ⚠️ 底必须用引擎 DEFAULTS，不能用 SW.BASE_PARAMS。
# BASE_PARAMS 是「降换手实验」那一轮的历史基线，它显式带着
# three_fail_exit=True / holding_max_days=0 —— 按「显式参数覆盖预设」的语义，
# 会把 V2 的「关失效线 + 60 日到期」整体抹掉。本轮冒烟时正是这样跑出
# PRE −3.51%（真值 +9.99%）、均持 3.3 日、n_tp 仅 7 笔，才抓到这个坑。
# 需要的 top_n=10 / slot_pct=0.08 / buy_filter=3 / regime_8050 在 DEFAULTS 里
# 本来就是同值，所以底只需要一个 preset。
BASE_P = {"preset": "v2"}

# 启动自检：把「以为传了」和「实际生效」当众对一遍。参数静默错配是这个项目
# 复发率最高的 bug（cost 漏传 / BUY_FILTERS 缺键 / _r 吞字符串 / 落盘对象不一致），
# 每次跑批前打印关键字段，比事后解释便宜得多。
_EXPECT_V2 = {
    "tp_pct": 0.20,
    "holding_max_days": 60,
    "stop_pct": -0.08,
    "max_ext_pct": 0.03,
    "three_fail_exit": False,
    "trail_activate": 0.0,
}


def _selfcheck():
    p = dict(BASE_P)
    p.update({"d0": "20251013", "d1": "20261012", "cost": 0.0015})
    eff = eng.apply_preset(p)
    bad = []
    for k, v in _EXPECT_V2.items():
        got = eff.get(k)
        if isinstance(v, float):
            if got is None or abs(float(got) - v) > 1e-9:
                bad.append("%s=%r(期望 %r)" % (k, got, v))
        elif got != v:
            bad.append("%s=%r(期望 %r)" % (k, got, v))
    if bad:
        raise SystemExit("预设没有真正生效，拒绝跑批：" + "；".join(bad))
    print("[自检] V2 预设生效：TP %.0f%% · 到期 %d 日 · 止损 %.0f%% · 追高门槛 %.0f%% · 失效线 %s"
          % (eff["tp_pct"] * 100, eff["holding_max_days"], eff["stop_pct"] * 100,
             eff["max_ext_pct"] * 100,
             "关" if eff["three_fail_exit"] is False else "开"), flush=True)

# 仓位组共用：10 槽 × 10% = 理论上限 100%，否则 cap 提到 100% 也撞不到
SLOT = {"top_n": 10, "slot_pct": 0.10, "slot_mode": "fixed"}
TIERS = "regime_tiers"

CONFIGS = [
    # ---------- W：门槛网格（新权重 55/25/20 下再问一次「要不要硬门槛」）----------
    ("W0", "V2 原样·门槛3%", "基准：位置权重已提到 55，唯一变量是评分口径", {}),
    ("W1", "门槛 0（全放开）", "完全由评分排序挑，不看位置", {"max_ext_pct": 0.0}),
    ("W2", "门槛 2%", "比 V2 更紧一档", {"max_ext_pct": 0.02}),
    ("W3", "门槛 5%", "比 V2 松一档", {"max_ext_pct": 0.05}),
    ("W4", "门槛 8%", "大幅放开", {"max_ext_pct": 0.08}),

    # ---------- B：三档仓位（high 提到 90/100，mid/low 分档试）----------
    ("B80", "high80·mid50·low30", "slot 放到 10%、cap 不动 → 看「只放槽不放帽」", dict(SLOT)),
    ("B90", "high90·mid50·low30", "high 提一档", dict(SLOT, cap_high=0.90)),
    ("B100", "high100·mid50·low30", "high 满仓，弱市仍 50/30", dict(SLOT, cap_high=1.00)),
    ("B100m60", "high100·mid60·low30", "mid 也提一点", dict(SLOT, cap_high=1.00, cap_mid=0.60)),
    ("B100m70", "high100·mid70·low30", "mid 提到 70", dict(SLOT, cap_high=1.00, cap_mid=0.70)),
    ("B100l20", "high100·mid50·low20", "弱市压更狠：low 降到 20", dict(SLOT, cap_high=1.00, cap_low=0.20)),
    ("B100m70l20", "high100·mid70·low20", "两头都拉开：强档满仓、弱档 20", dict(SLOT, cap_high=1.00, cap_mid=0.70, cap_low=0.20)),

    # ---------- 固化候选（2026-11-10 决赛）----------
    # v3 = B100l20 仓位 + 止盈成交按目标价打 30bp 折（影子诊断：45~50% 触价单冲高回落，
    # limit 乐观假设三年虚增 ~3.7pp；slip 30bp 是实测折价的下沿保守化）。
    ("Z1", "v3候选 B100l20+slip30",
     "若三年累计 ≥ B100l20（乐观口径）且三窗口全正，即作为 DEFAULT_PRESET=v3 固化",
     dict(SLOT, cap_high=1.00, cap_low=0.20, tp_fill_mode="slip", tp_slip=0.003)),
    ("Z2", "v3候选 B100l20+slip60",
     "更保守的 60bp 折价成交对照", 
     dict(SLOT, cap_high=1.00, cap_low=0.20, tp_fill_mode="slip", tp_slip=0.006)),

    # ---------- T：五档（high 内部再切三刀）----------
    ("T1", "五档 20/35/50/80/100", "tier0~4 线性；tier4=高出MA20 5% 以上才满仓", dict(
        SLOT, cap_mode=TIERS, cap_tiers=(0.20, 0.35, 0.50, 0.80, 1.00))),
    ("T2", "五档 20/35/50/85/100", "中强档 85", dict(
        SLOT, cap_mode=TIERS, cap_tiers=(0.20, 0.35, 0.50, 0.85, 1.00))),
    ("T3", "五档 30/45/60/90/100", "整体抬一档（弱市也别太空）", dict(
        SLOT, cap_mode=TIERS, cap_tiers=(0.30, 0.45, 0.60, 0.90, 1.00))),
    ("T4", "五档 15/30/50/80/100", "弱档压到 15", dict(
        SLOT, cap_mode=TIERS, cap_tiers=(0.15, 0.30, 0.50, 0.80, 1.00))),

    # ---------- F：成交口径（同参数，只换止盈成交假设）----------
    ("F1", "limit 乐观（现状）", "触价=按目标价成交，开盘跳空则按开盘", {}),
    ("F2", "close 保守", "触价日按收盘价；收盘回到目标下方=挂单没成交、尾盘了结",
     {"tp_fill_mode": "close"}),
    ("F3", "gap_only 最保守", "只有开盘就 ≥ 目标价才算成交，盘中触价一律视为没成交",
     {"tp_fill_mode": "gap_only"}),
    ("F4", "slip 30bp", "按目标价打 30bp 折扣成交", {"tp_fill_mode": "slip", "tp_slip": 0.003}),
    ("F5", "slip 60bp", "按目标价打 60bp 折扣成交", {"tp_fill_mode": "slip", "tp_slip": 0.006}),
]


def main(argv):
    only = [a for a in argv[1:] if not a.startswith("--")]
    win_only = []
    for i, a in enumerate(argv):
        if a == "--win" and i + 1 < len(argv):
            win_only = argv[i + 1].split(",")

    _selfcheck()
    rows = {}
    if os.path.exists(RESULT):
        try:
            rows = json.load(open(RESULT, encoding="utf-8")).get("rows", {})
        except Exception:
            rows = {}

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
                p = dict(BASE_P)
                p.update(over)
                p["d0"], p["d1"] = d0, d1
                p["cost"] = cost
                job = "v%s_%s_%s" % (cid, tag, ctag)
                if job in rows and not only:  # 断点续跑：引擎确定性，同参同果
                    print("%-3s %-22s %-6s （已有，跳过）" % (cid, cname, ctag),
                          flush=True)
                    continue
                t0 = time.time()
                payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
                if err or not payload:
                    print("%-3s %-22s %-6s ERROR %s" % (cid, cname, ctag, err), flush=True)
                    continue
                m = V.metrics(payload, d0, d1, cost)
                inv = m["avg_inv"]
                m.update({
                    "cid": cid, "name": cname, "note": note, "cost_tag": ctag,
                    "d0": d0, "d1": d1,
                    "bench_ret": round(bret, 2), "bench_dd": round(mdd * 100, 2),
                    "ret_full": (round(m["ret"] / (inv / 100.0), 2)
                                 if inv and inv > 0 else None),
                    "secs": round(time.time() - t0, 1),
                })
                rows[job] = m
                print("%-3s %-22s %-6s 组合 %+7.2f%%  满仓等价 %+7.2f%%  回撤 %6.2f%%  "
                      "Calmar %5s  笔数 %4d  胜率 %5s  换手 %5.2f  均持 %5s  均仓 %5s%%  "
                      "笔均超额 %+7.1fbp t=%5s  n_tp %4s"
                      % (cid, cname, ctag, m["ret"], m["ret_full"] or 0, m["dd"],
                         m["calmar"], m["n"],
                         m["win_rate"] if m["win_rate"] is not None else "—",
                         m["turnover"], m["avg_hold"],
                         round(inv, 1) if inv is not None else "—",
                         m["ex_bp"] or 0, m["ex_t"],
                         (payload["summary"].get("n_take_profit") or 0)),
                      flush=True)
                os.makedirs(os.path.dirname(RESULT), exist_ok=True)
                json.dump({"rows": rows}, open(RESULT, "w", encoding="utf-8"),
                          ensure_ascii=False)

    # ---------- 三窗口并排（122bp 真实资金口径）----------
    print("\n########## 三窗口并排 · 122bp（PRE 未参与挑参，是唯一诚实的裁判）")
    print("%-6s %-22s %9s %9s %9s %10s %8s %8s"
          % ("", "配置", "PRE", "IN", "OOS", "三年累计", "均仓OOS", "笔均OOS"))
    for cid, cname, _, _ in CONFIGS:
        a = rows.get("v%s_PRE_122bp" % cid)
        b = rows.get("v%s_IN_122bp" % cid)
        c = rows.get("v%s_OOS_122bp" % cid)
        if not (a and b and c):
            continue
        cum = ((1 + a["ret"] / 100) * (1 + b["ret"] / 100) * (1 + c["ret"] / 100) - 1) * 100
        print("%-6s %-22s %+8.2f%% %+8.2f%% %+8.2f%% %+9.2f%% %7.1f%% %+7.1fbp"
              % (cid, cname, a["ret"], b["ret"], c["ret"], cum,
                 c["avg_inv"] or 0, c["ex_bp"] or 0))

    print("\n产物：%s" % RESULT)


if __name__ == "__main__":
    main(sys.argv)
