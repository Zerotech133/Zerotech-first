# -*- coding: utf-8 -*-
r"""新闻传播关系判定：原创 / 首发 / 转发 / 采集重复 / 周期重播。

【为什么要这个】2026-11-08 老田令：「要分原创，首发，转发」。
  现库只有 id/source/ts/title/content，没有任何"这条是谁先发的、谁抄谁"的
  信息。下游 JEV 聚合因此把「同一篇稿被 4 家转」当成 4 个独立信号，把
  「南向资金净买入达 10 亿」这种每天复读的模板快讯当成 23 个事件——
  热度被虚增，方向统计被同一篇稿重复投票。

【实测底数（137,774 行，2026-11-08）】
  · id 结构自带线索：`cns:scroll:HASH` 与 `cns:finance:HASH` 是同一篇稿在
    两个频道各抓一次，hash 相同 → 1563 条采集重复（1.1%），全部来自
    chinanews，跨源 0 条。`INSERT OR IGNORE` 拦不住它，因为 id 前缀不同。
  · 标题归一化后重复：3800 组，其中跨源 1322 组 —— 这才是"转发"的候选。
  · 跨源同题组的正文 3-gram Jaccard：中位 1.00、72% ≥0.85 → 同题基本就是
    同一篇稿原文照搬，阈值取 0.5（覆盖 87%）足够。
  · 原创证据只在 chinanews 可用：64% 正文带「中新社/中新网…电」电头、
    34% 带记者署名；eastmoney 81%、sina 43% 的正文以「【】」开头，是转载
    体例，不是原创。故 original 只认「首见 + 电头属于本媒体」，其余一律
    记 first（首见但出处未证实），不猜。

【五档口径】
  original  原创：组内首见，且正文电头指向该源自身（目前只 chinanews 成立）
  first     首发：组内首见，但无法证明原创（编译/转载的第一棒）
  repost    转发：跨源、时间接近、正文高度相似 → repost_of 指向首发
  dup       采集重复：同一源同一篇稿被重复入库（频道重叠/短时重抓）
  recur     周期重播：同一源同一句话在不同时点复读（模板快讯），非新闻传播
  retell    同题异稿：跨源、标题同但正文不同（各写各的），不算转发

【硬边界】纯标准库；不调模型、不联网；只读现有字段，不删行、不改内容。
"""
from __future__ import annotations

import hashlib
import re

# ── 标题归一化 ─────────────────────────────────────────────
# 各源给同一篇稿加的前缀不一致：eastmoney「【标题】正文…」、新浪「【标题】」、
# 部分源带「| 来源」后缀。指纹必须先剥掉这些体例差异，否则同一篇稿算两组。
#
# 注意 title 与 content 的【】语义相反（实测）：
#   title   字段：新浪等把「【标题】正文前段」整段塞进 title → 【】里才是标题
#   content 字段：东财等把「【标题】正文」放正文     → 【】是引题，要去掉
# 用同一个函数处理两者，会让正文比较退化成标题比较（相似度虚高），
# 也会把标题里真正的内容扔掉。故分成 norm_title / norm_text。
_PREFIX = re.compile(r"^[\s\u3000]*[【\[]([^】\]]{1,80})[】\]]")
_SUFFIX = re.compile(r"\s*[|｜\-—]\s*(?:新浪财经|东方财富网?|华尔街见闻|中国新闻网|"
                     r"证券时报网?|财联社|界面新闻|第一财经)\s*$")
# 标点/空白/全角符号一律剔除；数字与汉字保留（"净买入10亿"与"20亿"是
# 两次不同播报，不能合并）
_NOISE = re.compile(r"[\s\u3000-\u303f\uff00-\uffef"
                    r"!-/:-@\[-`{-~]")


