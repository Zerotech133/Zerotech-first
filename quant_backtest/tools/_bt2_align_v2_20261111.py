# -*- coding: utf-8 -*-
"""2026-11-11 · bt2 align 化 v2：成本口径修正 + 三重诚实性过滤（任务 #106 补丁）。

v1 的三个问题（今晚自查）：
  ① trades.pnl 是**毛收益**（122bp/30bp 两版逐笔完全相同，净值层才扣成本）
     → t_raw 也是毛的，直接排名会选出「高换手高毛利」的假赢家；
  ② 名字带 OOS 的 job 是「在 OOS 窗上挑出来的变体」——其 OOS 高 t 是
     选择效应（v1 top10 全是 OOS 后缀，就是证据）；
  ③ 822/978 个 job 的窗口基准为正（牛市窗）——毛 t 高≠alpha。

v2 修正（在 _align_index.json 之上加三道过滤，不改原始读数）：
  F1 成本修正：cost_pct 从 job 名解析（122bp→1.22%，30bp→0.30%，无名→None），
     net_pnl = mean_pnl − cost_pct；t_net = t_raw × net_pnl/mean_pnl（同 se 近似）。
  F2 选择偏移标记：job 名含 _OOS 的标 selected_on_oos=True，排名单列。
  F3 基准为正警示：bench_pct>0 的牛市窗 job 标 bull_window=True。
排名口径：t_net 降序，只列 selected_on_oos=False（IN 窗选择）的 job 为
「可推荐候选」；OOS 选择组单独列表供参考。
产物：data/bt2/_align_index_v2.json + 台账二次回填（t_net 等字段）。
"""
import glob
import json
import os
import re
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT2 = os.path.join(HERE, "data", "bt2")
IDX1 = os.path.join(BT2, "_align_index.json")
LEDGER = os.path.join(HERE, "reports", "trial_ledger.json")
OUT = os.path.join(BT2, "_align_index_v2.json")


def main():
    idx = json.load(open(IDX1, encoding="utf-8"))
    out = {}
    for job, v in idx.items():
        if not isinstance(v, dict) or v.get("t_raw") is None:
            out[job] = dict(v or {})
            continue
        r = dict(v)
        m = re.search(r"_(\d+)bp", job)
        cost = float(m.group(1)) / 100.0 if m else None
        r["cost_pct"] = cost
        mean, t = v["mean_pnl"], v["t_raw"]
        if cost is not None and mean:
            net = round(mean - cost, 2)
            r["net_pnl"] = net
            r["t_net"] = round(t * net / mean, 2) if mean else None
        else:
            r["net_pnl"], r["t_net"] = None, None
        r["selected_on_oos"] = "_OOS" in job or job.endswith("_OOS")
        r["bull_window"] = (v.get("bench_pct") or 0) > 0
        out[job] = r

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

    # 排名：IN 选择 + 有 t_net
    rankable = [(r["t_net"], k, r) for k, r in out.items()
                if r.get("t_net") is not None and not r.get("selected_on_oos")]
    rankable.sort(key=lambda x: -x[0])
    print("== 可推荐候选（IN 窗选择 × 净口径）top12 ==")
    for t_net, k, r in rankable[:12]:
        print("  t_net=%-5s %-40s net=%+.2f%% gross=%+.2f%% n=%d %s" % (
            t_net, k, r["net_pnl"], r["mean_pnl"], r["n_sells"],
            "[牛市窗]" if r.get("bull_window") else ""))
    oos_sel = [(r["t_net"], k) for k, r in out.items()
               if r.get("t_net") is not None and r.get("selected_on_oos")]
    oos_sel.sort(key=lambda x: -x[0])
    print("\n== OOS 选择组（选择效应，仅供参考）top5 ==")
    for t_net, k in oos_sel[:5]:
        print("  t_net=%-5s %s" % (t_net, k))

    # 台账二次回填
    led = json.load(open(LEDGER, encoding="utf-8"))
    hit = 0
    for e in led["entries"]:
        if e.get("src") != "bt2":
            continue
        r = out.get(e["job"])
        if r and r.get("t_net") is not None:
            e["t_net"] = r["t_net"]
            e["net_pnl"] = r.get("net_pnl")
            e["selected_on_oos"] = r.get("selected_on_oos")
            hit += 1
    led["align_v2"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "note": "毛 t_raw 修正为净口径 t_net；OOS 选择偏移与"
                               "牛市窗已标注；可推荐排名=IN 选择×t_net",
                       "jobs_with_t_net": hit}
    json.dump(led, open(LEDGER, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n台账二次回填 t_net：%d 条 → %s" % (hit, LEDGER))


if __name__ == "__main__":
    main()
