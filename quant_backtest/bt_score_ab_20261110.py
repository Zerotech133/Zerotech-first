# -*- coding: utf-8 -*-
"""评分口径 A/B 复验（2026-11-10 三买口径修正落地后的固化 v1 证据）。

背景：market_scan.score_3buy 位置分基准 close/zg-1 → 回踩笔低点 low/zg-1，
背驰分加 bei24 第三源。信号层零变化（13635 键集逐条一致），但评分分布变化
（均值 54.1→58.2，top100 重合 41/100），而引擎「评分降序填槽」——评分变化
会实质改变入选次序，须按固化 v1 规则出对照证据。

方法：同参数（bt10m_sweep.BASE_PARAMS + ext03_m20_tr 当前最优卖出规则）、
同窗口（20251013-20261012）、同成本（30bp/122bp 两档），只换事件缓存：
- new = data/sig_events/ 主缓存（重扫后，low 基准）
- old = data/sig_events/bak_20261110_pre_beichi24/ 同名备份（重扫前，close 基准）
swap 期间独占（深夜无流水线），跑完立即换回并复核指纹。

产物：data/bt2/bt2_abscore_*.json（bt10m_align.metrics 可折算笔均超额+t）
+ reports/score_ab_20261110.json 汇总。
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CACHE_NAME = "sig_events_market_v3_20251013_20261012.json"
CACHE_FP = os.path.join(HERE, "data", "sig_events", CACHE_NAME)
BAK_FP = os.path.join(
    HERE, "data", "sig_events", "bak_20261110_pre_beichi24", CACHE_NAME
)
OUT_FP = os.path.join(HERE, "reports", "score_ab_20261110.json")

COST_30 = 0.0015   # 30bp 往返（单边 15bp）
COST_122 = 0.0061  # 122bp 往返（单边 61bp）

# 当前最优卖出规则（bt10m_sweep.VARIANTS 的 ext03_m20_tr）
OVER_EXT03_M20_TR = {
    "max_ext_pct": 0.03,
    "three_fail_exit": False,
    "holding_max_days": 20,
    "trail_activate": 0.05,
    "trail_drawdown": 0.035,
}


def _run(tag, cost):
    """跑一组 run_job，返回摘要 dict。job 产物落 data/bt2/bt2_abscore_*.json。"""
    import backtest_engine_v2 as eng
    import bt10m_sweep as SW

    p = dict(SW.BASE_PARAMS)
    p.update(OVER_EXT03_M20_TR)
    p["cost"] = cost
    p["job_id"] = "abscore_%s" % tag
    payload, err = eng.run_job(p, "abscore_%s" % tag, progress=lambda *a, **k: None)
    if err or not payload:
        raise SystemExit("[fail] %s: %s" % (tag, err))
    s = payload["summary"]
    turn = 0.0
    for rec in (payload.get("days") or {}).values():
        for a in rec.get("actions") or []:
            if a.get("type") == "buy":
                import re as _re
                m = _re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                if m:
                    turn += float(m.group(1)) / 100
    return {
        "job": "abscore_%s" % tag,
        "total_return_pct": s["total_return_pct"],
        "max_drawdown_pct": s["max_drawdown_pct"],
        "n_trades": s["n_trades"],
        "win_rate_pct": s.get("win_rate_pct"),
        "profit_factor": s.get("profit_factor"),
        "turnover_x": round(turn, 2),
        "annualized_pct": s.get("annualized_pct"),
    }


def main():
    for fp in (CACHE_FP, BAK_FP):
        if not os.path.exists(fp):
            raise SystemExit("[fail] 缓存不存在: %s" % fp)

    # 先跑 new（当前主缓存，不动文件）
    rows = {"new": {}, "old": {}}
    for cost, tag in ((COST_30, "30bp"), (COST_122, "122bp")):
        rows["new"][tag] = _run("new_%s" % tag, cost)
        print("[new %s] ret=%+.2f%% trades=%d win=%.1f%% turn=%.2fx"
              % (tag, rows["new"][tag]["total_return_pct"],
                 rows["new"][tag]["n_trades"],
                 rows["new"][tag]["win_rate_pct"] or -1,
                 rows["new"][tag]["turnover_x"]), flush=True)

    # swap 换旧缓存跑 old，随后立即换回
    shutil.copy2(CACHE_FP, BAK_FP + ".new_side")          # 新缓存暂存
    shutil.copy2(BAK_FP, CACHE_FP)                        # 旧缓存上主位
    try:
        for cost, tag in ((COST_30, "30bp"), (COST_122, "122bp")):
            rows["old"][tag] = _run("old_%s" % tag, cost)
            print("[old %s] ret=%+.2f%% trades=%d win=%.1f%% turn=%.2fx"
                  % (tag, rows["old"][tag]["total_return_pct"],
                     rows["old"][tag]["n_trades"],
                     rows["old"][tag]["win_rate_pct"] or -1,
                     rows["old"][tag]["turnover_x"]), flush=True)
    finally:
        shutil.copy2(BAK_FP + ".new_side", CACHE_FP)      # 换回新缓存
        os.remove(BAK_FP + ".new_side")
        # 复核：主缓存 meta 指纹必须是新口径
        with open(CACHE_FP, encoding="utf-8") as f:
            meta = json.load(f)["meta"]
        ok = "low-base" in str(meta.get("score_weights") or "")
        print("[restore] 主缓存指纹=%s 恢复%s"
              % (meta.get("score_weights"), "正确" if ok else "异常！"))
        if not ok:
            raise SystemExit("[fail] 主缓存未恢复为新口径，人工检查 %s" % CACHE_FP)

    out = {
        "date": "2026-11-10",
        "scope": "三买评分口径 A/B（同参数同窗口，只换评分缓存）",
        "params": "BASE_PARAMS + ext03_m20_tr（距上沿≤3%+关失效线+20日强平+移动止损5%/3.5%）",
        "window": "20251013-20261012",
        "new_low_base": rows["new"],
        "old_close_base": rows["old"],
        "note": "评分只影响「评分降序填槽」的入选次序与 score 槽位配权；信号集合两版完全一致",
    }
    os.makedirs(os.path.dirname(OUT_FP), exist_ok=True)
    json.dump(out, open(OUT_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[save] %s" % OUT_FP)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
