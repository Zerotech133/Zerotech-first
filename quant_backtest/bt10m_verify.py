# -*- coding: utf-8 -*-
"""1 年数据二次验证：同一套规则在「已用过的这一年」与「另一整年」各跑一遍。

老田 2026-11-10：「我们再次验证1年的数据」。

为什么不是把原来的回测再跑一次——引擎是确定性的，同一窗口重跑必然一字不差，
那不叫验证，叫复现。真正的验证只有一件事：**换一年数据，看结论还在不在**。

  IN  = 20251013 → 20261012（242 交易日）前几轮全部结论都出自这一年
  OOS = 20241008 → 20251010（246 交易日）紧邻其前、同长度、零重叠

前几轮的参数（距上沿≤3%、关失效线、20 日强平、移动止损 5%/3.5%、老田分层加仓）
全部是在 IN 这一年里挑出来的，所以只有 OOS 能回答「这是规律还是拟合」。

口径：底仓参数与 bt10m_sweep.BASE_PARAMS 一致（三买 · 8% 槽 · regime_8050 ·
T+1 开盘成交 · 单边成本扣买卖各一次），只改窗口 / 规则 / 成本三样。

用法：
  python bt10m_verify.py                 # 两年全部配置
  python bt10m_verify.py D E             # 只跑指定配置（按编号）
  python bt10m_verify.py --in-only
产出：reports/bt10m_verify_1y.json（增量落盘）

⚠ 命名区分（2026-11-10）：本脚本是「换一年数据」的策略验证工具；
「执行口径/内核变更后的回归验证入口」是 bt10m_exec_verify.py（selftest +
固化 selfcheck + 7 腿 NAV 对表），两者用途不同，别拿错。
"""
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m  # noqa: E402
import bt10m_sweep as SW  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports")
RESULT = os.path.join(OUT, "bt10m_verify_1y.json")

CAPITAL = 10_000_000.0

WINDOWS = [
    ("IN", "20251013", "20261012", "已用年（样本内·参数就是从这年挑的）"),
    ("OOS", "20241008", "20251010", "样本外年（紧邻其前·同长度·零重叠）"),
]

COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]

# 老田 11-09 方案的仓位口径：5 只 × 首笔 100 万、总额 1000 万、不按 80/50/30 压帽
FIVE = {"top_n": 5, "slot_pct": 0.10, "slot_mode": "fixed",
        "cap_mode": "fixed", "cap_fixed": 1.0}

