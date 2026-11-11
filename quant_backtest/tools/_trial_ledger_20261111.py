# -*- coding: utf-8 -*-
"""2026-11-11 · 选股数据集寻优协议 v1 · 第一步：试验台账（任务 #105）。

问题：回测寻优跑了几百组变体（bt10m 244 条 + bt2 994 个 job + P1-P69 散件），
但从无可数台账 → 多重检验校正没有分母，「最优」无法证显著。

本脚本只做登记（只读产物，不改任何历史数据）：
  ① 扫 data/bt2/*.json（994 个 run_job 产物）：job 名、summary 收益/回撤；
  ② 扫 reports/bt10m_*.json 30 个汇总：变体数、代表读数（有则取）；
  ③ P1-P69 散件按文件名登记（读数不解析，标 manual_review）；
  ④ 全部落 reports/trial_ledger.json：N 总数 + 每条 source/job/读数。
DSR 排名在台账之上另做（第二步）。
"""
import glob
import json
import os
import time
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT2 = os.path.join(HERE, "data", "bt2")
REP = os.path.join(HERE, "reports")
OUT = os.path.join(REP, "trial_ledger.json")


def main():
    ledger = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "protocol": "寻优协议 v1（2026-11-11）：先登记台账再出数；"
                          "任何新寻优必须追加到本台账，N 可数才能做 DSR 校正。",
              "sources": {}, "entries": []}
    n_bt2 = n_bt10m = n_p = 0

    # ① bt2 994 个 job
    for f in sorted(glob.glob(os.path.join(BT2, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
            s = d.get("summary") or {}
            ledger["entries"].append({
                "src": "bt2", "job": os.path.basename(f)[4:-5],
                "ret_pct": s.get("total_return_pct") or s.get("ret_pct"),
                "mdd_pct": s.get("max_dd_pct") or s.get("mdd_pct"),
                "n_trades": s.get("n_trades"),
            })
            n_bt2 += 1
        except Exception:
            ledger["entries"].append({"src": "bt2",
                                      "job": os.path.basename(f),
                                      "parse": "failed"})

    # ② bt10m 30 个汇总（变体级）
    for f in sorted(glob.glob(os.path.join(REP, "bt10m_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
            variants = None
            for k in ("variants", "grid", "rows", "windows", "legs"):
                if isinstance(d, dict) and k in d:
                    variants = len(d[k]); break
            if variants is None and isinstance(d, dict):
                variants = len(d)
            ledger["entries"].append({
                "src": "bt10m", "job": os.path.basename(f)[6:-5],
                "n_variants": variants,
            })
            n_bt10m += 1
        except Exception:
            ledger["entries"].append({"src": "bt10m",
                                      "job": os.path.basename(f),
                                      "parse": "failed"})

    # ③ P 号散件（读数人工复核，只登记存在性）
    p_files = sorted(x for x in os.listdir(REP)
                     if x.startswith("_p") and x.endswith(".json"))
    for f in p_files:
        ledger["entries"].append({"src": "p-series", "job": f[1:-5],
                                  "n_variants": 1, "manual_review": True})
        n_p += 1

    # P1-P69 的 69 个立项号：即使散件不齐，立项即算一次尝试（保守补记）
    known_p = {e["job"] for e in ledger["entries"] if e["src"] == "p-series"}
    p_declared = 69
    p_missing = max(0, p_declared - len(known_p))
    ledger["sources"] = {
        "bt2_jobs": n_bt2,
        "bt10m_reports": n_bt10m,
        "p_files": n_p,
        "p_declared": p_declared,
        "p_missing_declared_only": p_missing,
    }
    # N 总数：bt2 每个 job 一次 + bt10m 每个变体一次 + P 立项 69 次
    n_variants_bt10m = sum(e.get("n_variants") or 1
                           for e in ledger["entries"] if e["src"] == "bt10m")
    N = n_bt2 + n_variants_bt10m + p_declared
    ledger["N_total"] = N
    ledger["N_note"] = "N = bt2 jobs(%d) + bt10m 变体(%d) + P 立项(%d，含散件缺卡的保守补记)" % (
        n_bt2, n_variants_bt10m, p_declared)

    json.dump(ledger, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("台账 →", OUT)
    print("N_total =", N)
    print(json.dumps(ledger["sources"], ensure_ascii=False, indent=1))
    c = Counter(e["src"] for e in ledger["entries"])
    print("条目分布:", dict(c))


if __name__ == "__main__":
    main()
