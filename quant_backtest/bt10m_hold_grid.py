import json, sys, statistics, collections
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:/aios/quant_backtest")
import backtest_engine_v2 as eng
import bt10m_sweep as SW

BASE = dict(SW.BASE_PARAMS)
BASE.update({"max_ext_pct": 0.03, "three_fail_exit": False,
             "trail_activate": 0.05, "trail_drawdown": 0.035})

rows = []
for mh in (5, 10, 15, 20, 25, 30, 40, 60):
    p = dict(BASE); p["holding_max_days"] = mh
    job = "hg%02d" % mh
    payload, err = eng.run_job(p, job, progress=lambda *a, **k: None)
    if err or not payload:
        print(job, "失败", err); continue
    m = SW._metrics(payload, job, "持有期", "%d 日上限" % mh, {"holding_max_days": mh})
    d = payload
    eq = [e["day"] for e in d["equity_curve"]]
    idx = {x: i for i, x in enumerate(eq)}
    q = {}; pairs = []
    for t in d["trades"]:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t)
        else:
            l = q.get(t["stock"])
            if l:
                b = l.pop(0)
                pairs.append((idx[t["day"]] - idx[b["day"]], t.get("pnl"), t.get("how")))
    h = [x[0] for x in pairs]
    why = collections.Counter(x[2] for x in pairs)
    full = [x[1] for x in pairs if x[2] == "exit_open" or x[0] >= mh]
    cut = [x[1] for x in pairs if x[2] == "trail_stop" or x[2] == "sell_sig"]
    m.update({"avg_hold": round(statistics.mean(h), 2) if h else None,
              "med_hold": statistics.median(h) if h else None,
              "max_hold": max(h) if h else None,
              "n_exit_time": why.get("exit_time", 0) + why.get("time_stop", 0),
              "n_trail": why.get("trail_stop", 0),
              "how": dict(why)})
    rows.append(m)
    print("%-6s 上限%2d日 | 收益 %+7.2f%% DD %6.2f%% n=%3d 胜率 %5s PF %5s 换手 %5.2f 成本 %5.2f%% "
          "| 实持均值 %5.2f 中位 %4s 最长 %3s | trail %2d time %2d"
          % (job, mh, m["total_return_pct"], m["max_drawdown_pct"], m["n_trades"],
             m["win_rate_pct"], m["profit_factor"], m["turnover_x"], m["cost_total_pct"],
             m["avg_hold"] or 0, m["med_hold"], m["max_hold"], m["n_trail"], m["n_exit_time"]),
          flush=True)

json.dump({"grid": rows}, open("reports/bt10m_hold_grid.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
