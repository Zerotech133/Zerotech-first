# -*- coding: utf-8 -*-
r"""port v1 · 传播关系判定（news_collector/news_origin.py）单元测试。

【判例来源】2026-11-08 老田令：「要分原创，首发，转发」。
  现库只有 id/source/ts/title/content，无法回答"这条是谁先发的、谁抄谁"。
  下游把「同一篇稿被 4 家转」当 4 个独立信号、「南向资金净买入达 10 亿」
  这种每天复读的模板快讯当 23 个事件——热度虚增、方向被同一句话重复投票。

【实测底数（137,774 行）】
  · id 形如 `cns:scroll:HASH` / `cns:finance:HASH` 是同一篇稿两个频道各抓
    一次 → 1563 条采集重复（1.1%），跨源 0 条。`INSERT OR IGNORE` 拦不住，
    因为 id 前缀不同。
  · 跨源同题组的正文 3-gram Jaccard 中位 1.00、72% ≥0.85 → 阈值 0.5 够用。
  · 原创证据只在 chinanews 成立（64% 带中新电头）；eastmoney 81%/sina 43%
    正文以「【】」开头，是转载体例。

【硬边界】纯函数测试，不连库、不联网。

用例清单：
  NO-N01 P0 标题归一化：剥「【】」前缀与来源后缀、剔标点，字与数字保留
  NO-N02 P0 文章指纹忽略频道：cns:scroll:x 与 cns:finance:x 同指纹
  NO-N03 P0 不同 id 体系不误合并（sina:1 与 sina:12 是两条）
  NO-N04 P0 首见 + 本媒体电头 → 原创
  NO-N05 P0 首见 + 别家电头（中新网转新华社）→ 首发而非原创
  NO-N06 P0 跨源、1 分钟、正文一致 → 转发，且 repost_of 指回首发
  NO-N07 P0 跨源、同刻、一方无正文 → 判采集重复，不虚增转发
  NO-N08 P0 跨天同标题 → 分轮，各自成为新一轮首发（不被埋成 retell）
  NO-N09 P0 同源跨时点复读 → recur，不算转发
  NO-N10 P1 跨源但正文差异大 → 同题异稿 retell，不算转发
  NO-N11 P1 相似度对排版差异稳健（空白/标点不同仍判同稿）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NC = os.path.join(ROOT, "news_collector")
if NC not in sys.path:
    sys.path.insert(0, NC)

import news_origin as O  # noqa: E402


def D(y, m, d, hm=""):
    """日期/时间构造：走 %04d 而不是字符串字面量，避开输出通道串味。"""
    s = "%04d-%02d-%02d" % (y, m, d)
    return (s + " " + hm).strip()


def row(i, src, ts, title, content=""):
    return {"id": i, "source": src, "ts": ts, "title": title, "content": content}


# ── 归一化与指纹 ───────────────────────────────────────────

def test_no_n01_norm_title():
    """NO-N01 标题与正文的【】语义相反，必须分开归一化。

    实测：新浪把「【标题】正文前段」整段塞进 title 字段（

    东财只存标题本身）。若两者都只做「剥【】」，新浪那条会丢掉真标题、
    只剩正文片段，永远和东财合不上组；反之若都「取【】内文」，正文比较
    又会退化成标题比较，相似度虚高。故 norm_title 取【】内文、
    norm_text 剥【】留主体。
    """
    a = O.norm_title("【电动自行车新规实施】记者今天了解到，市场监管总局升级认证规则")
    b = O.norm_title("电动自行车新规实施")
    assert a == b
    assert O.norm_title("净买入达10亿港元。") != O.norm_title("净买入达20亿港元。")
    assert O.norm_title("标题 | 新浪财经") == O.norm_title("标题")
    assert O.norm_title("") == ""
    body = O.norm_text("【某标题】" + "正文内容" * 10)
    assert body.startswith("正文内容") and "某标题" not in body


def test_no_n02_article_fp_ignores_channel():
    """NO-N02 同 hash 跨频道 → 同指纹（这是 1563 条采集重复的根因）。"""
    a = O.article_fp("cns:scroll:c06b804f021b2d1a", "", "")
    b = O.article_fp("cns:finance:c06b804f021b2d1a", "", "")
    assert a == b
    assert a.startswith("h:")


def test_no_n03_article_fp_not_over_merged():
    """NO-N03 整串唯一的 id 不得互相合并。"""
    assert O.article_fp("sina:5201263", "", "") != O.article_fp("sina:5201262", "", "")
    assert O.article_fp("ann:AN202611081831530987", "", "").startswith("i:")


def test_no_n11_jaccard_robust_to_layout():
    """NO-N11 标点空白差异不影响"同稿"判断。"""
    a = "宁德时代在福鼎成立新材料科技公司，注册资本1.2亿元。"
    b = "宁德时代在福鼎成立新材料科技公司 注册资本 1.2亿元"
    assert O.jaccard(a, b) >= 0.9
    assert O.jaccard(a, "央行今日开展逆回购操作") < 0.2


# ── 传播角色 ───────────────────────────────────────────────

def test_no_n04_original_needs_own_masthead():
    """NO-N04 中新网自采（带中新电头）→ 原创。"""
    v = O.classify_group([row("cns:scroll:aaaa11112222", "chinanews",
                              D(2026, 11, 6, "09:00"), "某发布会举行",
                              "中新网北京11月6日电 记者获悉，发布会今日举行。")])
    assert v[0]["kind"] == "original"


def test_no_n05_other_masthead_is_first_not_original():
    """NO-N05 中新网转新华社稿 → 首发（不是原创，电头对不上本媒体）。"""
    v = O.classify_group([row("cns:scroll:bbbb11112222", "chinanews",
                              D(2026, 11, 6, "09:00"), "某发布会举行",
                              "新华社北京11月6日电 发布会今日举行。")])
    assert v[0]["kind"] == "first"


def test_no_n06_cross_source_repost_points_to_first():
    """NO-N06 跨源 1 分钟、正文一致 → 转发，repost_of 指回首发。"""
    body = "世界银行将为土耳其农业提供7.5亿美元贷款，用于灌溉设施改造。"
    rows = [
        row("sina:100", "sina", D(2026, 11, 6, "10:00"), "世界银行提供贷款", body),
        row("em:200", "eastmoney", D(2026, 11, 6, "10:01"), "世界银行提供贷款", body),
    ]
    v = O.classify_group(rows)
    by = {x["id"]: x for x in v}
    assert by["sina:100"]["kind"] == "first"
    assert by["em:200"]["kind"] == "repost"
    assert by["em:200"]["repost_of"] == "sina:100"
    assert by["em:200"]["lag_min"] == 1


def test_no_n07_blind_same_minute_is_dup_not_repost():
    """NO-N07 跨源、同刻、一方无正文（两个采集口抓同一份公告）→ 采集重复。

    实测样本：xd_notice 只存标题不存正文，announcement 存正文，
    同一份「投资者关系活动记录表」Δ=0、标题逐字不差。若判转发，
    等于把"两个采集口"虚增成"两家媒体转载"。
    """
    rows = [
        row("ann:AN1", "announcement", D(2026, 10, 15, "18:00"),
            "中科环保:2026年10月15日投资者关系活动记录表", "记录表正文……"),
        row("xd:notice:aa11bb22", "xd_notice", D(2026, 10, 15, "18:00"),
            "中科环保:2026年10月15日投资者关系活动记录表", ""),
    ]
    v = O.classify_group(rows)
    assert {x["kind"] for x in v[1:]} == {"dup"}


def test_no_n08_cross_day_starts_new_round():
    """NO-N08 同一句话隔天再发生 → 新一轮的首发，不能被埋成 retell。

    实测样本：「韩国交易所启动 SIDECAR 机制」10-13 / 10-15 / 10-30 / 11-05
    各播一次，每一轮都有自己的首发与转发链。
    """
    title = "韩国交易所启动SIDECAR机制，暂停KOSPI程序化卖盘。"
    body = title
    rows = [
        row("sina:1", "sina", D(2026, 10, 13, "08:48"), title, body),
        row("em:2", "eastmoney", D(2026, 10, 13, "08:49"), title, body),
        row("wsc:3", "wallstreetcn", D(2026, 10, 30, "09:05"), title, body),
        row("em:4", "eastmoney", D(2026, 10, 30, "09:07"), title, body),
    ]
    v = {x["id"]: x for x in O.classify_group(rows)}
    assert v["sina:1"]["kind"] == "first" and v["em:2"]["kind"] == "repost"
    assert v["wsc:3"]["kind"] == "first"      # 新一轮首发，不是 retell
    assert v["em:4"]["kind"] == "repost" and v["em:4"]["repost_of"] == "wsc:3"


def test_no_n09_same_source_recur():
    """NO-N09 同源跨时点复读 → recur（模板快讯，不是转发）。"""
    title = "南向资金净买入额达10亿港元。"
    rows = [
        row("sina:11", "sina", D(2026, 11, 2, "10:00"), title, title),
        row("sina:12", "sina", D(2026, 11, 2, "14:00"), title, title),
    ]
    v = O.classify_group(rows)
    assert v[1]["kind"] == "recur"


def test_no_n10_cross_source_low_similarity_is_retell():
    """NO-N10 同题但各写各的 → retell，不计为转发。"""
    title = "某公司发布三季度业绩"
    rows = [
        row("sina:21", "sina", D(2026, 11, 3, "09:00"), title,
            "公司三季度营收同比增长三成，毛利率提升两个百分点。"),
        row("em:22", "eastmoney", D(2026, 11, 3, "09:20"), title,
            "记者获悉，该公司在海外市场新签订单，产能利用率维持高位。"),
    ]
    v = O.classify_group(rows)
    assert v[1]["kind"] == "retell"


def test_no_n12_dup_same_id_hash():
    """NO-N12 同 id 内容 hash（跨频道各抓一次）→ 后入的那条判 dup。"""
    title = "我国面向全球的自贸网络已经初步成型"
    body = "新华社北京10月22日电 记者22日从国务院新闻办获悉……"
    rows = [
        row("cns:finance:c06b804f021b2d1a", "chinanews", D(2026, 10, 22, "15:14"), title, body),
        row("cns:scroll:c06b804f021b2d1a", "chinanews", D(2026, 10, 22, "15:14"), title, body),
    ]
    v = O.classify_group(rows)
    assert v[1]["kind"] == "dup"
    assert "同指纹" in v[1]["ev"]


def test_no_n13_empty_and_bad_ts_do_not_crash():
    """NO-N13 空组 / 坏时间戳 → 不抛异常，按原顺序给出判定。"""
    assert O.classify_group([]) == []
    v = O.classify_group([row("x:1", "sina", "坏时间", "标题", "")])
    assert v[0]["kind"] == "first"
