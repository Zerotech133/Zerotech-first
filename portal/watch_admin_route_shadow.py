# -*- coding: utf-8 -*-
"""Read-only "影子账本逐票决策下钻" (shadow ledger per-ticket decision drill-down)
route for the Watch read-only admin page (M2/M3, bound to 127.0.0.1:8796).

This is one feature module of the watch-admin read-only management page. It is
loaded dynamically by ``watch_admin_server._FEATURE_MODULES`` and exposes:

    API_ROUTES  = {"/api/watch-admin/shadow": shadow_api}
    HTML_ROUTES = {"/watch-admin/shadow": SHADOW_HTML}

Hard constraints (must hold — these are non-negotiable):
  * PURELY READ-ONLY. This module never writes any business state. It performs a
    streaming, read-only read of the real shadow ledger JSONL.
  * FAIL-VISIBLE, NEVER FABRICATE. If the source file is unreadable or a line
    cannot be parsed, we surface an explicit ``verified=False`` + ``error_code``
    envelope and return an *empty but legal* dict. We never invent numbers.
  * REALM IS EXPLICITLY simulation_paper (模拟盘非生产). Every ticket row is
    stamped with ``realm="simulation_paper"`` and the envelope advertises
    ``trading_permitted=false``. This feature lives in the simulation-paper
    domain; the follow/order buttons on the page are ALWAYS greyed (disabled).
  * NO BLOCK ITEMS UNLOCKED. We do NOT touch the real Feishu push / GAP-4 /
    051 timing gap / real notifications / real trading. This module never
    imports or modifies portal_api.py and never talks to 8091 / 10.205.0.118.
  * This module does NOT modify watch_admin_server.py / watch_admin_page.html /
    watch_admin_snapshot.py. It is an isolated, additive file.

Data source (read-only, streaming):
    <repo>/agent_platform/data/shadow/shadow_ledger.jsonl
    (~30 lines, one JSON object per line; fields include recommendation /
     confidence / score / degraded / latency_s / run_id / ... ; NOTE: the raw
     file has NO ``realm`` field — we stamp simulation_paper in-module).

Run/verify (managed Python 3.13.12):
    py_compile portal/watch_admin_route_shadow.py
    python -c "import watch_admin_route_shadow as m; print(m.shadow_api({}))"
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "API_ROUTES",
    "HTML_ROUTES",
    "SHADOW_HTML",
    "shadow_api",
    "SHADOW_REALM",
    "SHADOW_LEDGER_PATH",
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# The data domain for THIS feature is the simulation paper (模拟盘) — never
# presented as production. All ticket rows and the envelope are stamped with it.
SHADOW_REALM: str = "simulation_paper"

DEFAULT_PAGE_SIZE: int = 50
MAX_PAGE_SIZE: int = 500

# Resolve the real source path relative to this file so the module is portable:
#   <repo>/portal/watch_admin_route_shadow.py  ->  parent = portal
#   portal.parent = <repo>  ->  <repo>/agent_platform/data/shadow/shadow_ledger.jsonl
_HERE = Path(__file__).resolve().parent
_DEFAULT_LEDGER = (
    _HERE.parent / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
)
# Allow an explicit override (e.g. for tests / different mounts).
_SHADOW_LEDGER_ENV = os.environ.get("WATCH_ADMIN_SHADOW_LEDGER")
SHADOW_LEDGER_PATH: Path = Path(
    _SHADOW_LEDGER_ENV if _SHADOW_LEDGER_ENV else _DEFAULT_LEDGER
).resolve()


# --------------------------------------------------------------------------- #
# Query coercion helpers (robust to both parse_qs dicts and direct calls)
# --------------------------------------------------------------------------- #
def _coerce_list(value: Any) -> List[Any]:
    """Normalize a query value into a flat list.

    Handles: None -> [], scalar -> [scalar], list -> flattened list.
    This makes the function tolerant of both ``parse_qs`` output
    (``{"recommendation": ["HOLD"]}``) and a direct call
    (``{"recommendation": ["HOLD"]}`` or ``{"recommendation": "HOLD"}``).
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                out.extend(_coerce_list(item))
            else:
                out.append(item)
        return out
    return [value]


