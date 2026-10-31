# -*- coding: utf-8 -*-
"""影子盘监控（P3）：归因（行业/分数分桶/个股）+ 衰减熔断 + 门户 JSON。

规则（首批）：
  - 熔断线：净值相对峰值回撤 ≥ 8% → 状态 paused，写 ALERT flag；
  - 观察线：连续 5 日负收益或 20 日收益 < -5% → 状态 watch；
  - 否则 active。
归因：对每个调仓日 d（持仓来自 d 收盘），用 adj 收益 d→d+1 计算：
  contribution_i = w_i × ret_i；按行业、分数分桶（score 四分位）、个股聚合。
产出：
  research/shadow/selection/monitor.json
  portal/static/shadow_monitor.json
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
SHADOW = REPO / "research" / "shadow" / "selection"
STATIC = REPO / "portal" / "static"
DRAWDOWN_LIMIT = 0.08


def adj_close(dt: str, codes: list[str]) -> pd.Series:
    c = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close"]).to_pandas()
    af = DATA / "adj_factor" / f"d_{dt}.parquet"
    if af.exists():
        a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
        c = c.merge(a, on="ts_code", how="left")
        c["adj"] = c["close"] * c["adj_factor"].fillna(1.0)
    else:
        c["adj"] = c["close"]
    return c.set_index("ts_code")["adj"].reindex(codes)


def main() -> int:
    ledger = [json.loads(x) for x in (SHADOW / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    dates = [r["date"] for r in ledger]
    # 账本可能只有 net（无 nav 字段）：按净收益累乘重建 NAV，并回写 nav 便于排查
    nav_vals, cum = [], 1.0
    for r in ledger:
        cum *= 1.0 + float(r.get("net") or 0.0)
        r["nav"] = round(cum, 6)
        nav_vals.append(cum)
    (SHADOW / "ledger.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger) + "\n", encoding="utf-8")
    nav = pd.Series(nav_vals, index=dates)
    peak = nav.cummax()
    dd = nav / peak - 1.0
    ret20 = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    neg_streak = 0
    for r in reversed(ledger):
        if r["net"] < 0:
            neg_streak += 1
        else:
            break
    breached = float(dd.iloc[-1]) <= -DRAWDOWN_LIMIT
    status = "paused" if breached else ("watch" if (neg_streak >= 5 or ret20 < -0.05) else "active")

    # 归因
    ind_contrib, bucket_contrib, stock_contrib = {}, {}, {}
    for i in range(len(dates) - 1):
        d, nd = dates[i], dates[i + 1]
        pos_file = SHADOW / f"positions_{d}.json"
        if not pos_file.exists():
            continue
        pos = json.loads(pos_file.read_text(encoding="utf-8"))
        weights = {h["ts_code"]: h["weight"] for h in pos["holdings"] if h["weight"] > 0}
        if not weights:
            continue
        codes = list(weights)
        c0 = adj_close(d, codes)
        c1 = adj_close(nd, codes)
        ret = (c1 / c0 - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        ranked = sorted([h for h in pos["holdings"] if h["weight"] > 0], key=lambda x: -(x.get("score") or 0))
        n = len(ranked)
        for rank, h in enumerate(ranked):
            contrib = h["weight"] * float(ret.get(h["ts_code"], 0.0))
            ind = h.get("industry") or "未知"
            ind_contrib[ind] = ind_contrib.get(ind, 0.0) + contrib
            q = min(4, int(4 * rank / max(1, n)) + 1)
            bucket = f"score_q{q}"
            bucket_contrib[bucket] = bucket_contrib.get(bucket, 0.0) + contrib
            stock_contrib[h["ts_code"]] = stock_contrib.get(h["ts_code"], 0.0) + contrib
    top = sorted(stock_contrib.items(), key=lambda x: -x[1])[:5]
    worst = sorted(stock_contrib.items(), key=lambda x: x[1])[:5]

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "days": len(ledger), "nav": round(float(nav.iloc[-1]), 6),
        "peak": round(float(peak.iloc[-1]), 6), "dd_pct": round(float(dd.iloc[-1]) * 100, 2),
        "ret20_pct": round(ret20 * 100, 2), "neg_streak": neg_streak,
        "breaker": {"limit_pct": DRAWDOWN_LIMIT * 100, "breached": breached, "status": status},
        "attribution": {
            "industry_pct": {k: round(v * 100, 3) for k, v in sorted(ind_contrib.items(), key=lambda x: -abs(x[1]))[:10]},
            "score_bucket_pct": {k: round(v * 100, 3) for k, v in sorted(bucket_contrib.items())},
            "top_contributors": [{"ts_code": c, "contrib_pct": round(v * 100, 3)} for c, v in top],
            "worst_contributors": [{"ts_code": c, "contrib_pct": round(v * 100, 3)} for c, v in worst],
        },
        "action": ("S02 熔断：暂停组合运行，等待因子重做" if breached else
                   ("S02 观察：负收益/回撤触发预警" if status == "watch" else "S02 正常：继续影子盘")),
    }
    SHADOW.mkdir(parents=True, exist_ok=True)
    (SHADOW / "monitor.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "shadow_monitor.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if breached:
        flag = SHADOW / f"ALERT_BREAKER_{dates[-1]}.flag"
        flag.write_text(f"type=selection_shadow_breaker\nstatus=paused\ndd_pct={payload['dd_pct']}\nnav={payload['nav']}\n"
                        f"limit_pct={DRAWDOWN_LIMIT*100}\nnote=S02 20日回撤超限，暂停；等待因子重做/人工复核\n", encoding="utf-8")
        print("BREAKER ALERT:", flag, flush=True)
    print(json.dumps({k: payload[k] for k in ("days", "nav", "dd_pct", "ret20_pct", "breaker", "action")},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
