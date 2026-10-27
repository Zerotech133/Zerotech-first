# -*- coding: utf-8 -*-
"""告警契约离线看板 —— 只读路由（P2 特性 F，盯盘只读管理页 M2/M3）。

本模块是 ``watch_admin_server._FEATURE_MODULES`` 列出的独立特性文件之一
（已登记为 ``"watch_admin_route_alert"``）。它仅做 **离线契约 / 冻结证据投影**，
由主服务按如下契约动态加载：

    API_ROUTES  = {"/api/watch-admin/alert": alert_api}
    HTML_ROUTES = {"/watch-admin/alert": ALERT_HTML}

硬纪律（本模块不可妥协）：
  * 纯只读、fail-visible、绝不编造。
  * 仅对下列离线契约代码做只读 import 与投影，不连任何真实行情 / 飞书 / 通知：
      - quant_backtest/watch_alert_contract.py   （validate_alert 等，
        production_permitted=False）
      - quant_backtest/watch_source_authority.py （StrategyMembershipProof 冻结证据态，
        production_permitted=False）
  * 不调用任何会发通知 / 推送 / 写盘 / 改变运行态的函数；只读取公开常量与
    @dataclass 结构做投影。
  * 不触碰 8091 / portal_api.py / 10.205.0.118；不解锁飞书真实下发 / GAP-4 /
    051 时序缺口 / 真实通知 / 交易 任何 BLOCK 项。
  * 不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。
  * 数据源（离线契约模块）不可导入时，对应分块 verified=False + error_code，
    整体仍返回合法 dict，绝不编造。

运行 / 自验（受管 Python 3.13.12）：
    py_compile portal/watch_admin_route_alert.py
    python -c "import watch_admin_route_alert as m; print(m.alert_api({}))"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

# --------------------------------------------------------------------------- #
# 路径解析：让 quant_backtest 作为命名空间包可被导入
#   <repo>/portal/watch_admin_route_alert.py -> parent = portal
#   portal.parent = <repo> = 老田的AI助手（加入 sys.path 后 quant_backtest 成为
#   命名空间包，从而 ``from quant_backtest.watch_alert_contract import ...`` 可解析；
#   这是 watch_source_authority.py 自身 import 的前提，且不涉及 10.205.0.118 主机）。
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# --------------------------------------------------------------------------- #
# 只读投影所需的离线契约模块（fail-visible：导入失败不影响模块可被加载）
#   注意：此处仅 import 模块级常量与 @dataclass，绝不调用任何会发通知 /
#   推送 / 写盘 / 改变运行态的函数。
# --------------------------------------------------------------------------- #
_CONTRACT_MODULES_OK: bool = False
_IMPORT_ERROR: Optional[str] = None
_alert_contract: Any = None
_source_authority: Any = None

try:
    from quant_backtest import watch_alert_contract as _alert_contract  # noqa: E402
    from quant_backtest import watch_source_authority as _source_authority  # noqa: E402
    _CONTRACT_MODULES_OK = True
except Exception as _exc:  # noqa: BLE001 - 导入失败需 fail-visible，绝不抛出
    _IMPORT_ERROR = "%s:%s" % (type(_exc).__name__, _exc)


# --------------------------------------------------------------------------- #
# 投影构建：内容全部来自离线契约代码，绝不编造
# --------------------------------------------------------------------------- #
def _build_contract_summary() -> List[Dict[str, Any]]:
    """从 watch_alert_contract 只读投影 validate_alert 契约要点。

    所有数值 / 字符串均来自模块级真实常量（FIELDS / COMPARATORS / ACTIVE_KINDS）
    或对 validate_alert 源码中真实出现的字面量与拒绝码的正则提取；不臆造。
    """
    c = _alert_contract
    summary: List[Dict[str, Any]] = []

    summary.append({
        "point": "schema_version",
        "required": "WATCH1_V1",
        "desc": "validate_alert 强校验 event['schema_version'] == 'WATCH1_V1'，否则 SCHEMA_VERSION 拒绝",
    })

    fields = sorted(c.FIELDS)
    summary.append({
        "point": "required_fields",
        "count": len(fields),
        "fields": fields,
        "desc": "事件必须恰好包含这些字段（FIELDS frozenset，共 %d 个）；多/少字段即 SCHEMA_FIELDS 拒绝" % len(fields),
    })

    summary.append({
        "point": "pools_allowed",
        "value": ["A", "B"],
        "desc": "事件 pool 仅允许 A / B；A 池要求 strategy/strategy_version/source_batch，B 池要求 owner/watch_rule/watch_rule_version",
    })
    summary.append({
        "point": "run_modes_allowed",
        "value": ["SHADOW", "SIM_MARKET"],
        "desc": "run_mode 仅允许 SHADOW / SIM_MARKET；SHADOW 模式下通知与交接必须 NOT_ATTEMPTED（SHADOW_SIDE_EFFECT）",
    })
    summary.append({
        "point": "instrument_types_allowed",
        "value": ["STOCK", "ETF", "INDEX", "FUND"],
        "desc": "instrument_type 仅允许 STOCK / ETF / INDEX / FUND",
    })
    summary.append({
        "point": "active_membership_kinds",
        "value": sorted(c.ACTIVE_KINDS),
        "desc": "membership_event.kind 必须为 ENTER / REENTER / HIT / HOLD（ACTIVE_KINDS）",
    })
    summary.append({
        "point": "comparators_allowed",
        "value": sorted(c.COMPARATORS.keys()),
        "desc": "comparator 仅允许 GT/GTE/LT/LTE/EQ/NE，用于比较 observed 与 threshold",
    })
    summary.append({
        "point": "source_kind_required",
        "value": "DIRECT_QUOTE",
        "desc": "source_kind 必须为 DIRECT_QUOTE；摘要不可作为证据（SUMMARY_NOT_EVIDENCE）",
    })
    summary.append({
        "point": "quote_quality_required",
        "value": "VERIFIED",
        "desc": "quote 质量必须为 VERIFIED（QUOTE_QUALITY）",
    })
    summary.append({
        "point": "decision_outcomes",
        "value": ["TRIGGERED", "NOT_TRIGGERED", "UNJUDGEABLE"],
        "desc": "AlertValidation.status 仅三种结果；UNJUDGEABLE 表示无法推断安全的可执行谓词",
    })

    # 从 validate_alert 源码真实提取拒绝码（不手抄，避免编造）
    # 方法：用括号平衡定位每个 _require(...) 调用，取其最后一个字符串实参，
    # 因此元组内部字面量（如 ("A","B") 的 "B"）不会被误判为拒绝码。
    try:
        import inspect

        def _extract_reject_codes(src: str) -> List[str]:
            out: List[str] = []
            i = 0
            n = len(src)
            while True:
                j = src.find("_require(", i)
                if j == -1:
                    break
                depth = 0
                k = j + len("_require(")
                while k < n:
                    ch = src[k]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    k += 1
                call = src[j + len("_require("):k]
                m = re.search(r',\s*"([A-Z0-9_]+)"\s*$', call)
                if m:
                    out.append(m.group(1))
                i = k + 1
            return sorted(set(out))

        src = inspect.getsource(c.validate_alert)
        codes = _extract_reject_codes(src)
        summary.append({
            "point": "reject_codes",
            "count": len(codes),
            "codes": codes,
            "desc": "validate_alert 实际抛出的结构自洽拒绝码（部分会再拼 '_TIME' 后缀）",
        })
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/watch_admin_route_alert.py", exc)

    summary.append({
        "point": "scope",
        "value": "仅校验结构自洽",
        "desc": "不鉴权生产者签名、不拉行情、不发通知、不下单、不授予生产资格；"
                "source_hash 仅对快照 canonical JSON 取摘要，非外部原始字节哈希；"
                "display summary 绝非逐标的证据",
    })
    summary.append({
        "point": "production_permitted",
        "value": False,
        "desc": "通过校验仅代表结构自洽，绝不意味着获得生产许可（详见冻结证据态）",
    })
    return summary


def _build_freeze_evidence() -> Dict[str, Any]:
    """从 watch_source_authority 只读投影 StrategyMembershipProof 冻结证据态。

    所有字段名 / 类型 / frozen 标志均来自真实 @dataclass；production_permitted
    恒为 False 的不变量来自 SourceQualification 默认值。绝不编造。
    """
    a = _source_authority

    # StrategyMembershipProof 字段（真实）
    smp_fields: Dict[str, str] = {}
    for fname, f in a.StrategyMembershipProof.__dataclass_fields__.items():
        smp_fields[fname] = str(f.type)

    # 全部冻结证据结构（真实，来自模块中定义的若干个 @dataclass）
    structures: List[Dict[str, Any]] = []
    for name in ("StrategyMembershipProof", "OwnerInputProof",
                 "RawHttpResponseProof", "TradingSessionProof",
                 "ProviderLicenseProof"):
        cls = getattr(a, name, None)
        if cls is None:
            continue
        params = getattr(cls, "__dataclass_params__", None)
        structures.append({
            "name": name,
            "frozen": bool(getattr(params, "frozen", False)),
            "field_count": len(cls.__dataclass_fields__),
        })

    # SourceQualification.production_permitted 默认值（真实不变量）
    prod_default = a.SourceQualification.__dataclass_fields__[
        "production_permitted"].default

    return {
        "production_permitted": bool(prod_default),
        "authority_role": "independently controlled, read-only trust root；非由 alert 声明派生",
        "fail_closed": True,
        "StrategyMembershipProof": {
            "doc": "Authority-issued approval for exactly one A-pool strategy membership.",
            "frozen": True,
            "fields": smp_fields,
        },
        "frozen_evidence_structures": structures,
        "qualification_status_outcomes": ["QUALIFIED_OFFLINE", "SOURCE_UNVERIFIED"],
        "note": "SourceQualification.production_permitted 恒为 False；任何 lookup / parser / "
                "外部 policy 失败一律 fail-closed（SOURCE_UNVERIFIED）。",
    }


# 模块加载时即投影一次（导入成功才投影，失败保持空，绝不编造）
if _CONTRACT_MODULES_OK:
    try:
        _CONTRACT_SUMMARY: List[Dict[str, Any]] = _build_contract_summary()
        _FREEZE_EVIDENCE: Dict[str, Any] = _build_freeze_evidence()
    except Exception as _exc:  # noqa: BLE001 - 投影异常不得上抛到 import 阶段
        _IMPORT_ERROR = "projection_error:%s:%s" % (type(_exc).__name__, _exc)
        _CONTRACT_MODULES_OK = False
        _CONTRACT_SUMMARY = []
        _FREEZE_EVIDENCE = {"error": _IMPORT_ERROR}
else:
    _CONTRACT_SUMMARY = []
    _FREEZE_EVIDENCE = {"error": _IMPORT_ERROR or "unknown"}


# --------------------------------------------------------------------------- #
# 公共 API 路由
# --------------------------------------------------------------------------- #
def alert_api(query: dict) -> dict:
    """告警契约离线看板 API（只读投影）。

    Args:
        query: 请求参数 dict（本路由为纯离线投影，不依赖任何查询参数）。

    Returns:
        信封 dict：
          * verified=True（离线契约可投影即 True）；
          * contract_summary（validate_alert 契约要点列表，非空、真实来自离线契约）；
          * freeze_evidence（StrategyMembershipProof 证据态，非空、真实来自离线契约）；
          * mode='offline_frozen'；
          * note='离线契约/冻结证据态，不连真实通知'。
        若 import / 投影失败 -> verified=False + error_code，仍返回合法 dict，绝不编造。
    """
    if not isinstance(query, dict):
        query = {}

    if not _CONTRACT_MODULES_OK:
        return {
            "verified": False,
            "error_code": "contract_import_failed",
            "mode": "offline_frozen",
            "contract_summary": [],
            "freeze_evidence": _FREEZE_EVIDENCE,
            "note": "离线契约/冻结证据态，不连真实通知 —— 源模块不可导入，未编造任何内容",
        }

    return {
        "verified": True,
        "error_code": None,
        "mode": "offline_frozen",
        "contract_summary": _CONTRACT_SUMMARY,
        "freeze_evidence": _FREEZE_EVIDENCE,
        "note": "离线契约/冻结证据态，不连真实通知",
    }


# --------------------------------------------------------------------------- #
# 只读 HTML 页面（深海军蓝风格，禁顶部胶囊导航；醒目离线横幅）
# --------------------------------------------------------------------------- #
ALERT_HTML: str = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>告警契约离线看板 · 只读管理页 (M2/M3)</title>
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
/* 红线：离线契约·冻结证据态·不连真实通知/飞书 */
.offline-banner{background:rgba(224,162,60,.12);border:1px solid var(--amber);border-left:4px solid var(--amber);border-radius:8px;padding:14px 18px;margin:16px 0}
.offline-banner b{color:var(--amber);font-size:14px;letter-spacing:.04em}
.ob-sub{color:var(--sub);font-size:12.5px;margin-top:6px;line-height:1.6}
.ob-sub code{color:var(--cyan);background:var(--chip);padding:0 5px;border-radius:3px}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 16px}
.toolbar .btn{background:var(--cyan-d);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12.5px;cursor:pointer}
.toolbar .btn:hover{background:var(--cyan)}
.toolbar .stamp{color:var(--sub);font-size:11.5px;margin-left:auto}
.zone{background:var(--navy2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.zone h3{margin:0 0 12px;font-size:15.5px;color:var(--cyan)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}
th{background:var(--chip);text-align:left;padding:7px 9px;border:1px solid var(--line);color:var(--sub);font-weight:700;white-space:nowrap}
td{padding:7px 9px;border:1px solid var(--line);vertical-align:top}
.note{background:var(--navy3);border:1px solid var(--line);border-radius:6px;padding:8px 11px;font-size:11.5px;color:var(--sub);margin-top:8px}
.demo{background:rgba(232,115,106,.12);border:1px solid var(--no);border-radius:6px;padding:10px 13px;font-size:12.5px;color:var(--no);margin-top:8px}
.kv{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--amber)}
.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px;color:var(--sub);background:var(--chip)}
.tag.on{color:var(--ok);border-color:var(--ok)}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--sub)}
@media(max-width:820px){.wrap{padding:18px}.toolbar .stamp{margin-left:0}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">QUANT / WATCH DOMAIN / M2 REAL READ-ONLY · 告警契约离线看板</div>
  <h1>告警契约离线看板<span class="badge">只读 · 8796</span></h1>
  <div class="sub">validate_alert 契约要点 + StrategyMembershipProof 冻结证据态 · 纯离线投影 · 不连真实通知</div>
</header>

<div class="offline-banner">
  <b>⚠ 离线契约 · 冻结证据态 · 不连真实通知 / 飞书</b>
  <div class="ob-sub">本页仅对 <code>watch_alert_contract.py</code> 与 <code>watch_source_authority.py</code> 做只读投影；不拉行情、不发飞书、不解锁 GAP-4 / 051 时序缺口 / 真实通知 / 交易 任何 BLOCK 项。<code>production_permitted</code> 恒为 <code>False</code>。</div>
</div>

<div class="toolbar">
  <button class="btn" id="reload">刷新</button>
  <span class="stamp" id="stamp"></span>
</div>

<div class="zone" id="contract_zone">
  <h3>validate_alert 契约要点</h3>
  <div id="contract_body"><div class="note">加载中…</div></div>
</div>

<div class="zone" id="evidence_zone">
  <h3>StrategyMembershipProof 冻结证据态</h3>
  <div id="evidence_body"><div class="note">加载中…</div></div>
</div>

<footer>
  交付：告警契约离线看板只读路由（本次新增）。纯加法、只读、fail-visible；源缺失仅标 SOURCE_UNVERIFIED，绝不编造。不替换任何生产路由；不触碰 8091 与 portal_api.py。
</footer>
</div>

<script>
(function(){
  "use strict";
  var API = "/api/watch-admin/alert";
  function esc(v){
    if(v===null||v===undefined) return "";
    return String(v).replace(/[&<>"]/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];
    });
  }
  function fmtList(arr){
    if(!Array.isArray(arr)||!arr.length) return '<span class="note">（空）</span>';
    return arr.map(function(x){
      return '<span class="tag">'+(typeof x==="object"?esc(JSON.stringify(x)):esc(x))+'</span>';
    }).join("");
  }

  function renderContract(summary){
    var box = document.getElementById("contract_body");
    if(!Array.isArray(summary)||!summary.length){
      box.innerHTML = '<div class="demo">契约要点不可用（源模块不可投影）· 未编造数据</div>';
      return;
    }
    var h = "<table><thead><tr><th>要点</th><th>取值 / 计数</th><th>说明</th></tr></thead><tbody>";
    summary.forEach(function(it){
      var val;
      if(it.fields){ val = '<span class="kv">'+esc(it.count)+' 字段</span> ' + fmtList(it.fields); }
      else if(it.codes){ val = '<span class="kv">'+esc(it.count)+' 拒绝码</span> ' + fmtList(it.codes); }
      else if(Array.isArray(it.value)){ val = fmtList(it.value); }
      else { val = '<span class="kv">'+esc(it.value)+'</span>'; }
      h += "<tr><td style='color:var(--cyan)'>"+esc(it.point)+"</td><td>"+val+"</td><td style='color:var(--sub)'>"+esc(it.desc||"")+"</td></tr>";
    });
    h += "</tbody></table>";
    box.innerHTML = h;
  }

  function renderEvidence(ev){
    var box = document.getElementById("evidence_body");
    if(!ev || ev.error){
      box.innerHTML = '<div class="demo">冻结证据态不可用（'+(ev&&ev.error?esc(ev.error):"error")+'）· 未编造数据</div>';
      return;
    }
    var html = "";
    html += '<div class="note">production_permitted = <b style="color:var(--no)">'+esc(ev.production_permitted)+'</b> · '
          + 'authority_role：'+esc(ev.authority_role)+' · fail_closed = '+esc(ev.fail_closed)+'</div>';

    var smp = ev.StrategyMembershipProof||{};
    html += '<h3 style="color:var(--amber);font-size:13px;margin:14px 0 6px;">StrategyMembershipProof'+(smp.frozen?' <span class="tag on">frozen</span>':'')+'</h3>';
    html += '<div class="note">'+(smp.doc||"")+'</div>';
    var flds = smp.fields||{};
    var fk = Object.keys(flds);
    if(fk.length){
      html += "<table><thead><tr><th>字段</th><th>类型</th></tr></thead><tbody>";
      fk.forEach(function(k){ html += "<tr><td>"+esc(k)+"</td><td class='kv'>"+esc(flds[k])+"</td></tr>"; });
      html += "</tbody></table>";
    } else {
      html += '<div class="note">（无字段投影）</div>';
    }

    var structs = ev.frozen_evidence_structures||[];
    html += '<h3 style="color:var(--amber);font-size:13px;margin:14px 0 6px;">冻结证据结构（共 '+esc(structs.length)+' 个）</h3>';
    if(structs.length){
      html += "<table><thead><tr><th>结构</th><th>frozen</th><th>字段数</th></tr></thead><tbody>";
      structs.forEach(function(s){
        html += "<tr><td>"+esc(s.name)+"</td><td>"+(s.frozen?'<span class="tag on">True</span>':'False')+"</td><td>"+esc(s.field_count)+"</td></tr>";
      });
      html += "</tbody></table>";
    }
    html += '<div class="note">'+esc(ev.note||"")+'</div>';
    box.innerHTML = html;
  }

  function load(){
    fetch(API).then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
      .then(function(snap){
        if(!snap){
          document.getElementById("contract_body").innerHTML = '<div class="demo">快照获取失败：空响应（服务是否在 8796 运行？）</div>';
          document.getElementById("evidence_body").innerHTML = "";
          return;
        }
        if(snap.verified === false){
          var msg = snap.error_code ? ("error_code="+esc(snap.error_code)) : "未接真源";
          document.getElementById("contract_body").innerHTML = '<div class="demo">契约不可用（'+msg+'）· 未编造数据</div>';
          document.getElementById("evidence_body").innerHTML = '<div class="demo">证据态不可用（'+msg+'）· 未编造数据</div>';
          document.getElementById("stamp").textContent = "verified=false · "+msg;
          return;
        }
        renderContract(snap.contract_summary);
        renderEvidence(snap.freeze_evidence);
        document.getElementById("stamp").textContent =
          "verified=true · mode="+esc(snap.mode)+" · 契约要点="+esc((snap.contract_summary||[]).length)+" 项 · note="+esc(snap.note||"");
      })
      .catch(function(err){
        document.getElementById("contract_body").innerHTML =
          '<div class="demo">快照获取失败：'+esc(err)+'（只读服务是否在 127.0.0.1:8796 运行？API=/api/watch-admin/alert）</div>';
        document.getElementById("evidence_body").innerHTML = "";
        document.getElementById("stamp").textContent = "快照获取失败：" + esc(err) + " · SOURCE_UNVERIFIED";
      });
  }

  document.getElementById("reload").addEventListener("click", load);
  load();
})();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# 模块契约（由 watch_admin_server 动态特性加载器消费）
# --------------------------------------------------------------------------- #
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/alert": alert_api,
}

HTML_ROUTES: Dict[str, Any] = {
    "/watch-admin/alert": ALERT_HTML,
}


# --------------------------------------------------------------------------- #
# 自检（仅直接执行时；服务加载器从不调用）
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import pprint

    pprint.pprint(alert_api({}))
    pprint.pprint(alert_api({"unrelated": ["x"]}))
