# -*- coding: utf-8 -*-
"""微信公众号 KOL 采集（C9A-KOL-001，2026-11-08 老田批）。

v1：wechat2rss 公共实例（xlab）财经 feed 池，名单在 news_config.json
    的 wechat_kol_feeds 键（url+name），增删号=改配置，无需动代码。
v2（规划）：雪球专栏影子源补足——雪球专栏文章与公众号同源
    （存量帖内 mp.weixin 直链旁证），待 v1 稳定后接入。

管线约定（对齐 news_collector.fetch_* 家族）：
    fetch_wechat_kol(size) -> [{"id", "ts", "title", "content"}]
    id 前缀 wechat_kol:（news 表 PRIMARY KEY 去重）；
    ts 为 CST "%Y-%m-%d %H:%M"（parse_full_time 完整分支，保留原年份）。
自包含设计：不 import news_collector（避免循环导入），strip_html 独立实现。

独立自检：python wechat_kol.py [--dry]
    --dry 抓取并打印统计/样本，不落库。
"""
import email.utils
import hashlib
import html
import re
import sys
from datetime import timedelta, datetime, timezone
import xml.etree.ElementTree as ET

import requests

CST = timezone(timedelta(hours=8))
_NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_PER_FEED_CAP = 15       # 单 feed 单轮最多入库条数（RSS 最新在前）
_BODY_CAP = 800          # 正文截断（快讯源 300，KOL 深度文放宽到 800）


def _strip_html(s: str) -> str:
    """语义对齐 news_collector.strip_html：去标签 + 实体还原 + 压空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _load_feeds():
    from config_local import load_news_config
    feeds = load_news_config().get("wechat_kol_feeds") or []
    return [f for f in feeds if isinstance(f, dict) and f.get("url")]


def _parse_pubdate(s: str) -> str:
    try:
        dt = email.utils.parsedate_to_datetime((s or "").strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def fetch_wechat_kol(size=30):
    """抓全部已配置 feed，返回管线约定 items 结构。单 feed 失败不拖垮整轮。"""
    feeds = _load_feeds()
    out, seen, fails = [], set(), []
    for f in feeds:
        url = f["url"]
        try:
            r = requests.get(url, timeout=_TIMEOUT,
                             headers={"User-Agent": _UA})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            fails.append("%s: %s" % (f.get("name") or url[-16:], str(e)[:80]))
            continue
        got = 0
        for item in root.iter("item"):
            if got >= _PER_FEED_CAP:
                break
            title = _strip_html(item.findtext("title") or "")[:80]
            link = (item.findtext("link") or "").strip()
            pub = _parse_pubdate(item.findtext("pubDate") or "")
            body = _strip_html(
                item.findtext(_NS_CONTENT + "encoded")
                or item.findtext("description") or "")
            if not body or not link:
                continue
            fid = "wechat_kol:" + hashlib.sha1(
                link.encode("utf-8")).hexdigest()[:16]
            if fid in seen:
                continue
            seen.add(fid)
            out.append({
                "id": fid,
                "ts": pub,
                "title": title or body[:80],
                "content": body[:_BODY_CAP],
            })
            got += 1
        print("[wechat_kol] %s: %d 条" % (f.get("name") or url[-16:], got))
    for m in fails:
        print("[wechat_kol] FAIL %s" % m)
    return out


def main() -> int:
    items = fetch_wechat_kol()
    print("合计 %d 条（未落库，自检模式）" % len(items))
    for it in items[:3]:
        print("  [%s] %s" % (it["ts"], it["title"][:50]))
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
