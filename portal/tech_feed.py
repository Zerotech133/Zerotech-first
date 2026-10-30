"""tech_feed.py — 科技前沿域采集（免费公开源）。

源：①GitHub Trending（daily, html 抓 repo 名/描述/star）②arXiv cs.AI 最新（Atom XML）。
纪律：同 policy_geo_collector——限频/UA/失败如实/空不凑样本；SSL 代理链降级仅无凭据 GET。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "tech_feed.jsonl")
UA = "Mozilla/5.0 (compatible; ZeroOS-TechBot/0.1)"
GH = "https://github.com/trending?since=daily"
ARXIV = "http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=10"


def _fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        if "CERTIFICATE" not in str(e):
            raise
        import ssl

        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()  # nosec B323 - 同上，只读公开源
        ) as r:
            return r.read().decode("utf-8", errors="replace")


def gh_trending(max_n: int = 8) -> list[dict]:
    html = _fetch(GH)
    out = []
    for m in re.finditer(
        r'<h2[^>]*>\s*<a[^>]+href="/([^"/]+/[^"/]+)"[^>]*>[\s\S]*?</h2>[\s\S]{0,400}?<p[^>]*>([^<]{4,120})',
        html,
    ):
        repo, desc = m.group(1), m.group(2).strip()
        out.append(
            {
                "date": time.strftime("%Y-%m-%d"),
                "title": repo,
                "note": desc[:90],
                "src": "github/trending",
            }
        )
    # star 数可选补:找到 svg aria-label star 行——简化省略,描述已可用
    return out[:max_n]


def arxiv_ai(max_n: int = 6) -> list[dict]:
    xml = _fetch(ARXIV)
    out = []
    for m in re.finditer(r"<entry>([\s\S]*?)</entry>", xml):
        blk = m.group(1)
        t = re.search(r"<title>([^<]+)</title>", blk)
        d = re.search(r"<published>(\d{4}-\d{2}-\d{2})", blk)
        s = re.search(r"<summary>([\s\S]{10,200}?)</summary>", blk)
        if t and d:
            out.append(
                {
                    "date": d.group(1),
                    "title": " ".join(t.group(1).split())[:80],
                    "note": (" ".join(s.group(1).split())[:90] + "…") if s else "",
                    "src": "arxiv/cs.AI",
                }
            )
    return out[:max_n]


def load_recent(n: int = 12) -> list[dict]:
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
    return dedup[:n]


def main() -> int:
    items: list[dict] = []
    fails: list[str] = []
    for fn, tag in ((gh_trending, "github"), (arxiv_ai, "arxiv")):
        try:
            items.extend(fn())
        except Exception as e:
            fails.append(tag + ":" + str(e)[:60])
    if fails:
        print("PARTIAL FAIL: %s" % "; ".join(fails))
    if not items:
        print("COLLECT FAIL: 双源全失败")
        return 1
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
    print("OK: 新增 %d / 本轮 %d" % (len(fresh), len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
