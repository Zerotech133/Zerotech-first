# -*- coding: utf-8 -*-
"""P3 去噪层：股吧/淘股吧散户文本的 spam/promotion 检测（规则版 v1）。

借鉴 RetailTide 五维 LLM 标注思路，先用零成本规则层落三档：
  spam      —— 同模板重复水军（同窗口内同文本出现 >=3 次）→ 过滤
  promotion —— 推广话术（加微/直播间/财富密码/带你吃肉…）→ 过滤
  fomo      —— 诱导话术（错过/速来/最后机会）→ 仅打标不过滤（误杀风险高）

2026-11-09 实测噪声密度：股吧 0.6%（40 文件 5037 条）、淘股吧 0.2%（596 条）。
规则只求高查全下的可接受查准：推广词表用强信号词，弱信号（翻倍/牛股等歧义词）
必须组合出现才判。另附 clean_html 清洗淘股吧 summary 的 HTML 转义残留。

导入即用；集成点=sentiment_card.py 打分前过滤。
"""
import html
import re

# ---- 强信号推广词（单词命中即判）----
PROMO_STRONG = re.compile(
    r"(加微|加V|加vx|V信|威信|微?信.{0,3}[1-9一二三四五六七八九]{3,}|私聊|私我|私信领|"
    r"建.{0,2}群|入群|交流群|免费领|免费送|领取.{0,4}(牛股|福利|资料)|"
    r"直播间|财富密码|财富之路|带你吃肉|带你飞|带你埋伏|全程带|"
    r"收.{0,2}学员|招生|一对一指导|操作建议.{0,4}私|实力带单|老师.{0,4}(带|指导|荐)|"
    r"(资金|合作).{0,4}账号|代客理财|配资|外盘|恒指|德指|纳指.{0,3}操作)")

# ---- 弱信号词：需 >=2 个不同弱信号同现才判 ----
PROMO_WEAK = ["牛股", "翻倍", "暴涨", "内幕", "独家", "精准", "稳赚", "必涨",
              "跟上节奏", "上车", "建仓.{0,4}通知", "目标价.{0,3}翻", "错过", "速来",
              "最后.{0,3}机会", "名额有限", "限时"]
_WEAK_RE = [re.compile(w) for w in PROMO_WEAK]

# FOMO 诱导（仅标记）
FOMO_RE = re.compile(r"(再不上车就|最后.{0,3}机会|过这村没这店|速来|抓紧上车|末班车)")

# URL（股吧正文外链多为导流）
URL_RE = re.compile(r"(https?://|www\.[\w-]+\.|t\.cn/|cn\.miaopai)")

SPAM_REPEAT_MIN = 3  # 同窗口同文本重复阈值


def clean_html(text):
    """清洗 HTML 转义残留与内联标签（淘股吧 summary 实测有 &lt;font&gt; 残留）。"""
    if not text:
        return text
    t = str(text)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return t.strip()


def spam_label(text):
    """单条文本静态检测。返回 (is_spam, reason)；fomo 只标记不判 spam。"""
    t = str(text or "")
    if not t:
        return False, ""
    if PROMO_STRONG.search(t):
        return True, "promotion"
    # FOMO 在弱组合之前：诱导话术常混真实观点，只标记不杀
    fomo_hit = bool(FOMO_RE.search(t))
    weak_hits = sum(1 for r in _WEAK_RE if r.search(t))
    if weak_hits >= 2 and not fomo_hit:
        return True, "promotion_weak"
    if URL_RE.search(t) and weak_hits >= 1:
        return True, "promotion_url"
    if fomo_hit:
        return False, "fomo"
    return False, ""


def denoise(rows, text_key=("title", "content", "news", "bb", "summary")):
    """行级去噪。rows: list[dict]；返回 (kept, stats)。
    stats: {"spam_repeat": n, "spam_promo": n, "fomo": n, "kept": n, "total": n}
    spam 判定后仍保留在 kept 外的独立列表——这里直接丢弃，stats 计数。"""
    from collections import Counter
    def _text(r):
        return " ".join(str(r.get(k) or "") for k in text_key).strip()
    # 模板重复：规范化（去空白/标点差异）后计数
    norm_counter = Counter(re.sub(r"[\s，。！？、,!?.\-—~～]+", "", _text(r)) for r in rows)
    kept, stats = [], {"spam_repeat": 0, "spam_promo": 0, "fomo": 0,
                       "kept": 0, "total": len(rows)}
    seen_norm = set()
    for r in rows:
        t = _text(r)
        norm = re.sub(r"[\s，。！？、,!?.\-—~～]+", "", t)
        # 1) 模板重复水军
        if norm and norm_counter[norm] >= SPAM_REPEAT_MIN:
            stats["spam_repeat"] += 1
            continue
        # 2) 推广话术
        is_spam, reason = spam_label(t)
        if is_spam:
            stats["spam_promo"] += 1
            continue
        if reason == "fomo":
            stats["fomo"] += 1
        # 3) HTML 清洗后保留
        r = dict(r)
        for k in text_key:
            if r.get(k):
                r[k] = clean_html(r[k])
        kept.append(r)
    stats["kept"] = len(kept)
    return kept, stats


if __name__ == "__main__":
    cases = [
        ("多维时空欢迎你，当你点击文章那一刻起，你的财富之路将被开启", True),   # 推广
        ("加微信 lao8888 领取明日牛股名单", True),                            # 推广
        ("实力带单老师指导操作，名额有限速来", True),                          # 推广(强+弱)
        ("这票要翻倍了", False),                                             # 弱信号单命中→放行
        ("独家内幕牛股稳赚必涨", True),                                       # 弱信号4连→判
        ("业绩快报低于预期，年报可能暴雷", False),                             # 正常看空
        ("再不上车就晚了，最后机会", False),                                   # fomo 标记不杀
        ("今天缩量调整，正常", False),
    ]
    ok = True
    for t, want in cases:
        got, why = spam_label(t)
        mark = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print("%s spam=%-5s (%s) | %s" % (mark, got, why or "-", t[:26]))
    # 模板重复
    rows = [{"title": "多维时空欢迎你"}, {"title": "多维时空欢迎你"},
            {"title": "多维时空欢迎你"}, {"title": "正常帖子"}]
    kept, stats = denoise(rows, text_key=("title",))
    print("模板重复: kept=%d stats=%s" % (kept and len(kept), stats))
    assert stats["spam_repeat"] == 3
    print("\n全部通过" if ok and len(kept) == 1 else "\n有失败用例")
