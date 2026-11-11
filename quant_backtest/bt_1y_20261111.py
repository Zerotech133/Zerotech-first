# -*- coding: utf-8 -*-
"""最近一年回测（2026-11-11 老田指令「回测1年的数据」）。

窗口：D0=20251111 → D1=20261110（数据已同步的最后交易日）。
口径：bt10m_sweep.BASE_PARAMS，两组——
  A) 当前最优卖出规则 ext03_m20_tr（距上沿≤3%+关失效线+20日强平+移动止损5%/3.5%）
     × 30bp/122bp 双成本；
  B) buy_filter 1买/2买/3买 裸规则 × 30bp（对照组，看新评分缓存下三类的相对位次）。
收尾用 bt10m_align（monkeypatch D0/D1 到本窗口）折算每笔笔均超额 bp + t
（对等权全A基准）并打印基准年度表现。

产物：data/bt2/bt2_*1y*.json + reports/bt_1y_20261111.json
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

D0, D1 = "20251111", "20261110"
OUT_FP = os.path.join(HERE, "reports", "bt_1y_20261111.json")

COST_30 = 0.0015
COST_122 = 0.0061

OVER_EXT03_M20_TR = {
    "max_ext_pct": 0.03,
    "three_fail_exit": False,
    "holding_max_days": 20,
    "trail_activate": 0.05,
    "trail_drawdown": 0.035,
}

JOBS = [
    ("ext1y_30bp", {"cost": COST_30, **OVER_EXT03_M20_TR}),
    ("ext1y_122bp", {"cost": COST_122, **OVER_EXT03_M20_TR}),
    ("grid1y_1b_30bp", {"cost": COST_30, "buy_filter": "1"}),
    ("grid1y_2b_30bp", {"cost": COST_30, "buy_filter": "2"}),
    ("grid1y_3b_30bp", {"cost": COST_30, "buy_filter": "3"}),
]


def run_all():
    import backtest_engine_v2 as eng
    import bt10m_sweep as SW

    out = {}
    for job, over in JOBS:
        p = dict(SW.BASE_PARAMS)
        p.update(over)
        p["d0"] = D0  # 必须显式覆盖：BASE_PARAMS 烙死旧窗口（20251013-20261012）
        p["d1"] = D1
        p["job_id"] = job
        payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
        if err or not payload:
            raise SystemExit("[fail] %s: %s" % (job, err))
        s = payload["summary"]
        turn = 0.0
        for rec in (payload.get("days") or {}).values():
            for a in rec.get("actions") or []:
                if a.get("type") == "buy":
                    import re as _re
                    m = _re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                    if m:
                        turn += float(m.group(1)) / 100
        out[job] = {
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "n_trades": s["n_trades"],
            "win_rate_pct": s.get("win_rate_pct"),
            "profit_factor": s.get("profit_factor"),
            "turnover_x": round(turn, 2),
            "annualized_pct": s.get("annualized_pct"),
        }
        print("[%s] ret=%+.2f%% mdd=%.2f%% trades=%d win=%.1f%% turn=%.2fx"
              % (job, s["total_return_pct"], s["max_drawdown_pct"],
                 s["n_trades"], s.get("win_rate_pct") or -1, turn), flush=True)
    return out


def align():
    """monkeypatch bt10m_align 的窗口常量，折笔均超额+t + 基准。"""
    import bt10m_align as AL
    AL.D0, AL.D1 = D0, D1
    print("\n== 等权全A基准（%s→%s）==" % (D0, D1))
    AL.cmd_bench()
    print("\n== 笔均超额 bp / t（对等权全A）==")
    jobs = [j for j, _ in JOBS]
    AL.cmd_variants(jobs)


def main():
    rows = run_all()
    align()
    meta_cache = os.path.join(HERE, "data", "sig_events",
                              "sig_events_market_v3_%s_%s.json" % (D0, D1))
    fp_meta = None
    if os.path.exists(meta_cache):
        with open(meta_cache, encoding="utf-8") as f:
            fp_meta = json.load(f)["meta"].get("score_weights")
    out = {
        "date": "2026-11-11",
        "window": "%s-%s" % (D0, D1),
        "cache_fingerprint": fp_meta,
        "jobs": rows,
        "note": "A=当前最优卖出规则×双成本；B=1/2/3买裸规则30bp对照（新评分 low-base+bei24 缓存）",
    }
    json.dump(out, open(OUT_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[save] %s" % OUT_FP)


if __name__ == "__main__":
    main()
