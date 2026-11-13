# -*- coding: utf-8 -*-
"""T2 建仓通道闸门（2026-11-13 老田拍板，回测证据：T2 通道再收口）。

证据（10-15~11-12 一个月回放回测 + 24 份归档分桶）：
  · 无 chanlun 买点 tag 的 T2（靠 strong/qpzm 单独抬层）T+5 均值 -7.61%
    (n=19, 胜率 16%)，是 T2 层最大毒源；
  · 带 chanlun 买点的 T2 明显较好：1买 -0.44% / 2买 -1.23% / 3买 -3.34%；
  · 24 档历史归档 1331 条 T2 中 570 条(42.8%)无 chanlun 买点，
    **全部集中在 2026-10-09~10-16 的旧档**；10-19 起为 0
    ——11-12 上线的三闸（T2 收口 bull=1）已经把这条通道挡住了。
  · 因此本闸门是**防口径回退的显式护栏**，不是补一个仍在漏的洞：
    万一 bull 闸被放宽、或上游换源让 strong/qpzm 抬层票重新进 T2，
    模拟盘也不会再吃进这批负期望样本。

判定口径（fail-closed，与三闸 fail-open 相反）：
  · 有效买点 = sources 里存在 s == 'chanlun' 且 tag 含 '买' 且不含 '卖'；
  · 无有效买点一律剔除（含 strong/qpzm 单独抬层、sources 信息缺失）；
  · 剔除只减少买入敞口、不产生持仓，宁可空仓 —— 故可安全 fail-closed。

判据审计三字段（JUDGMENT_AUDIT 台账纪律）：
  assumptions: 回算窗口是弱势月，'无 chanlun 买点' 与该月负期望的关联
               是样本内统计，不等于因果；强势月需重新验证。
  falsifier:   新口径 T2（11-12 起）本就 0 毒源；若未来某档出现大量
               被剔票且这些票后验显著跑赢保留票，说明闸门过紧。
  cost_if_wrong: 误剔好票 = 少赚（机会成本），不会造成真金亏损；
               但会污染「剔除组」训练样本，故每笔剔除都留 why + sources。
"""

from __future__ import annotations

CHANLUN = "chanlun"


def is_chanlun_buy(src):
    """单条 source 是否为有效缠论买点（1买/2买/3买，排除卖点与非买点 tag）。"""
    if not isinstance(src, dict):
        return False
    if str(src.get("s") or "") != CHANLUN:
        return False
    tag = str(src.get("tag") or "")
    return "买" in tag and "卖" not in tag


def has_chanlun_buy(stock):
    """整只票是否带有效 chanlun 买点。"""
    srcs = stock.get("sources") if isinstance(stock, dict) else None
    if not isinstance(srcs, list):
        return False
    return any(is_chanlun_buy(x) for x in srcs)


def reject_reason(stock):
    """剔除原因（留痕用），按诊断价值从高到低细分：

      no_signal_info               sources 缺失/空/非 list
      chanlun_nonbuy               有 chanlun 源但全是卖点或非买点 tag（方向异常，
                                   这类票本就不该进 T2，后验要单独看）
      no_chanlun_buy/lifted_by:*   无 chanlun 买点，靠 strong/qpzm 等抬层（本次
                                   回算的 -7.61% 毒源就是这一类）
      no_chanlun_buy               无买点且无其他抬层源
    """
    srcs = stock.get("sources") if isinstance(stock, dict) else None
    if not isinstance(srcs, list) or not srcs:
        return "no_signal_info"
    has_cl = any(isinstance(x, dict) and str(x.get("s") or "") == CHANLUN
                 for x in srcs)
    lift = []
    for x in srcs:
        if isinstance(x, dict):
            s = str(x.get("s") or "")
            if s and s != CHANLUN and s not in lift:
                lift.append(s)
    if has_cl:
        return "chanlun_nonbuy"
    if lift:
        return "no_chanlun_buy/lifted_by:" + "+".join(sorted(lift))
    return "no_chanlun_buy"


def gate_t2(stocks):
    """T2 通道收口。返回 (kept, rejected)：

    kept     原 stock 对象（不拷贝、不改字段，调用方继续按原结构消费）；
    rejected [{code, name, why, sources}] —— 逐笔留痕供后验复核。
    非 dict / 缺 code 的脏行按 fail-closed 剔除，绝不静默放行。
    """
    kept, rejected = [], []
    for s in stocks or []:
        if not isinstance(s, dict) or not s.get("code"):
            rejected.append({"code": None, "name": None,
                             "why": "malformed_row", "sources": []})
            continue
        if has_chanlun_buy(s):
            kept.append(s)
        else:
            rejected.append({
                "code": s.get("code"), "name": s.get("name"),
                "why": reject_reason(s),
                "sources": [{"s": x.get("s"), "tag": x.get("tag")}
                            for x in (s.get("sources") or [])
                            if isinstance(x, dict)],
            })
    return kept, rejected


def gate_report(kept, rejected):
    """一行摘要，供 review/open 打印与 risk_gate 落盘。"""
    n_all = len(kept) + len(rejected)
    return {
        "applied": True,
        "rule": "T2 必须带有效 chanlun 买点（剔除 strong/qpzm 单独抬层）",
        "t2_in": n_all, "t2_kept": len(kept), "t2_rejected": len(rejected),
        "reject_pct": round(100.0 * len(rejected) / n_all, 1) if n_all else 0.0,
        "rejected": rejected,
    }
