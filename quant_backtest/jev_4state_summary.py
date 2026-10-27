# -*- coding: utf-8 -*-
"""JEV 四态对照表汇总（077m）：读 reports/qpzm_jev4state_*.json 出对照。

用法：python jev_4state_summary.py [hold]
输出：控制台对照表——主口径 w_cur_th7550_gate_on（当前参数+闸门开），
按 hold 期分列：买入数/成交数/均收益/超额/胜率。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ["missing", "neutral50", "w015", "replay"]
LABEL = {
    "missing": "缺维降权(现状)",
    "neutral50": "50中性",
    "w015": "0.15权重重组",
    "replay": "态0档案重放",
}
PRIMARY = "w_cur_th7550_gate_on"


def load(mode):
    fp = os.path.join(HERE, "reports", "qpzm_jev4state_%s.json" % mode)
    if not os.path.exists(fp):
        return None
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def main():
    hold_filter = sys.argv[1] if len(sys.argv) > 1 else None
    reps = {m: load(m) for m in MODES}
    if not any(reps.values()):
        print("reports/qpzm_jev4state_*.json 不存在——先跑四态回测")
        return 1
    base = next(r for r in reps.values() if r)
    meta = base.get("meta", {})
    print(
        "全量窗 start=%s n_stocks=%s cost=%s"
        % (
            (meta.get("window") or {}).get("start", "?"),
            meta.get("n_stocks", "?"),
            meta.get("cost", "?"),
        )
    )
    holds = (
        sorted(base["variants"][0]["holds"].keys(), key=int)
        if hold_filter is None
        else [hold_filter]
    )
    for hold in holds:
        print("\n== hold %s 日 · 主口径 %s ==" % (hold, PRIMARY))
        print(
            "%-14s %8s %10s %10s %8s %7s"
            % ("态", "买入数", "均收益", "超额", "胜率", "中位")
        )
        for m in MODES:
            r = reps[m]
            if not r:
                print("%-14s %8s" % (LABEL[m], "缺报告"))
                continue
            v = next((x for x in r["variants"] if x["label"] == PRIMARY), None)
            if v is None or hold not in v["holds"]:
                print("%-14s %8s" % (LABEL[m], "缺"))
                continue
            h = v["holds"][hold]
            print(
                "%-14s %8d %9.4f%% %+9.4f%% %7.1f%% %9.4f%%"
                % (
                    LABEL[m],
                    v["n_buy_in_window"],
                    h["mean"] * 100,
                    h.get("excess_mean", 0) * 100,
                    h.get("win_rate", 0) * 100,
                    h.get("median", 0) * 100,
                )
            )
    print(
        "\n注：replay 态 jev 维仅覆盖 jev_replay_series 日期范围"
        "（约 09-25 起 16 天，末日部分数据已剔），覆盖外自然缺维；"
        "初版对照供结构判断，终版待归档积累后重跑。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
