# -*- coding: utf-8 -*-
"""JEV 衰减不对称 + 来源异质实证校准表（E1+E7，K-ORG-167）。

数据链复用 sentiment_backtest.run()（同源口径：news.db jev 判定 → A股
一码一日去重 → ts_daily T+1/3/5 行情），本模块只做「方向样本 → 多窗口
命中/收益衰减曲线 → 不对称与来源异质半衰期」校准映射，纯后处理零成本。

借鉴：
  E1 不对称衰减 —— Sentiment Feedback Asymmetries（arXiv 2509.11970：
  负向情绪持久性长于正向、媒体悲观滞后 1-21 月逐步反映，好/坏消息
  冲击不对称）；Hawkes 情绪自激半衰期 33min vs 价格 16min。
  E7 来源异质 —— IC decay 矩阵对角主导：快讯线 EOD 衰减完、长文/公告
  跨日持续（Mental Momentum 研究）。

输出：news_collector/data/jev_decay.json（原子写）
  { generated_at, n_dir, hi_cut,
    bias_decay: {bull/bear: {n, h1, h3, h5, hit3_ratio, hl_h, lambda}},
    asym_supported: bool,
    source_decay: [{source, n, h1, h3, hit3_ratio, hl_h, applied}],
    hint }
λ = ln2/半衰期小时；asym_supported 为真才在 news_ai.py 下发不对称 λ，
source 仅 n≥min_n_src 且衰减显著差异的来源下发（applied=True）。

用法：python jev_decay.py
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sentiment_backtest as sbt  # noqa: E402  (复用其 run/_write_json)

RESULT = os.path.join(HERE, "data", "jev_decay.json")
DIRS = {"bull": 1.0, "bear": -1.0}
HI_CUT = 0.7          # 对齐 _JEV_LOW_CONF（低置信不参与净倾向）
MIN_SRC_N = 30        # 来源级最小方向样本数
HL_MIN, HL_MAX = 4.0, 24.0   # 半衰期钳制范围（小时）
SYM_LAMBDA = 0.1155   # ln2/6（对称基准，K-ORG-157）
ASYM_PERSIST_GAP = 0.05  # 不对称采纳阈值：bear 的 hit3 保持率须高于 bull 该量


def _hit(rows, k, bias):
    """hit@Tk：市场相对口径（去市况污染，pct_k vs mkt_k 超额方向命中）——
    bias 给定 → DIR(bias)*pct_k > DIR(bias)*mkt_k；
    bias=None → 超额上涨 pct_k > mkt_k（来源级不分方向）。
    缺 mkt_k 的样本不计（T+1/3/5 均带 mkt_k）。"""
    sk = []
    for r in rows:
        p = r.get("pct%d" % k)
        m = r.get("mkt%d" % k)
        if p is None or m is None:
            continue
        sk.append((r, p, m))
    if not sk:
        return None, 0
    if bias is None:
        hit = sum(1 for _r, p, m in sk if p > m)
    else:
        hit = sum(1 for _r, p, m in sk if DIRS[bias] * p > DIRS[bias] * m)
    return round(hit / len(sk) * 100, 1), len(sk)


def _hl_from_ratio(h1, h3):
    """由 hit3/hit1 拟合指数半衰期（小时），钳制 [HL_MIN, HL_MAX]；
    hit3≥hit1（无衰减）取上限（最长）。"""
    if not h1 or h1 <= 0 or h3 is None:
        return None
    r = h3 / h1
    if r >= 1.0:
        return HL_MAX
    try:
        hl = -3.0 * math.log(2) / math.log(max(0.15, r))
    except (ValueError, ZeroDivisionError):
        return None
    return round(min(max(hl, HL_MIN), HL_MAX), 1)


def main():
    res = sbt.run()
    if not res:
        print("DECAY no result (datamart/样本缺失)")
        return None
    rows = [r for r in res.get("rows", []) if r["bias"] in DIRS
            and r["conf"] >= HI_CUT]
    n_dir = len(rows)

    bias_decay = {}
    for b in ("bull", "bear"):
        sub = [r for r in rows if r["bias"] == b]
        d = {"n": len(sub), "conf_hi_cut": HI_CUT}
        for k in (1, 3, 5):
            h, nk = _hit(sub, k, b)
            d["h%d" % k] = h
            d["h%d_n" % k] = nk
        hl = _hl_from_ratio(d.get("h1"), d.get("h3"))
        d["hl_h"] = hl
        d["lambda"] = round(math.log(2) / hl, 4) if hl else None
        bias_decay[b] = d
    bl, bh = bias_decay["bull"], bias_decay["bear"]
    # 不对称采纳：bear 保持率(小样本带噪) > bull 保持率 + 间隙，且两档 h1 均有效
    r_bull = bl["h3"] / bl["h1"] if (bl.get("h1") and bl["h3"]) else None
    r_bear = bh["h3"] / bh["h1"] if (bh.get("h1") and bh["h3"]) else None
    asym = (r_bear is not None and r_bull is not None
            and r_bear - r_bull >= ASYM_PERSIST_GAP)

    # 来源级：方向样本按 source 分组，仅统计 n≥MIN_SRC_N
    by_src = {}
    for r in rows:
        s = r.get("source") or "?"
        by_src.setdefault(s, []).append(r)
    src_rows = []
    for s, sub in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
        if len(sub) < MIN_SRC_N:
            continue
        h1, n1 = _hit(sub, 1, None)  # 来源级不分方向：绝对命中（T+1 涨）
        h3, n3 = _hit(sub, 3, None)
        hl = _hl_from_ratio(h1, h3)
        entry = {"source": s, "n": len(sub), "h1": h1, "h3": h3,
                 "hl_h": hl}
        # 显著差异：与对称基准半衰期(6h)差距 ≥ 2h 才下发（小样本不下发）
        entry["applied"] = bool(hl and abs(hl - 6.0) >= 2.0)
        src_rows.append(entry)

    obj = {
        "generated_at": res["generated_at"],
        "n_total": res.get("n", 0),
        "n_dir": n_dir,
        "hi_cut": HI_CUT,
        "bias_decay": bias_decay,
        "asym_supported": asym,
        "persist3": {"bull": round(r_bull, 3) if r_bull else None,
                     "bear": round(r_bear, 3) if r_bear else None},
        "source_decay": src_rows,
        "hint": ("asym_supported=true 时 news_ai 净倾向按利空更慢/利多更快半衰期"
                 "加权；来源级 applied=true 的来源按各自半衰期，未达样本阈值不"
                 "下发（回落对称 6h）。样本不足档位一律不下发。"),
    }
    sbt._write_json(RESULT, obj)
    print("JEV 衰减校准表 %s  n_dir=%d 不对称=%s"
          % (obj["generated_at"][:16], n_dir, asym and "是" or "否"))
    for b in ("bull", "bear"):
        d = bias_decay[b]
        print("  %-5s n=%-4d h1=%-5s h3=%-5s h5=%-5s 半衰期=%sh λ=%.4f"
              % (b, d["n"], d.get("h1"), d.get("h3"), d.get("h5"),
                 d.get("hl_h") or "-",
                 d.get("lambda") or 0.0))
    for e in src_rows:
        print("  来源 %-14s n=%-4d h1=%-5s h3=%-5s 半衰期=%s%s"
              % (e["source"], e["n"], e["h1"], e["h3"],
                 e["hl_h"] if e["hl_h"] else "-",
                 " applied" if e["applied"] else ""))
    print("saved -> %s" % RESULT)
    return 0  # rc=0 正常（sys.exit 吃返回值）


if __name__ == "__main__":
    sys.exit(main())
