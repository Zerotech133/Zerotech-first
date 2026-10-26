# -*- coding: utf-8 -*-
"""Standalone read-only route: 缠论信号池全量下钻 (Chanlun signal-pool drill-down).

Pure read-only feature module for the Watch admin read-only page (M2/M3, bound to
``127.0.0.1:8796``). It is dynamically loaded by ``watch_admin_server.py`` via the
module-level contracts ``API_ROUTES`` / ``HTML_ROUTES`` and never edits any other
file.

Hard constraints honored by this module:
  * Read-only. Exactly one file is opened, read-only: the ST1 缠论信号池
    (``quant_backtest/data/chanlun_pool.jsonl``). No state is ever written.
  * Fail-visible. When the source file is missing/unreadable, the API returns a
    VALID envelope with ``verified=False`` and ``error_code="read_error:..."``,
    and NEVER fabricates counts (``total=0``, ``rows=[]``, distributions ``{}``).
  * Streaming. The file is scanned line-by-line (jsonl). The whole filtered list
    is never loaded into memory before paging: a single scan performs
    count + offset and only buffers the rows of the *requested page*
    (memory bounded by ``page_size``), while still counting the full filtered
    total and the type/market distributions across all matching lines.
  * Does not touch 8091 / ``portal_api.py`` / ``10.205.0.118``, and does not
    unlock any BLOCK item (Feishu real push / GAP-4 / 051 timing gap / real
    notifications / trading). Does not modify ``watch_admin_server.py`` /
    ``watch_admin_page.html`` / ``watch_admin_snapshot.py``.

Contract (consumed by watch_admin_server.py):
  * ``API_ROUTES = {"/api/watch-admin/chanlun": chanlun_api}``
  * ``HTML_ROUTES = {"/watch-admin/chanlun": CHANLUN_HTML}``
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- path resolution ----------------------------------------------------- #
_HERE = Path(__file__).resolve().parent            # .../portal
_PROJECT_ROOT = _HERE.parent                       # .../老田的AI助手
# Allow an override via env, but keep it OFF the forbidden 10.205.0.118 host.
_CHANLUN_PATH = Path(os.environ.get(
    "CHANLUN_POOL_PATH",
    str(_PROJECT_ROOT / "quant_backtest" / "data" / "chanlun_pool.jsonl"),
))

# Filterable fields -> record field names (exact string match when active).
_FILTER_FIELDS = ("market", "type", "period", "trend")

# Canonical column order for the result table (UI only).
_CANON_COLS = [
    "market", "code", "name", "period", "type", "dt", "price",
    "detect_date", "lag", "entry_px", "trend", "reg",
    "bei", "bei2", "vv", "source", "first_seen",
]


# --------------------------------------------------------------------------- #
# Low-level helpers (fail-visible, never raise from the public API)            #
# --------------------------------------------------------------------------- #
def _to_str(value: Any) -> str:
    """Normalize a record value to a comparable string (without fabrication)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # Integer-valued floats collapse to int-string so "3.0" == "3" in filters.
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def _bump(dist: Dict[str, int], key: Any) -> None:
    """Increment a distribution bucket; empty values bucket as '(空)'."""
    label = _to_str(key) if key != "" else "(空)"
    if label == "":
        label = "(空)"
    dist[label] = dist.get(label, 0) + 1


def _matches(rec: Dict[str, Any], active: Dict[str, str]) -> bool:
    """Return True iff the record satisfies every active exact-match filter."""
    for field, wanted in active.items():
        actual = rec.get(field)
        if actual is None:
            return False
        if _to_str(actual) != wanted:
            return False
    return True


