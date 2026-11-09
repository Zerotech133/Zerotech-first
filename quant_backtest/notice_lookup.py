# -*- coding: utf-8 -*-
"""公告文本查询层（本地库只读）：stock_notice（xtcloud，沪深）+ ts_anns_d（tushare，含北交所）。

为什么要有这一层（2026-11-06 复核）：
    `badnews_lookup.py` 的 C 段原先把「本地公告表没入库，请人工去巨潮搜」
    写死在源码里，而库里实际已有 22 万条公告（最新到 2026-11-05）。
    数据在库、结论却写死 → 工具等于没接。本模块把「查不查得到」
    交给数据本身回答，查不到才降级为人工指引。

通道口径：
    · stock_notice  data/tdx/tdx_info.db（SQLite）—— 带 notice_type 分类；
      板块后缀不可信：北交所 9,119 条被标成 '.SZ'（库内 '.BJ' 零条），故按 6 位码查
    · ts_anns_d     data/tdx/ts_anns_d/part_*.parquet —— 后缀正确、含北交所，无分类，按标题归类

两条通道合并去重（同日期 + 归一化标题），任一通道不可用不影响另一条；
通道处于同步发布态时标记「同步中」，**不当作 0 条**——这是原缺陷的同类错误。
"""

import logging
import re
from datetime import datetime, timedelta

import tdx_read as tdxr
from warehouse_read_guard import PublicationPending

log = logging.getLogger(__name__)

# 关键词归类：(标签, 命中词, 性质)。同一标题可命中多个标签（如「年报问询函」）。
# 性质只分两类：risk = 可能构成文本利空；info = 提示性（不当利空读）。
BUCKETS = (
    ("减持", ("减持", "拟减持"), "risk"),
    ("质押", ("质押",), "risk"),
    ("问询函", ("问询函", "关注函", "监管函", "监管工作函"), "risk"),
    ("退市", ("退市", "终止上市", "风险警示"), "risk"),
    ("监管处罚", ("处罚", "立案", "警示函", "监管措施"), "risk"),
    ("业绩预告", ("业绩预告", "预亏", "预减", "业绩快报"), "risk"),
    ("大宗交易", ("大宗交易",), "risk"),
    ("诉讼仲裁", ("诉讼", "仲裁", "冻结"), "risk"),
    ("增持", ("增持",), "info"),
    ("定增", ("向特定对象发行", "向不特定对象发行", "非公开发行", "发行股票"), "info"),
    ("定期报告", ("年度报告", "年报", "半年度报告", "季度报告"), "info"),
)

RISK_TAGS = frozenset(name for name, _, kind in BUCKETS if kind == "risk")

DEFAULT_CHANNELS = ("stock_notice", "ts_anns_d")


def classify(title):
    """标题 → 命中的标签列表（顺序按 BUCKETS 声明，去重）。"""
    text = str(title or "")
    return [name for name, words, _ in BUCKETS if any(w in text for w in words)]


def has_risk(hit):
    """该条是否命中 risk 类标签（info 类如定期报告不算）。"""
    return bool(set(hit.get("tags") or []) & RISK_TAGS)


_COMPANY_PREFIX = re.compile(r"^[^:：]{2,15}[:：]\s*")
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _title_key(title, name=""):
    """标题归一：去「公司名:」前缀、去公司名、去空白标点。

    两通道同一份公告的标题形态不同（xtcloud 是「贵州茅台:贵州茅台关于…」，
    tushare 是「贵州茅台关于…」），不归一就合并不掉。
    """
    text = _COMPANY_PREFIX.sub("", str(title or ""))
    if name:
        text = text.replace(str(name), "")
    return _PUNCT.sub("", text)


def _clean_title(title):
    """展示用标题：去掉「公司名:」前缀（xtcloud 会重复一遍公司名）。"""
    return _COMPANY_PREFIX.sub("", str(title or "")).strip()


def _ymd(value):
    """任意日期形态 → 'YYYYMMDD'。"""
    s = str(value or "").strip()
    m = re.match(r"(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", s)
    return "%s%s%s" % (m.group(1), m.group(2), m.group(3)) if m else s[:8]


def _dash(ymd):
    """'YYYYMMDD' → 'YYYY-MM-DD'（展示口径统一用短横线）。"""
    s = _ymd(ymd)
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else s


def _from_sqlite(code, since, limit):
    """通道一：tdx_info.db 的 stock_notice（带分类）。

    板块后缀不可信：xtcloud 把 9,119 条北交所公告标成 '.SZ'（实测 2026-11-06，
    库内 '.BJ' 零条）。所以按 **6 位码** 查，不按 ts_code 全码等值查，
    否则北交所票在这一通道永远查不到。
    """
    key = str(code).split(".")[0]
    rows = tdxr.read_info("stock_notice", codes=[key], since=since, limit=limit)
    out = []
    for r in rows:
        out.append(
            {
                "date": str(r.get("notice_date") or "")[:10],
                "title": _clean_title(r.get("title")),
                "name": str(r.get("name") or "").strip(),
                "ts_code": str(r.get("ts_code") or "").strip(),
                "type": str(r.get("notice_type") or "").strip(),
                "url": str(r.get("url") or "").strip(),
                "channel": "stock_notice",
            }
        )
    return out


