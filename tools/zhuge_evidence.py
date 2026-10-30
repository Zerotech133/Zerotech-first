# -*- coding: utf-8 -*-
"""zhuge_evidence.py — 诸葛亮能力底座 P0-2/P0-3：联网取证 + 证据 schema。

只读、无执行：
- fetch_url：抓取 http(s) 文本（HTML 去标签），返回 title/text/抓取时间/来源等级；
  SSRF 防护：禁回环/私网/链路本地/保留地址，禁非 http(s)，禁 URL userinfo；
- cite_source：统一引用格式（[S1] 标题 — URL（抓取时间））；
- claims schema：claims=[{text, source_ids, confidence}]；
  validate_claims 强制「无来源不编」：有事实主张但无 source_ids → 告警/失败。

用法（MCP 工具内部调用，或独立脚本）：
    from zhuge_evidence import fetch_url, cite_source, validate_claims
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
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
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

USER_AGENT = "ZhugeCapability/0.1 (+read-only research; local)"
MAX_BYTES = 400_000
DEFAULT_MAX_CHARS = 4000
CONFIDENCES = ("high", "medium", "low", "unknown")
TIER_LABELS = {1: "official", 2: "academic/repo", 3: "media", 4: "other", 5: "unknown"}


def _now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


class _TextExtractor(HTMLParser):
    """极简 HTML → (title, text)。不执行脚本/不取样式。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style", "noscript", "svg") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self.text_parts.append(data)


def source_tier(url: str) -> dict:
    """来源等级（1 官方 > 2 学术/代码仓 > 3 媒体 > 4 其他）。只按域名，不推断内容。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    official_suffixes = (".gov", ".gov.cn", ".gov.uk", ".int", ".mil")
    academic_suffixes = (".edu", ".edu.cn", ".ac.cn")
    repo_hosts = ("github.com", "gitlab.com", "gitee.com", "pypi.org",
                  "npmjs.com", "arxiv.org", "modelcontextprotocol.io")
    media_hosts = ("reuters.com", "apnews.com", "bbc.com", "xinhua.net",
                   "people.com.cn", "caixin.com", "36kr.com", "ft.com", "wsj.com")
    if not host:
        tier, reason = 5, "no_host"
    elif host.endswith(official_suffixes):
        tier, reason = 1, "government/international"
    elif host.endswith(academic_suffixes) or host in repo_hosts or host.endswith(".arxiv.org"):
        tier, reason = 2, "academic/repo"
    elif host in media_hosts or any(host.endswith("." + m) for m in media_hosts):
        tier, reason = 3, "media"
    else:
        tier, reason = 4, "other"
    return {"tier": tier, "label": TIER_LABELS[tier], "reason": reason}


def _host_ips(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def url_is_safe(url: str) -> tuple[bool, str]:
    """SSRF 防护：只允许 http(s) + 公网地址；禁回环/私网/链路本地/保留。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False, "url_parse_error"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme_denied"
    if parsed.username or parsed.password:
        return False, "userinfo_denied"
    host = parsed.hostname
    if not host:
        return False, "host_missing"
    try:
        if ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback:
            return False, "private_ip_denied"
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/zhuge_evidence.py", exc)
    if host.lower() in ("localhost",) or host.lower().endswith((".local", ".internal")):
        return False, "local_host_denied"
    try:
        ips = _host_ips(host)
    except OSError:
        return False, "dns_failed"
    if not ips:
        return False, "dns_empty"
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False, "bad_ip"
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False, "non_public_ip_denied"
    return True, "ok"


def _html_to_text(raw: bytes, content_type: str) -> tuple[str, str]:
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if "html" not in (content_type or "").lower() and not text.lstrip().startswith("<"):
        return "", re.sub(r"\s+", " ", text).strip()
    parser = _TextExtractor()
    parser.feed(text)
    title = re.sub(r"\s+", " ", html.unescape(" ".join(parser.title_parts))).strip()
    body = re.sub(r"\s+", " ", html.unescape(" ".join(parser.text_parts))).strip()
    return title, body


