# -*- coding: utf-8 -*-
"""news.db 的新闻—标的显式代码证据只读投影。"""

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

_ENDPOINT = "/api/intel/evidence-targets"
REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
_DB = REPO / "news_collector" / "news.db"
_CST = timezone(timedelta(hours=8))
_CODE = re.compile(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)\Z", re.ASCII)
_BIASES = frozenset(("bull", "bear", "neutral"))


def _integer(params, name, default, maximum):
    value = params.get(name, [str(default)])[0]
    if not re.fullmatch(r"[1-9]\d*", value, re.ASCII) or len(value) > 3:
        raise ValueError(name + " must be an integer from 1 to " + str(maximum))
    number = int(value)
    if number > maximum:
        raise ValueError(name + " must be an integer from 1 to " + str(maximum))
    return number


def _codes(raw):
    if raw is None or raw == "":
        return []
    codes = json.loads(raw)
    if not isinstance(codes, list):
        raise ValueError("stocks is not an array")
    return [code for code in codes if isinstance(code, str) and _CODE.fullmatch(code)]


def _jev(raw):
    if raw is None or raw == "":
        return None
    judgement = json.loads(raw)
    if not isinstance(judgement, dict):
        raise ValueError("jev is not an object")
    bias, conf = judgement.get("bias"), judgement.get("conf")
    if bias not in _BIASES or isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
        raise ValueError("invalid jev judgement")
    return {"bias": bias, "conf": conf, "kind": "model_judgement"}


def load_evidence_targets(qs, db_path=None):
    """返回 (响应体, HTTP 状态)；count 是全窗口匹配关系数，不受 limit 截断。"""
    result = {"endpoint": _ENDPOINT, "items": [], "count": 0,
              "excluded_count": 0, "bad_rows": 0}
    try:
        params = parse_qs(qs or "", keep_blank_values=True, strict_parsing=True)
        if set(params) - {"code", "days", "limit"} or any(len(v) != 1 for v in params.values()):
            raise ValueError("unknown or repeated query parameter")
        code = params.get("code", [None])[0]
        if code is not None and not _CODE.fullmatch(code):
            raise ValueError("code must be an A-share symbol like 600000.SH")
        days = _integer(params, "days", 7, 30)
        limit = _integer(params, "limit", 50, 100)
    except ValueError as exc:
        result["error"] = {"code": "invalid_query", "message": str(exc)}
        return result, 400

    path = db_path if db_path is not None else _DB
    if not os.path.isfile(path):
        result["error"] = {"code": "database_missing", "message": "news.db is missing"}
        return result, 503
    now = datetime.now(_CST)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    end = now.strftime("%Y-%m-%d %H:%M")
    try:
        # URI mode=ro prevents accidental database creation or writes.
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT id, source, ts, created_at, title, content, stocks, jev "
                "FROM news WHERE ts >= ? AND ts <= ? ORDER BY ts DESC, id DESC",
                (start, end))
            for nid, source, occurred, ingested, title, content, stocks, raw_jev in rows:
                try:
                    if not isinstance(nid, str) or not nid or not isinstance(source, str) or not source or not isinstance(occurred, str) or not occurred or not isinstance(ingested, str) or not ingested:
                        raise ValueError("missing evidence identity or timestamp")
                    codes = set(_codes(stocks))
                    if not codes:
                        result["excluded_count"] += 1
                        continue
                    matched = sorted(codes & {code}) if code else sorted(codes)
                    if not matched:
                        result["excluded_count"] += 1
                        continue
                except (ValueError, TypeError, json.JSONDecodeError):
                    result["bad_rows"] += 1
                    continue
                try:
                    judgement = _jev(raw_jev)
                except (ValueError, TypeError, json.JSONDecodeError):
                    result["bad_rows"] += 1
                    judgement = None
                result["count"] += len(matched)
                for symbol in matched:
                    if len(result["items"]) < limit:
                        result["items"].append({
                            "evidence_id": nid, "source_ref": nid, "source": source,
                            "occurred_at": occurred, "ingested_at": ingested,
                            "title": title or "", "content": content or "",
                            "code": symbol, "match_basis": "news.stocks:exact_code",
                            "jev": judgement})
    except (sqlite3.Error, OSError) as exc:
        result = {"endpoint": _ENDPOINT, "items": [], "count": 0,
                  "excluded_count": 0, "bad_rows": 0,
                  "error": {"code": "database_error", "message": str(exc)}}
        return result, 503
    return result, 200


class Handler:
    def _intel_evidence_targets(self, qs):
        body, status = load_evidence_targets(qs)
        self._json(body, status)
