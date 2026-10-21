# -*- coding: utf-8 -*-
"""组合器静态配置下钻（quad 展开 + 红线标注）只读路由。

盯盘只读管理页（M2/M3，绑 127.0.0.1:8796）的 P2 特性 D。

纯只读、fail-visible、绝不编造的特性模块。由主服务
``watch_admin_server._FEATURE_MODULES`` 按如下契约动态加载：

    API_ROUTES  = {"/api/watch-admin/combos": combos_api}
    HTML_ROUTES = {"/watch-admin/combos": COMBOS_HTML}

硬纪律（P2 特性 D，非协商）：
  * 纯只读。仅静态读取 ``k_stock/paper/combos.json`` 这一个文件，不写回、不发起
    任何网络请求、不触碰 8091 / portal_api.py / 10.205.0.118。
  * 绝不调用 ``engine.exec_run`` / ``signal_consumer`` / 任何交易或执行链。本模块
    不 import engine、不 import signal_consumer；红线原文只做「展示 + 标注」，
    绝不据此触发任何执行动作。
  * 不解锁任何 BLOCK 项（飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易）。
  * 不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。
    本文件是孤立的、加法的独立模块。
  * fail-visible：源文件缺失/不可解析时，API 返回合法信封并置
    ``verified=False`` + ``error_code``，且绝不编造任何组合或红线。若 API 不可达，
    页面诚实显示「快照获取失败」，绝不假绿。

数据域：本页所有组合均为 ``k_stock/paper`` 下的**虚拟组合**（模拟盘非生产）。
含红线的组合（如 ``combo_zhongxing``）显式标 ``is_shadow_period=True``，其红线
原文明示「影子期 D14=2026-11-04 评审+老田签准前绝不接 engine.exec_run/信号_consumer/
交易链」，本页仅展示与留痕。

数据源（只读）：
    <repo>/k_stock/paper/combos.json
    （registry_schema_version + combos[]；每个 combo 含 combo_id/name/quad；
     红线组合额外含 _wo 字段）

实跑自验（受管 Python 3.13.12）：
    py_compile portal/watch_admin_route_combos.py
    python -c "import watch_admin_route_combos as m; print(m.combos_api({}))"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "API_ROUTES",
    "HTML_ROUTES",
    "COMBOS_HTML",
    "combos_api",
    "COMBOS_JSON_PATH",
    "REALM",
]


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
# 数据域：本页所有组合均为模拟盘（虚拟组合），非生产。
REALM: str = "simulation_paper"

# 路径解析：相对本模块文件位置，避免依赖 cwd；并保留绝对兜底路径。
#   <repo>/portal/watch_admin_route_combos.py  ->  _HERE = portal
#   _HERE.parent = <repo>  ->  <repo>/k_stock/paper/combos.json
_HERE = Path(__file__).resolve().parent
_DEFAULT_COMBOS = _HERE.parent / "k_stock" / "paper" / "combos.json"
# 允许显式覆盖（例如测试 / 不同挂载）；仍强制落在本地，不指向 10.205.0.118。
_COMBOS_ENV = os.environ.get("WATCH_ADMIN_COMBOS_PATH")
COMBOS_JSON_PATH: Path = Path(_COMBOS_ENV if _COMBOS_ENV else _DEFAULT_COMBOS).resolve()

# quad 四元组的四个固定槽位（顺序稳定，供 UI 展开展示）。
QUAD_SLOTS: Tuple[str, str, str, str] = (
    "pick_source",
    "persona",
    "watch_style",
    "trader",
)

# 影子期（红线）判定的关键词提示（命中其一即视为处于影子期 / 执行未解锁）。
_SHADOW_HINTS = ("影子期", "影子", "shadow", "d14", "评审", "签准")

# 红线中「锁定执行动作」的关键词（仅用于从 _wo 原文中派生展示，绝不据此触发执行）。
_EXEC_BLOCK_KEYWORDS = (
    "engine.exec_run",
    "exec_run",
    "signal_consumer",
    "信号_consumer",
    "交易链",
    "真实交易",
)


# --------------------------------------------------------------------------- #
# 工具：容错读取 + quad 展开 + 红线解析（均 fail-visible、绝不编造）
# --------------------------------------------------------------------------- #
def _read_combos_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """只读解析 combos.json。

    返回 ``(registry_dict|None, error_code|None)``。
      * 文件缺失        -> (None, "missing")
      * JSON 非法       -> (None, "invalid_json")
      * 读取异常        -> (None, "read_error")
      * 成功            -> (dict, None)
    绝不编造：任何失败都返回 (None, code)，由调用方置 verified=False。
    """
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, "missing"
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid_json"
    except OSError:
        return None, "read_error"
    if not isinstance(data, dict):
        return None, "invalid_json"
    return data, None


def _expand_quad(combo: Dict[str, Any]) -> Dict[str, Any]:
    """将单个 combo 的 quad 四元组展开为可读结构。

    每个槽位展开为 {id, version}；persona 额外保留 params（风险参数）。
    缺失字段以 None 兜底，绝不编造不存在的字段。
    """
    quad_raw = combo.get("quad") or {}
    if not isinstance(quad_raw, dict):
        quad_raw = {}

    expanded: Dict[str, Any] = {}
    for slot in QUAD_SLOTS:
        node = quad_raw.get(slot) or {}
        if not isinstance(node, dict):
            node = {}
        entry: Dict[str, Any] = {
            "id": node.get("id"),
            "version": node.get("version"),
        }
        if slot == "persona":
            params = node.get("params") or {}
            entry["params"] = params if isinstance(params, dict) else {}
        expanded[slot] = entry
    return expanded


def _persona_risk_params(quad: Dict[str, Any]) -> Dict[str, Any]:
    """从展开后的 quad 中抽取 persona 风险参数（扁平化，便于展示与校验）。"""
    persona = quad.get("persona") or {}
    params = persona.get("params") or {}
    return {
        "risk_appetite": params.get("risk_appetite"),
        "conf_floor": params.get("conf_floor"),
        "max_pos_pct": params.get("max_pos_pct"),
        "max_total_pct": params.get("max_total_pct"),
    }


def _is_shadow_period(redline: Optional[str]) -> bool:
    """判定组合是否处于影子期 / 执行未解锁。

    基于红线原文中的影子期措辞（影子期 / 影子 / shadow / D14 / 评审 / 签准）
    判定，绝不凭空设定。
    """
    if not redline:
        return False
    low = redline.lower()
    return any(hint.lower() in low for hint in _SHADOW_HINTS)


def _locked_actions(redline: Optional[str]) -> List[str]:
    """从红线原文中派生「被锁定的执行动作」列表（仅展示用，绝不据此触发执行）。

    逐关键词扫描真实红线文本，命中的才列入；未命中返回空列表。
    """
    if not redline:
        return []
    out: List[str] = []
    for kw in _EXEC_BLOCK_KEYWORDS:
        if kw in redline:
            out.append(kw)
    return out


def _normalize_combo(combo: Any) -> Optional[Dict[str, Any]]:
    """将一个原始 combo 记录规范化为展示/下钻结构。

    结构含：id / name / quad 四元组展开 / persona_risk_params /
    _wo 红线原文 / is_shadow_period 标记 / locked_actions 派生列表。
    非 dict 记录直接丢弃（返回 None），绝不编造。
    """
    if not isinstance(combo, dict):
        return None
    quad = _expand_quad(combo)
    redline = combo.get("_wo")
    return {
        "id": combo.get("combo_id"),
        "name": combo.get("name"),
        "quad": quad,
        "persona_risk_params": _persona_risk_params(quad),
        "_wo": redline,
        "is_shadow_period": _is_shadow_period(redline),
        "locked_actions": _locked_actions(redline),
    }


def _build_redline_summary(combos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """汇总所有含红线的组合，形成红线项摘要（fail-visible，绝不编造）。

    仅纳入确实携带 _wo 的组合；每个摘要项保留 combo_id / name /
    is_shadow_period / redline_original / locked_actions，便于页面醒目标注。
    """
    summary: List[Dict[str, Any]] = []
    for c in combos:
        if not isinstance(c, dict):
            continue
        redline = c.get("_wo")
        if not redline:
            continue
        # 注意：此时传入的是「规范化后」的 combo，id 键为 "id"（非原始 "combo_id"）。
        summary.append({
            "combo_id": c.get("id"),
            "name": c.get("name"),
            "is_shadow_period": _is_shadow_period(redline),
            "redline_original": redline,
            "locked_actions": _locked_actions(redline),
        })
    return summary


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def combos_api(query: dict) -> dict:
    """组合器静态配置下钻 API（只读）。

    Args:
        query: 请求参数 dict（本特性无过滤参数，保留以兼容主服务
               ``parse_qs`` 风格调用；``{}`` 即全量返回）。

    Returns:
        信封 dict，含：
          * ``verified``        —— 文件成功解析则为 True，否则 False。
          * ``error_code``      —— None 或 "missing" / "invalid_json" / "read_error"。
          * ``combos``          —— 列表，每个含 id/name、quad 四元组展开、
                                   persona 风险参数、_wo 红线原文、is_shadow_period 标记。
          * ``redline_summary`` —— 红线项汇总列表（含组合维度 + 原文 + 锁定动作）。
          * ``note``            —— 人类可读说明。
        文件异常时仍返回合法 dict（verified=False + error_code），绝不编造组合。
    """
    query = query or {}

    registry, err = _read_combos_json(COMBOS_JSON_PATH)
    if err is not None:
        note = (
            "组合注册表文件不可读（error_code=%s）；未接真源，未编造任何组合或红线。"
            "模拟盘·非生产·trading_permitted=false。" % err
        )
        return {
            "verified": False,
            "error_code": err,
            "combos": [],
            "redline_summary": [],
            "note": note,
            "source": str(COMBOS_JSON_PATH),
            "total": 0,
            "realm": REALM,
            "trading_permitted": False,
            "is_shadow_period_any": False,
        }

    raw_combos = registry.get("combos")
    if not isinstance(raw_combos, list):
        raw_combos = []

    combos = [c for c in (_normalize_combo(x) for x in raw_combos) if c is not None]
    redline_summary = _build_redline_summary(combos)
    is_shadow_any = any(bool(c.get("is_shadow_period")) for c in combos)

    schema_version = registry.get("registry_schema_version")

    note = (
        "组合注册表（%s）：真实只读静态解析（registry_schema_version=%s）。"
        "quad 四元组展开 + persona 风险参数 + 红线原文标注；所有组合均为模拟盘虚拟组合，"
        "trading_permitted=false，绝不接 engine.exec_run/signal_consumer/交易链。"
        % (COMBOS_JSON_PATH, schema_version)
    )

    return {
        "verified": True,
        "error_code": None,
        "combos": combos,
        "redline_summary": redline_summary,
        "note": note,
        "source": str(COMBOS_JSON_PATH),
        "total": len(combos),
        "schema_version": schema_version,
        "realm": REALM,
        "trading_permitted": False,
        "is_shadow_period_any": is_shadow_any,
    }


# --------------------------------------------------------------------------- #
# 动态加载契约（被 watch_admin_server.py 消费）
# --------------------------------------------------------------------------- #
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/combos": combos_api,
}


# --------------------------------------------------------------------------- #
# 只读 HTML 页面（视觉对齐现有页：深海军蓝 #0e1b2a / 青 #27c9c9 / 琥珀 #e0a23c；
# 字体 "Microsoft YaHei",system-ui,sans-serif；禁止顶部胶囊导航）
# --------------------------------------------------------------------------- #
COMBOS_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>组合器静态配置下钻 · 只读管理页 (M2/M3)</title>
<style>
:root{
  --navy:#0e1b2a; --navy2:#13243a; --navy3:#1b3354;
  --cyan:#27c9c9; --cyan-d:#0e8f8f;
  --amber:#e0a23c; --amber-d:#b57d1e;
  --ink:#e9eff6; --sub:#9fb2c8; --line:#243a57;
  --ok:#39c08a; --no:#e8736a; --chip:#16283f;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#0c1726,#0e1b2a 240px);color:var(--ink);font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:30px 32px 70px}
header{border-bottom:2px solid var(--cyan);padding-bottom:16px;margin-bottom:18px}
.eyebrow{color:var(--cyan);letter-spacing:.22em;font-size:11px;font-weight:700}
h1{font-size:23px;margin:8px 0 6px}
.sub{color:var(--sub);font-size:12.5px}
/* 红线横幅：虚拟/影子期·只读展示·绝不接执行链 */
.redline{background:rgba(232,115,106,.10);border:1px solid var(--no);border-left:4px solid var(--no);border-radius:8px;padding:14px 18px;margin:16px 0}
.redline b{color:var(--no);font-size:15px;letter-spacing:.04em}
.rl-sub{color:var(--sub);font-size:12.5px;margin-top:6px;line-height:1.7}
.rl-sub code{color:var(--amber);background:var(--chip);padding:0 5px;border-radius:3px}
.rl-tags{margin-top:10px;display:flex;flex-wrap:wrap;gap:8px}
.rl-tag{color:var(--no);font-size:11.5px;border:1px solid var(--no);border-radius:4px;padding:2px 9px}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 16px}
.toolbar .btn{background:var(--cyan-d);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12.5px;cursor:pointer}
.toolbar .btn:hover{background:var(--cyan)}
.toolbar .stamp{color:var(--sub);font-size:11.5px;margin-left:auto}
.panel{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.panel h3{margin:0 0 12px;font-size:15.5px;color:var(--cyan)}
.note{background:var(--navy3);border:1px solid var(--line);border-radius:6px;padding:8px 11px;font-size:11.5px;color:var(--sub);margin-top:8px}
.demo{background:rgba(232,115,106,.12);border:1px solid var(--no);border-radius:6px;padding:10px 13px;font-size:12.5px;color:var(--no);margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th{background:var(--chip);text-align:left;padding:8px 10px;border:1px solid var(--line);color:var(--sub);font-weight:700;white-space:nowrap}
td{padding:8px 10px;border:1px solid var(--line);vertical-align:top}
.tt{font-family:"Consolas",ui-monospace,monospace;font-size:11.5px}
.tt b{color:var(--cyan)}
.params{font-size:11.5px;color:var(--sub);line-height:1.6}
.params b{color:var(--ink)}
.ok{color:var(--ok);font-weight:700}.wait{color:var(--amber);font-weight:700}.no{color:var(--no);font-weight:700}
.shadow-tag{display:inline-block;color:var(--no);border:1px solid var(--no);border-radius:4px;padding:1px 8px;font-size:11px}
.normal-tag{display:inline-block;color:var(--sub);border:1px solid var(--line);border-radius:4px;padding:1px 8px;font-size:11px}
/* 红线原文高亮 */
.wo{background:rgba(232,115,106,.10);border:1px solid var(--no);border-left:3px solid var(--no);border-radius:6px;padding:8px 11px;font-size:12px;color:var(--ink);line-height:1.7}
.wo-empty{color:var(--sub);font-size:12px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--sub)}
@media(max-width:820px){.wrap{padding:18px}.toolbar .stamp{margin-left:0;width:100%}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">QUANT / WATCH DOMAIN / M2 REAL READ-ONLY + M3 A/B SHADOW</div>
  <h1>组合器静态配置下钻<span style="display:inline-block;border:1px solid var(--amber);color:var(--amber);border-radius:4px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:2px">P2 特性 D · 只读</span></h1>
  <div class="sub">combos.json 静态配置只读下钻（quad 四元组展开 + persona 风险参数 + 红线标注）· 不替换任何生产页 · 独立端口 8796 · 飞书/真实通知/交易 维持 BLOCK</div>
</header>

<div class="redline">
  <b>⚠ 虚拟/影子期 · 只读展示 · 绝不接执行链</b>
  <div class="rl-sub">本页所有组合均为 <code>k_stock/paper</code> 下的<b>虚拟组合（模拟盘非生产）</b>。含红线的组合（如 <code>combo_zhongxing</code>）处于<b>影子期</b>，其红线明示「评审+老田签准前绝不接 <code>engine.exec_run</code>/<code>signal_consumer</code>/交易链」。本页仅静态解析与留痕，<b>不 import、不调用任何执行链</b>；任何 BLOCK 项（飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易）均不解锁。</div>
  <div class="rl-tags">
    <span class="rl-tag">trading_permitted=false</span>
    <span class="rl-tag">realm=simulation_paper</span>
    <span class="rl-tag">仅静态读取 combos.json</span>
  </div>
</div>

<div class="toolbar">
  <button class="btn" id="reload">刷新快照</button>
  <span class="stamp" id="stamp"></span>
</div>

<div class="panel" id="combo_panel">
  <h3>组合列表 · quad 四元组展开 + persona 风险参数 + 红线原文</h3>
  <div id="combo_area"><div class="note">加载中…</div></div>
</div>

<div class="panel" id="redline_panel">
  <h3>红线项汇总（redline_summary）</h3>
  <div id="redline_area"><div class="note">加载中…</div></div>
</div>

<footer>
  交付：组合器静态配置下钻只读路由（P2 特性 D，本次新增）。纯加法、只读、fail-visible；源缺失仅标 SOURCE_UNVERIFIED，绝不编造组合或红线。不替换任何生产路由；不触碰 8091 与 portal_api.py；不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。
  <div id="sig" style="margin-top:6px;color:var(--cyan)"></div>
</footer>
</div>

<script>
(function(){
  "use strict";
  var API = "/api/watch-admin/combos";

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

  // 渲染 quad 槽位为 id@version
  function slotTT(node){
    if(!node || (!node.id && !node.version)) return "—";
    return '<span class="tt"><b>'+esc(node.id||"")+'</b>@'+esc(node.version||"")+'</span>';
  }
  function paramsText(p){
    if(!p) return "—";
    return '<div class="params">'+
      'risk_appetite=<b>'+fmt(p.risk_appetite)+'</b> · '+
      'conf_floor=<b>'+fmt(p.conf_floor)+'</b> · '+
      'max_pos_pct=<b>'+fmt(p.max_pos_pct)+'</b> · '+
      'max_total_pct=<b>'+fmt(p.max_total_pct)+'</b></div>';
  }

  function renderCombos(payload){
    var area = document.getElementById("combo_area");
    var combos = payload.combos || [];
    if(!combos.length){
      area.innerHTML = '<div class="note">无组合数据（total='+esc(payload.total)+'）。</div>';
      return;
    }
    var cols = ["id","name","pick_source","persona","persona_risk_params","watch_style","trader","影子期","红线 _wo"];
    var head = "<tr>"+cols.map(function(c){return "<th>"+esc(c)+"</th>";}).join("")+"</tr>";
    var body = combos.map(function(c){
      var q = c.quad||{};
      var shadow = c.is_shadow_period
        ? '<span class="shadow-tag">影子期</span>'
        : '<span class="normal-tag">常规</span>';
      var wo = c._wo
        ? '<div class="wo">'+esc(c._wo)+'</div>'
        : '<div class="wo-empty">—（无红线）</div>';
      return "<tr>"+
        "<td>"+fmt(c.id)+"</td>"+
        "<td>"+fmt(c.name)+"</td>"+
        "<td>"+slotTT(q.pick_source)+"</td>"+
        "<td>"+slotTT(q.persona)+"</td>"+
        "<td>"+paramsText(c.persona_risk_params)+"</td>"+
        "<td>"+slotTT(q.watch_style)+"</td>"+
        "<td>"+slotTT(q.trader)+"</td>"+
        "<td>"+shadow+"</td>"+
        "<td>"+wo+"</td>"+
      "</tr>";
    }).join("");
    area.innerHTML = "<table><thead>"+head+"</thead><tbody>"+body+"</tbody></table>";
  }

  function renderRedlines(payload){
    var area = document.getElementById("redline_area");
    var rows = payload.redline_summary || [];
    if(!rows.length){
      area.innerHTML = '<div class="note">无红线项（当前注册表未标注红线）。</div>';
      return;
    }
    var cols = ["combo_id","name","影子期","锁定执行动作","红线原文"];
    var head = "<tr>"+cols.map(function(c){return "<th>"+esc(c)+"</th>";}).join("")+"</tr>";
    var body = rows.map(function(r){
      var shadow = r.is_shadow_period
        ? '<span class="shadow-tag">影子期</span>'
        : '<span class="normal-tag">否</span>';
      var locks = (r.locked_actions&&r.locked_actions.length)
        ? r.locked_actions.map(function(a){return '<span class="rl-tag">'+esc(a)+'</span>';}).join(" ")
        : "—";
      return "<tr>"+
        "<td>"+fmt(r.combo_id)+"</td>"+
        "<td>"+fmt(r.name)+"</td>"+
        "<td>"+shadow+"</td>"+
        "<td>"+locks+"</td>"+
        "<td><div class=\"wo\">"+esc(r.redline_original)+"</div></td>"+
      "</tr>";
    }).join("");
    area.innerHTML = "<table><thead>"+head+"</thead><tbody>"+body+"</tbody></table>";
  }

  function render(payload){
    document.getElementById("combo_area").innerHTML = "";
    document.getElementById("redline_area").innerHTML = "";
    renderCombos(payload);
    renderRedlines(payload);

    var s = "verified="+(payload.verified?"true":"false")+
            " · realm="+esc(payload.realm||"simulation_paper")+
            " · total="+esc(payload.total)+
            " · trading_permitted=false";
    if(payload.error_code) s += " · error_code="+esc(payload.error_code);
    if(payload.is_shadow_period_any) s += " · 含影子期组合";
    if(payload.note) s += " · "+esc(payload.note);
    document.getElementById("stamp").textContent = s;
  }

  function showFail(msg){
    var fail = '<div class="demo">快照获取失败：'+esc(msg)+'（只读服务是否在 127.0.0.1:8796 运行？API=/api/watch-admin/combos）</div>';
    document.getElementById("combo_area").innerHTML = fail;
    document.getElementById("redline_area").innerHTML = "";
    document.getElementById("stamp").textContent = "快照获取失败："+esc(msg)+" · SOURCE_UNVERIFIED";
  }

  function load(){
    document.getElementById("combo_area").innerHTML = '<div class="note">加载中…</div>';
    document.getElementById("redline_area").innerHTML = '<div class="note">加载中…</div>';
    fetch(API)
      .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
      .then(function(payload){
        // 诚实展示：API 返回但未接真源（verified=false）时仍显示失败，绝不假绿
        if(!payload || payload.verified === false){
          var code = payload && payload.error_code ? ("error_code="+esc(payload.error_code)) : "未接真源";
          document.getElementById("combo_area").innerHTML =
            '<div class="demo">组合注册表不可用（'+code+'）· 未编造任何组合或红线</div>';
          document.getElementById("redline_area").innerHTML = "";
          document.getElementById("stamp").textContent =
            "SOURCE_UNVERIFIED："+code+" · trading_permitted=false";
          return;
        }
        render(payload);
      })
      .catch(function(err){ showFail(err.message || err); });
  }

  document.getElementById("reload").addEventListener("click", load);
  document.getElementById("sig").textContent =
    "组合器静态配置下钻 · 来源 fail-visible · 未接真源一律报错不编造 · 绝不接执行链";
  load();
})();
</script>
</body>
</html>"""


HTML_ROUTES: Dict[str, Any] = {
    "/watch-admin/combos": COMBOS_HTML,
}


# --------------------------------------------------------------------------- #
# 自检（仅直接执行时；主服务 loader 不调用此分支）
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import pprint

    print("== combos_api({}) ==")
    result = combos_api({})
    pprint.pprint(result)
    print("\n== summary ==")
    print("verified:", result["verified"], "| total:", result["total"],
          "| redline_summary 条数:", len(result["redline_summary"]))