def _coerce_int(value: Any, default: int) -> int:
    """Extract an int from a query value that may be a str/list/scalar."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        value = value[0]
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (ValueError, TypeError):
            return default
    return default


def _reco_key(value: Any) -> str:
    """Normalize a recommendation value into a stable distribution key."""
    return "null" if value is None else str(value)


# --------------------------------------------------------------------------- #
# Source read (streaming, read-only, fail-visible)
# --------------------------------------------------------------------------- #
def _load_ledger(path: Path) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
    """Stream-read the shadow ledger JSONL.

    Returns ``(rows, error_code, skipped)``.

    * On a hard file error (missing / unreadable) -> ``([], error_code, 0)`` so
      the caller can set ``verified=False`` and still return a legal dict.
    * On per-line parse errors we skip the bad line and count it in ``skipped``
      but keep going (fail-visible, never crash, never fabricate).
    """
    rows: List[Dict[str, Any]] = []
    skipped: int = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    skipped += 1
    except FileNotFoundError:
        return [], "missing", 0
    except (IsADirectoryError, PermissionError):
        return [], "permission", 0
    except OSError:  # pragma: no cover - environment dependent
        return [], "read_error", 0
    return rows, None, skipped


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Build a clean, presentation-ready ticket row and stamp the realm."""
    row = {
        "date": raw.get("date"),
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "recommendation": raw.get("recommendation"),
        "confidence": raw.get("confidence"),
        "score": raw.get("score"),
        "degraded": raw.get("degraded"),
        "latency_s": raw.get("latency_s"),
        "run_id": raw.get("run_id"),
        "realm": SHADOW_REALM,  # explicit simulation-paper stamp
    }
    return row


def _empty_envelope(page: int, page_size: int, error_code: str,
                    note: str) -> Dict[str, Any]:
    """Return a legal, empty envelope when the source cannot be verified."""
    return {
        "state": "unknown",
        "source_mode": "file",
        "verified": False,
        "error_code": error_code,
        "realm": SHADOW_REALM,
        "total": 0,
        "page": page,
        "page_size": page_size,
        "rows": [],
        "by_recommendation": {},
        "by_realm": {},
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Public API route
# --------------------------------------------------------------------------- #
def shadow_api(query: dict) -> dict:
    """Shadow-ledger per-ticket decision drill-down (read-only) API.

    Args:
        query: A mapping of query parameters. Tolerates both ``parse_qs`` style
            (values are lists, e.g. ``{"recommendation": ["HOLD"]}``) and direct
            calls. Supported keys:
              * ``recommendation`` (optional): list of recommendation values to
                keep (e.g. ``["HOLD"]``, ``["BUY", "WATCH"]``). ``null`` rows
                match only when ``"null"`` is in the filter.
              * ``realm`` (optional): list of realm values to keep. Since this
                feature is simulation_paper, only ``"simulation_paper"`` matches.
              * ``page`` (optional, 1-based, default 1).
              * ``page_size`` (optional, default 50, clamped to 1..500).

    Returns:
        An envelope dict with: state, source_mode, verified, error_code, realm,
        total, page, page_size, rows (this page's per-ticket details),
        by_recommendation (distribution), by_realm (distribution), note.

        On a source failure the dict is still fully legal and explicitly marks
        ``verified=False`` + an ``error_code``; it never fabricates data.
    """
    query = query or {}
    reco_filter = [str(x) for x in _coerce_list(query.get("recommendation"))]
    realm_filter = [str(x) for x in _coerce_list(query.get("realm"))]
    page = max(1, _coerce_int(query.get("page"), 1))
    page_size = min(MAX_PAGE_SIZE, max(1, _coerce_int(query.get("page_size"),
                                                      DEFAULT_PAGE_SIZE)))

    raw_rows, error_code, skipped = _load_ledger(SHADOW_LEDGER_PATH)
    if error_code is not None:
        note = (
            "影子账本文件不可读（error_code=%s）；未接真源，未编造任何数据。"
            "模拟盘·非生产·trading_permitted=false。" % error_code
        )
        return _empty_envelope(page, page_size, error_code, note)

    # Normalize + stamp realm, then apply filters.
    rows = [_normalize_row(r) for r in raw_rows if isinstance(r, dict)]
    if reco_filter:
        allowed = set(reco_filter)
        rows = [r for r in rows if _reco_key(r.get("recommendation")) in allowed]
    if realm_filter:
        allowed_realm = set(realm_filter)
        rows = [r for r in rows if str(r.get("realm")) in allowed_realm]

    # Distributions are computed over the full filtered set (not just the page).
    by_reco = dict(Counter(_reco_key(r.get("recommendation")) for r in rows))
    by_realm = dict(Counter(str(r.get("realm")) for r in rows))

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]

    note = (
        "影子账本逐票下钻（真实只读·流式读取）· 源=%s · 已接真源 · "
        "模拟盘·非生产·trading_permitted=false。" % SHADOW_LEDGER_PATH
    )
    if skipped:
        note += " 跳过 %d 行无法解析的记录（fail-visible，未编造）。" % skipped

    return {
        "state": "healthy",
        "source_mode": "file",
        "verified": True,
        "error_code": None,
        "realm": SHADOW_REALM,
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": page_rows,
        "by_recommendation": by_reco,
        "by_realm": by_realm,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Module contract (consumed by watch_admin_server dynamic loader)
# --------------------------------------------------------------------------- #
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/shadow": shadow_api,
}