def _scan_pool(path: Path, active: Dict[str, str], page: int, page_size: int) -> Dict[str, Any]:
    """Stream the jsonl pool, apply filters, and page with count + offset.

    Single pass: total and the type/market/period/trend distributions are
    accumulated across ALL matching lines, but only the rows inside the requested
    page window are buffered (bounded memory). The whole filtered list is never
    materialized.
    """
    offset = (page - 1) * page_size
    total = 0
    by_type: Dict[str, int] = {}
    by_market: Dict[str, int] = {}
    by_period: Dict[str, int] = {}
    by_trend: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []

    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Skip unparseable lines; never fabricate — they are simply
                # excluded from total/distributions (honest omission).
                continue
            if not isinstance(rec, dict):
                continue
            if not _matches(rec, active):
                continue
            total += 1
            _bump(by_type, rec.get("type"))
            _bump(by_market, rec.get("market"))
            _bump(by_period, rec.get("period"))
            _bump(by_trend, rec.get("trend"))
            # Buffer only the current page window (count + offset paging).
            if total > offset and len(rows) < page_size:
                rows.append(rec)
            # Rows beyond the window keep contributing to total/distributions
            # but are NOT retained in memory.

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
    return {
        "total": total,
        "rows": rows,
        "by_type": by_type,
        "by_market": by_market,
        "by_period": by_period,
        "by_trend": by_trend,
        "total_pages": total_pages,
    }


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def chanlun_api(query: dict) -> dict:
    """Drill-down API for the ST1 缠论 signal pool.

    ``query`` mirrors ``urllib.parse.parse_qs`` output: values are lists; we take
    the first element. Supported parameters:
      market   (e.g. CN / SH / SZ / HK / US)
      type     (e.g. 3买 / 2买 / 类二买)
      period   (e.g. 1d / 60m)
      trend    (e.g. up / down)
      page     (default 1)
      page_size(default 50)

    Returns an envelope dict (always a valid envelope, even on read failure —
    with ``verified=False`` and ``error_code`` set, and NO fabricated numbers).
    """
    def _first(name: str, default: Optional[str]) -> Optional[str]:
        raw = query.get(name)
        val = raw[0] if isinstance(raw, list) and raw else raw
        if val is None:
            return default
        val = str(val).strip()
        return val if val != "" else default

    market = _first("market", None)
    sig_type = _first("type", None)      # "type" builtin; keep a distinct var name
    period = _first("period", None)
    trend = _first("trend", None)

    try:
        page = max(1, int(_first("page", "1") or "1"))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(_first("page_size", "50") or "50")
    except (TypeError, ValueError):
        page_size = 50
    page_size = max(1, min(page_size, 500))   # bound page size to protect memory

    filters = {"market": market, "type": sig_type,
               "period": period, "trend": trend}
    active = {k: v for k, v in filters.items() if v is not None}

    # Envelope skeleton (defaults express the fail-visible / honest baseline).
    envelope: Dict[str, Any] = {
        "state": "unknown",
        "source_mode": "file",
        "verified": False,
        "error_code": None,
        "total": 0,
        "page": page,
        "page_size": page_size,
        "rows": [],
        "by_type": {},
        "by_market": {},
        # Additive (beyond the minimal contract) — useful for UI dropdowns:
        "by_period": {},
        "by_trend": {},
        "total_pages": 0,
        "note": "",
    }

    try:
        scanned = _scan_pool(_CHANLUN_PATH, active, page, page_size)
        envelope.update(scanned)
        envelope["verified"] = True
        envelope["state"] = "healthy"
        envelope["note"] = (
            "ST1 缠论信号池（%s）：真实只读文件源，流式逐行扫描（jsonl），"
            "不整库载入、不写回。按过滤条件计数+偏移分页（单次扫描，仅缓冲本页行）。"
            % _CHANLUN_PATH
        )
    except Exception as exc:  # noqa: BLE001  (fail-visible — never raise)
        envelope["verified"] = False
        envelope["state"] = "unknown"
        envelope["error_code"] = "read_error:%s:%s" % (
            type(exc).__name__, str(exc)[:200])
        envelope["note"] = (
            "缠论信号池文件不可读或扫描异常（路径 %s）。返回合法信封但未编造任何数字"
            "（total=0 / rows=[] / 分布={}）。" % _CHANLUN_PATH
        )
        # total / rows / distributions remain 0 / [] / {} — honest, no fabrication.

    return envelope