CONFIGS = [
    ("A", "基准三买", "现状口径：跌破中枢上沿次日走·无最短持有期", {}),
    ("B", "关失效线+20日", "关掉失效线，只靠 20 日强平", {
        "three_fail_exit": False, "holding_max_days": 20}),
    ("C", "距上沿≤3%+关+20日", "把买点从追涨挪回回抽（缠论三买原意）", {
        "max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20}),
    ("D", "C+移动止损5%/3.5%", "上一轮的头号候选（+11.45% 那个）", {
        "max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20,
        "trail_activate": 0.05, "trail_drawdown": 0.035}),
    ("E", "老田分层加仓", "5只×100万 + 涨5%加60/40万（10日窗）+ 同 D 的卖出规则",
     dict(FIVE, ladder_mode="up", ladder_step=0.05, ladder_window=10,
          max_ext_pct=0.03, three_fail_exit=False, holding_max_days=20,
          trail_activate=0.05, trail_drawdown=0.035)),
    ("F", "只首笔5只×100万", "E 的对照：同样选 5 只，永不加仓（50% 资金闲置）",
     dict(FIVE, max_ext_pct=0.03, three_fail_exit=False, holding_max_days=20,
          trail_activate=0.05, trail_drawdown=0.035)),
]

_BENCH = {}


def bench(d0, d1):
    k = (d0, d1)
    if k not in _BENCH:
        nav, n = bt10m.load_market_bench(d0, d1)
        ks = sorted(nav)
        _BENCH[k] = (nav, ks, n)
    return _BENCH[k]


def bench_ret(d0, d1, a, b):
    """买入日 a → 卖出日 b 的等权全A收益（%）。"""
    nav, ks, _ = bench(d0, d1)
    lo = [k for k in ks if k <= a]
    hi = [k for k in ks if k <= b]
    if not lo or not hi:
        return None
    return (nav[hi[-1]] / nav[lo[-1]] - 1) * 100


def quarterly(eq):
    """按自然季度切净值曲线，出逐季收益。"""
    q = {}
    for e in eq:
        q.setdefault(e["day"][:4] + "Q" + str((int(e["day"][4:6]) - 1) // 3 + 1), []).append(e)
    out = []
    for k in sorted(q):
        rows = q[k]
        out.append({"q": k, "ret_pct": round((rows[-1]["equity"] / rows[0]["equity"] - 1) * 100, 2),
                    "n_days": len(rows)})
    return out


def metrics(payload, d0, d1, cost):
    s = payload["summary"]
    eq = payload["equity_curve"]
    days = payload["days"]
    trades = payload["trades"]

    turnover = 0.0
    for rec in days.values():
        for a in rec.get("actions") or []:
            if a.get("type") == "buy":
                m = re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                if m:
                    turnover += float(m.group(1)) / 100.0

    # FIFO 配对 → 持仓日数 + 笔均超额
    idx = {e["day"]: i for i, e in enumerate(eq)}
    q, pairs = {}, []
    for t in trades:
        st = t["stock"]
        if t["side"] == "BUY":
            q.setdefault(st, []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(st)
            if lst:
                pairs.append((st, lst.pop(0), t["day"], t.get("pnl")))

    holds, ex = [], []
    for st, bd, sd, pnl in pairs:
        if bd in idx and sd in idx:
            holds.append(idx[sd] - idx[bd])
        if pnl is None:
            continue
        br = bench_ret(d0, d1, bd, sd)
        if br is not None:
            ex.append(float(pnl) - br)

    t_val = 0.0
    if len(ex) > 1:
        sd_ = statistics.stdev(ex)
        if sd_:
            t_val = statistics.mean(ex) / (sd_ / math.sqrt(len(ex)))

    end = CAPITAL * eq[-1]["equity"]
    ret = (end / CAPITAL - 1) * 100
    dd = s["max_drawdown_pct"]
    return {
        "ret": round(ret, 2),
        "dd": round(dd, 2),
        "calmar": round(ret / abs(dd), 2) if dd else None,
        "n": len(pairs),
        "win_rate": s.get("win_rate_pct"),
        "pf": s.get("profit_factor"),
        "turnover": round(turnover, 2),
        "cost_pct": round(turnover * cost * 2 * 100, 2),
        "avg_inv": s.get("avg_invested_pct"),
        "avg_hold": round(statistics.mean(holds), 1) if holds else None,
        "med_hold": statistics.median(holds) if holds else None,
        "ex_bp": round(statistics.mean(ex) * 100, 1) if ex else None,
        "ex_t": round(t_val, 2),
        "n_add": (s.get("n_add2") or 0) + (s.get("n_add3") or 0),
        "quarterly": quarterly(eq),
    }


def main(argv):
    only = [a for a in argv[1:] if not a.startswith("--")]
    in_only = "--in-only" in argv

    rows = {}
    if os.path.exists(RESULT):
        try:
            rows = json.load(open(RESULT, encoding="utf-8")).get("rows", {})
        except Exception:
            rows = {}

    wins = [w for w in WINDOWS if not (in_only and w[0] == "OOS")]
    for tag, d0, d1, desc in wins:
        print("\n########## %s %s → %s  %s" % (tag, d0, d1, desc), flush=True)
        bx = bench(d0, d1)
        nav, ks, nf = bx
        bret = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
        peak, mdd = nav[ks[0]], 0.0
        for k in ks:
            peak = max(peak, nav[k])
            mdd = min(mdd, nav[k] / peak - 1)
        print("等权全A基准：%+.2f%%  回撤 %.2f%%  %d 日  %d 只文件"
              % (bret, mdd * 100, len(ks), nf), flush=True)
        for cid, cname, note, over in CONFIGS:
            if only and cid not in only:
                continue
            for cost, ctag in COSTS:
                p = dict(SW.BASE_PARAMS)
                p.update(over)
                p["d0"], p["d1"] = d0, d1
                p["cost"] = cost
                job = "v%s_%s_%s" % (cid, tag, ctag)
                t0 = time.time()
                payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
                if err or not payload:
                    print("%-3s %-18s %-6s 失败：%s" % (cid, cname, ctag, err), flush=True)
                    continue
                m = metrics(payload, d0, d1, cost)
                m.update({"cid": cid, "name": cname, "note": note, "win_tag": tag,
                          "cost_tag": ctag, "d0": d0, "d1": d1,
                          "bench_ret": round(bret, 2), "bench_dd": round(mdd * 100, 2),
                          "ret_full": (round(m["ret"] / (m["avg_inv"] / 100.0), 2)
                                       if m["avg_inv"] else None),
                          "secs": round(time.time() - t0, 1)})
                rows[job] = m
                print("%-3s %-18s %-6s 组合 %+7.2f%%  回撤 %6.2f%%  Calmar %5s  "
                      "笔数 %4d  胜率 %5s  PF %5s  换手 %5.2f  成本 %5.2f%%  "
                      "均持 %5s  均仓 %5s  笔均超额 %+7.1fbp t=%5s  满仓等价 %+7.2f%%"
                      % (cid, cname, ctag, m["ret"], m["dd"], m["calmar"], m["n"],
                         m["win_rate"],
                         m["pf"], m["turnover"], m["cost_pct"],
                         m["avg_hold"], m["avg_inv"], m["ex_bp"] or 0, m["ex_t"],
                         m["ret_full"] or 0),
                      flush=True)
                json.dump({"rows": rows, "windows": WINDOWS},
                          open(RESULT, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)

    # ---------- 两年对照 ----------
    print("\n########## 同一规则 · 两年并排（30bp 口径）")
    print("「满仓等价」= 组合收益 ÷ 平均仓位，用来和满仓的等权基准同尺度比；")
    print("「笔均超额」= 每笔收益 − 该笔持有期等权全A收益，是唯一剔除仓位与大盘的选股 alpha。")
    print("%-3s %-18s %10s %10s %10s %10s %10s"
          % ("", "配置", "IN 组合", "IN 满仓等价", "OOS 组合", "OOS 满仓等价", "OOS 基准"))
    for cid, cname, _, _ in CONFIGS:
        a = rows.get("v%s_IN_30bp" % cid)
        b = rows.get("v%s_OOS_30bp" % cid)
        if not a or not b:
            continue
        print("%-3s %-18s %+9.2f%% %+11.2f%% %+9.2f%% %+11.2f%% %+9.2f%%"
              % (cid, cname, a["ret"], a["ret_full"] or 0, b["ret"],
                 b["ret_full"] or 0, b["bench_ret"]))

    print("\n选股 alpha（笔均超额 · 唯一跨年可比的量）：")
    print("%-3s %-18s %10s %8s %10s %8s %8s" %
          ("", "配置", "IN bp", "t", "OOS bp", "t", "结论"))
    for cid, cname, _, _ in CONFIGS:
        a = rows.get("v%s_IN_30bp" % cid)
        b = rows.get("v%s_OOS_30bp" % cid)
        if not a or not b:
            continue
        sig = (abs(a["ex_t"]) >= 2) + (abs(b["ex_t"]) >= 2)
        if sig == 0:
            verdict = "两年都不显著"
        elif (a["ex_bp"] or 0) * (b["ex_bp"] or 0) > 0:
            verdict = "同向"
        else:
            verdict = "翻转"
        print("%-3s %-18s %+9.1f %8.2f %+9.1f %8.2f %8s"
              % (cid, cname, a["ex_bp"] or 0, a["ex_t"], b["ex_bp"] or 0,
                 b["ex_t"], verdict))

    # 逐季分解（只对头号候选 D，两年并排看收益是否集中在某一季）
    print("\nD（距上沿≤3%+关失效线+20日+移动止损）逐季收益 30bp：")
    for tag in ("IN", "OOS"):
        r = rows.get("vD_%s_30bp" % tag)
        if r:
            print("  %-4s %s" % (tag, "  ".join("%s %+.1f%%" % (q["q"], q["ret_pct"])
                                                for q in r["quarterly"])))

    print("\n产物：%s" % RESULT)


if __name__ == "__main__":
    main(sys.argv)
