# -*- coding: utf-8 -*-
"""JEV 历史重放器（077k 线A）：逐交易日重放产线池 jev 分序列。

背景：news.db 保留全部历史（09-25 起 27157 条已判断新闻），
「仅近 7 天」只是产线查询窗口。本工具按 created_at 滚动窗重放
每个交易日 T 的池打分（公式与 _jev_pool_scores 一致：
score = 50 + 平均净多强度/2，bull/bear 计向、中性不计）。

歧义声明：关键词用现行 watchlist（历史池漂移不可还原）——
与 jev_archive 每日归档（c9d53bd）互补：归档存证当下，重放补历史。

用法：
  python jev_replay.py                 # 全窗口重放 → data/jev_archive/jev_replay_series.json
  python jev_replay.py --start 2026-09-28   # 指定起点

输出格式：{"meta": {...}, "series": {"2026-10-01": {code: {"score":61,"n":12}}}}
"""

import argparse
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
NEWS_DB = os.path.join(os.path.dirname(HERE), "news_collector", "news.db")
OUT = os.path.join(HERE, "data", "jev_archive", "jev_replay_series.json")
START_DEFAULT = "2026-09-25"
MATCH_LIMIT = 3000  # 与产线单次读库上限一致
HITS_CAP = 20  # 每股明细留存上限


def load_keywords():
    """现行 watchlist → {关键词: code}（与 _jev_pool_scores 同构）。"""
    import runner

    wl = json.load(
        open(os.path.join(runner.NEWS_DIR, "news_config.json"), encoding="utf-8")
    )["watchlist"]
    kws = {}
    for e in wl:
        code = e.get("code", "")
        if not code:
            continue
        ks = {e.get("name") or ""}
        for a in e.get("aliases") or []:
            if len(str(a)) >= 2:
                ks.add(str(a))
        ks.discard("")
        for k in ks:
            kws.setdefault(k, code)
    return kws


def load_judged():
    """全部已判断新闻 [(created_at, title, bias, conf)]（中性不入）。"""
    import sqlite3

    db = sqlite3.connect(NEWS_DB)
    rows = db.execute(
        "SELECT created_at, title, jev FROM news WHERE jev IS NOT NULL "
        "ORDER BY created_at"
    ).fetchall()
    db.close()
    out = []
    for ca, title, j in rows:
        if not title:
            continue
        try:
            jv = json.loads(j)
        except Exception:
            continue
        b = jv.get("bias")
        if b not in ("bull", "bear"):
            continue
        out.append((str(ca), title, b, (jv.get("conf") or 0) * 100))
    return out


def replay_day(day, news, kws):
    """重放某日池打分：滚动窗 [day-7d, day]，最新 MATCH_LIMIT 条内匹配。"""
    dt = datetime.date.fromisoformat(day)
    since = (dt - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    upto = day + " 23:59"
    win = [x for x in news if since <= x[0] <= upto][-MATCH_LIMIT:]
    agg = {}
    for ca, title, b, cf in win:
        for k, code in kws.items():
            if k in title:
                a = agg.setdefault(code, {"net": 0.0, "n": 0, "hits": []})
                a["net"] += cf if b == "bull" else -cf
                a["n"] += 1
                if len(a["hits"]) < HITS_CAP:
                    a["hits"].append(
                        {"kw": k, "title": title[:80], "bias": b, "conf": round(cf, 2)}
                    )
                break
    return {
        code: {
            "score": round(max(0, min(100, 50 + (a["net"] / a["n"]) / 2))),
            "n": a["n"],
            "hits": a["hits"],
        }
        for code, a in agg.items()
        if a["n"]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=datetime.date.today().isoformat())
    a = ap.parse_args()

    kws = load_keywords()
    news = load_judged()
    print("keywords=%d news=%d window %s ~ %s" % (len(kws), len(news), a.start, a.end))
    series, d = {}, datetime.date.fromisoformat(a.start)
    while d.isoformat() <= a.end:
        day = d.isoformat()
        pool = replay_day(day, news, kws)
        if pool:
            series[day] = pool
        d += datetime.timedelta(days=1)
    doc = {
        "meta": {
            "generated_at": datetime.datetime.now().isoformat(
                sep=" ", timespec="seconds"
            ),
            "start": a.start,
            "end": a.end,
            "days": len(series),
            "partial_last_day": True,
            "note": "关键词用现行 watchlist（历史池漂移不可还原，"
            "与每日归档互补）；末一日为重放时刻前的部分数据"
            "（077k 审查注），回测消费建议剔除末日",
        },
        "series": series,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("days=%d -> %s" % (len(series), OUT))
    for day in sorted(series)[-3:]:
        p = series[day]
        print(
            " ",
            day,
            "pool=%d" % len(p),
            " ".join(
                "%s:%s(n%d)" % (c, v["score"], v["n"]) for c, v in list(p.items())[:4]
            ),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