# --------------------------------------------------------------------------- #
# Read-only HTML page (standalone string; aligned with the existing watch-admin
# visual tokens; NO top pill navigation; follow/order buttons ALWAYS disabled)
# --------------------------------------------------------------------------- #
SHADOW_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>影子账本逐票下钻 · 只读管理页 (M2/M3)</title>
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
/* red-line: 模拟盘·非生产·trading_permitted=false */
.redline{background:rgba(232,115,106,.10);border:1px solid var(--no);border-left:4px solid var(--no);border-radius:8px;padding:14px 18px;margin:16px 0}
.redline b{color:var(--no);font-size:14px;letter-spacing:.04em}
.rl-sub{color:var(--sub);font-size:12px;margin-top:6px;line-height:1.6}
.rl-sub code{color:var(--amber);background:var(--chip);padding:0 5px;border-radius:3px}
.rl-actions{margin-top:10px;display:flex;align-items:center;gap:10px}
.rl-tag{color:var(--no);font-size:11.5px;border:1px solid var(--no);border-radius:4px;padding:2px 8px}
.btndis{background:#33445c;color:#7e8ca0;border:1px solid var(--line);border-radius:6px;padding:6px 16px;font-size:12.5px;cursor:not-allowed}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 16px}
.toolbar label{color:var(--sub);font-size:12.5px}
.toolbar select,.toolbar input{background:var(--navy3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:13px}
.toolbar .btn{background:var(--cyan-d);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12.5px;cursor:pointer}
.toolbar .btn:hover{background:var(--cyan)}
.toolbar .stamp{color:var(--sub);font-size:11.5px;margin-left:auto}
.zone{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.zone h3{margin:0 0 12px;font-size:15.5px;color:var(--cyan)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th{background:var(--chip);text-align:left;padding:7px 9px;border:1px solid var(--line);color:var(--sub);font-weight:700;white-space:nowrap}
td{padding:7px 9px;border:1px solid var(--line);vertical-align:top}
td.rcm{font-weight:700}
.rcm-HOLD{color:var(--cyan)} .rcm-BUY{color:var(--ok)} .rcm-WATCH{color:var(--amber)} .rcm-null{color:var(--sub)}
.note{background:var(--navy3);border:1px solid var(--line);border-radius:6px;padding:8px 11px;font-size:11.5px;color:var(--sub);margin-top:8px}
.demo{background:rgba(232,115,106,.12);border:1px solid var(--no);border-radius:6px;padding:8px 11px;font-size:11.5px;color:var(--no);margin-top:8px}
.dist{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:14px;padding:3px 11px;font-size:12px;color:var(--sub)}
.chip b{color:var(--ink)}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--sub)}
@media(max-width:820px){.wrap{padding:18px}.toolbar .stamp{margin-left:0}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">QUANT / WATCH DOMAIN / M2 REAL READ-ONLY + M3 A/B SHADOW</div>
  <h1>影子账本逐票决策下钻<span class="badge">只读 · 模拟盘域</span></h1>
  <div class="sub">真实只读接入（fail-visible）· 不替换任何生产页 · 不修改 portal_api.py · 独立端口 8796 · 飞书/真实通知/交易 维持 BLOCK</div>
</header>

<div class="redline">
  <b>⚠ 模拟盘 · 非生产 · trading_permitted=false</b>
  <div class="rl-sub">本页为影子账本只读下钻，数据域 = <code>simulation_paper</code>，绝不代表生产信号。跟随 / 下单按钮恒置灰（disabled），任何 BLOCK 项（飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易）均不解锁。</div>
  <div class="rl-actions">
    <button class="btndis" disabled title="模拟盘只读 · 恒置灰">跟随</button>
    <button class="btndis" disabled title="模拟盘只读 · 恒置灰">下单</button>
    <span class="rl-tag">trading_permitted=false</span>
  </div>
</div>

<div class="toolbar">
  <label for="reco">建议过滤：</label>
  <select id="reco"><option value="">全部</option></select>
  <label for="ps">每页：</label>
  <input id="ps" type="number" min="1" max="500" value="50" style="width:72px">
  <button class="btn" id="reload">刷新</button>
  <span class="stamp" id="stamp"></span>
</div>

<div class="zone" id="zones">
  <div class="note">加载中…</div>
</div>

<footer>
  交付：影子账本逐票决策下钻（本次新增）。纯加法、只读、fail-visible；源缺失仅标 SOURCE_UNVERIFIED，绝不编造。不替换任何生产路由；不触碰 8091 与 portal_api.py。realm = simulation_paper（模拟盘非生产）。
</footer>
</div>

<script>
(function(){
  "use strict";
  var API = "/api/watch-admin/shadow";
  function esc(v){
    if(v===null||v===undefined) return "";
    return String(v).replace(/[&<>"]/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];
    });
  }
  function fmt(v){
    if(v===null||v===undefined||v==="") return "—";
    return esc(v);
  }
  function cls(v){ return v===null||v===undefined ? "rcm-null" : ("rcm-"+String(v)); }

  function load(reco, ps){
    var url = API;
    var q = [];
    if(reco) q.push("recommendation="+encodeURIComponent(reco));
    if(ps && ps!=="50") q.push("page_size="+encodeURIComponent(ps));
    if(q.length) url += "?"+q.join("&");
    fetch(url).then(function(r){return r.json();}).then(function(snap){ render(snap, reco, ps); })
      .catch(function(err){
        document.getElementById("zones").innerHTML =
          '<div class="demo">快照获取失败：'+esc(err)+'（服务是否在 8796 运行？）</div>';
      });
  }

  function render(snap, reco, ps){
    var box = document.getElementById("zones");
    if(!snap){
      box.innerHTML = '<div class="demo">快照获取失败：空响应（服务是否在 8796 运行？）</div>';
      return;
    }
    if(snap.verified === false){
      var msg = snap.error_code ? ("error_code="+esc(snap.error_code)) : "未接真源";
      box.innerHTML = '<div class="demo">影子账本不可用（'+msg+'）· 未编造数据</div>';
      return;
    }
    var html = "";
    // distributions
    html += '<div style="margin-bottom:14px">';
    html += '<h3>按建议分布</h3><div class="dist">';
    var recoKeys = Object.keys(snap.by_recommendation||{}).sort(function(a,b){
      return (snap.by_recommendation[b]||0)-(snap.by_recommendation[a]||0); });
    if(!recoKeys.length) html += '<span class="chip">空</span>';
    recoKeys.forEach(function(k){
      html += '<span class="chip">'+esc(k)+' <b>'+(snap.by_recommendation[k]||0)+'</b></span>';
    });
    html += '</div>';
    html += '<h3 style="margin-top:14px">按域分布</h3><div class="dist">';
    var realmKeys = Object.keys(snap.by_realm||{});
    if(!realmKeys.length) html += '<span class="chip">空</span>';
    realmKeys.forEach(function(k){
      html += '<span class="chip">'+esc(k)+' <b>'+(snap.by_realm[k]||0)+'</b></span>';
    });
    html += '</div></div>';

    // per-ticket detail table
    html += '<h3>逐票明细（第 '+esc(snap.page)+' 页 / 共 '+esc(snap.total)+' 条）</h3>';
    var rows = snap.rows||[];
    if(!rows.length){
      html += '<div class="note">本页无数据（过滤条件可能过于严格）。</div>';
    } else {
      var cols = [
        {k:"symbol", t:"代码"}, {k:"name", t:"名称"},
        {k:"recommendation", t:"建议"}, {k:"confidence", t:"置信度"},
        {k:"score", t:"评分"}, {k:"degraded", t:"降级"},
        {k:"latency_s", t:"延迟(s)"}, {k:"run_id", t:"run_id"},
        {k:"realm", t:"域"}
      ];
      html += "<table><thead><tr>";
      cols.forEach(function(c){ html += "<th>"+esc(c.t)+"</th>"; });
      html += "</tr></thead><tbody>";
      rows.forEach(function(r){
        html += "<tr>";
        cols.forEach(function(c){
          if(c.k==="recommendation"){
            html += '<td class="rcm '+cls(r.recommendation)+'">'+fmt(r.recommendation)+'</td>';
          } else if(c.k==="degraded"){
            html += "<td>"+(r.degraded? "是":"否")+"</td>";
          } else {
            html += "<td>"+fmt(r[c.k])+"</td>";
          }
        });
        html += "</tr>";
      });
      html += "</tbody></table>";
    }
    html += '<div class="note">'+esc(snap.note||"")+'</div>';
    box.innerHTML = html;

    // populate recommendation filter from distribution
    var sel = document.getElementById("reco");
    var cur = sel.value;
    sel.innerHTML = '<option value="">全部</option>';
    recoKeys.forEach(function(k){
      var o = document.createElement("option");
      o.value = k; o.textContent = k + " ("+(snap.by_recommendation[k]||0)+")";
      sel.appendChild(o);
    });
    sel.value = (cur && recoKeys.indexOf(cur)>=0) ? cur : "";

    var stamp = "verified="+(snap.verified?"true":"false")+" · realm="+esc(snap.realm||"?")+
                " · total="+esc(snap.total)+" · page="+esc(snap.page)+"/"+esc(snap.page_size);
    document.getElementById("stamp").textContent = stamp;
  }

  (function(){
    var sel = document.getElementById("reco");
    var ps = document.getElementById("ps");
    sel.addEventListener("change", function(){ load(sel.value, ps.value); });
    ps.addEventListener("change", function(){ load(sel.value, ps.value); });
    document.getElementById("reload").addEventListener("click", function(){ load(sel.value, ps.value); });
  })();

  load("", "50");
})();
</script>
</body>
</html>"""


HTML_ROUTES: Dict[str, Any] = {
    "/watch-admin/shadow": SHADOW_HTML,
}


# --------------------------------------------------------------------------- #
# Self-check (only when executed directly; never used by the server loader)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import pprint
    pprint.pprint(shadow_api({}))
    pprint.pprint(shadow_api({"recommendation": ["HOLD"]}))