def norm_title(title: str) -> str:
    """标题归一化：带【】的取【】内文（那才是标题），再剥后缀与标点。"""
    t = (title or "").strip()
    m = _PREFIX.match(t)
    if m:
        t = m.group(1)
    t = _SUFFIX.sub("", t)
    return _NOISE.sub("", t)


def norm_text(text: str) -> str:
    """正文归一化：剥掉「【标题】」引题，保留正文主体。"""
    t = (text or "").strip()
    m = _PREFIX.match(t)
    if m:
        # 【】后面还有实质正文 → 只丢引题；后面几乎没字 → 【】内就是全部
        t = t[m.end():] if len(t) - m.end() >= 20 else m.group(1)
    t = _SUFFIX.sub("", t)
    return _NOISE.sub("", t)


def title_fp(title: str) -> str:
    """标题指纹（16 位）：跨源比对"是不是同一句话"用。"""
    n = norm_title(title)
    if not n:
        return ""
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:16]


# ── 文章指纹 ───────────────────────────────────────────────
# 只有形如 `<媒体>:<频道>:<hex>` 的 id 才需要"忽略频道取 hash"——同一篇稿
# 在滚动/财经两个频道各有一条，id 前缀不同但 hash 相同。其余 id（ann/em/
# sina/wsc/report/survey/govpol）本身就是唯一编号，整串即指纹。
_HEX = re.compile(r"^[0-9a-f]{12,32}$")


def article_fp(news_id: str, title: str = "", content: str = "") -> str:
    """文章指纹：优先 id 里的内容 hash，退化到标题+正文前 200 字。"""
    parts = (news_id or "").split(":")
    if len(parts) >= 3 and _HEX.match(parts[-1]):
        return "h:" + parts[-1]
    if news_id:
        return "i:" + news_id
    seed = (norm_title(title) + (content or "")[:200]).encode("utf-8")
    return "t:" + hashlib.sha1(seed).hexdigest()[:16]


# ── 正文相似度（字符 3-gram Jaccard）────────────────────────
def shingles(text: str, n: int = 3) -> set:
    # 正文走 norm_text（剥【标题】引题），不能走 norm_title——否则两段正文
    # 实际只在比标题，相似度会虚高，"同题异稿"会被误判成"同一篇稿"。
    s = norm_text(text)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a: str, b: str, n: int = 3) -> float:
    A, B = shingles(a, n), shingles(b, n)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


# ── 原创证据 ───────────────────────────────────────────────
# 电头只能证明"稿件出自哪家"，不能证明"这个源就是原创方"。所以判 original
# 必须电头与本媒体对得上；对不上（如中新网转新华社稿）一律记 first。
_MEDIA_HEAD = {
    "chinanews": re.compile(r"中新[社网][^。]{0,14}电"),
}
# 反向证据：出现别家电头 = 这条是转载
_OTHER_HEAD = re.compile(r"(新华社|人民网|央视新闻|经济日报|证券时报|中国证券报)"
                         r"[^。]{0,14}电")
# 转载体例：正文以「【标题】」开头（东财/新浪快讯转载格式）
_REPOST_STYLE = re.compile(r"^[\s\u3000]*【")


def _has_own_head(source: str, content: str) -> bool:
    """正文电头是否指向该源自身（可证原创）。"""
    rx = _MEDIA_HEAD.get(source)
    if not rx or not content:
        return False
    return bool(rx.search(content)) and not _OTHER_HEAD.search(content)


def is_repost_style(content: str) -> bool:
    """正文是否是转载体例（【】开头）。仅作辅助证据，不单独定案。"""
    return bool(_REPOST_STYLE.match(content or ""))


# ── 判定阈值 ───────────────────────────────────────────────
SIM_REPOST = 0.50      # 跨源正文相似度 ≥ 此值 → 视为同一篇稿（实测覆盖 87%）
SIM_DUP = 0.90         # 同源短时重复入库
DUP_WINDOW_MIN = 30    # 同源 30 分钟内同标题 → 采集重复
REPOST_WINDOW_MIN = 1440   # 转发只认当天（24h）
BLIND_REPOST_MIN = 360     # 双方无正文时，靠标题 + 6 小时内 兜底判转发


