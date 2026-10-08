# -*- coding: utf-8 -*-
"""K263：股吧舆论分析 —— 词频词云 + 恐慌度 + 事件契合关联。

消费 guba_crawl.py 采集的 data/guba/{code6}.jsonl：
  · jieba 分词（股民帖标题，news=0）→ 窗口词频 TopN（词云数据）；
  · 恐慌/乐观词表启发分 + 股吧自带 bullish_bearish 多空票 → 每日情绪；
  · 事件契合：events.jsonl 公告日（K260 tag）↔ 前后 1 日帖词云命中
    （tag 关键词组在词频前列 = 舆论重心与事件同频）。
被 kg_portal.api(kind="guba") 消费（门户作战地图舆论面板）。
CLI 自测：python guba_sentiment.py 000338.SZ --days 30
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
GUBA_DIR = os.path.join(HERE, "data", "guba")

# 停用词：通用虚词 + 股吧高频噪声（不承载情绪/主题）
STOP = set("""的 了 是 在 我 你 他 她 它 们 和 与 及 或 就 都 而 及 跟 被 把
这个 那个 什么 怎么 怎样 为什么 哪个 哪些 谁 多少 现在 今天 明天 昨天
大家 你们 我们 他们 一个 一些 有点 真的 还是 就是 不是 没有 还是 觉得
知道 应该 可能 可以 这样 那样 东西 时候 问题 事情 直接 继续 一下
出来 起来 下来 上去 过去 以后 以前 最近 目前 老师 各位 兄弟 老铁
股票 股吧 楼主 分享 查看 点击 回复 收藏 举报 阅读 评论 首页 更多
说 看 走 来 去 做 说 想 问 让 给 拿 等 才 又 再 也 很 太 挺 蛮 更
有 无 好 坏 大 小 多 少 高 低 上 下 中 前 后 个 只 条 亿 万 百""".split())

# 情绪词表（启发式，非全量情感词典——够用优先）
PANIC_WORDS = ["跑路", "出逃", "割肉", "完了", "暴跌", "崩盘", "垃圾",
               "退市", "索赔", "跌停", "血亏", "套牢", "傻眼", "雷了",
               "闪崩", "跳水", "绝望", "清仓", "避险"]
HOPE_WORDS = ["涨停", "大涨", "起飞", "满仓", "加仓", "抄底", "翻倍",
              "牛市", "利好", "突破", "新高", "启动", "爆发", "龙头",
              "中签", "分红"]

# 事件 tag → 舆论契合词组（tag 词 + 股民口语变体）
TAG_WORDS = {
    "股东减持": ["减持", "套现", "跑路"],
    "监管处罚": ["处罚", "立案", "警示", "违规"],
    "诉讼冻结": ["诉讼", "冻结", "起诉", "官司"],
    "退市风险": ["退市", "ST", "摘牌"],
    "业绩预亏": ["预亏", "亏损", "下滑"],
    "终止重组": ["终止", "黄了", "告吹"],
    "业绩预增": ["预增", "增长", "翻倍"],
    "回购增持": ["回购", "增持"],
    "分红派息": ["分红", "派息", "股息"],
    "股权激励": ["激励", "期权"],
    "中标合同": ["中标", "订单", "合同"],
    "资产收购": ["收购", "并购"],
    "资产重组": ["重组"],
    "对外投资": ["投资", "扩产"],
    "股权转让": ["转让", "受让"],
    "合作协议": ["合作", "签约"],
    "政府补助": ["补助", "补贴"],
}


# 词云同义归一（口径同 K266 热词归一）：只并真同义变体，不并组合词/方向词。
# 计数与词反查同源：反查时规范词自动扩展变体子串命中。
# 2026-10-08 按全量数据（61 股 90 日 4383 词）核定。
WORD_ALIAS = {
    "房地产": "地产",
    "投资人": "投资者",
    "国庆节": "国庆",
    "涨停板": "涨停",
}

# K263-W7 词表外置：data/lexicon.json（portal /kg/lexicon 维护页写入）。
# json 键存在则覆盖内置默认（py 内值仅为出厂兜底，json 是唯一编辑源）；
# mtime 变更时懒加载——portal 长跑进程改词表免重启，短跑脚本（alias_audit
# 等）进程每次 import 自动取新值。保存端写全量快照。
_LEX_FP = os.path.join(HERE, "data", "lexicon.json")
_LEX_MTIME = -1.0


def _apply_lexicon(force=False):
    """词表热加载：lexicon.json mtime 变了就覆盖对应全局词表。"""
    global STOP, PANIC_WORDS, HOPE_WORDS, TAG_WORDS, WORD_ALIAS, _LEX_MTIME
    try:
        m = os.path.getmtime(_LEX_FP)
    except OSError:
        return
    if not force and m == _LEX_MTIME:
        return
    try:
        with open(_LEX_FP, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("stop"), list):
            STOP = set(d["stop"])
        if isinstance(d.get("panic"), list):
            PANIC_WORDS = list(d["panic"])
        if isinstance(d.get("hope"), list):
            HOPE_WORDS = list(d["hope"])
        if isinstance(d.get("tag_words"), dict):
            TAG_WORDS = dict(d["tag_words"])
        if isinstance(d.get("word_alias"), dict):
            WORD_ALIAS = dict(d["word_alias"])
        _LEX_MTIME = m
    except (OSError, ValueError):
        pass


_apply_lexicon(force=True)


def _code6(tc):
    c = str(tc or "").strip().lower()
    if "." in c:
        c = c.split(".")[0]
    return re.sub(r"^(sz|sh|bj)", "", c)


def _load_posts(tc, days):
    """jsonl → 近 days 天股民帖（news=0）。"""
    p = os.path.join(GUBA_DIR, _code6(tc) + ".jsonl")
    if not os.path.exists(p):
        return []
    cutoff = (datetime.now() - timedelta(days=days)
              ).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("news"):
                continue
            if (r.get("ts") or "") < cutoff:
                continue
            out.append(r)
    out.sort(key=lambda r: r.get("ts") or "")
    return out


def _tokens(title):
    import jieba
    return [w for w in jieba.lcut(str(title))
            if len(w) >= 2 and w not in STOP and not re.match(
                r"^[\dA-Za-z%.:：,，！!？?]+$", w)]


def guba_summary(tc, days=30, top_n=40, word=None):
    """主分析：词云 + 每日情绪 + 事件契合。

    K263 二期②：word 非空 → 词反查模式，返回含该词的帖子列表
    （标题子串命中，最新在前，上限 50）。
    """
    _apply_lexicon()          # 词表热加载（portal 维护页改完免重启生效）
    posts = _load_posts(tc, days)
    if not posts:
        return {"ok": False, "error": "无采集数据（先跑 guba_crawl.py）",
                "code": tc}
    if word:
        # 词反查：规范词自动扩展变体（点"地产"能搜回标题写"房地产"的帖）
        variants = [word] + [k for k, v in WORD_ALIAS.items() if v == word]
        hits = [r for r in posts
                if any(v in str(r.get("title") or "") for v in variants)][:50]
        return {"ok": True, "code": tc, "word": word, "n": len(hits),
                "posts": [{"ts": (r.get("ts") or "")[:10],
                           "title": r.get("title"),
                           "click": r.get("click"),
                           "comment": r.get("comment"),
                           "user": r.get("user"), "bb": r.get("bb")}
                          for r in reversed(hits)]}
    # 全窗词频（词云）
    wf = Counter()
    day_posts = {}      # date → [(title, bb)]
    for r in posts:
        toks = _tokens(r["title"])
        wf.update(WORD_ALIAS.get(w, w) for w in toks)
        d = (r.get("ts") or "")[:10]
        day_posts.setdefault(d, []).append((r["title"], r.get("bb")))
    # 每日情绪：恐慌/乐观词命中 + bb 多空票
    days_out = []
    for d in sorted(day_posts):
        titles = [t for t, _ in day_posts[d]]
        bbs = [b for _, b in day_posts[d] if b in (1, -1)]
        panic = sum(len(re.findall("|".join(PANIC_WORDS), t)) for t in titles)
        hope = sum(len(re.findall("|".join(HOPE_WORDS), t)) for t in titles)
        days_out.append({
            "date": d.replace("-", ""), "n": len(titles),
            "panic": panic, "hope": hope,
            "bb_bull": sum(1 for b in bbs if b == 1),
            "bb_bear": sum(1 for b in bbs if b == -1),
            "mood": ("panic" if panic > hope + 1 else
                     "hope" if hope > panic + 1 else "flat"),
        })
    # 事件契合：公告 tag 日 ↔ 前后 1 日词频命中
    events = []
    try:
        sys.path.insert(0, HERE)
        from event_graph import events_for_stock
        ev = events_for_stock(tc, days=days)
        day_set = {d.replace("-", ""): d for d in day_posts}
        for e in ev.get("events", []):
            tag = e.get("tag")
            if not tag or tag == "中性披露" or tag not in TAG_WORDS:
                continue
            d8 = (e.get("ts") or "")[:10].replace("-", "")
            from datetime import date as _d, timedelta as _td
            try:
                anchor = datetime.strptime(d8, "%Y%m%d").date()
            except Exception:
                continue
            win = [(anchor + timedelta(days=offs)).strftime("%Y%m%d")
                   for offs in (-1, 0, 1)]
            hit = []
            for w in win:
                dd = day_set.get(w)
                if not dd:
                    continue
                for t, _ in day_posts[dd]:
                    for kw in TAG_WORDS[tag]:
                        if kw in t and kw not in hit:
                            hit.append(kw)
            events.append({"date": d8, "tag": tag, "dir": e.get("bias"),
                           "hit_words": hit,
                           "matched": bool(hit)})
    except Exception:
        pass
    bb_bull = sum(1 for r in posts if r.get("bb") == 1)
    bb_bear = sum(1 for r in posts if r.get("bb") == -1)
    return {
        "ok": True, "code": tc, "n_posts": len(posts),
        "range": [(posts[0].get("ts") or "")[:10],
                  (posts[-1].get("ts") or "")[:10]],
        "words": [{"w": w, "n": n} for w, n in wf.most_common(top_n)],
        "days": days_out,
        "bb": {"bull": bb_bull, "bear": bb_bear},
        "events": events,
        "event_match_rate": (round(sum(1 for e in events if e["matched"])
                                   / len(events), 2) if events else None),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    print(json.dumps(guba_summary(a.code, a.days), ensure_ascii=False, indent=1))