def fetch_url(url: str, max_chars: int = DEFAULT_MAX_CHARS, timeout: float = 10.0) -> dict:
    """只读抓取网页文本；失败返回 ok=False（不抛穿）。"""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": {"code": "url_missing", "message": "url 为空"}}
    safe, reason = url_is_safe(url)
    if not safe:
        return {"ok": False, "url": url,
                "error": {"code": "url_unsafe", "message": reason}}
    max_chars = max(200, min(int(max_chars or DEFAULT_MAX_CHARS), 8000))
    fetched_at = _now_cn()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        with safe_urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES)
            status = getattr(resp, "status", None)
            final_url = resp.geturl()
            content_type = resp.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "fetched_at": fetched_at,
                "error": {"code": "fetch_failed",
                          "message": "%s: %s" % (type(exc).__name__, str(exc)[:200])}}
    title, body = _html_to_text(raw, content_type)
    truncated = len(body) > max_chars
    return {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "fetched_at": fetched_at,
        "status_code": status,
        "content_type": content_type,
        "title": title[:300],
        "text": body[:max_chars],
        "truncated": truncated,
        "source_tier": source_tier(final_url or url),
        "read_only": True,
    }


def cite_source(source: dict, source_id: str = "S1") -> str:
    """统一引用格式；缺 url/时间时诚实标注。"""
    title = (source.get("title") or "").strip() or "(无标题)"
    url = source.get("final_url") or source.get("url") or "(无URL)"
    at = source.get("fetched_at") or "未记录时间"
    tier = (source.get("source_tier") or {}).get("label", "unknown")
    return "[%s] %s — %s（抓取 %s；来源等级 %s）" % (source_id, title, url, at, tier)


def make_claim(text: str, source_ids: list[str], confidence: str = "medium") -> dict:
    conf = confidence if confidence in CONFIDENCES else "low"
    return {"text": (text or "").strip(), "source_ids": list(source_ids or []),
            "confidence": conf}


def validate_claims(claims: list, sources: list | None = None) -> dict:
    """校验证据 schema；核心规则：有事实主张就必须有 source_ids（无来源不编）。

    sources 可为 [{"id":"S1",...}]；只校验 claim 引用的 id 是否存在。
    """
    issues = []
    known_ids = set()
    for src in sources or []:
        sid = src.get("id") or src.get("source_id")
        if sid:
            known_ids.add(sid)
    if not isinstance(claims, list):
        return {"ok": False, "issues": [{"code": "claims_not_list", "message": "claims 必须是数组"}]}
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            issues.append({"code": "claim_not_object", "index": idx})
            continue
        text = str(claim.get("text") or "").strip()
        ids = claim.get("source_ids") or []
        conf = claim.get("confidence") or "unknown"
        if not text:
            issues.append({"code": "claim_text_empty", "index": idx})
        if not isinstance(ids, list):
            issues.append({"code": "source_ids_not_list", "index": idx})
        if not ids and conf != "unknown":
            issues.append({"code": "claim_without_source", "index": idx,
                           "message": "有事实主张但无 source_ids（无来源不编）"})
        for sid in ids if isinstance(ids, list) else []:
            if known_ids and sid not in known_ids:
                issues.append({"code": "source_id_missing", "index": idx, "source_id": sid})
        if conf not in CONFIDENCES:
            issues.append({"code": "confidence_invalid", "index": idx, "confidence": conf})
    return {"ok": not issues, "claim_count": len(claims), "issues": issues}


def build_answer(text: str, claims: list | None = None, sources: list | None = None,
                 warnings: list | None = None) -> dict:
    """输出结构化答复；无来源/校验失败自动加 warning。"""
    sources = list(sources or [])
    claims = list(claims or [])
    warns = list(warnings or [])
    check = validate_claims(claims, sources)
    if not check["ok"]:
        warns.append("证据校验未通过：%d 项问题（详见 issues）" % len(check["issues"]))
    for src in sources:
        if src.get("ok") is False:
            warns.append("来源不可用：%s" % (src.get("url") or "unknown"))
    return {
        "text": text,
        "claims": claims,
        "sources": sources,
        "citations": [cite_source(s, "S%d" % (i + 1)) for i, s in enumerate(sources)],
        "confidence": "unknown" if not sources else "medium",
        "warnings": warns,
        "read_only": True,
        "evidence_check": check,
    }