def _minutes_between(ts_a: str, ts_b: str):
    """两个 'YYYY-MM-DD HH:MM' 之间的分钟差；解析失败返回 None。"""
    import datetime as dt
    try:
        a = dt.datetime.strptime(str(ts_a)[:16], "%Y-%m-%d %H:%M")
        b = dt.datetime.strptime(str(ts_b)[:16], "%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return None
    return int((b - a).total_seconds() // 60)


def _split_rounds(items):
    """按 24h 断裂把同标题组切成"轮"。

    为什么必须分轮：同一句话会隔几天再发生一次（「韩国交易所启动 SIDECAR
    机制」「南向资金净买入达 10 亿」这类模板快讯），10-13 那波和 10-30 那波
    是两个独立事件。不分轮的话，10-30 的独家首发会被拿去和 10-13 的基准比，
    判成"相距 24497 分钟的 retell"，把一次真首发埋掉。
    """
    rounds, cur = [], [items[0]]
    for r in items[1:]:
        lag = _minutes_between(cur[0]["ts"], r["ts"])
        if lag is not None and lag > REPOST_WINDOW_MIN:
            rounds.append(cur)
            cur = [r]
        else:
            cur.append(r)
    rounds.append(cur)
    return rounds


def classify_group(rows) -> list:
    """判定同一「标题指纹」组内的传播关系。

    rows: 可迭代，每项需含 id / source / ts / title / content（dict 或
          具名元组皆可，用 [] 取值）。
    返回与 rows 等长的 list[dict]：kind / repost_of / lag_min / ev / fp。

    步骤：先按 24h 断裂分轮，再在**每轮内部**按 ts 升序判定（轮首为基准）：
      轮首         → original（本媒体电头）/ first（其余）
      同源后续     → dup（同 fp，或 30 分钟内高相似）/ recur（模板快讯复读）
      跨源后续     → dup（同刻且一方无正文 = 两个采集口抓同一份公告）/
                     repost（≤24h 且相似 ≥0.5，或一方无正文但 ≤6h）/
                     retell（同题异稿：各写各的）
    """
    items = sorted(rows, key=lambda r: str(r["ts"] or ""))
    out = []
    if not items:
        return out
    seen_fp = set()
    for rnd in _split_rounds(items):
        base = rnd[0]
        base_fp = article_fp(base["id"], base["title"], base["content"])
        base_own = _has_own_head(base["source"], base["content"])
        base_kind = "original" if base_own else "first"
        base_ev = ("首见；正文电头为 %s 自身" % base["source"]) if base_own \
            else "首见；无本媒体电头，出处未证实"
        if base_fp in seen_fp:
            base_kind, base_ev = "dup", "同指纹重复入库（id 内容 hash 相同）"
        seen_fp.add(base_fp)
        out.append({"id": base["id"], "fp": base_fp, "kind": base_kind,
                    "repost_of": None, "lag_min": 0, "ev": base_ev})

        for r in rnd[1:]:
            fp = article_fp(r["id"], r["title"], r["content"])
            lag = _minutes_between(base["ts"], r["ts"])
            same_src = r["source"] == base["source"]
            sim = jaccard(base["content"], r["content"]) if (
                base["content"] and r["content"]) else None

            if fp in seen_fp:
                kind, ev = "dup", "同指纹重复入库（id 内容 hash 相同）"
            elif same_src:
                if lag is not None and lag <= DUP_WINDOW_MIN and (
                        sim is None or sim >= SIM_DUP):
                    kind, ev = "dup", "同源 %d 分钟内同标题重复抓取" % lag
                elif sim is not None and sim < SIM_REPOST:
                    # 同源、同标题、正文却差很远 → 固定栏目复用标题（「美股盘前
                    # 要闻速递」「南向资金」这类每天一条、内容全新）。它不是
                    # 复读，是当天的独立事件；判 recur 会把整年的盘前速递压成
                    # 一条，等于把 20 天的信息全丢掉。
                    kind = "column"
                    ev = "同源栏目复用：标题相同但正文相似仅 %.2f（内容为新事件）" % sim
                else:
                    kind, ev = "recur", "同源复读：同一句话在不同时点再次播报"
            else:
                # 跨源、几乎同刻、且至少一方没有正文 → 不是"媒体转媒体"，是同一份
                # 内容（公告/研报）被两个采集器各抓一次。实测 xd_notice 与
                # announcement 对同一份投资者关系记录表就是这种：Δ=0、标题逐字
                # 不差、xd_notice 侧只存标题不存正文。判 dup 而非 repost，避免把
                # "两个采集口"虚增成"两家转载"。
                if (lag is not None and lag <= DUP_WINDOW_MIN
                        and not (base["content"] and r["content"])):
                    kind = "dup"
                    ev = "跨源同刻同题、一方无正文（同一份内容被两个采集口各抓一次）"
                elif lag is not None and lag <= REPOST_WINDOW_MIN and sim is not None:
                    if sim >= SIM_REPOST:
                        kind = "repost"
                        ev = "跨源转发：落后 %d 分钟，正文相似 %.2f" % (lag, sim)
                    else:
                        kind = "retell"
                        ev = "同题异稿：跨源但正文相似仅 %.2f" % sim
                elif lag is not None and lag <= BLIND_REPOST_MIN:
                    kind = "repost"
                    ev = "跨源转发：落后 %d 分钟（无正文可比，按标题+时窗）" % lag
                else:
                    kind, ev = "retell", "同题异稿：跨源但时间相距过远"
            seen_fp.add(fp)
            out.append({"id": r["id"], "fp": fp, "kind": kind,
                        "repost_of": base["id"] if kind in ("repost", "retell") else None,
                        "lag_min": lag, "ev": ev})
    return out


# ── 增量路径（采集写入时即时判定）──────────────────────────
_COLS = ("origin_kind", "origin_fp", "repost_of", "lag_min", "origin_ev")


def judge_and_write(con, news_id: str, source: str, ts: str,
                    title: str, content: str = "") -> dict:
    """新入库的一条新闻即时判定并回写字段；失败只返回 {} 不抛。

    为什么不能等离线回填：采集是每分钟都在写，回填是手工跑的。中间这段
    空窗里，聚合层读到的 origin_kind 全是 NULL，"转发算不算独立信号"的
    判断就永远起不了作用。所以入库即判，离线回填只负责补历史。

    口径与全量完全一致：拉出同标题组的已有行，连同新行一起交给
    classify_group，取新行那一条结果（新行不一定是最晚的——补采时可能是
    中间时刻，所以按 id 匹配而不是取末尾）。
    """
    tfp = title_fp(title)
    if not tfp or not news_id:
        return {}
    row = {"id": news_id, "source": source, "ts": ts,
           "title": title, "content": content or ""}
    try:
        peers = [dict(id=r[0], source=r[1], ts=r[2], title=r[3], content=r[4] or "")
                 for r in con.execute(
                     "SELECT id, source, ts, title, content FROM news"
                     " WHERE origin_fp=? AND id<>?", (tfp, news_id))]
        verdicts = classify_group(peers + [row])
        v = next((x for x in verdicts if x["id"] == news_id), None)
        if not v:
            return {}
        con.execute(
            "UPDATE news SET origin_kind=?, origin_fp=?, repost_of=?,"
            " lag_min=?, origin_ev=? WHERE id=?",
            (v["kind"], tfp, v["repost_of"], v["lag_min"], v["ev"], news_id))
        return v
    except Exception:  # noqa: BLE001 —— 判定属于"锦上添花"，绝不阻断入库
        return {}
