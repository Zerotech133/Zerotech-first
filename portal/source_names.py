# -*- coding: utf-8 -*-
"""渠道（source）标识 → 中文名：全站单一事实源。

老田令 2026-11-07：「渠道说中文」。news.db 里 source 是采集端的内部标识
（sina / xd_news / announcement…），直接给用户看等于让人猜。所有对外
JSON 统一附 source_cn；前端只读该字段，页面里不再各写一份映射表。

约定：
- 已知渠道走 SOURCE_CN；未知渠道原样返回（不编造、不显示空白）；
- source id 保留在响应里（兼容既有消费方 / 排障对日志）；
- 需要更精确口径时用 desc()，给「这是哪一类来源」的一句话。
"""

SOURCE_CN = {
    # ── 财经快讯类 ──
    "sina": "新浪财经",
    "eastmoney": "东方财富",
    "wallstreetcn": "华尔街见闻",
    # ── 官方披露类 ──
    "announcement": "交易所公告",
    "xd_notice": "通达信公告",
    "govpolicy": "政府政策",
    # ── 媒体类 ──
    "chinanews": "中国新闻网",
    "world": "海外媒体",
    # ── 数据中台新闻池 ──
    "xd_news": "通达信新闻",
    # ── 机构类 ──
    "report": "机构研报",
    "survey": "调研纪要",
}

SOURCE_DESC = {
    "sina": "新浪财经 7×24 快讯",
    "eastmoney": "东方财富快讯",
    "wallstreetcn": "华尔街见闻资讯流",
    "announcement": "沪深交易所信息披露（东财公告接口）",
    "xd_notice": "数据中台公告池（stock_notice）",
    "govpolicy": "中国政府网政策文件库",
    "chinanews": "中国新闻网滚动/财经 RSS",
    "world": "海外财经媒体与监管机构 RSS",
    "xd_news": "数据中台新闻池（stock_news）",
    "report": "券商研报（东财研报接口）",
    "survey": "机构调研纪要",
}


def cn(source) -> str:
    """渠道中文名；未知渠道原样返回（绝不返回空）。"""
    s = (source or "").strip()
    if not s:
        return "未知渠道"
    return SOURCE_CN.get(s, s)


def desc(source) -> str:
    """渠道一句话说明；未知返回空串。"""
    return SOURCE_DESC.get((source or "").strip(), "")


def attach(item: dict, key: str = "source") -> dict:
    """给 dict 补 source_cn / source_desc（就地返回，便于链式）。"""
    s = item.get(key)
    item[key + "_cn"] = cn(s)
    d = desc(s)
    if d:
        item[key + "_desc"] = d
    return item
