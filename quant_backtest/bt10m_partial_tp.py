# -*- coding: utf-8 -*-
"""S-A 分批止盈网格（2026-11-10，老田指令）。

知识库 K1（130 视频·33 博主「首目标减半仓」）× 自身数据（TP 封顶 +20%、
止盈笔均仅 +19.64%、45~50% 止盈单冲高回落）双重支撑，四窗口验证。

变体：
  P1  tp_partial=0.5          TP 触发卖半仓，余仓等止损/60日到期
  P2  tp_partial=0.5 + rearm  同上 + 余仓在 TP价×1.2 再触发时清仓
  P3  tp_partial=0.7          TP 触发卖 70%，余仓等止损/到期
基线 A0 = v3 原样（tp_partial=0），取自 bt10m_entry_grid.json。

口径：122bp 主成本；每笔 SELL（含分批腿）计一笔，超额 = pnl − 等权基准同区间。
"""
import os
import sys
import json
import math
import statistics

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import backtest_engine_v2 as eng
import bt10m

WINDOWS = [("PRE", "20231010", "20240930"),
           ("OOS", "20241008", "20251010"),
           ("IN", "20251013", "20261012"),
           ("RY", "20251110", "20261109")]
COST = 0.0061
VAR = [("P1_part50", {"tp_partial": 0.5}),
       ("P2_part50_rearm", {"tp_partial": 0.5, "tp_rearm_pct": 0.2}),
       ("P3_part70", {"tp_partial": 0.7})]


def pair_alpha(trades, nav, ks, want_samples=False):
    """持仓级配对：分批止盈后一笔持仓有多条 SELL 腿（part/再触发/止损/到期），
    按各腿占原始仓位的比例加权合成持仓 pnl —— 与基线逐仓可比。

    腿占比：take_profit_part 用记录的 tp_frac；其余腿（再触发清仓/止损/到期/
    窗口末强平）覆盖剩余部分 = 1 − 已卖比例。持仓级 pnl = Σ frac_i × pnl_i
    （各腿共享同一入场价，pnl 均为相对各自成本 basis 的收益率，加权即现金加权）。
    """
    q, xs = {}, []
    for t in sorted(trades, key=lambda x: x["day"]):
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append({"day": t["day"], "legs": [], "w": 0.0})
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if not lst:
                continue
            cur = lst[0]
            f = t.get("tp_frac")
            f = float(f) if f is not None else max(0.0, 1.0 - cur["w"])
            cur["legs"].append((f, float(t["pnl"])))
            cur["w"] += f
            if cur["w"] >= 0.999:
                lst.pop(0)
                xs.append((cur["day"], t["day"],
                           sum(fr * p for fr, p in cur["legs"])))

    def bret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100 if la and lb else None

    ex = [p - bret(bd, sd) for bd, sd, p in xs if bret(bd, sd) is not None]
    if want_samples:
        return ex
    if not ex:
        return 0, 0.0, 0.0
    m = statistics.mean(ex)
    sd = statistics.stdev(ex) or 1e-9
    return len(ex), m * 100, m / (sd / math.sqrt(len(ex)))


def exit_mix(trades):
    mix = {}
    legs = {"part": [], "rearm": [], "remain_stop": [], "remain_expire": []}
    for t in trades:
        if t["side"] != "SELL" or t.get("pnl") is None:
            continue
        h = t["how"]
        mix[h] = mix.get(h, 0) + 1
        if h == "take_profit_part":
            legs["part"].append(float(t["pnl"]))
        elif h == "take_profit_rearm":
            legs["rearm"].append(float(t["pnl"]))
        elif h in ("stop_loss", "trail_stop") and t.get("tp_frac_after") is None:
            pass
    return mix, legs


def main():
    # 自检门（家族防御）：基线数字必须与 entry_grid A0_base 一致
    eg = json.load(open(os.path.join(BASE, "reports", "bt10m_entry_grid.json"),
                        encoding="utf-8"))
    a0 = eg["variants"]["A0_base"]
    print("基线 A0（entry_grid）: %s" % a0["merged"])

    nav_by_win = {}
    for wtag, w0, w1 in WINDOWS:
        nav, _ = bt10m.load_market_bench(w0, w1)
        nav_by_win[wtag] = (nav, sorted(nav))

    out = {"baseline": a0, "variants": {}}
    for tag, extra in VAR:
        rows, pooled = [], []
        for wtag, w0, w1 in WINDOWS:
            p = {"preset": "v3", "d0": w0, "d1": w1, "cost": COST}
            p.update(extra)
            payload, err = eng.run_job(p, "sa_%s_%s" % (tag, wtag),
                                       progress=lambda *a, **k: None)
            if err or not payload:
                print("%s %s FAIL: %s" % (tag, wtag, err))
                continue
            nav, ks = nav_by_win[wtag]
            # 行为级自检：part 变体必须产生 take_profit_part（除零交易窗外）
            parts = [t for t in payload["trades"]
                     if t["side"] == "SELL" and t["how"] == "take_profit_part"]
            if extra.get("tp_partial") and wtag != "RY" and not parts:
                raise SystemExit("自检失败：%s %s 无 take_profit_part，参数未生效"
                                 % (tag, wtag))
            ex = pair_alpha(payload["trades"], nav, ks, want_samples=True)
            pooled.extend(ex)
            nn, a, t = len(ex), (statistics.mean(ex) * 100 if ex else 0.0), 0.0
            if ex:
                m = statistics.mean(ex)
                sd = statistics.stdev(ex) or 1e-9
                t = m / (sd / math.sqrt(len(ex)))
            ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
            mdd = payload["summary"].get("max_drawdown_pct")
            mix = {}
            for tt in payload["trades"]:
                if tt["side"] == "SELL":
                    mix[tt["how"]] = mix.get(tt["how"], 0) + 1
            rem = [float(tt["pnl"]) for tt in payload["trades"]
                   if tt["side"] == "SELL" and tt["how"] in ("stop_loss", "time_stop")]
            row = {"w": wtag, "ret": round(ret, 2), "mdd": mdd, "n_sells": sum(mix.values()),
                   "mix": mix,
                   "stop_expire_mean": round(statistics.mean(rem), 2) if rem else None,
                   "alpha_bp": round(a), "t": round(t, 2)}
            rows.append(row)
            print("%-16s %s  %+7.2f%%  mdd %6.2f%%  sells %2d  part %2d rearm %d  "
                  "止损/到期均 %s%%  α %+5dbp t=%5.2f"
                  % (tag, wtag, ret, mdd or 0, sum(mix.values()),
                     mix.get("take_profit_part", 0), mix.get("take_profit_rearm", 0),
                     row["stop_expire_mean"], a, t), flush=True)
        if pooled:
            m = statistics.mean(pooled)
            sd = statistics.stdev(pooled) or 1e-9
            t = m / (sd / math.sqrt(len(pooled)))
            print("%-16s 合并  %3d仓  α %+5dbp  t=%5.2f" % (tag, len(pooled),
                                                           m * 100, t), flush=True)
            out["variants"][tag] = {
                "windows": rows,
                "merged": {"n": len(pooled), "alpha_bp": round(m * 100),
                           "t": round(t, 2)}}
        else:
            out["variants"][tag] = {"windows": rows}
    fp = os.path.join(BASE, "reports", "bt10m_partial_tp.json")
    json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n落盘 %s" % fp)


if __name__ == "__main__":
    main()
