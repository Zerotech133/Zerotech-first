# -*- coding: utf-8 -*-
"""2026-11-11 · 评分成分前瞻体检：bei / bei2（任务 #102，想法 2）。

背景：score_3buy 的 bei30 权重 = bei(0.40) + bei2(0.30) + bei24(0.30)。
bei24 已两轮验证无前瞻信息（任务 #100/#101）。本脚本把剩下两源
——bei（回踩 vs 前序同向腿力度衰竭）、bei2（回踩 vs 前序反向腿）——
在同一框架下各跑一遍，给 11-26 评审一张完整的「评分成分有效性表」。

数据：data/sig_events/sig_events_market_v3_20251111_20261111.json
  （17:38 重扫版，13141 事件；3买档 bei/bei2 有值：bei=True 848/1163、
   bei2=True 477/1163，可做四格对照）
口径与 _tbe24_fwd_20261111.py 完全一致：5/20 日前瞻（t+1 起算）、
同 dt 全体 3买等权基准、log-cumsum 复合、零成本如实标注、t=mean/se。
产物：reports/bei_pair_forward_20261111.json
"""
import json
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "data", "sig_events",
                   "sig_events_market_v3_20251111_20261111.json")
OUT = os.path.join(HERE, "reports", "bei_pair_forward_20261111.json")
HOR = (5, 20)
COST_NOTE = "零成本口径；122bp 往返未扣"


def main():
    import pandas as pd
    j = json.load(open(SRC, encoding="utf-8"))
    ev = j["events"]

    # 收盘矩阵 + 日历
    data_dir = os.path.join(HERE, "data", "ashare")
    stocks = {e["stock"] for e in ev}
    closes = {}
    for s in stocks:
        fp = os.path.join(data_dir, s.replace(".", "_") + ".parquet")
        if os.path.exists(fp):
            b = pd.read_parquet(fp, columns=["trade_date", "close"])
            closes[s] = dict(zip(b["trade_date"].astype(str), b["close"].astype(float)))
    cal = pd.read_parquet(os.path.join(data_dir, "000001_SZ.parquet"),
                          columns=["trade_date"])["trade_date"].astype(str).tolist()
    cal_idx = {d: i for i, d in enumerate(cal)}

    def fwd(stock, dt, n):
        m = closes.get(stock)
        if not m or dt not in cal_idx:
            return None
        i0 = cal_idx[dt]
        if i0 + 1 + n >= len(cal):
            return None
        a, b = m.get(cal[i0 + 1]), m.get(cal[i0 + 1 + n])
        if not a or not b:
            return None
        return b / a - 1.0

    rows = [e for e in ev if e.get("type") == "3买"]
    for e in rows:
        e["_dt"] = str(e.get("dt") or e.get("day"))

    res = {"meta": {
        "src": os.path.basename(SRC), "scan_meta": j.get("meta"),
        "horizons": HOR, "cost_note": COST_NOTE,
        "coverage": {
            "n_3buy": len(rows),
            "bei_true": sum(1 for e in rows if e.get("bei")),
            "bei2_true": sum(1 for e in rows if e.get("bei2")),
            "both": sum(1 for e in rows if e.get("bei") and e.get("bei2")),
        },
    }, "groups": {}}

    for n in HOR:
        day_rets = defaultdict(list)
        for e in rows:
            r = fwd(e["stock"], e["_dt"], n)
            if r is not None:
                day_rets[e["_dt"]].append(r)
        day_mean = {d: sum(v) / len(v) for d, v in day_rets.items() if v}

        def bucket(pred):
            xs, raws = [], []
            for e in rows:
                if not pred(e):
                    continue
                r = fwd(e["stock"], e["_dt"], n)
                bm = day_mean.get(e["_dt"])
                if r is None or bm is None:
                    continue
                xs.append(r - bm)
                raws.append(r)
            if not xs:
                return None
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / max(1, len(xs) - 1)
            se = math.sqrt(var / len(xs)) if var > 0 else None
            return {"n": len(xs), "mean_excess_bp": round(mean * 1e4, 1),
                    "t": round(mean / se, 2) if se else None}

        g = {}
        for label, pred in (
            ("bei_T", lambda e: bool(e.get("bei"))),
            ("bei_F", lambda e: not e.get("bei")),
            ("bei2_T", lambda e: bool(e.get("bei2"))),
            ("bei2_F", lambda e: not e.get("bei2")),
            ("both_T", lambda e: bool(e.get("bei")) and bool(e.get("bei2"))),
            ("none", lambda e: not e.get("bei") and not e.get("bei2")),
        ):
            g[label] = bucket(pred)
        res["groups"]["h%d" % n] = g

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res["meta"]["coverage"], ensure_ascii=False))
    for h, g in res["groups"].items():
        print("===", h, "===")
        for k, v in g.items():
            if v:
                print("  %-7s n=%-5d %+7.1fbp  t=%s" % (k, v["n"], v["mean_excess_bp"], v["t"]))
    print("\n产物:", OUT)


if __name__ == "__main__":
    main()
