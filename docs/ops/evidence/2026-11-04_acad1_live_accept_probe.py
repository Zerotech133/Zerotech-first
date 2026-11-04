# -*- coding: utf-8 -*-
"""ACAD1 军团接口 · 只读 live 验收探针（task-57 / WO-ACAD1-LIVE-ACCEPT-20261104）。

只读约束：
  - 仅 GET/POST 未登录探测（POST 只用于 8105 /api/v1/auth/portal-sso 的“无 session”负例，
    不带任何 cookie/token/凭据，服务端在缺少 zb_session 时立即 401，不会写数据）；
  - 不发送 Cookie / Authorization；不打印或落盘 cookie/token；
  - 不重启任何服务、不写生产数据；200 响应只落“摘要字段”，不落全量数据。

输出：脱敏 raw JSON（本文件同目录），供 docs/ops 报告引用。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HOST = "127.0.0.1"
UA = {"User-Agent": "acad1-live-accept/ro"}
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent


def call(port: int, method: str, path: str, body: bytes | None = None,
         headers: dict | None = None, timeout: float = 20.0) -> dict:
    t0 = time.perf_counter()
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    hdrs = dict(UA)
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    try:
        conn.request(method, path, body=body, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        resp_headers = {k.lower(): v for k, v in r.getheaders()}
        status = r.status
    finally:
        conn.close()
    row = {
        "port": port,
        "method": method,
        "path": path,
        "status": status,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "body_len": len(raw),
        "content_type": resp_headers.get("content-type", ""),
        "location": resp_headers.get("location", ""),
        "www_authenticate": resp_headers.get("www-authenticate", ""),
        "server": resp_headers.get("server", ""),
        "body_sha256_12": hashlib.sha256(raw).hexdigest()[:12],
        "_raw": raw,
        "_headers": resp_headers,
    }
    return row


def json_of(row: dict):
    try:
        return json.loads(row["_raw"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def brief(row: dict, log: list) -> dict:
    """落盘用：去掉 _raw/_headers，JSON 只留摘要；401 保留 detail（无敏感）。"""
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    payload = json_of(row)
    if payload is not None:
        if row["status"] == 200:
            # 200 只留字段名，不落全量业务数据（摘要见 summaries）
            out["body_keys"] = list(payload.keys())[:20] if isinstance(payload, dict) else ["<list>"]
        else:
            out["body"] = payload
    else:
        text = row["_raw"].decode("utf-8", "replace")
        out["body_sample"] = re.sub(r"[\x00-\x1f]+", " ", text)[:300]
    log.append(out)
    return out


def summary_pool(row: dict) -> dict:
    """8091 / 8734 /api/acad1/pool 摘要（不落全量）。"""
    j = json_of(row) or {}
    items = j.get("items") if isinstance(j.get("items"), list) else []
    summ = j.get("summary") if isinstance(j.get("summary"), dict) else {}
    return {
        "status": row["status"],
        "ok": j.get("ok"),
        "count": j.get("count"),
        "pool_dates": j.get("pool_dates"),
        "read_semantics": j.get("read_semantics"),
        "freshness_by_market": j.get("freshness_by_market"),
        "published_at": j.get("published_at"),
        "review_status": j.get("review_status"),
        "degraded_reason": j.get("degraded_reason"),
        "plan": j.get("plan"),
        "dates": j.get("dates"),
        "pg_table": j.get("pg_table"),
        "schema": j.get("schema"),
        "mode": j.get("mode"),
        "source_mode": j.get("source_mode"),
        "official": j.get("official"),
        "source_pool": j.get("source_pool"),
        "pool_id": j.get("pool_id"),
        "pool_date": j.get("pool_date"),
        "data_date": j.get("data_date"),
        "stale": j.get("stale"),
        "freshness_days": j.get("freshness_days"),
        "trading_permitted": j.get("trading_permitted"),
        "view": j.get("view"),
        "summary_pool_total": summ.get("pool_total"),
        "summary_reviewed": summ.get("reviewed"),
        "summary_pending": summ.get("pending"),
        "items_len": len(items),
        "generated_at": j.get("generated_at"),
    }


def summary_comparison(row: dict) -> dict:
    j = json_of(row) or {}
    items = j.get("items") if isinstance(j.get("items"), list) else []
    return {
        "read_only": j.get("read_only"),
        "market": j.get("market"),
        "pool_date": j.get("pool_date"),
        "total": j.get("total"),
        "selected": j.get("selected"),
        "quota": j.get("quota"),
        "expected_top_n": j.get("expected_top_n"),
        "quota_shortfall": j.get("quota_shortfall"),
        "items_len": len(items),
        "top3_codes": [x.get("code") for x in items[:3]],
    }


ACAD1_SUBPATHS = ["pool", "stats", "reviews", "decisions", "evidence", "calendar"]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev = {
        "tool": "acad1_live_accept_probe",
        "task": "task-57 / WO-ACAD1-LIVE-ACCEPT-20261104",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "notes": [
            "未登录探测：不发送 Cookie/Authorization；不打印/落盘 cookie/token。",
            "POST 仅用于 8105 /api/v1/auth/portal-sso 无 session 负例（服务端 401 前不写数据）。",
            "200 响应只落摘要字段，不落全量业务数据。",
        ],
        "conclusion_hint": "",
        "probes": [],
        "summaries": {},
        "openapi_routes": {},
    }

    log: list = ev["probes"]
    rawmap: dict[str, dict] = {}

    def probe(key: str, *args, **kwargs) -> dict:
        row = call(*args, **kwargs)
        rawmap[key] = row
        brief(row, log)
        return row

    # A. 8105 智策 OS v2 API
    r = probe("8105_health", 8105, "GET", "/api/v1/health")
    j = json_of(r) or {}
    ev["summaries"]["8105_health"] = {
        "status": r["status"], "app": j.get("app"), "version": j.get("version"),
        "service_status": j.get("status"),
    }
    probe("8105_portal_sso_get_method_only", 8105, "GET", "/api/v1/auth/portal-sso")
    r = probe("8105_portal_sso_no_session", 8105, "POST", "/api/v1/auth/portal-sso",
              body=b"{}")
    ev["summaries"]["8105_portal_sso_no_session"] = {
        "status": r["status"], "body": json_of(r),
    }
    probe("8105_acad1_research_pool_direct", 8105, "GET", "/api/v1/acad1-research/pool")
    r = probe("8105_hermes_acad1_comparison", 8105, "GET", "/api/v1/hermes/acad1-comparison")
    ev["summaries"]["8105_hermes_acad1_comparison"] = summary_comparison(r)

    # B. 8734 门户入口（nginx /quant-api/ → 8105；/api/ → 8091）
    for sub in ACAD1_SUBPATHS:
        probe("8734_acad1_research_%s_unauth" % sub, 8734, "GET",
              "/quant-api/api/v1/acad1-research/%s" % sub)
    r = probe("8734_hermes_acad1_comparison", 8734, "GET",
              "/quant-api/api/v1/hermes/acad1-comparison")
    ev["summaries"]["8734_hermes_acad1_comparison"] = summary_comparison(r)
    a = rawmap["8105_hermes_acad1_comparison"]
    b = rawmap["8734_hermes_acad1_comparison"]
    ev["summaries"]["comparison_8105_vs_8734"] = {
        "same_status": a["status"] == b["status"],
        "same_body_sha256_12": a["body_sha256_12"] == b["body_sha256_12"],
        "sha256_12": a["body_sha256_12"],
        "body_len": a["body_len"],
        "body_identical": a["_raw"] == b["_raw"],
    }

    r = probe("8734_api_acad1_pool", 8734, "GET", "/api/acad1/pool")
    ev["summaries"]["8734_api_acad1_pool"] = summary_pool(r)

    r = probe("8734_academy_shuijing", 8734, "GET", "/academy/shuijing")
    html = r["_raw"].decode("utf-8", "replace")
    title = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    ev["summaries"]["8734_academy_shuijing"] = {
        "status": r["status"],
        "content_type": r["content_type"],
        "html_len": r["body_len"],
        "title": (title.group(1).strip() if title else None),
        "references_shuijing_js": "shuijing.js" in html,
        "references_quant_api": "quant-api" in html,
        "references_api_acad1_pool": "/api/acad1/pool" in html,
    }
    probe("8734_acad1_pool_page", 8734, "GET", "/acad1/pool")  # 附：另一入口，仅记录状态

    # C. 8091 portal-api
    r = probe("8091_api_acad1_pool", 8091, "GET", "/api/acad1/pool")
    ev["summaries"]["8091_api_acad1_pool"] = summary_pool(r)

    # D. 交叉：8734 /api/acad1/pool 与 8091 直连是否同源同体
    a = rawmap["8734_api_acad1_pool"]
    b = rawmap["8091_api_acad1_pool"]
    ev["summaries"]["pool_8734_vs_8091"] = {
        "same_status": a["status"] == b["status"],
        "same_body_sha256_12": a["body_sha256_12"] == b["body_sha256_12"],
        "sha256_12": a["body_sha256_12"],
        "body_len": a["body_len"],
    }

    # E. acad1-research 六路径 401 一致性（8734 与 8105 直连对照）
    rows = [p for p in log if p["path"].startswith("/quant-api/api/v1/acad1-research/")]
    body_shas = {p["body_sha256_12"] for p in rows}
    ev["summaries"]["acad1_research_unauth"] = {
        "paths": [p["path"] for p in rows],
        "statuses": [p["status"] for p in rows],
        "body_sha256_12_set": sorted(body_shas),
        "all_same_401_body": len(body_shas) == 1,
        "body_sample": next((p.get("body") for p in rows if p.get("body")), None),
        "native_8105_same_status": rawmap["8105_acad1_research_pool_direct"]["status"],
        "native_8105_body_sha256_12": rawmap["8105_acad1_research_pool_direct"]["body_sha256_12"],
        "servers_8734_401": sorted({p.get("server", "") for p in rows}),
        "server_8105_401": rawmap["8105_acad1_research_pool_direct"].get("server", ""),
    }

    # F. 8105 OpenAPI：路由存在性/方法（只记相关路径）
    r = call(8105, "GET", "/openapi.json", timeout=30)
    spec = json_of(r) or {}
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    wanted = {}
    for p, ops in paths.items():
        if any(k in p for k in ("acad1-research", "hermes/acad1-comparison",
                                "auth/portal-sso", "/health")):
            wanted[p] = sorted([m.upper() for m in ops.keys()])
    ev["openapi_routes"] = {
        "openapi_status": r["status"],
        "openapi_paths_total": len(paths),
        "matched": wanted,
    }

    # 清理 raw 引用（不落全量 body）
    rawmap_keys = sorted(rawmap.keys())
    ev["probe_keys"] = rawmap_keys
    out = OUT_DIR / "2026-11-04_acad1_live_accept_raw.json"
    out.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "out": str(out),
        "ts": ev["ts"],
        "health_8105": ev["summaries"]["8105_health"],
        "portal_sso_no_session": ev["summaries"]["8105_portal_sso_no_session"],
        "acad1_research_401": ev["summaries"]["acad1_research_unauth"],
        "comparison_8734": ev["summaries"]["8734_hermes_acad1_comparison"],
        "pool_8091": {k: ev["summaries"]["8091_api_acad1_pool"].get(k) for k in (
            "status", "source_mode", "official", "pool_date", "data_date",
            "summary_pool_total", "items_len", "stale")} if "status" in ev["summaries"]["8091_api_acad1_pool"] else {
            k: v for k, v in ev["summaries"]["8091_api_acad1_pool"].items()
            if k in ("source_mode", "official", "pool_date", "data_date", "summary_pool_total", "items_len", "stale")},
        "shuijing": ev["summaries"]["8734_academy_shuijing"],
        "pool_8734_vs_8091": ev["summaries"]["pool_8734_vs_8091"],
        "openapi_matched": ev["openapi_routes"]["matched"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
