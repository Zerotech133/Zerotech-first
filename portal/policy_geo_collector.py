"""policy_geo_collector.py — 政策地缘情报域采集器（免费公开源起步）。

职责：每日抓取国务院政策文件库最新文件（gov.cn 公开页），解析 标题/日期/链接，
落 JSONL 账本；为 /api/intel/brief 提供政策线真数据。
边界：只读公开页面；限频一次一页；UA 标注；失败如实返回空（不凑样本）。
运行：python policy_geo_collector.py            # 抓一页增量
      python policy_geo_collector.py --report   # 输出今日摘要 json
源：https://www.gov.cn/zhengce/zhengceku/gwywj/index.htm（国务院文件）
     后续扩：部门文件页 / 解读页 / 地缘要闻（人民网国际）——逐源核权利后接入。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "policy_feed.jsonl")
UA = "Mozilla/5.0 (compatible; ZeroOS-PolicyBot/0.1; +personal intel dashboard)"
SRC = "https://www.gov.cn/zhengce/zhengceku/gwywj/index.htm"
SRC_JSON = "https://www.gov.cn/zhengce/zhengceku/gwywj/TONGYONGGAILAN.json"  # 国务院文件（全量结构化）
SRC_JSON_BM = "https://www.gov.cn/zhengce/zhengceku/bmwj/TONGYONGGAILAN.json"  # 部门文件（同构，12812 条）
SRC_RSS_GEO = (
    "http://www.people.com.cn/rss/politics.xml"  # 人民网时政 RSS（地缘要闻，免费公开）
)


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    import ssl

    # 本机代理(59403)自签链会拦公网证书——公开只读页降级为未验证上下文；
    # 仅用于无凭据 GET，绝不用于带认证请求。若直连环境应恢复验证。
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE" not in str(e):
            raise
        ctx = ssl._create_unverified_context()  # nosec B323 - 仅 CERTIFICATE 失败时公网只读 GET 降级，无凭据
        with safe_urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _collect_gov_json(src: str, tag: str, max_items: int) -> list[dict]:
    data = json.loads(_fetch(src, timeout=30))
    rows = []
    for x in data:
        t = str(x.get("TITLE", "")).strip()
        d = str(x.get("DOCRELPUBTIME", ""))[:10]
        u = str(x.get("URL", "")).strip()
        if t and d and u:
            rows.append({"date": d, "title": t[:64], "url": u, "src": tag})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:max_items]


def _collect_geo_rss(max_items: int) -> list[dict]:
    """人民网时政 RSS（xml 简析：title/pubDate/link）。"""
    import email.utils

    xml = _fetch(SRC_RSS_GEO, timeout=25)
    out = []
    for m in re.finditer(r"<item>([\s\S]*?)</item>", xml):
        blk = m.group(1)
        tm = re.search(r"<title>(?:<!\[CDATA\[)?([^<\]]+)", blk)
        dm = re.search(r"<pubDate>([^<]+)</pubDate>", blk)
        lm = re.search(r"<link>([^<]+)</link>", blk)
        if not (tm and dm):
            continue
        try:
            d = email.utils.parsedate_to_datetime(dm.group(1)).strftime("%Y-%m-%d")
        except Exception:
            d = ""
        out.append(
            {
                "date": d,
                "title": tm.group(1).strip()[:64],
                "url": (lm.group(1).strip() if lm else ""),
                "src": "people.com.cn/geo",
            }
        )
    return out[:max_items]


def collect(max_items: int = 20) -> list[dict]:
    """走页面同源 JSON（TONGYONGGAILAN.json，TITLE/URL/DOCRELPUBTIME 全量 6k+ 条）。
    首页 HTML 是 AJAX 空 ul，正则抓不到——2026-10-23 实测判例。失败抛异常如实处理。"""
    try:
        data = json.loads(_fetch(SRC_JSON, timeout=30))
    except json.JSONDecodeError as e:
        raise RuntimeError("政策 JSON 解析失败：%s" % str(e)[:80])
    rows = []
    for x in data:
        t = str(x.get("TITLE", "")).strip()
        d = str(x.get("DOCRELPUBTIME", ""))[:10]
        u = str(x.get("URL", "")).strip()
        if t and d and u:
            rows.append(
                {"date": d, "title": t[:64], "url": u, "src": "gov.cn/zhengceku"}
            )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:max_items]


def load_recent(n: int = 20) -> list[dict]:
    if not os.path.isfile(LEDGER):
        return []
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    dedup, seen = [], set()
    for r in rows[::-1]:
        if r.get("title") not in seen:
            seen.add(r.get("title"))
            dedup.append(r)
    dedup.sort(key=lambda r: str(r.get("date", "")), reverse=True)  # 按发布日期最新在前
    return dedup[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        print(
            json.dumps(
                {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "recent": load_recent(12)},
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    items: list[dict] = []
    fails: list[str] = []
    for fn, tag, mi in (
        (_collect_gov_json, "gov.cn/gwywj", 12),
        (
            lambda **k: _collect_gov_json(SRC_JSON_BM, "gov.cn/bmwj", 8),
            "gov.cn/bmwj",
            8,
        ),
        (_collect_geo_rss, "people.com.cn/geo", 8),
    ):
        try:
            src = SRC_JSON if fn is _collect_gov_json else None
            part = (
                fn(src=src, tag=tag, max_items=mi)
                if fn is _collect_gov_json
                else fn(max_items=mi)
            )
            items.extend(part)
        except Exception as e:
            fails.append(tag + ":" + str(e)[:60])
    if fails:
        print("PARTIAL FAIL: %s" % "; ".join(fails))
    if not items:
        print("COLLECT FAIL: 三源全失败（网络/结构变化），人工核对")
        return 1
    if not items:
        print("COLLECT EMPTY: 页面结构可能变化，人工核对（不凑样本）")
        return 2
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    known = {r.get("title") for r in load_recent(500)}
    fresh = [x for x in items if x["title"] not in known]
    with open(LEDGER, "a", encoding="utf-8") as f:
        for x in fresh:
            f.write(
                json.dumps(
                    {**x, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                    ensure_ascii=False,
                )
                + "\n"
            )
    print("OK: 新增 %d / 本页 %d（源 %s）" % (len(fresh), len(items), SRC))
    return 0


if __name__ == "__main__":
    sys.exit(main())