def _from_parquet(code, since, limit):
    """通道二：ts_anns_d 分片（含北交所，无分类）。"""
    df = tdxr.read_range(
        "ts_anns_d",
        start=since,
        codes=[code],
        columns=["ts_code", "ann_date", "name", "title", "url"],
    )
    if df is None or getattr(df, "empty", True):
        return []
    df = df.sort_values("ann_date", ascending=False).head(limit)
    out = []
    for r in df.to_dict("records"):
        out.append(
            {
                "date": _dash(r.get("ann_date")),
                "title": _clean_title(r.get("title")),
                "name": str(r.get("name") or "").strip(),
                "type": "",
                "url": str(r.get("url") or "").strip(),
                "channel": "ts_anns_d",
            }
        )
    return out


_CHANNEL_FETCH = {"stock_notice": _from_sqlite, "ts_anns_d": _from_parquet}


def collect(code, days=365, since=None, limit=60, channels=None):
    """查某票的公告文本。

    返回 dict：
        hits    [{date,title,type,url,channel,tags,channels}]  按日期倒序，跨通道去重
        stats   {通道: 条数 或 None(不可用)}
        errors  [(通道, 原因)]
        degraded  bool  是否有通道不可用（此时「查不到」不能当成「没有」）
    """
    since_ymd = _ymd(since) if since else (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    chans = channels or DEFAULT_CHANNELS
    hits, stats, errors = [], {}, []

    for ch in chans:
        fetch = _CHANNEL_FETCH.get(ch)
        if fetch is None:
            stats[ch] = None
            errors.append((ch, "未知通道"))
            continue
        try:
            rows = fetch(code, since_ymd, limit)
        except PublicationPending:
            # 同步器正在发布：数据不是「没有」，是「此刻读不了」
            stats[ch] = None
            errors.append((ch, "同步发布中"))
            continue
        except Exception as exc:  # noqa: BLE001 - 单通道失败不拖垮查询
            log.warning("silent-except %s: %s", __name__, exc)
            stats[ch] = None
            errors.append((ch, str(exc)))
            continue
        stats[ch] = len(rows)
        for r in rows:
            # tags 只记关键词桶命中；notice_type 单独留在 type 里，
            # 否则「董事会决议公告」这类常规类型会被误算成风险标签。
            r["tags"] = classify(r["title"])
            hits.append(r)

    merged, order = {}, []
    for h in hits:
        key = (h["date"], _title_key(h["title"], h.get("name")))
        if key in merged:
            merged[key]["channels"].append(h["channel"])
            if not merged[key]["type"] and h["type"]:
                merged[key]["type"] = h["type"]
            # 两通道给的是同一份公告：优先留官方原文（cninfo PDF），其次东财页
            cur = merged[key].get("url") or ""
            new = h.get("url") or ""
            if new and ("cninfo.com.cn" in new) and ("cninfo.com.cn" not in cur):
                merged[key]["url"] = new
            elif not cur and new:
                merged[key]["url"] = new
            continue
        h["channels"] = [h["channel"]]
        merged[key] = h
        order.append(key)

    ordered = sorted((merged[k] for k in order), key=lambda x: x["date"], reverse=True)
    suffix = "." + code.split(".", 1)[1] if "." in code else ""
    mismatch = sum(
        1
        for h in ordered
        if h["channel"] == "stock_notice"
        and h.get("ts_code")
        and suffix
        and not h["ts_code"].endswith(suffix)
    )
    return {
        "code": code,
        "since": _dash(since_ymd),
        "hits": ordered,
        "stats": stats,
        "errors": errors,
        "degraded": any(v is None for v in stats.values()),
        "suffix_mismatch": mismatch,
    }


def format_section(result, view_limit=15):
    """把 collect() 结果渲染成 CLI 行（供 badnews_lookup 复用，避免结论写死）。"""
    lines = ["\n=== C. 公告文本（本地库，近 %s 起） ===" % result["since"]]
    stats = result.get("stats") or {}
    parts = []
    for ch in stats:
        n = stats[ch]
        parts.append("%s=%s" % (ch, "不可用" if n is None else "%d 条" % n))
    lines.append("  通道：" + " / ".join(parts))

    for ch, why in result.get("errors") or []:
        lines.append("  ! %s 不可用：%s（本条通道未计入「无公告」）" % (ch, why))
    if result.get("suffix_mismatch"):
        lines.append(
            "  ! 通道一有 %d 条板块后缀与 %s 不符（xtcloud 把北交所标成 .SZ），已按 6 位码兼容"
            % (result["suffix_mismatch"], result["code"])
        )

    hits = result.get("hits") or []
    if not hits:
        if result.get("degraded"):
            lines.append("  通道不可用，无法判定有无公告——请稍后重试，勿当作「无公告」。")
        else:
            lines.append("  两条通道均可用且无记录：近 %s 起该票无公告。" % result["since"])
        return lines

    bad = [h for h in hits if has_risk(h)]
    lines.append("  命中 %d 条（其中带风险标签 %d 条）：" % (len(hits), len(bad)))
    for h in hits[:view_limit]:
        tag = "/".join(h.get("tags") or []) or (h.get("type") or "常规")
        lines.append(
            "  %s  [%s]  %s%s"
            % (h["date"], tag, h["title"][:60], "  %s" % h["url"] if h.get("url") else "")
        )
    if len(hits) > view_limit:
        lines.append("  …… 另有 %d 条未展开（--notice-limit 可调）" % (len(hits) - view_limit))
    if not bad:
        lines.append("  未命中风险关键词（减持/质押/问询函/定增/处罚等），仍建议抽查原文。")
    return lines