# --------------------------------------------------------------------------- #
# Read-only HTML page (style aligned to watch_admin_page.html; no top pill nav)#
# --------------------------------------------------------------------------- #
CHANLUN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>缠论信号池全量下钻 · 只读管理页</title>
<style>
:root{
  --navy:#0e1b2a; --navy2:#13243a; --navy3:#1b3354;
  --cyan:#27c9c9; --cyan-d:#0e8f8f;
  --amber:#e0a23c; --amber-d:#b57d1e;
  --ink:#e9eff6; --sub:#9fb2c8; --line:#243a57;
  --ok:#39c08a; --wait:#e0a23c; --no:#e8736a; --chip:#16283f;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#0c1726,#0e1b2a 240px);color:var(--ink);font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:30px 32px 70px}
header{border-bottom:2px solid var(--cyan);padding-bottom:16px;margin-bottom:18px}
.eyebrow{color:var(--cyan);letter-spacing:.22em;font-size:11px;font-weight:700}
h1{font-size:23px;margin:8px 0 6px}
.sub{color:var(--sub);font-size:12.5px}
.badge{display:inline-block;border:1px solid var(--amber);color:var(--amber);border-radius:4px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:2px}
.m0{background:var(--chip);border:1px solid var(--line);border-left:3px solid var(--amber);border-radius:8px;padding:12px 16px;margin:16px 0;font-size:12.5px;color:var(--sub)}
.m0 b{color:var(--amber)}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 16px}
.toolbar label{color:var(--sub);font-size:12.5px}
.toolbar select{background:var(--navy3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:13px}
.toolbar input{background:var(--navy3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:13px;width:64px}
.toolbar .btn{background:var(--cyan-d);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12.5px;cursor:pointer}
.toolbar .btn:hover{background:var(--cyan)}
.toolbar .btn:disabled{background:#33445c;color:#7e8ca0;cursor:not-allowed}
.toolbar .stamp{color:var(--sub);font-size:11.5px;margin-left:auto}
.panel{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.panel h3{margin:0 0 12px;font-size:15.5px;color:var(--cyan);display:flex;align-items:center;gap:8px}
.note{background:var(--navy3);border:1px solid var(--line);border-radius:6px;padding:8px 11px;font-size:11.5px;color:var(--sub);margin-top:8px}
.dist{display:flex;flex-wrap:wrap;gap:8px}
.dchip{background:var(--navy3);border:1px solid var(--line);border-radius:6px;padding:5px 10px;font-size:12.5px}
.dchip b{color:var(--cyan)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th{background:var(--chip);text-align:left;padding:7px 9px;border:1px solid var(--line);color:var(--sub);font-weight:700;white-space:nowrap}
td{padding:6px 9px;border:1px solid var(--line);vertical-align:top}
.ok{color:var(--ok);font-weight:700}.wait{color:var(--wait);font-weight:700}.no{color:var(--no);font-weight:700}
.demo{background:rgba(232,115,106,.12);border:1px solid var(--no);border-radius:6px;padding:10px 13px;font-size:12.5px;color:var(--no);margin-top:8px}
.pager{display:flex;align-items:center;gap:12px;margin-top:14px}
.pager .info{color:var(--sub);font-size:12px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--sub)}
@media(max-width:820px){.toolbar .stamp{margin-left:0;width:100%}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">QUANT / WATCH DOMAIN / M2 REAL READ-ONLY</div>
  <h1>缠论信号池全量下钻<span class="badge">只读 · 8796</span></h1>
  <div class="sub">ST1 缠论信号池（quant_backtest/data/chanlun_pool.jsonl）真实只读逐行扫描 · 流式分页 · 不整库载入 · 不写回</div>
</header>

<div class="m0">
  <b>只读纪律（已锁定）：</b>仅文件只读扫描，不触碰 8091 / portal_api.py / 10.205.0.118；不解锁飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易 任何 BLOCK 项；不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。数据源不可达时页面诚实显示「快照获取失败」，绝不假绿。
</div>

<div class="toolbar" id="toolbar">
  <label for="f_market">市场</label>
  <select id="f_market"><option value="">全部</option></select>
  <label for="f_type">类型</label>
  <select id="f_type"><option value="">全部</option></select>
  <label for="f_period">周期</label>
  <select id="f_period"><option value="">全部</option></select>
  <label for="f_trend">趋势</label>
  <select id="f_trend"><option value="">全部</option></select>
  <label for="f_page">页码</label>
  <input id="f_page" type="number" min="1" value="1">
  <button class="btn" id="btn_query">查询</button>
  <span class="stamp" id="stamp"></span>
</div>

<div class="panel" id="dist_panel">
  <h3>分布（按过滤后的全集统计）</h3>
  <div class="dist" id="dist_type"></div>
  <div style="height:10px"></div>
  <div class="dist" id="dist_market"></div>
</div>

<div class="panel" id="rows_panel">
  <h3>信号明细（本页样本）</h3>
  <div id="rows_area"><div class="note">加载中…</div></div>
  <div class="pager" id="pager"></div>
</div>

<footer>
  交付：缠论信号池全量下钻只读路由（本次）。纯加法、只读、fail-visible；任何过滤条件下均可分页；数据源缺失仅标 SOURCE_UNVERIFIED，绝不编造数字。
  <div id="sig" style="margin-top:6px;color:var(--cyan)"></div>
</footer>
</div>

<script>
(function(){
  "use strict";
  var API = "/api/watch-admin/chanlun";
  var COLS = ["market","code","name","period","type","dt","price","detect_date","lag","entry_px","trend","reg","bei","bei2","vv","source","first_seen"];

  function esc(v){
    return String(v==null?"":v).replace(/[&<>"]/g,function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];
    });
  }

  // current filter from the toolbar dropdowns / page input
  function currentParams(){
    var p = new URLSearchParams();
    var m = document.getElementById("f_market").value;
    var t = document.getElementById("f_type").value;
    var pe = document.getElementById("f_period").value;
    var tr = document.getElementById("f_trend").value;
    var pg = document.getElementById("f_page").value;
    if(m) p.set("market", m);
    if(t) p.set("type", t);
    if(pe) p.set("period", pe);
    if(tr) p.set("trend", tr);
    if(pg && parseInt(pg,10) > 1) p.set("page", pg);
    return p;
  }

  function fillSelect(id, values, selected){
    var sel = document.getElementById(id);
    var cur = sel.value;
    sel.innerHTML = '<option value="">全部</option>';
    (values||[]).forEach(function(v){
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      sel.appendChild(o);
    });
    // keep current selection if still present, else honor requested
    sel.value = (selected!=null) ? selected : cur;
  }

  function renderDist(elId, dist){
    var el = document.getElementById(elId);
    var keys = Object.keys(dist||{});
    if(!keys.length){ el.innerHTML = '<span class="note">（空）</span>'; return; }
    keys.sort(function(a,b){ return (dist[b]||0) - (dist[a]||0); });
    el.innerHTML = keys.map(function(k){
      return '<span class="dchip">'+esc(k)+' <b>'+esc(dist[k])+'</b></span>';
    }).join("");
  }

  function renderRows(payload){
    var rows = payload.rows || [];
    var area = document.getElementById("rows_area");
    if(!rows.length){
      area.innerHTML = '<div class="note">本页无样本（total='+esc(payload.total)+'）。</div>';
    } else {
      var cols = COLS.filter(function(c){ return c in rows[0]; });
      if(cols.length === 0) cols = Object.keys(rows[0]);
      var h = "<tr>"+cols.map(function(c){return "<th>"+esc(c)+"</th>";}).join("")+"</tr>";
      var b = rows.map(function(r){
        return "<tr>"+cols.map(function(c){return "<td>"+esc(r[c])+"</td>";}).join("")+"</tr>";
      }).join("");
      area.innerHTML = "<table><thead>"+h+"</thead><tbody>"+b+"</tbody></table>";
    }

    // pager
    var tp = payload.total_pages || 0;
    var pg = payload.page || 1;
    var pager = document.getElementById("pager");
    var prevBtn = '<button class="btn" id="pg_prev"'+(pg<=1?" disabled":"")+'>上一页</button>';
    var nextBtn = '<button class="btn" id="pg_next"'+(tp===0||pg>=tp?" disabled":"")+'>下一页</button>';
    pager.innerHTML = prevBtn + nextBtn +
      '<span class="info">第 '+esc(pg)+' / '+esc(tp)+' 页 · 每页 '+esc(payload.page_size)+
      ' · 过滤后 total='+esc(payload.total)+' · verified='+esc(payload.verified)+'</span>';
    var pv = document.getElementById("pg_prev");
    var nx = document.getElementById("pg_next");
    if(pv) pv.addEventListener("click", function(){
      var v = Math.max(1, (parseInt(document.getElementById("f_page").value,10)||1) - 1);
      document.getElementById("f_page").value = v; query();
    });
    if(nx) nx.addEventListener("click", function(){
      var v = (parseInt(document.getElementById("f_page").value,10)||1) + 1;
      document.getElementById("f_page").value = v; query();
    });
  }

  function render(payload){
    renderDist("dist_type", payload.by_type);
    renderDist("dist_market", payload.by_market);
    renderRows(payload);

    var s = "state="+(payload.state||"?")+" · verified="+(payload.verified?"true":"false");
    if(payload.error_code) s += " · error_code="+esc(payload.error_code);
    if(payload.total!=null) s += " · total="+esc(payload.total);
    if(payload.note) s += " · "+esc(payload.note);
    document.getElementById("stamp").textContent = s;

    // populate dropdowns from the full-filtered universe (additive fields)
    var prev = currentParams();
    fillSelect("f_market", Object.keys(payload.by_market||{}), prev.get("market"));
    fillSelect("f_type", Object.keys(payload.by_type||{}), prev.get("type"));
    fillSelect("f_period", Object.keys(payload.by_period||{}), prev.get("period"));
    fillSelect("f_trend", Object.keys(payload.by_trend||{}), prev.get("trend"));
  }

  function query(){
    var p = currentParams();
    // reflect into URL (so the page is shareable / bookmarkable)
    var qs = p.toString();
    history.replaceState(null, "", qs ? ("?"+qs) : location.pathname);
    document.getElementById("rows_area").innerHTML = '<div class="note">加载中…</div>';
    fetch(API + (qs ? ("?"+qs) : ""))
      .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
      .then(render)
      .catch(function(err){
        // Honest failure — never fake green.
        document.getElementById("rows_area").innerHTML =
          '<div class="demo">快照获取失败：'+esc(err)+'（只读服务是否在 127.0.0.1:8796 运行？API=/api/watch-admin/chanlun）</div>';
        document.getElementById("dist_type").innerHTML = "";
        document.getElementById("dist_market").innerHTML = "";
        document.getElementById("stamp").textContent =
          "快照获取失败：" + esc(err) + " · SOURCE_UNVERIFIED";
      });
  }

  function initFromUrl(){
    var p = new URLSearchParams(location.search);
    if(p.has("market")) document.getElementById("f_market").value = p.get("market");
    if(p.has("type")) document.getElementById("f_type").value = p.get("type");
    if(p.has("period")) document.getElementById("f_period").value = p.get("period");
    if(p.has("trend")) document.getElementById("f_trend").value = p.get("trend");
    if(p.has("page")) document.getElementById("f_page").value = p.get("page");
  }

  document.getElementById("btn_query").addEventListener("click", function(){
    document.getElementById("f_page").value = "1"; query();
  });
  document.getElementById("sig").textContent =
    "缠论信号池全量下钻 · 来源 fail-visible · 未接真源一律报错不编造";

  initFromUrl();
  query();
})();
</script>
</body>
</html>
"""


# Module contract consumed by watch_admin_server.py (dynamic feature loading).
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/chanlun": chanlun_api,
}
HTML_ROUTES: Dict[str, Any] = {
    "/watch-admin/chanlun": CHANLUN_HTML,
}


if __name__ == "__main__":
    import pprint

    print("== empty query ==")
    pprint.pprint(chanlun_api({}))
    print("\n== type=3买 ==")
    pprint.pprint(chanlun_api({"type": ["3买"], "page": ["1"]}))
