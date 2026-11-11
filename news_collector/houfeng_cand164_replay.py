# -*- coding: utf-8 -*-
"""侯风 20261106 原始候选池(164 只) · 只读复现 + 金标准校验。

身份边界（OSINT1 马良）：本脚本【不产生任何研判】，只做两件事：
  1) 用 _pick_20261106_report.py 的冻结参数，确定性复现既有报告的 BUY 矩阵；
  2) 提取信号日 BUY=True 的全部标的代码（既有报告记为 164），并做双金标准校验。

只有两项校验全部通过才落盘；任何一项不过即拒绝输出、如实报错（不产出清单）。

校验 A：len(cand) == 164 == 既有报告 meta.n_cand_gate_off / pool_off.n_raw
校验 B：按冻结参数（drop_p1 + SC 降序）复现的 top30 代码序列，
        与既有 houfeng_pick_20261106.json 的 30 只逐只、逐序一致

用途：仅作为 OSINT 开源情报采集范围依据（影子池/观察名单，非策略信号、非买卖方向）。
"""
import os
import sys
import json
import time

import numpy as np

QB = r"C:/aios/quant_backtest"
HERE = r"C:/aios/news_collector"
sys.path.insert(0, QB)

import qpzm_backtest as qb  # noqa: E402
import portfolio_sim as ps  # noqa: E402

SIG = 20261106
W0, W1 = 20251106, 20261106
TOP_N = 30
PICK_FILE = r"C:/aios/portal/houfeng/houfeng_pick_20261106.json"
OUT = os.path.join(HERE, "houfeng_candidates_164.json")
NAMES = os.path.join(QB, "data", "ashare", "_names.json")


def quiet(*a, **k):
    pass


def main():
    t0 = time.time()
    print("[1/4] load_wide（全市场 ashare 行情，只读）...", flush=True)
    wide = qb.load_wide(ps.DATA_DIR, log_every=100000, keep_raw=True)
    qb.apply_exact_unfill(wide, W0, W1)
    dates = wide["dates"]
    idx_sig = int(np.searchsorted(dates, SIG, side="right")) - 1
    idx_w0 = int(np.searchsorted(dates, W0, side="left"))
    pos_lo = max(idx_w0 - 120, 0)  # 与冻结脚本一致：预热 120 行
    t_sig = idx_sig - pos_lo
    print("[2/4] build_matrices(gate_on=False) ...", flush=True)
    M = ps.build_matrices(wide, pos_lo, idx_sig, gate_on=False, log=quiet)
    del wide

    codes = list(M["codes"])
    BUY, SC, RPS50 = M["BUY"], M["SC"], M["RPS50"]
    cand = np.flatnonzero(BUY[t_sig])
    print("[3/4] 信号日 %d 复现候选数 = %d" % (SIG, len(cand)), flush=True)

    ref = json.load(open(PICK_FILE, encoding="utf-8"))
    n_ref = int(ref["meta"]["n_cand_gate_off"])
    ref30 = [p["code"] for p in ref["pool_off"]["picks"]]

    okA = (len(cand) == n_ref == 164)
    print("  校验A 候选数: 复现=%d 既有=%d 期望=164 -> %s"
          % (len(cand), n_ref, "PASS" if okA else "FAIL"), flush=True)

    # 复刻冻结脚本 build_pool 的 drop_p1 + SC 降序（只取代码，不算份额/权重）
    cand2 = cand
    p1 = RPS50[t_sig, cand].astype(float)
    keep = ~np.isnan(p1) & (p1 >= 50.0)
    if keep.sum() >= 10:
        cand2 = cand[keep]
    order = np.argsort(
        -np.nan_to_num(SC[t_sig, cand2].astype(float), nan=-1e9), kind="stable"
    )
    sel = cand2[order[:TOP_N]]
    top30 = [codes[j] for j in sel]
    okB = (top30 == ref30)
    print("  校验B top30 逐只逐序一致 -> %s" % ("PASS" if okB else "FAIL"), flush=True)
    if not okB:
        print("    复现前8:", top30[:8], flush=True)
        print("    既有前8:", ref30[:8], flush=True)
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(top30, ref30)) if a != b]
        print("    差异数=%d 首个=%s" % (len(diff), diff[:3]), flush=True)

    if not (okA and okB):
        print("!! 金标准校验未通过 → 拒绝落盘，不产出任何清单", flush=True)
        return 2

    all_codes = [codes[j] for j in cand]
    names = {}
    if os.path.exists(NAMES):
        try:
            names = json.load(open(NAMES, encoding="utf-8"))
        except Exception:
            pass
    payload = {
        "schema": "houfeng.candidate_pool/v1",
        "source": ("确定性复现自 quant_backtest/_qpzm_rd_20261106/paper/"
                   "_pick_20261106_report.py（冻结参数 gate_on=False）"),
        "signal_date": SIG,
        "asof": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_candidates": len(all_codes),
        "gate_safe": False,
        "note": ("原始候选池（影子池）全量代码；非策略信号、非买卖方向，"
                 "仅作 OSINT 开源情报采集范围依据"),
        "gold_checks": {
            "candidate_count_164": True,
            "top30_exact_match": True,
            "ref_pick_file": os.path.basename(PICK_FILE),
        },
        "top30_from_pick": ref30,
        "candidates": [{"code": c, "name": names.get(c, "")} for c in all_codes],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[4/4] 已落盘 %s（%d 只，%.0fs）"
          % (OUT, len(all_codes), time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
