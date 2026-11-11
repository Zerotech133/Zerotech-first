# -*- coding: utf-8 -*-
"""P0 观察腿：外部组合 × 结构池 每日对照（2026-11-11 想法 1）。

输入：data/external_portfolio.json（老田 30 只组合，缺省不跑，存在才执行）
数据：当晚 pool_state_<asof>.json（结构状态）+ data/ashare 日线（当日涨跌）
输出：reports/portfolio_watch_<asof>.json
  · 逐票：结构档 / 趋势 / 距 zg / 信号 / 当日涨跌 / 相对分组均值
  · 分组：均值 vs 板块 5 日强弱（sector_state，T+1 口径如实标注）
纪律：纯观察层（P0）——不产信号、不评分、不进任何交易链路；
     结构池未覆盖的票如实标 not_in_pool，不编数据。
用法：python portfolio_watch.py   （pool_daily 第 8.5 步 soft 调用亦可）
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

PORT = os.path.join(HERE, "data", "external_portfolio.json")
REPORTS = os.path.join(HERE, "reports")


def _latest(prefix):
    fs = sorted(glob.glob(os.path.join(REPORTS, prefix + "_2*.json")))
    return fs[-1] if fs else None


def main():
    if not os.path.exists(PORT):
        print("[P0] 无 external_portfolio.json，跳过（soft）")
        return None
    pf = json.load(open(PORT, encoding="utf-8"))
    hold = pf["holdings"]

    pool_fp = _latest("pool_state")
    if not pool_fp:
        print("[P0] 无 pool_state，跳过")
        return None
    pool = json.load(open(pool_fp, encoding="utf-8"))
    asof = pool.get("data_asof")

    # 结构池索引
    idx = {}
    for tier, rows in pool["buckets"].items():
        for r in rows:
            idx[r["code"]] = {"tier": tier, **{k: r.get(k) for k in
                ("trend", "dist_zg", "sig_type", "pct", "name")}}

    # 当日收盘价（ashare parquet）
    import pandas as pd
    rows = []
    for h in hold:
        code = h.get("code")
        # 2026-11-11 v2：未点名占位票（code=null）只计数，不出数据不出价位
        r = {"code": code or "—", "name": h["name"], "group": h["group"],
             "tier": "unconfirmed" if not code else None}
        if not code:
            rows.append(r)
            continue
        p = idx.get(code)
        r["in_pool"] = p is not None
        if p:
            r.update({"tier": p["tier"], "trend": p.get("trend"),
                      "dist_zg_pct": round((p.get("dist_zg") or 0) * 100, 1)
                      if p.get("dist_zg") is not None else None,
                      "sig": p.get("sig_type"),
                      "day_pct": round((p.get("pct") or 0) * 100, 2)})
        else:
            fp = os.path.join(HERE, "data", "ashare",
                              code.replace(".", "_") + ".parquet")
            if os.path.exists(fp):
                b = pd.read_parquet(fp, columns=["trade_date", "pct_chg"])
                tail = b[b["trade_date"].astype(str) <= str(asof)].tail(2)
                if len(tail) == 2:
                    r["day_pct"] = round(float(tail.iloc[-1]["pct_chg"]), 2)
                r["tier"] = "not_in_pool"
            else:
                r["tier"] = "no_data"
        rows.append(r)

    # 分组统计
    groups = {}
    for r in rows:
        if r.get("day_pct") is not None:
            g = groups.setdefault(r["group"], {"sum": 0.0, "n": 0})
            g["sum"] += r["day_pct"]
            g["n"] += 1
    group_stats = {k: {"mean_pct": round(v["sum"] / v["n"], 3), "n": v["n"]}
                   for k, v in groups.items()}

    # 板块层对照（T+1 口径如实带出）
    sec_fp = _latest("sector_state")
    sec_asof = None
    sec_map = {}
    if sec_fp:
        sj = json.load(open(sec_fp, encoding="utf-8"))
        sec_asof = sj.get("asof")
        for s in sj.get("sectors") or []:
            sec_map[s.get("name")] = s

    out = {
        "asof": asof, "sector_asof": sec_asof,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bench_map": pf["meta"].get("bench_map", {}),
        "groups": group_stats,
        "sector_context": {
            g: {"strongest": sorted(
                    [(s.get("name"), round((s.get("r5") or 0) * 100, 1))
                     for s in sec_map.values() if s.get("r5") is not None],
                    key=lambda x: -x[1])[:3]}
            for g in ["小盘", "银行", "电力", "航运能源"] if sec_map
        },
        "rows": rows,
    }
    dst = os.path.join(REPORTS, "portfolio_watch_%s.json" % asof)
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[P0] %s：%d 只（池内 %d）→ %s" % (
        asof, len(rows), sum(1 for r in rows if r.get("in_pool")), dst))
    return out


if __name__ == "__main__":
    main()
