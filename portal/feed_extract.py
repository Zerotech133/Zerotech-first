# -*- coding: utf-8 -*-
"""投喂正文抽取（P2 投喂补强，2026-10-26）。

策略链（逐级降级、诚实标注 extractor）：
  1) trafilatura（若安装）——最佳正文抽取；
  2) lxml + bs4 启发式（去 script/style/nav/footer/aside/header/隐藏节点，
     取 article/main/[role=main]/最大段落块）——内置兜底；
  3) 纯文本清洗（HTML 剥标签）。
输出：{"title","text","extractor","chars"}；不抛异常（失败返回文本为空）。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
import logging

DROP_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "aside",
    "header",
    "form",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "button",
    "input",
    "select",
)
CAND_REGEX = re.compile(r"\s+")
TITLE_MAX = 120
TEXT_MAX = 30000


def _clean(t):
    t = str(t or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _title(soup, url=""):
    for sel in (
        ("meta", {"property": "og:title"}),
        ("meta", {"name": "twitter:title"}),
        ("h1", None),
        ("title", None),
    ):
        try:
            if sel[0] == "meta":
                node = soup.find("meta", attrs=sel[1])
                if node and node.get("content"):
                    return str(node["content"]).strip()[:TITLE_MAX]
            else:
                node = soup.find(sel[0])
                if node and node.get_text(strip=True):
                    return node.get_text(" ", strip=True)[:TITLE_MAX]
        except Exception:  # noqa: BLE001
            continue
    return ""


def _remove_hidden(soup):
    """删 display:none / visibility:hidden / aria-hidden 节点（隐藏指令载体）。"""
    for el in soup.find_all(style=True):
        st = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in st or "visibility:hidden" in st:
            el.decompose()
    for el in soup.find_all(attrs={"aria-hidden": "true"}):
        el.decompose()


def _trafilatura(html, url=""):
    try:
        import trafilatura  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        text = (
            trafilatura.extract(
                html, url=url or None, include_comments=False, include_tables=False
            )
            or ""
        )
        if len(text.strip()) >= 200:
            return _clean(text)
    except Exception:  # noqa: BLE001
        return None
    return None


def _heuristic(html, url=""):
    soup = BeautifulSoup(html, "lxml")
    title = _title(soup, url)
    _remove_hidden(soup)
    for tag in soup(DROP_TAGS):
        tag.decompose()
    best = ""
    for sel in (
        "article",
        "main",
        "[role=main]",
        "#content",
        ".content",
        ".article",
        ".post",
        "body",
    ):
        try:
            nodes = soup.select(sel)
        except Exception:  # noqa: BLE001
            continue
        for node in nodes:
            parts = [p.get_text(" ", strip=True) for p in node.find_all("p")]
            text = "\n\n".join(x for x in parts if x)
            if not text:
                text = node.get_text("\n", strip=True)
            text = _clean(text)
            if len(text) > len(best):
                best = text
        if len(best) >= 20:
            break
    return title, best[:TEXT_MAX]


def extract_main(raw, url=""):
    """raw: bytes/str（HTML 或纯文本）→ {title,text,extractor,chars}。"""
    try:
        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    except Exception:  # noqa: BLE001
        html = ""
    if not html.strip():
        return {"title": "", "text": "", "extractor": "none", "chars": 0}
    # 1) trafilatura
    t = _trafilatura(html, url)
    if t:
        title = ""
        try:
            soup = BeautifulSoup(html, "lxml")
            title = _title(soup, url)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feed_extract.py", exc)
        return {
            "title": title,
            "text": t[:TEXT_MAX],
            "extractor": "trafilatura",
            "chars": len(t),
        }
    # 2) lxml 启发式
    try:
        title, text = _heuristic(html, url)
        if len(text) >= 20:
            return {
                "title": title,
                "text": text,
                "extractor": "lxml-heuristic",
                "chars": len(text),
            }
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feed_extract.py", exc)
    # 3) 纯文本兜底（同样先删隐藏节点/脚本等，防隐藏指令入库）
    try:
        soup = BeautifulSoup(html, "lxml")
        _remove_hidden(soup)
        for tag in soup(DROP_TAGS):
            tag.decompose()
        text = _clean(soup.get_text("\n", strip=True))[:TEXT_MAX]
        return {
            "title": _title(soup, url),
            "text": text,
            "extractor": "text-fallback",
            "chars": len(text),
        }
    except Exception:  # noqa: BLE001
        return {
            "title": "",
            "text": _clean(html)[:TEXT_MAX],
            "extractor": "raw-fallback",
            "chars": 0,
        }


if __name__ == "__main__":
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = (
        open(p, "rb").read()
        if p
        else (
            "<html><head><title>T</title><style>x</style></head><body>"
            "<nav>菜单</nav><article><h1>标题</h1><p>正文一</p><p>正文二</p>"
            "</article><script>evil()</script></body></html>"
        )
    )
    print(extract_main(raw, "https://example.com/a?x=1"))
