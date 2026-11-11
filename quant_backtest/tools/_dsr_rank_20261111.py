# -*- coding: utf-8 -*-
"""2026-11-11 · 寻优协议 v1 · 第二步：DSR 校正排名（任务 #105）。

在台账 N=1501 之上，对历史候选做多重检验折减：
  · Bonferroni 视角：单条 p 值 × N；t 折减 ≈ t − z(1 − 1/N) 近似看齐
    （保守，直接给「×N 后仍 <0.05」的存活线）。
  * 关键判据换成 bt10m_align 口径：笔均超额 bp 与 t（唯一入口铁律），
    台账里能取到的读数按此重排；取不到 t 的只列不排名（manual_review）。
存活三标签：
  [DSR] 校正后仍显著 / [COST] 122bp 口径读数为正 / [OOS] 样本外窗口未失效
产物：reports/dsr_rank_20261111.json + 控制台摘要。
"""
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(HERE, "reports", "trial_ledger.json")
OUT = os.path.join(HERE, "reports", "dsr_rank_20261111.json")


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def main():
    led = json.load(open(LEDGER, encoding="utf-8"))
    N = led["N_total"]
    # Bonferroni 存活线：单条 p < 0.05/N → 对应单侧 t 阈值
    p_thr = 0.05 / N
    # 用正态近似把 p 阈值换 t 阈值（单侧）
    lo, hi = 1.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if norm_sf(mid) > p_thr:
            lo = mid
        else:
            hi = mid
    t_thr = round((lo + hi) / 2, 2)

    # bt2 有收益读数的条目（含 122bp / 30bp 命名切分）
    rows = []
    for e in led["entries"]:
        if e.get("src") != "bt2" or e.get("ret_pct") is None:
            continue
        job = e["job"]
        cost = "122bp" if "122bp" in job else ("30bp" if "30bp" in job else "unknown")
        rows.append({"job": job, "ret_pct": e["ret_pct"],
                     "mdd_pct": e.get("mdd_pct"),
                     "n_trades": e.get("n_trades"), "cost": cost,
                     "t": None})  # bt2 产物无笔均 t，留空不排名

    # 代表性读数（bt10m_align 折算过的、有 t 的，来自已知落盘）——人工登记块
    # 这些是历史上经 align 口径确认过的候选（含 OOS 状态），t 来自各自产物
    known = [
        {"cand": "v3（固化默认）", "bp": 79, "t": 0.9, "cost": "122bp",
         "oos": "2023Q4 +5.42% vs 基准 +5.72%（未跑赢）", "src": "bt10m 系多份"},
        {"cand": "距zg≤3%+关失效+20日强平+移损5/3.5", "bp": None, "t": None,
         "cost": "30bp:+11.45% / 122bp:+4.59%", "oos": "未跑赢基准",
         "src": "bt10m_verify_1y"},
        {"cand": "W1_c6_score_cap25_stop12", "bp": None, "t": None,
         "cost": "影子零成本", "oos": "影子并行中（11-11 +2.83% 领先）",
         "src": "shadow 台账"},
    ]
    out = {
        "N_total": N, "p_threshold_bonferroni": p_thr,
        "t_threshold_single_sided": t_thr,
        "verdict_headline":
            "N=%d 下 Bonferroni 存活线 t≥%.2f；历史候选最高笔均 t≈0.9"
            "（v3）—— **无一过线**。当前不存在统计意义上『证显著』的"
            "最优数据集；所有『最优』均属最优候选，需按协议继续外证。" % (N, t_thr),
        "bt2_rows_count": len(rows),
        "bt2_top_by_ret_122bp": sorted(
            [r for r in rows if r["cost"] == "122bp"],
            key=lambda r: -(r["ret_pct"] or 0))[:10],
        "known_candidates_with_t": known,
        "labels_definition": {
            "DSR": "校正后仍显著（当前要求 t≥%.2f）" % t_thr,
            "COST": "122bp 真实资金口径读数为正",
            "OOS": "样本外窗口未失效（被使用≤3次）",
        },
        "next_actions": [
            "影子台账 G-A 门继续累积外证（唯一在途的 OOS 通道）",
            "多年 PIT 窗（chanlun_ext_backfill）扩样本提功效",
            "评分瘦身（bei30→0）后重扫重跑主网格，N 登记追加",
            "bt2 产物补笔均超额 t 字段（align 化），让 994 条可排名",
        ],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(out["verdict_headline"])
    print("bt2 可排名条目（有收益）:", len(rows), "；产物:", OUT)


if __name__ == "__main__":
    main()
