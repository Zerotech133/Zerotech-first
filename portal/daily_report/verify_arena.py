# -*- coding: utf-8 -*-
"""arena 正确性校验：不靠自洽，靠外部锚点。

三把尺子：
  A) S1 收益 vs cohort_track 的已结算 1 日 ret_mean（两者独立实现、同一批票）
  B) 抽查成交价的复权换算是否等于真实价
  C) 止盈/止损笔数是否由价格本身造成，而不是规则写错
"""
import json
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_arena as PA  # noqa: E402

COH = os.path.join(HERE, "cohorts")


def load_settle():
    rows = []
    fp = os.path.join(COH, "settlements.jsonl")
    for l in io.open(fp, encoding="utf-8"):
        l = l.strip()
        if l:
            rows.append(json.loads(l))
    return rows


def main():
    r = json.load(io.open(os.path.join(HERE, "arena", "latest.json"), encoding="utf-8"))
    print("=== A) S0 对账口径 vs cohort 已结算 1 日收益 ===")
    print("（S0 = 开盘买当日收盘卖，与 cohort 的「1 日」同口径；arena 扣 0.3% 费）")
    fp = os.path.join(HERE, "arena", "S0", "trades.jsonl")
    if not os.path.exists(fp):
        print("S0 账本不存在，跳过")
        t0 = []
    else:
        t0 = [json.loads(l) for l in io.open(fp, encoding="utf-8") if l.strip()]
    by_sig = {}
    for x in t0:
        by_sig.setdefault(x["sig"], []).append(x["ret"])

    per_cohort = {}
    for s in r["signals"]:
        f = os.path.join(COH, "cohort_%s_%s.json" % (s["asof"], s["pool"]))
        if not os.path.exists(f):
            continue
        c = json.load(io.open(f, encoding="utf-8"))
        st = c.get("settled", {}).get("1")
        if st:
            per_cohort[s["key"]] = st["ret_mean"]

    print("%-16s %10s %10s %12s %6s" % ("信号", "cohort均", "arena(含费)", "差", "票数"))
    diffs = []
    for key in sorted(per_cohort):
        base = per_cohort[key]
        if key not in by_sig:
            print("%-16s %9.4f%% %10s" % (key, base * 100, "未成交"))
            continue
        mine = sum(by_sig[key]) / len(by_sig[key])
        d = mine - base
        diffs.append(d)
        print("%-16s %9.4f%% %9.4f%% %11.4f%% %6d"
              % (key, base * 100, mine * 100, d * 100, len(by_sig[key])))
    if diffs:
        avg = sum(diffs) / len(diffs)
        print("\n平均差 = %.4f%%（理论 -0.30%%，容差 ±0.10pp）" % (avg * 100))
        print("判定:", "通过" if abs(avg + 0.0030) <= 0.0010 else "不通过 —— 需查复权或成本口径")

    print("\n=== B) 抽查成交价的复权换算 ===")
    fp = os.path.join(HERE, "arena", "S1", "trades.jsonl")
    rows = [json.loads(l) for l in io.open(fp, encoding="utf-8") if l.strip()]
    px = PA.Px()
    bad = 0
    for x in rows[:3]:
        raw_b = px.day(x["entry_date"]).get(x["code"], {})
        raw_s = px.day(x["exit_date"]).get(x["code"], {})
        adj_b = px.price(x["entry_date"], x["code"], "open")
        adj_s = px.price(x["exit_date"], x["code"], "close")
        print("%s  买 %s 原始open=%.2f 复权=%.4f | 卖 %s 原始close=%.2f 复权=%.4f | 账本 %.4f→%.4f"
              % (x["code"], x["entry_date"], raw_b.get("open", 0), adj_b or 0,
                 x["exit_date"], raw_s.get("close", 0), adj_s or 0,
                 x["entry_px"], x["exit_px"]))
        if adj_b is None or adj_s is None:
            bad += 1
            print("   ⚠ 复权价缺失")
            continue
        if abs(adj_b - x["entry_px"]) > 1e-3 or abs(adj_s - x["exit_px"]) > 1e-3:
            bad += 1
            print("   ⚠ 与账本不一致")
        # 账本收益应等于 复权比 × (1-费)^2 - 1
        exp = (adj_s / adj_b) * (1 - PA.COST_ONE) ** 2 - 1
        if abs(exp - x["ret"]) > 1e-5:
            bad += 1
            print("   ⚠ 收益口径不符：期望 %.6f 账本 %.6f" % (exp, x["ret"]))
    print("抽查 3 笔，异常 %d 笔 → %s" % (bad, "通过" if bad == 0 else "不通过"))

    print("\n=== C) 止盈/止损是否由价格造成 ===")
    for sid, rule in (("S5T", "take_profit"), ("S5L", "stop_loss")):
        fp = os.path.join(HERE, "arena", sid, "trades.jsonl")
        if not os.path.exists(fp):
            continue
        tr = [json.loads(l) for l in io.open(fp, encoding="utf-8") if l.strip()]
        th = PA.STRAT_BY_ID[sid].get(rule)
        viol = []
        for x in tr:
            # 毛收益（复权、不含费）
            gross = (x["exit_px"] / x["entry_px"]) - 1.0
            if rule == "take_profit" and gross < th - 1e-6:
                viol.append((x["code"], round(gross, 4)))
            if rule == "stop_loss" and gross > -th + 1e-6:
                viol.append((x["code"], round(gross, 4)))
        print("%s 阈值 %.0f%%  成交 %d 笔  违反阈值 %d 笔 %s"
              % (sid, th * 100, len(tr), len(viol), viol[:3] if viol else ""))
        if tr:
            gs = sorted((x["exit_px"] / x["entry_px"] - 1) for x in tr)
            print("   毛收益区间 %.2f%% ~ %.2f%%（中位 %.2f%%）"
                  % (gs[0] * 100, gs[-1] * 100, gs[len(gs) // 2] * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
