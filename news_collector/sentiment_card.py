# -*- coding: utf-8 -*-
"""五源情绪分布卡 —— P3：每标的跨平台情绪分布聚合。

源与口径（v1，2026-11-09）：
  · 股吧   data/guba/{code}.jsonl        引擎打分（sentiment_score）7 天窗
  · 淘股吧 data/taoguba/{code}.jsonl     引擎打分（游资口径）
  · 互动易 data/irm/{code}.jsonl         提问打分 + 已答率（updateDate 推断）
  · 雪球   xueqiu_info.db xq_status_classified（LLM 已标注）按 norm_tickers
           canonical={code}.SZ/.SH 关联，取原生 sentiment 分布
  · 新闻流 news.db（10 源）title 按代码/名称命中 7 天 → 舆情量（不打分）

产物：reports/sentiment_cards_{date}.md（逐标的卡）+ 终端汇总表。
CLI：python sentiment_card.py [--days 7] [--top 15] [codes]
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import sentiment_score as SS
import denoise as DN

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
GUBA_DIR = r"C:/aios/quant_backtest/data/guba"
TGB_DIR = os.path.join(os.path.dirname(HERE), "data", "taoguba")
IRM_DIR = os.path.join(os.path.dirname(HERE), "data", "irm")
XQ_DB = r"C:/aios/quant_backtest/data/xueqiu/xueqiu_info.db"
NEWS_DB = os.path.join(PROJ, "news_collector", "news.db")

_CODE2NAME = {}


def _name_of(code):
    return _CODE2NAME.get(code, "")


def _read_jsonl(path, days, ts_key="ts"):
    """读 jsonl，返回窗口内行列表（ts 形如 2026-11-09 10:38[:00]）。"""
    if not os.path.exists(path):
        return []
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts = str(r.get(ts_key) or "")
            if ts and ts[:10] >= cut:
                out.append(r)
    return out


def card_guba(code, days):
    rows = _read_jsonl(os.path.join(GUBA_DIR, "%s.jsonl" % code), days)
    if not rows:
        return None
    rows, dn = DN.denoise(rows)
    if not rows:
        return {"n": 0, "bullish": 0, "neutral": 0, "bearish": 0,
                "polarity": 0.0, "denoise": dn}
    texts = [("%s %s" % (r.get("title") or "", r.get("content") or "")).strip() for r in rows]
    scores = [SS.score_text(t) for t in texts]
    b, n, r_, pol = SS.dist_label(scores)
    return {"n": len(rows), "bullish": b, "neutral": n, "bearish": r_,
            "polarity": pol, "denoise": dn}


def card_taoguba(code, days):
    rows = _read_jsonl(os.path.join(TGB_DIR, "%s.jsonl" % code), days)
    if not rows:
        return None
    rows, dn = DN.denoise(rows)
    if not rows:
        return {"n": 0, "bullish": 0, "neutral": 0, "bearish": 0,
                "polarity": 0.0, "views": 0, "denoise": dn}
    texts = [("%s %s" % (r.get("title") or "", r.get("content") or "")).strip() for r in rows]
    scores = [SS.score_text(t) for t in texts]
    b, n, r_, pol = SS.dist_label(scores)
    views = sum(int(r.get("view") or 0) for r in rows if str(r.get("view") or "0").isdigit())
    return {"n": len(rows), "bullish": b, "neutral": n, "bearish": r_,
            "polarity": pol, "views": views, "denoise": dn}


def card_irm(code, days):
    rows = _read_jsonl(os.path.join(IRM_DIR, "%s.jsonl" % code), days)
    if not rows:
        return None
    # P2：互动易提问=规范书面语，走 0.6 模型+0.4 词典融合（模型不可用自动降级纯词典）
    scores = SS.score_fused_batch([r.get("content") or "" for r in rows])
    b, n, r_, pol = SS.dist_label(scores)
    # 已答口径（P1.1 终解）：has_reply=getQuestionDetail 实采回答全文（irm_backfill_answers 回填）
    answered = sum(int(r.get("has_reply") or r.get("answered") or 0) for r in rows)
    return {"n": len(rows), "bullish": b, "neutral": n, "bearish": r_,
            "polarity": pol, "answered": "%d/%d" % (answered, len(rows))}


def card_xueqiu(code, days):
    if not os.path.exists(XQ_DB):
        return None
    db = sqlite3.connect("file:%s?mode=ro" % XQ_DB.replace("\\", "/"), uri=True)
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    # canonical 格式实测：A 股="SZ300308"/"SH600xxx"，指数="300308.SZ" 例外，
    # HK=纯数字。用带引号边界匹配防子串误命中。
    pats = ('%%"%s"%%' % code,          # "SZ300308" / "SH300308" 类
            '%%%s.SZ%%' % code,         # 指数格式
            '%%%s.SH%%' % code)
    cond = " OR ".join("norm_tickers LIKE ?" for _ in pats)
    rows = db.execute(
        "SELECT sentiment FROM xq_status_classified "
        "WHERE parse_ok=1 AND labeled_at >= ? AND (%s)" % cond,
        [cut] + list(pats)).fetchall()
    db.close()
    if not rows:
        return None
    b = sum(1 for (s,) in rows if s == "bullish")
    r_ = sum(1 for (s,) in rows if s == "bearish")
    n = len(rows) - b - r_
    pol = round((b - r_) / len(rows), 3)
    return {"n": len(rows), "bullish": b, "neutral": n, "bearish": r_, "polarity": pol}


def card_news(code, days):
    if not os.path.exists(NEWS_DB):
        return None
    db = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.replace("\\", "/"), uri=True)
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    name = _name_of(code)
    pats = ["%%%s%%" % code] + (["%%%s%%" % name] if name and len(name) >= 3 else [])
    cond = " OR ".join("title LIKE ?" for _ in pats)
    n = db.execute("SELECT COUNT(*) FROM news WHERE ts >= ? AND (%s)" % cond,
                   [cut] + pats).fetchone()[0]
    db.close()
    return {"n": n} if n else None


def card_sse(code, days):
    """上证e互动问答（sns.sseinfo.com），与互动易同构。"""
    rows = _read_jsonl(os.path.join(os.path.dirname(HERE), "data", "sse",
                                    "%s.jsonl" % code), days)
    if not rows:
        return None
    # 提问=规范书面语 → 融合打分；回答=公司口径，单独计
    q_scores = SS.score_fused_batch([r.get("content") or "" for r in rows])
    b, n, r_, pol = SS.dist_label(q_scores)
    answered = sum(int(r.get("has_reply") or 0) for r in rows)
    return {"n": len(rows), "bullish": b, "neutral": n, "bearish": r_,
            "polarity": pol, "answered": "%d/%d" % (answered, len(rows))}


def build_card(code, days=7):
    return {
        "股吧": card_guba(code, days),
        "淘股吧": card_taoguba(code, days),
        "互动易": card_irm(code, days),
        "e互动": card_sse(code, days),
        "雪球": card_xueqiu(code, days),
        "新闻流": card_news(code, days),
    }


def _fmt(src, c):
    if not c:
        return "  —  "
    if src == "新闻流":
        return "%4d 条" % c["n"]
    if src == "互动易":
        return "%3d问 多%d/空%d 答%s" % (c["n"], c["bullish"], c["bearish"], c.get("answered", "?"))
    if src == "e互动":
        if not c["n"]:
            return "  —  "
        return "%3d问 多%d/空%d 答%s" % (c["n"], c["bullish"], c["bearish"], c.get("answered", "?"))
    return "%4d 多%3d/中%3d/空%3d 极性%+.2f" % (c["n"], c["bullish"], c["neutral"],
                                               c["bearish"], c["polarity"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="", help="逗号分隔；缺省全队列")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=15, help="报告展示条数（按总声量）")
    args = ap.parse_args()

    # 名称映射
    qpath = os.path.join(HERE, "target_queue_chanlun.json")
    if os.path.exists(qpath):
        q = json.load(open(qpath, encoding="utf-8"))
        for it in q.get("all", []):
            _CODE2NAME[it["code"].split(".")[0]] = it.get("name", "")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = sorted({p.split(os.sep)[-1].split(".")[0]
                        for d in (GUBA_DIR, TGB_DIR, IRM_DIR)
                        for p in glob.glob(os.path.join(d, "*.jsonl"))})
        codes = [c for c in codes if c.isdigit()]

    cards = {}
    for code in codes:
        cards[code] = build_card(code, args.days)

    # 汇总表：按总声量排
    def volume(c):
        return sum((s or {}).get("n", 0) for s in c.values())

    ranked = sorted(cards.items(), key=lambda kv: volume(kv[1]), reverse=True)
    date = datetime.now().strftime("%Y-%m-%d")
    L = ["# 五源情绪分布卡（%s，窗口 %d 天）" % (date, args.days), ""]
    L.append("| 标的 | 股吧 | 淘股吧 | 互动易 | e互动 | 雪球(LLM) | 新闻流 | 总声量 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for code, c in ranked[:args.top]:
        L.append("| %s %s | %s | %s | %s | %s | %s | %s | %d |" % (
            code, _name_of(code),
            _fmt("股吧", c["股吧"]), _fmt("淘股吧", c["淘股吧"]),
            _fmt("互动易", c["互动易"]), _fmt("e互动", c["e互动"]),
            _fmt("雪球", c["雪球"]), _fmt("新闻流", c["新闻流"]), volume(c)))
    L.append("")
    L.append("口径：股吧/淘股吧=词典引擎（金融域词表+否定翻转）；互动易/e互动=0.6 FinBERT+0.4 词典融合（规范文本）+回答全文实采已答率；")
    L.append("雪球=LLM 原生标注；新闻流=标题命中量（不打分）。— = 窗口内无数据。")

    report = os.path.join(PROJ, "reports", "sentiment_cards_%s.md" % date)
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # 机读契约（portal api_daily_data `sent` 节消费；JSON 文件是唯一契约，
    # 生成器换实现不影响出口——沿 sentiment_bt_result.json 判例）
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": args.days,
        "n_codes": len(codes),
        "cards": [
            {
                "code": code,
                "name": _name_of(code),
                "volume": volume(c),
                "src": c,
            }
            for code, c in ranked
        ],
    }
    out_json = os.path.join(HERE, "data", "sentiment_cards.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    print("\n".join(L[:40]))
    print("\n报告: %s\nJSON: %s (%d 标的)" % (report, out_json, len(codes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
