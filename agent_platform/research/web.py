# -*- coding: utf-8 -*-
"""只读 Web 取证：SSRF 防护 + 域名白名单 + 24h 缓存 + 每日预算 + 来源引用。

边界：
  · 仅 http/https，禁回环/私网/链路本地/非 80-443 端口；解析后 IP 再校验（防 DNS rebinding 基础）；
  · 域名白名单（agent_platform/research/allowlist.json），未列入一律拒绝；
  · 响应 ≤300KB、10s 超时；结果抽 title/text/excerpt，存 sha256 与缓存；
  · 每日预算（默认 30 次，env ZHAOYUN_RESEARCH_DAILY_LIMIT 可调；缓存命中不耗预算）；
  · citation 落 data/research/citations.jsonl，供报告引用；不抓取/不提交任何凭据。
"""

from __future__ import annotations

import hashlib
import html.parser
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
ALLOWLIST = PLATFORM / "research" / "allowlist.json"
CACHE = PLATFORM / "data" / "research_cache"
RESEARCH = PLATFORM / "data" / "research"
CITATIONS = RESEARCH / "citations.jsonl"
BUDGET = RESEARCH / "budget.json"
MAX_BYTES = 300_000
TIMEOUT = 10.0
CACHE_TTL_HOURS = 24
DEFAULT_DAILY_LIMIT = 30
UA = "zhaoyun-research/1.0 (+local governance evidence collector)"


def _allow_domains() -> list:
    try:
        data = json.loads(ALLOWLIST.read_text(encoding="utf-8-sig"))
        return [str(d).lower() for d in data.get("domains") or []]
    except (OSError, ValueError):
        return []


def allowlist() -> dict:
    return {
        "ok": True,
        "domains": _allow_domains(),
        "note": "只允许这些域名；新增需人工评审",
    }


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_url(url: str) -> dict:
    try:
        u = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return {"ok": False, "error": "bad_url"}
    if u.scheme not in ("http", "https"):
        return {"ok": False, "error": "scheme_not_allowed", "scheme": u.scheme}
    host = (u.hostname or "").lower()
    if not host:
        return {"ok": False, "error": "no_host"}
    if host in ("localhost",) or host.endswith(".local"):
        return {"ok": False, "error": "host_not_allowed", "host": host}
    if u.port and u.port not in (80, 443):
        return {"ok": False, "error": "port_not_allowed", "port": u.port}
    domains = _allow_domains()
    ok_domain = any(host == d or host.endswith("." + d) for d in domains)
    if not ok_domain:
        return {"ok": False, "error": "domain_not_allowlisted", "host": host}
    try:
        infos = socket.getaddrinfo(
            host,
            u.port or (443 if u.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except OSError as e:
        return {"ok": False, "error": "dns_failed", "detail": str(e)[:120]}
    ips = sorted({info[4][0] for info in infos})
    bad = [ip for ip in ips if not _is_public_ip(ip)]
    if bad:
        return {"ok": False, "error": "resolved_to_private_or_bad_ip", "ips": bad[:5]}
    return {"ok": True, "url": u.geturl(), "host": host, "ips": ips[:5]}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def budget(limit: int | None = None) -> dict:
    try:
        b = json.loads(BUDGET.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        b = {"date": _today(), "count": 0}
    if b.get("date") != _today():
        b = {"date": _today(), "count": 0}
    try:
        lim = int(
            limit
            or os.environ.get("ZHAOYUN_RESEARCH_DAILY_LIMIT")
            or DEFAULT_DAILY_LIMIT
        )
    except ValueError:
        lim = DEFAULT_DAILY_LIMIT
    b["limit"] = lim
    b["remaining"] = max(0, lim - int(b.get("count") or 0))
    return b


def _budget_consume() -> bool:
    b = budget()
    if b["remaining"] <= 0:
        return False
    b["count"] = int(b.get("count") or 0) + 1
    b["date"] = _today()
    RESEARCH.mkdir(parents=True, exist_ok=True)
    BUDGET.write_text(
        json.dumps({"date": b["date"], "count": b["count"]}), encoding="utf-8"
    )
    return True


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        self.parts.append(text)


def _extract(body: str) -> dict:
    p = _TextExtractor()
    try:
        p.feed(body)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/research/web.py", exc)
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return {"title": p.title[:300], "text": text[:20000]}


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with safe_urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read(MAX_BYTES + 1)
        ctype = (r.headers.get("Content-Type") or "").lower()
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    return raw.decode(charset, errors="replace")


def fetch(url: str, force: bool = False) -> dict:
    v = validate_url(url)
    if not v.get("ok"):
        return v
    key = hashlib.sha256(v["url"].encode("utf-8")).hexdigest()[:32]
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / (key + ".json")
    if not force and cache_file.exists():
        try:
            c = json.loads(cache_file.read_text(encoding="utf-8"))
            age_h = (time.time() - float(c.get("fetched_ts") or 0)) / 3600
            if age_h <= CACHE_TTL_HOURS:
                return dict(c, cached=True, budget=budget())
        except (OSError, ValueError, TypeError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/research/web.py", exc)
    if not _budget_consume():
        return {"ok": False, "error": "budget_exceeded", "budget": budget()}
    try:
        body = _http_get(v["url"])
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": "fetch_failed",
            "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
            "url": v["url"],
        }
    sha = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
    meta = _extract(body)
    citation = {
        "schema": "zhaoyun_citation/v1",
        "url": v["url"],
        "host": v["host"],
        "title": meta["title"],
        "sha256": sha,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "excerpt": meta["text"][:500],
        "bytes": len(body),
    }
    rec = {
        "ok": True,
        "url": v["url"],
        "host": v["host"],
        "title": meta["title"],
        "text": meta["text"],
        "excerpt": meta["text"][:800],
        "sha256": sha,
        "fetched_at": citation["fetched_at"],
        "fetched_ts": time.time(),
        "bytes": len(body),
        "citation": citation,
        "cached": False,
        "budget": budget(),
    }
    cache_file.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    try:
        RESEARCH.mkdir(parents=True, exist_ok=True)
        with open(CITATIONS, "a", encoding="utf-8") as f:
            f.write(json.dumps(citation, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/research/web.py", exc)
    return rec


def recent_citations(limit: int = 20) -> dict:
    rows = []
    try:
        lines = CITATIONS.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-max(1, min(int(limit), 100)) :]:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/research/web.py", exc)
    return {
        "ok": True,
        "count": len(rows),
        "items": rows,
        "budget": budget(),
        "allowlist": _allow_domains(),
    }
