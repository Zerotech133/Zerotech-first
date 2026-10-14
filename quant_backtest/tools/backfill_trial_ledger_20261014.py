# -*- coding: utf-8 -*-
"""一次性回填：本会话（2026-10-14）已做过的参数网格试验 → trial_ledger。

含被否决的配置（N 必须含失败试验，否则 DSR 虚高）。数字出处=当日留痕
与 workspace docs 各报告，sharpe 未知处留 None 如实不造值。
跑完即归档 tools/research（一次性）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # tools/
sys.path.insert(0, os.path.dirname(HERE))                  # quant_backtest/

from trial_ledger import record_trial, ledger_stats  # noqa: E402

SRC = "backfill_20261014"
ROWS = [
    ("cross", '{"grid":"36combos","best":"k3_P75"}', 36, "pending",
     None, None, None, "PF5=1.168擦线,近一年两段PF<1;k2全否PF0.92;暂缓升格维持影子"),
    ("chanlun", '{"grid":"beichi_9tier_family"}', 9, "rejected",
     None, None, None, "P6-a六档diff非正+P5现行档反向;维持仅标注"),
    ("double_knife", '{"hold":10,"main":true}', 6, "rejected",
     None, None, None, "PF1.039<1.1未达标"),
    ("double_knife", '{"combo":"V1xV2_rotHfq_hold20"}', 6, "rejected",
     None, None, "OOS/IS=0.626<0.9,OOS均值-0.75%",
     "IS过PF关但OOS不过;强市专属定性"),
    ("double_knife", '{"gate":"theta40/45/50"}', 6, "rejected",
     None, None, "仅theta50单档1.044孤立尖峰", "不上产线门控,维持B注记"),
    ("qpzm", '{"gate_grid":"breadth45_50_med20"}', 10, "kept",
     None, None, None, "45~50观察带三策略互证;40不够紧"),
    ("ninedim", '{"ind_quantile":"3versions"}', 3, "rejected",
     None, None, None, "反指稀释-5.9%vs-8.7%;维持全市场分位"),
    ("strong", '{"self_calib":"P85_P90"}', 2, "rejected",
     None, None, None, "厚度压不进目标带,T+20单调恶化,维持固定±10pp"),
    ("linkage", '{"timing":"6潮两段式"}', 2, "rejected",
     None, None, None, "全期-1.89%vs持有+15.88%,正交假设证伪"),
    ("cross", '{"sig9dim":"down0.5反向"}', 2, "kept",
     None, None, None, "9dim反向语义入net净向分"),
]


def main():
    n = 0
    for strat, cfg, ntr, verdict, sharpe, t, oos, note in ROWS:
        ok, tid = record_trial({
            "strategy": strat, "config_fingerprint": cfg,
            "sharpe": sharpe, "t": t, "n_trials": ntr, "oos": oos,
            "verdict": verdict, "note": "%s;%s" % (note, SRC),
            "source": SRC})
        print("%s %s %s id=%s" % ("OK " if ok else "DUP", strat, cfg, tid))
        n += 1 if ok else 0
    print("backfilled +%d" % n)
    print(ledger_stats())


if __name__ == "__main__"