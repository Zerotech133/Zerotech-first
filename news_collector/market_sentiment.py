# -*- coding: utf-8 -*-
"""P5 市场级情绪指数 —— 六源情绪卡 → 单一市场情绪读数 + 状态机。

输入：news_collector/data/sentiment_cards.json（sentiment_card.py 契约，
      cards[].src.{源} = {n, bullish, neutral, bearish, polarity, ...}）
输出：
  · news_collector/data/market_sentiment.json            机读契约（portal 可挂）
  · news_collector/data/market_sentiment_history.jsonl   日快照（append-only，
      同日重跑覆盖同日行；未来攒 ≥60 日可建 R1 式分位闸，对齐
      zerobound-kg kg_riskguard health0.jsonl 模式）
  · reports/market_sentiment_{date}.md                   人读报告

口径（v1，2026-11-09）：
  · 源权重（极性合成，缺源自动归一）：
      股吧 0.40（散户广度，样本最大，词典法口语可靠）
      淘股吧 0.20（游资先手）
      雪球 0.20（LLM 标注价值盘）
      互动易 0.10 / e互动 0.10（官方问答，信息性>情绪性，低权）
    新闻流无极性不打分，仅作声量上下文。
  · 源极性 = 各标的 polarity 按 n 加权均值（比中位数抗小样本抖动）；
    标的 polarity 缺失（null）不参与。
  · index = 50 + 50 * composite ∈ [0,100]。
  · breadth = 活跃标的中 polarity>0 占比（情绪广度，类"上涨家数比"）。
  · 状态机 5 档（极端档优先判定，防均值掩盖尾部）：
      极度谨慎 index<=30 或 breadth<=30
      谨慎     index<=42
      中性     42<index<58
      乐观     index>=58
      极度乐观 index>=70 且 breadth>=65

CLI：python market_sentiment.py [--cards 路径] [--no-history]
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
CARDS_F = os.path.join(HERE, "data", "sentiment_cards.json")
INDEX_F = os.path.join(HERE, "data", "market_sentiment.json")
HIST_F = os.path.join(HERE, "data", "market_sentiment_history.jsonl")

# 极性源权重（合计 1.0；缺源按剩余权重归一）
SRC_WEIGHTS = {"股吧": 0.40, "淘股吧": 0.20, "雪球": 0.20,
               "互动易": 0.10, "e互动": 0.10}
NO_POLARITY_SOURCES = ("新闻流",)


# ── 纯函数（离线可测）───────────────────────────────────────────────
def aggregate_source(cards, src_name):
    """单源聚合：n 加权极性 + 多空中性总量 + 活跃标的数。

    返回 None 表示该源全市场无数据（不参与合成）。"""
    n_total = bull = bear = neutral = 0
    w_sum = 0.0
    p_sum = 0.0
    n_active = 0
    pos_stocks = 0
    for c in cards:
        v = (c.get("src") or {}).get(src_name)
        if not v or not isinstance(v, dict) or not v.get("n"):
            continue
        n_total += v["n"]
        bull += v.get("bullish", 0) or 0
        bear += v.get("bearish", 0) or 0
        neutral += v.get("neutral", 0) or 0
        n_active += 1
        p = v.get("polarity")
        if p is not None:
            p_sum += float(p) * v["n"]
            w_sum += v["n"]
            if float(p) > 0:
                pos_stocks += 1
    if n_total == 0:
        return None
    out = {
        "n_total": n_total, "n_active": n_active,
        "bullish": bull, "neutral": neutral, "bearish": bear,
        "polarity_w": round(p_sum / w_sum, 4) if w_sum else None,
    }
    if n_active:
        out["breadth_pct"] = round(pos_stocks * 100.0 / n_active, 1)
    if bull and bear:
        out["bull_bear_ratio"] = round(bull / float(bear), 2)
    return out


def composite_index(src_aggs):
    """合成指数：源权重归一加权 → [0,100]。无任何极性源 → None。"""
    w_sum = 0.0
    p_sum = 0.0
    for name, w in SRC_WEIGHTS.items():
        agg = src_aggs.get(name)
        if not agg or agg.get("polarity_w") is None:
            continue
        p_sum += w * agg["polarity_w"]
        w_sum += w
    if not w_sum:
        return None
    return round(50 + 50 * (p_sum / w_sum), 1)


def market_breadth(cards):
    """全市场广度：有极性的活跃标的中 polarity>0 占比（跨源取极性均值
    的标的级口径：任一极性源 polarity>0 视作该标的多头家——保守取
    各标的极性源的 n 加权均值）。无极性标的 → 不计。"""
    pos = tot = 0
    for c in cards:
        ps = []
        ws = []
        for name in SRC_WEIGHTS:
            v = (c.get("src") or {}).get(name)
            if v and isinstance(v, dict) and v.get("polarity") is not None:
                ps.append(float(v["polarity"]) * v["n"])
                ws.append(v["n"])
        if not ws:
            continue
        tot += 1
        if sum(ps) / sum(ws) > 0:
            pos += 1
    return round(pos * 100.0 / tot, 1) if tot else None


def classify_state(index, breadth):
    """5 档状态机（极端档优先）。index None → unknown。"""
    if index is None:
        return "unknown"
    if index <= 30 or (breadth is not None and breadth <= 30):
        return "极度谨慎"
    if index >= 70 and breadth is not None and breadth >= 65:
        return "极度乐观"
    if index <= 42:
        return "谨慎"
    if index >= 58:
        return "乐观"
    return "中性"


def build_index(cards, generated_at=None, days=None):
    """全流程纯计算：cards → 指数契约 dict（不落盘）。"""
    src_aggs = {}
    for name in SRC_WEIGHTS:
        agg = aggregate_source(cards, name)
        if agg:
            src_aggs[name] = agg
    index = composite_index(src_aggs)
    breadth = market_breadth(cards)
    news = aggregate_source(cards, "新闻流")
    return {
        "generated_at": generated_at or datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"),
        "days": days,
        "index": index,
        "state": classify_state(index, breadth),
        "breadth_pct": breadth,
        "n_codes": len(cards),
        "n_samples": sum(a["n_total"] for a in src_aggs.values()) +
                     (news["n_total"] if news else 0),
        "sources": src_aggs,
        "news_volume": ({"n_total": news["n_total"],
                         "n_active": news["n_active"]} if news else None),
        "weights": dict(SRC_WEIGHTS),
        "ver": "market_sentiment_v1",
    }


# ── 落盘 ────────────────────────────────────────────────────────────
def _append_history(entry, path=HIST_F):
    """日快照：同日重跑覆盖同日行（历史序列一日一值，分位闸前提）。"""
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("date") != entry["date"]:
                    rows.append(r)
    rows.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return len(rows)


def render_report(idx, hist_n=None):
    L = ["# 市场级情绪指数 %s" % idx["generated_at"][:10], ""]
    L.append("**指数 %s / 100 · 状态「%s」 · 广度 %s%% · 六源样本 %s 条**" % (
        idx["index"], idx["state"], idx["breadth_pct"], idx["n_samples"]))
    L.append("")
    L.append("| 源 | 样本 | 活跃标的 | 多 | 中 | 空 | n加权极性 | 多空比 | 标的广度 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in ("股吧", "淘股吧", "雪球", "互动易", "e互动", "新闻流"):
        a = idx["sources"].get(name)
        if not a:
            L.append("| %s | - | - | - | - | - | - | - | - |" % name)
            continue
        L.append("| %s | %d | %d | %d | %d | %d | %s | %s | %s |" % (
            name, a["n_total"], a["n_active"], a["bullish"],
            a["neutral"], a["bearish"],
            a.get("polarity_w") if a.get("polarity_w") is not None else "-",
            a.get("bull_bear_ratio", "-"),
            ("%s%%" % a["breadth_pct"]) if "breadth_pct" in a else "-"))
    L.append("")
    L.append("口径：index=50+50·Σ(w·polarity_w)/Σw；权重 %s；"
             "新闻流仅声量（%s）。" % (
                 idx["weights"],
                 ("n=%d" % idx["news_volume"]["n_total"])
                 if idx.get("news_volume") else "无声量"))
    if hist_n:
        L.append("历史序列 %d 日（market_sentiment_history.jsonl）。" % hist_n)
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=CARDS_F)
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args()

    with open(args.cards, "r", encoding="utf-8") as f:
        contract = json.load(f)
    cards = contract["cards"]
    idx = build_index(cards, days=contract.get("days"))

    with open(INDEX_F, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)

    hist_n = None
    if not args.no_history and idx["index"] is not None:
        hist_n = _append_history({
            "date": idx["generated_at"][:10],
            "index": idx["index"], "state": idx["state"],
            "breadth_pct": idx["breadth_pct"],
            "n_samples": idx["n_samples"],
            "ver": idx["ver"],
        })

    rep_dir = os.path.join(PROJ, "reports")
    if not os.path.isdir(rep_dir):
        os.makedirs(rep_dir)
    rep = os.path.join(rep_dir, "market_sentiment_%s.md" %
                       idx["generated_at"][:10])
    with open(rep, "w", encoding="utf-8") as f:
        f.write(render_report(idx, hist_n))

    print("index=%s state=%s breadth=%s%% -> %s" % (
        idx["index"], idx["state"], idx["breadth_pct"], INDEX_F))
    print("report: %s" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
