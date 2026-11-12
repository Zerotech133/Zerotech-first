"""把盘后复盘的「评分排序」观测挂进事件账，供自进化链路积累。

设计要点（都是踩过的坑）：

* 评价对象是**某一次选票**，不是某一次盘前流程。所以发现挂到 signal_day
  对应的既有事件上，另开一天会把流程账天数虚增。
* 只写「单次观测、方向未定」，不写「反向已确认」。单日 |rho|<0.15 是噪声，
  写成结论的话，攒够 quorum 就会自动立一个错误假设。
* 带 regime 标签。三次同类观测若都发生在普跌日，是同一个 beta 的三次
  采样，不是三个独立观测；quorum 只数天数看不出这件事，所以证据里必须留。
* 幂等。重复跑复盘不该多出一条「独立观测」。

用法：
    python tools/preopen/log_review.py --dry
    python tools/preopen/log_review.py --review var/audit/review_picks_20261112.json
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import event_log  # noqa: E402


PATTERN = "SCORE_RANK_INVERTED"


def _latest_review() -> str:
    files = sorted(glob.glob(os.path.join(ROOT, "var", "audit", "review_picks_*.json")))
    if not files:
        raise SystemExit("var/audit/ 下没有复盘产物，先跑复盘脚本")
    return files[-1]


def _regime(win_pct: float) -> str:
    """只用上涨占比做粗分档，够用来判断「是不是又是同一类行情」。"""
    if win_pct < 30.0:
        return "普跌"
    if win_pct > 70.0:
        return "普涨"
    return "分化"


def build_finding(rev: dict, src: str) -> dict:
    sig = str(rev["signal_day"])
    exe = str(rev["exec_day"])
    ic = rev.get("rank_ic") or {}
    tiers = rev.get("tiers") or {}
    mkt = rev.get("market_b") or {}
    smy = rev.get("summary") or {}
    picks = smy.get("picks_b") or {}
    pool = smy.get("pool_b") or {}
    cost = rev.get("cost_pct")
    regime = _regime(float(mkt.get("win") or 0))

    def tier_txt(k):
        v = tiers.get(k) or {}
        return "%s 均值%+.3f%% / 胜率%.0f%%" % (k, v.get("mean", 0), v.get("win", 0))

    rho_b, rho_a = ic.get("score_vs_open_to_close"), ic.get("score_vs_prev_close_to_close")
    evidence = (
        "%s 执行（%s 信号），共识池 %d 只，评分 vs 开盘买入当日收盘 rho=%s，"
        "评分 vs 信号日收盘→执行日收盘 rho=%s（Spearman）；分层 %s、%s、%s。"
        "同口径基准：选出 15 只 %+.3f%%、共识池 %+.3f%%、全市场 %d 只 %+.3f%%，"
        "全市场上涨占比 %.1f%%（%s日）。双边成本 %.2f%%。"
        % (exe, sig, ic.get("n", 0), rho_b, rho_a,
           tier_txt("Top1-5"), tier_txt("Top6-10"), tier_txt("Top11-15"),
           picks.get("mean", 0), pool.get("mean", 0),
           mkt.get("n", 0), mkt.get("mean", 0), mkt.get("win", 0),
           regime, cost or 0)
    )

    return {
        "id": PATTERN,
        "severity": "low",
        "title": "评分排序与实现收益呈弱负相关（单次观测，方向未定）",
        "sample_kind": "single_day",
        "independent_observations": 1,
        "signal_day": sig,
        "exec_day": exe,
        "regime": regime,
        "n_pool": ic.get("n"),
        "rho_open_to_close": rho_b,
        "rho_prev_close_to_close": rho_a,
        "tiers": tiers,
        "cost_pct": cost,
        "evidence": evidence,
        "so_what": (
            "单日、单一行情状态，|rho| 均小于 0.15 且 n=%d，属弱相关，"
            "不构成「评分方向已反转」的证据。但分层单调（前 5 名胜率 0%%，"
            "末 5 名胜率 60%%）与既有质量因子实验的反向结果同向，值得留痕。"
            "当日选票相对全市场少跌约 0.25 个百分点，小于双边成本；"
            "本条只登记观测，不改任何参数。"
            % (ic.get("n") or 0)
        ),
        "next": (
            "①不据此调整评分权重；②同类观测必须跨行情状态才算独立样本——"
            "3 次都发生在普跌日，是同一个 beta 的 3 次采样，不是 3 个独立观测，"
            "凑够天数也不能直接立假设，比对时要按 regime 分组；"
            "③真要验证须先按预判规范交 precheck（信息上限 = 分位多空毛 spread "
            "减成本），上限不足即弃。"
        ),
        "source": os.path.relpath(src, ROOT).replace("\\", "/"),
        "appended_by": "houfeng/preopen:log_review",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="盘后复盘观测 → 事件账")
    ap.add_argument("--review", default=None, help="复盘 JSON 路径，默认取最新一份")
    ap.add_argument("--signal-day", default=None, help="挂到哪天的事件，默认取复盘里的 signal_day")
    ap.add_argument("--dry", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()

    src = os.path.abspath(a.review) if a.review else _latest_review()
    with open(src, "r", encoding="utf-8") as fh:
        rev = json.load(fh)

    finding = build_finding(rev, src)
    day = a.signal_day or str(rev["signal_day"])

    if a.dry:
        print(json.dumps({"attach_to": day, "finding": finding},
                         ensure_ascii=False, indent=1))
        return 0

    ev, added = event_log.attach_drift(day, finding)
    print("[log_review] 复盘 %s → 事件 %s（%s 信号）" % (src, day, finding["signal_day"]))
    print("[log_review] %s  id=%s  exec_day=%s  regime=%s"
          % ("新增" if added else "已存在，未重复累加", PATTERN,
             finding["exec_day"], finding["regime"]))
    print("[log_review] 该事件现有 drift 数 = %d，事件账天数不增加"
          % len(ev.get("drift") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
