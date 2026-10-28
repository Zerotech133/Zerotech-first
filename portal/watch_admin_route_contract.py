# -*- coding: utf-8 -*-
"""盯盘只读管理页 —— 席位/模型契约一致性看板（只读路由，P2 特性 E）。

硬纪律（纯只读、fail-visible、绝不编造）：
  * 绝不写入任何文件、绝不发起除本机 8098 只读探活外的任何网络请求、绝不调用
    8091 / portal_api.py / 10.205.0.118。
  * 不触碰任何 BLOCK 项（飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易）。
  * 不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。
  * fail-visible：任一数据源（文件或 8098）不可读时，对应分块 verified=False +
    error_code，整体仍返回合法 dict，绝不编造缺失数据。
  * 8098 model-admin（portal/api_model_admin.py 暴露的静态配置）：仅当本机 8098
    服务在线时只读真实静态态；否则该部分标 UNVERIFIED（与 Zone2 一致，by design，
    非缺陷），其余文件源仍 verified。error_code 固定为 "http_error"。
  * 输出不得含 NaN / Inf：非有限浮点规整为 None 后再返回。

本模块由主服务（watch_admin_server.py）按如下契约动态加载：
  * API_ROUTES  = {"/api/watch-admin/contract": contract_api}
  * HTML_ROUTES = {"/watch-admin/contract": CONTRACT_HTML}
其中 contract_api(query: dict) -> dict 返回信封，含：
  * seats         已注册席位列表与角色（org.json + team_invest.json 控辩审）
  * bindings      各席位实际 llm_binding vs M0 默认对照，命中/偏离标注
  * redline_items 红线项（本只读契约永不直接触碰的 BLOCK 项）汇总
  * model_tiers   档位快照（model_tiers.json）
  * model_admin   8098 静态配置（http 源；不可达时 verified=False / http_error）
  * source_mode   区分 file / http
"""

from __future__ import annotations

import json
import math
import os
import urllib.error  # noqa: F401
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 路径解析（相对本模块文件位置，避免依赖 cwd；并保留绝对兜底路径）
# --------------------------------------------------------------------------- #
_MODULE_DIR: str = os.path.dirname(os.path.abspath(__file__))
# portal 目录的父目录 = 项目根（agent_platform 在其下）
_STATION_ROOT: str = os.path.normpath(os.path.join(_MODULE_DIR, ".."))
_REGISTRY_DIR: str = os.path.normpath(os.path.join(_STATION_ROOT, "agent_platform", "registry"))

ORG_PATH: str = os.path.join(_REGISTRY_DIR, "org.json")
TEAM_INVEST_PATH: str = os.path.join(_REGISTRY_DIR, "team_invest.json")
MODEL_TIERS_PATH: str = os.path.join(_REGISTRY_DIR, "model_tiers.json")

# 8098 model-admin（静态配置只读端点，无鉴权；仅本机）
MODEL_ADMIN_HOST: str = os.environ.get("MODEL_ADMIN_HOST", "127.0.0.1")
MODEL_ADMIN_PORT: int = int(os.environ.get("MODEL_ADMIN_PORT", "8098"))
_HTTP_TIMEOUT: float = 3.0

# --------------------------------------------------------------------------- #
# 维度常量（按契约）
# --------------------------------------------------------------------------- #
# M0 默认绑定（契约冻结口径，与 watch_admin_snapshot.zone2 对齐）。
# 仅这些席位存在 M0 默认对照；其余席位按 org 实际绑定，无 M0 默认。
# 2026-10-23 P2：移除 QS1 行——WO-LEGION-S43 已显式裁定 QS1 llm_binding=none
# （无模型绑定）。保留旧行使对照区永久显示 deviation 噪声；也不得把 default 改成
# "none" 伪装命中（那会把「显式无绑定」洗成「与默认一致」，属静默造数）。
M0_DEFAULT_BINDING: Dict[str, Dict[str, str]] = {
    "WA1": {"default": "ollama/qwen3.5:9b", "fallback": "云 glm-5.3 强判读"},
    "张郃": {"default": "ollama/qwen3.6:35b-a3b", "fallback": "本地 9b"},
    "ST1": {"default": "glm/glm-5.3", "fallback": "—"},
}

# 红线项（本只读契约永不直接触碰的 BLOCK 项）。这些不是编造的市场数据，而是本模块
# 硬纪律所恪守的系统红线，原样在一致性看板上汇总公示，供操作员明确边界。
REDLINE_ITEMS: List[Dict[str, str]] = [
    {
        "id": "feishu_real_dispatch",
        "label": "飞书真实下发",
        "policy": "BLOCK · 红线",
        "source": "本模块只读契约（硬纪律）",
        "note": "只读看板，绝不触达飞书真实下发通道。",
    },
    {
        "id": "gap4",
        "label": "GAP-4",
        "policy": "BLOCK · 红线",
        "source": "本模块只读契约（硬纪律）",
        "note": "GAP-4 缺口项为冻结项，看板不尝试补齐或改写。",
    },
    {
        "id": "t051_sequence_gap",
        "label": "051 时序缺口",
        "policy": "BLOCK · 红线",
        "source": "本模块只读契约（硬纪律）",
        "note": "051 时序缺口为冻结项，看板仅静态呈现，不重算。",
    },
    {
        "id": "real_notify",
        "label": "真实通知",
        "policy": "BLOCK · 红线",
        "source": "本模块只读契约（硬纪律）",
        "note": "看板不发送任何真实通知。",
    },
    {
        "id": "trading",
        "label": "交易",
        "policy": "BLOCK · 红线",
        "source": "本模块只读契约（硬纪律）",
        "note": "看板纯只读，绝不连接任何真实或模拟下单通道。",
    },
]


# --------------------------------------------------------------------------- #
# 基础工具：容错 JSON 读取 + 非有限浮点规整 + 受限 HTTP GET
# --------------------------------------------------------------------------- #
def _read_json_file(path: str) -> Tuple[Any, Optional[str]]:
    """读取 JSON 文件，对可能存在的 NaN/Infinity 做容错处理。

    Returns:
        (value, error_code)：成功 -> (value, None)；失败 -> (None, error_code)。
        error_code ∈ {missing, invalid_json, read_error}。
    """
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw: str = fh.read()
        # 源文件偶发写入非标准 JSON 字面量 NaN/Infinity，标准 json.loads 会失败，
        # 这里在解析前替换为 null，保证读取健壮（fail-visible 而非崩溃）。
        sanitized: str = (
            raw.replace("NaN", "null")
            .replace("Infinity", "null")
            .replace("-Infinity", "null")
        )
        return json.loads(sanitized), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid_json"
    except OSError:
        return None, "read_error"


def _http_get_json(url: str) -> Tuple[Any, Optional[str]]:
    """只读 GET 返回解析后的 JSON。有界超时；永不抛异常。

    8098 不可达（连接失败 / 超时 / HTTP 非 200 / JSON 解析失败）统一回传
    (None, "http_error")，供调用方标 UNVERIFIED。
    """
    try:
        req = urllib.request.Request(url, headers={})
        # 避免代理把本机调用路由到外网（绝不触达 10.205.0.118）。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=_HTTP_TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                return None, "http_error"
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw), None
    except urllib.error.HTTPError:
        return None, "http_error"
    except Exception:  # socket.timeout / URLError / JSONDecodeError / etc.
        return None, "http_error"


def _sanitize(obj: Any) -> Any:
    """递归规整：将 dict/list 中的非有限浮点（NaN/Inf）替换为 None。"""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# 源记录构造（与 watch_admin_snapshot._source 字段对齐，便于 UI 统一渲染）
# --------------------------------------------------------------------------- #
def _source_record(
    name: str,
    *,
    state: str,
    source_mode: str,
    error_code: Optional[str],
    note: str,
    data: Any,
    source: str,
    verified: bool,
) -> Dict[str, Any]:
    """构造单个数据源记录。

    ``verified`` 区分真实可观测源与未能抵达的源；未验证源显式标
    ``SOURCE_UNVERIFIED``，UI 与 QA 断言可据此无歧义识别"缺源"。
    """
    return {
        "name": name,
        "state": state,                 # healthy | unknown
        "source_mode": source_mode,     # file | http | computed
        "error_code": error_code,       # None | missing | invalid_json | read_error | http_error
        "note": note,
        "data": data,
        "source": source,
        "verified": verified,
        "source_quality": "verified" if verified else "SOURCE_UNVERIFIED",
    }


# --------------------------------------------------------------------------- #
# 分块 (a)：已注册席位列表与角色（org.json + team_invest 控辩审）
# --------------------------------------------------------------------------- #
def _build_seats() -> Dict[str, Any]:
    """聚合已注册席位与角色。

    数据来源（均只读）：
      * org.json：chief + 各 dept.agent_profiles（agent_id / role_profile /
        grade / llm_binding / dept）
      * team_invest.json：alpha 团队控辩审拓扑（bull/bear/evaluator 角色）
    """
    org, oerr = _read_json_file(ORG_PATH)
    team, terr = _read_json_file(TEAM_INVEST_PATH)

    seats: Dict[str, Dict[str, Any]] = {}

    # ---- org.json 主源 ----
    if isinstance(org, dict):
        chief = org.get("chief") or {}
        if chief.get("agent_id"):
            seats[chief["agent_id"]] = {
                "agent_id": chief.get("agent_id"),
                "role_profile": chief.get("role_profile"),
                "role": None,
                "dept": "chief",
                "grade": None,
                "llm_binding": chief.get("llm_binding"),
                "name": chief.get("name"),
                "source": "org",
            }
        for dept in org.get("departments", []) or []:
            dkey = dept.get("dept", "")
            for prof in dept.get("agent_profiles", []) or []:
                aid = prof.get("agent_id")
                if not aid:
                    continue
                seats[aid] = {
                    "agent_id": aid,
                    "role_profile": prof.get("role_profile"),
                    "role": None,
                    "dept": dkey,
                    "grade": prof.get("grade"),
                    "llm_binding": prof.get("llm_binding"),
                    "name": (prof.get("persona") or {}).get("name")
                    if isinstance(prof.get("persona"), dict) else None,
                    "source": "org",
                }

    # ---- team_invest.json 控辩审拓扑（补充 role 维度，去重合并） ----
    ctrl_roles: List[Dict[str, Any]] = []
    if isinstance(team, dict):
        for a in team.get("agents", []) or []:
            rp = a.get("role_profile", "")
            role = a.get("role")
            if role in ("bull", "bear", "evaluator") or rp in (
                "t1_bull", "t2_bear", "d1_decision"
            ):
                aid = a.get("agent_id")
                ctrl_roles.append({
                    "agent_id": aid,
                    "role": role,
                    "role_profile": rp,
                    "llm_binding": a.get("llm_binding"),
                    "team": team.get("team"),
                    "dept": team.get("dept"),
                })
                if aid and aid in seats:
                    # 已注册席位补注控辩审角色
                    seats[aid]["role"] = role or seats[aid].get("role")
                elif aid:
                    # 仅出现在 team_invest 的席位也纳入（如 D1）
                    seats[aid] = {
                        "agent_id": aid,
                        "role_profile": rp,
                        "role": role,
                        "dept": team.get("dept"),
                        "grade": None,
                        "llm_binding": a.get("llm_binding"),
                        "name": None,
                        "source": "team_invest",
                    }

    seat_list: List[Dict[str, Any]] = [s for s in seats.values() if s.get("agent_id")]
    seat_list.sort(key=lambda s: str(s.get("agent_id")))

    verified: bool = isinstance(org, dict) or isinstance(team, dict)
    notes: List[str] = []
    if isinstance(org, dict):
        notes.append("org.json 席位/角色（chief + 各部门 agent_profiles）")
    else:
        notes.append("org.json 缺失/不可读 → %s" % (oerr or "unverified"))
    if isinstance(team, dict):
        notes.append("team_invest.json 控辩审拓扑（bull/bear/evaluator）")
    else:
        notes.append("team_invest.json 缺失/不可读 → %s" % (terr or "unverified"))

    return _source_record(
        "seats",
        state="healthy" if verified else "unknown",
        source_mode="file",
        error_code=(oerr or terr),
        note="；".join(notes) + "（席位注册对照，只读源）",
        data={
            "count": len(seat_list),
            "seats": seat_list,
            "control_debate_review": ctrl_roles,
        },
        source="%s | %s" % (ORG_PATH, TEAM_INVEST_PATH),
        verified=verified,
    )


# --------------------------------------------------------------------------- #
# 分块 (b)：各席位实际 llm_binding vs M0 默认对照（命中/偏离标注）
# --------------------------------------------------------------------------- #
def _build_bindings() -> Dict[str, Any]:
    """对照各席位实际 llm_binding 与 M0 默认绑定，标注命中/偏离。

    规则：
      * 存在 M0 默认：actual == default -> 命中(hit)；否则 偏离(deviation)。
      * 无 M0 默认：按 org 实际绑定呈现，标 no_m0_ref（不编造默认）。
    任何席位实际绑定缺失（org 不可读或字段为空）-> verified=False（fail-visible）。
    """
    org, oerr = _read_json_file(ORG_PATH)

    # 收集 org 中实际绑定（含 chief 与各部门 agent_profiles）
    actual: Dict[str, Optional[str]] = {}
    if isinstance(org, dict):
        chief = org.get("chief") or {}
        if chief.get("agent_id"):
            actual[chief["agent_id"]] = chief.get("llm_binding")
        for dept in org.get("departments", []) or []:
            for prof in dept.get("agent_profiles", []) or []:
                aid = prof.get("agent_id")
                if aid:
                    actual[aid] = prof.get("llm_binding")

    rows: List[Dict[str, Any]] = []
    hit = dev = no_ref = 0
    # 优先纳入有 M0 默认的席位（契约一致性核心对照）
    for aid, m0 in M0_DEFAULT_BINDING.items():
        real = actual.get(aid)
        if real is None:
            status = "no_actual"
            note = "org 实际绑定缺失（不编造）"
        elif real == m0["default"]:
            status = "hit"
            note = "实际绑定 == M0 默认"
            hit += 1
        else:
            status = "deviation"
            note = "实际绑定偏离 M0 默认"
            dev += 1
        rows.append({
            "agent_id": aid,
            "actual_binding": real,
            "m0_default": m0["default"],
            "fallback": m0.get("fallback"),
            "status": status,
            "note": note,
        })
    # 其余已注册席位（有实际绑定但无 M0 默认对照）
    for aid, real in actual.items():
        if aid in M0_DEFAULT_BINDING:
            continue
        rows.append({
            "agent_id": aid,
            "actual_binding": real,
            "m0_default": None,
            "fallback": None,
            "status": "no_m0_ref",
            "note": "已注册席位，无 M0 默认对照（按 org 实际绑定）",
        })
        no_ref += 1
    rows.sort(key=lambda r: str(r.get("agent_id")))

    verified: bool = isinstance(org, dict)
    summary_note: str
    if verified:
        summary_note = "命中 %d / 偏离 %d / 无M0默认 %d（M0 默认仅作契约建议，非运行时路由）" % (
            hit, dev, no_ref)
    else:
        summary_note = "org.json 缺失/不可读 → 绑定对照 SOURCE_UNVERIFIED（不编造）"

    return _source_record(
        "bindings",
        state="healthy" if verified else "unknown",
        source_mode="file",
        error_code=oerr,
        note="各席位实际 llm_binding vs M0 默认对照：" + summary_note,
        data={
            "rows": rows,
            "counts": {"hit": hit, "deviation": dev, "no_m0_ref": no_ref,
                       "no_actual": sum(1 for r in rows if r["status"] == "no_actual")},
            "m0_default_binding": M0_DEFAULT_BINDING,
        },
        source=ORG_PATH,
        verified=verified,
    )


# --------------------------------------------------------------------------- #
# 分块 (c)：红线项汇总（本只读契约永不直接触碰的 BLOCK 项）
# --------------------------------------------------------------------------- #
def _build_redline_items() -> Dict[str, Any]:
    """汇总系统红线项（BLOCK 项）。

    这些是看板硬纪律恪守的边界，不是编造的数据，而是契约自身约束的原样公示。
    """
    return _source_record(
        "redline_items",
        state="healthy",
        source_mode="computed",
        error_code=None,
        note="本只读看板永不直接触碰的红线项（硬纪律，非市场数据）。",
        data={
            "count": len(REDLINE_ITEMS),
            "items": REDLINE_ITEMS,
        },
        source="本模块只读契约（硬纪律）",
        verified=True,
    )


# --------------------------------------------------------------------------- #
# 分块 (d)：档位快照（model_tiers.json）
# --------------------------------------------------------------------------- #
def _build_model_tiers() -> Dict[str, Any]:
    """model_tiers.json 模型档位表快照（只读静态配置）。"""
    tiers, terr = _read_json_file(MODEL_TIERS_PATH)
    if isinstance(tiers, dict):
        snap = {
            "registry": tiers.get("registry"),
            "version": tiers.get("version"),
            "models": tiers.get("models"),
            "grade_policy": tiers.get("grade_policy"),
            "degraded_fallback": tiers.get("degraded_fallback"),
            "note_field": tiers.get("_note"),
        }
        state = "healthy"
        note = "model_tiers.json 模型档位表（E3=强模型/glm-5.3；E1=本地 qwen3.5:9b 降级档）。静态配置，非运行时实例。"
    else:
        snap = None
        state = "unknown"
        note = "model_tiers.json 缺失/不可读 → SOURCE_UNVERIFIED"
    return _source_record(
        "model_tiers",
        state=state,
        source_mode="file",
        error_code=terr,
        note=note,
        data=snap,
        source=MODEL_TIERS_PATH,
        verified=isinstance(tiers, dict),
    )


# --------------------------------------------------------------------------- #
# 分块 (e)：8098 model-admin 静态配置（http 源；不可达标 UNVERIFIED）
# --------------------------------------------------------------------------- #
def _build_model_admin() -> Dict[str, Any]:
    """8098 model-admin 静态配置（只读 GET）。

    仅当本机 8098 在线时返回真实静态态；否则 verified=False / error_code="http_error"，
    整体仍返回合法 dict（与 Zone2 一致，by design，非缺陷）。
    """
    agents_url = "http://%s:%d/api/agents" % (MODEL_ADMIN_HOST, MODEL_ADMIN_PORT)
    models_url = "http://%s:%d/api/models" % (MODEL_ADMIN_HOST, MODEL_ADMIN_PORT)

    agents_val, agents_err = _http_get_json(agents_url)
    models_val, models_err = _http_get_json(models_url)

    if isinstance(agents_val, dict) and isinstance(models_val, dict):
        ag = agents_val.get("agents") or []
        cloud = models_val.get("cloud") or {}
        data = {
            "agent_count": len(ag),
            "agents_sample": [
                {"agent_id": a.get("agent_id"),
                 "binding": a.get("binding"),
                 "grade": a.get("grade")}
                for a in ag[:8]
            ],
            "loaded_models": models_val.get("loaded") or [],
            "cloud_key_configured": bool(cloud.get("ok")),
            "cloud_key_src": cloud.get("key_src"),
        }
        return _source_record(
            "model_admin",
            state="healthy",
            source_mode="http",
            error_code=None,
            note=("8098 model-admin 静态配置（/api/agents + /api/models）在线；"
                  "静态绑定聚合，非运行时用量。"),
            data=data,
            source="%s | %s" % (agents_url, models_url),
            verified=True,
        )

    # 任一不可达 -> 整体该项 UNVERIFIED，明确 http_error
    return _source_record(
        "model_admin",
        state="unknown",
        source_mode="http",
        error_code="http_error",
        note=("8098 model-admin 不可达（服务未启动或端口占用）→ SOURCE_UNVERIFIED。"
              "静态绑定仍可经 org.json / model_tiers.json 文件源读取。"),
        data=None,
        source="%s | %s" % (agents_url, models_url),
        verified=False,
    )


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def contract_api(query: Dict[str, Any]) -> Dict[str, Any]:
    """席位/模型契约一致性看板 API 处理函数。

    Args:
        query: 请求参数 dict（本特性为只读快照，query 当前仅作透传，不做语义过滤）。

    Returns:
        信封 dict，含 seats / bindings / redline_items / model_tiers / model_admin
        五部分；所有非有限浮点已规整为 None，可直接 json.dumps。
        任一文件/网络源不可达均 fail-visible（对应分块 verified=False），整体仍合法。
    """
    if not isinstance(query, dict):
        query = {}

    seats = _build_seats()
    bindings = _build_bindings()
    redline = _build_redline_items()
    model_tiers = _build_model_tiers()
    model_admin = _build_model_admin()

    # 顶层 source_mode：混合（文件源 + http 源）
    envelope: Dict[str, Any] = {
        "seats": seats,
        "bindings": bindings,
        "redline_items": redline,
        "model_tiers": model_tiers,
        "model_admin": model_admin,
        "meta": {
            "feature": "watch-admin-contract",
            "read_only": True,
            "source_mode": "file+http",
            "model_admin_host": MODEL_ADMIN_HOST,
            "model_admin_port": MODEL_ADMIN_PORT,
            "data_dir": _REGISTRY_DIR,
            "note": ("席位/模型契约一致性看板：纯只读快照；8098 不可达时该部分标 "
                     "SOURCE_UNVERIFIED（by design，非缺陷）。"),
        },
    }
    return _sanitize(envelope)


# --------------------------------------------------------------------------- #
# 只读 HTML 页面（深海军蓝风格，禁顶部胶囊导航）
# --------------------------------------------------------------------------- #
CONTRACT_HTML: str = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>席位/模型契约一致性看板 · 盯盘只读</title>
<style>
  :root{
    --navy:#0e1b2a; --navy2:#13263a; --cyan:#27c9c9; --amber:#e0a23c;
    --line:#1f3a52; --txt:#cfe3ee; --muted:#7e98ab; --red:#e06a6a; --green:#5fcf9b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--navy);color:var(--txt);
       font-family:"Microsoft YaHei",system-ui,sans-serif;}
  header{padding:18px 24px;border-bottom:1px solid var(--line);
         background:linear-gradient(180deg,var(--navy2),var(--navy));}
  header h1{margin:0;font-size:20px;color:var(--cyan);letter-spacing:.5px;}
  header .sub{margin-top:4px;color:var(--muted);font-size:12px;}
  main{padding:20px 24px;max-width:1180px;margin:0 auto;}
  section{margin-bottom:28px;}
  h2{font-size:15px;color:var(--amber);border-left:3px solid var(--amber);
     padding-left:8px;margin:0 0 12px;}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
         border:1px solid var(--line);margin-left:6px;}
  .ok{color:var(--green);border-color:var(--green);}
  .bad{color:var(--red);border-color:var(--red);background:rgba(224,106,106,.08);}
  .unverified{color:var(--amber);border-color:var(--amber);
              background:rgba(224,162,60,.10);font-weight:bold;}
  .meta{color:var(--muted);font-size:12px;margin-bottom:10px;}
  table{width:100%;border-collapse:collapse;font-size:13px;
        background:var(--navy2);border:1px solid var(--line);}
  th,td{padding:8px 10px;border:1px solid var(--line);text-align:left;}
  thead th{color:var(--cyan);background:rgba(39,201,201,.08);}
  .na{color:var(--muted);}
  .hit{color:var(--green);font-weight:bold;}
  .dev{color:var(--red);font-weight:bold;}
  .ref{color:var(--muted);}
  .note{font-size:12px;color:var(--muted);margin:6px 0 0;}
  .err{padding:16px;border:1px solid var(--red);color:var(--red);
       background:rgba(224,106,106,.06);border-radius:6px;font-size:13px;}
  .redline{border:1px solid var(--red);border-radius:6px;padding:10px 12px;
           background:rgba(224,106,106,.06);}
  .redline li{margin:4px 0;font-size:13px;}
  .tag{font-size:10px;padding:1px 6px;border:1px solid var(--line);border-radius:8px;}
  code{color:var(--amber);}
</style>
</head>
<body>
<header>
  <h1>席位/模型契约一致性看板</h1>
  <div class="sub">盯盘只读管理页 · 纯只读快照 · 数据源：org.json / team_invest.json / model_tiers.json / 8098 · 绝不编造</div>
</header>
<main>
  <div id="status"></div>

  <section>
    <h2>① 已注册席位与角色 <span id="seats-badge" class="badge"></span></h2>
    <div id="seats-body"></div>
  </section>

  <section>
    <h2>② 绑定命中/偏离（实际 llm_binding vs M0 默认） <span id="bind-badge" class="badge"></span></h2>
    <div id="bind-body"></div>
  </section>

  <section>
    <h2>③ 红线项汇总（本页永不直接触碰） <span id="rl-badge" class="badge"></span></h2>
    <div id="rl-body"></div>
  </section>

  <section>
    <h2>④ 模型档位快照 <span id="tier-badge" class="badge"></span></h2>
    <div id="tier-body"></div>
  </section>

  <section>
    <h2>⑤ 8098 model-admin 静态配置 <span id="ma-badge" class="badge"></span></h2>
    <div id="ma-body"></div>
  </section>
</main>

<script>
function setBadge(el, part){
  if(part.verified===true){ el.className='badge ok'; el.textContent='VERIFIED'; }
  else { el.className='badge unverified'; el.textContent='SOURCE_UNVERIFIED（'+(part.error_code||'error')+'）'; }
}
function esc(s){
  if(s===null||s===undefined) return '<span class="na">—</span>';
  return String(s).replace(/[&<>]/g, function(c){
    return c==='&'?'&amp;':c==='<'?'&lt;':'&gt;';});
}
function renderSeats(d){
  var el=document.getElementById('seats-body');
  if(d.verified!==true){
    el.innerHTML='<div class="err">席位源不可用：'+(d.note||d.error_code||'未知错误')+'</div>';return;
  }
  var data=d.data||{}; var seats=data.seats||[];
  var html='<div class="meta">已注册席位数='+(data.count||0)+' · 控辩审角色='+
           ((data.control_debate_review||[]).length||0)+'</div>';
  html+='<table><thead><tr><th>agent_id</th><th>角色/role_profile</th><th>dept/team</th>'+
        '<th>grade</th><th>llm_binding</th><th>来源</th></tr></thead><tbody>';
  seats.forEach(function(s){
    html+='<tr><td>'+esc(s.agent_id)+'</td><td>'+esc(s.role||s.role_profile||'-')+
          '</td><td>'+esc(s.dept||s.team||'-')+'</td><td>'+esc(s.grade||'-')+
          '</td><td>'+esc(s.llm_binding||'—')+'</td><td class="na">'+esc(s.source||'-')+'</td></tr>';
  });
  html+='</tbody></table>';
  el.innerHTML=html;
}
function renderBindings(d){
  var el=document.getElementById('bind-body');
  if(d.verified!==true){
    el.innerHTML='<div class="err">绑定对照不可用：'+(d.note||d.error_code||'未知错误')+'</div>';return;
  }
  var data=d.data||{}; var rows=data.rows||[]; var c=data.counts||{};
  html='<div class="meta">'+esc(d.note)+'</div>';
  html+='<table><thead><tr><th>agent_id</th><th>实际绑定</th><th>M0 默认</th>'+
        '<th>降级档</th><th>状态</th><th>说明</th></tr></thead><tbody>';
  rows.forEach(function(r){
    var st='<span class="ref">'+esc(r.status)+'</span>';
    if(r.status==='hit') st='<span class="hit">命中</span>';
    else if(r.status==='deviation') st='<span class="dev">偏离</span>';
    else if(r.status==='no_actual') st='<span class="dev">缺实际</span>';
    html+='<tr><td>'+esc(r.agent_id)+'</td><td>'+esc(r.actual_binding||'—')+
          '</td><td>'+esc(r.m0_default||'—')+'</td><td>'+esc(r.fallback||'—')+
          '</td><td>'+st+'</td><td class="na">'+esc(r.note||'')+'</td></tr>';
  });
  html+='</tbody></table>';
  html+='<div class="note">命中='+(c.hit||0)+' · 偏离='+(c.deviation||0)+
        ' · 无M0默认='+(c.no_m0_ref||0)+' · 缺实际='+(c.no_actual||0)+'</div>';
  el.innerHTML=html;
}
function renderRedline(d){
  var el=document.getElementById('rl-body');
  var data=d.data||{}; var items=data.items||[];
  var html='<div class="meta">红线项数量='+(data.count||0)+' · '+esc(d.note)+'</div>';
  html+='<ul class="redline">';
  items.forEach(function(it){
    html+='<li><span class="tag" style="border-color:var(--red);color:var(--red);">'+esc(it.policy)+
          '</span> <b>'+esc(it.label)+'</b> — '+esc(it.note)+
          ' <span class="na">['+esc(it.source)+']</span></li>';
  });
  html+='</ul>';
  el.innerHTML=html;
}
function renderTiers(d){
  var el=document.getElementById('tier-body');
  if(d.verified!==true){
    el.innerHTML='<div class="err">档位快照不可用：'+(d.note||d.error_code||'未知错误')+'</div>';return;
  }
  var data=d.data||{}; var models=data.models||{}; var gp=data.grade_policy||{};
  var html='<div class="meta">registry='+esc(data.registry||'-')+' · version='+esc(data.version||'-')+
           ' · 降级档='+esc(data.degraded_fallback||'-')+'</div>';
  html+='<table><thead><tr><th>模型</th><th>tier</th><th>scope</th><th>enabled</th><th>note</th></tr></thead><tbody>';
  Object.keys(models).forEach(function(k){
    var m=models[k]||{};
    html+='<tr><td>'+esc(k)+'</td><td>'+esc(m.tier||'-')+'</td><td>'+esc(m.scope||'-')+
          '</td><td>'+esc(m.enabled)+'</td><td class="na">'+esc(m.note||'')+'</td></tr>';
  });
  html+='</tbody></table>';
  var gk=Object.keys(gp);
  if(gk.length){
    html+='<h3 style="color:var(--cyan);font-size:13px;margin:14px 0 6px;">职级档位策略</h3>';
    html+='<table><thead><tr><th>grade</th><th>label</th><th>allow</th><th>default</th></tr></thead><tbody>';
    gk.forEach(function(g){
      var p=gp[g]||{};
      html+='<tr><td>'+esc(g)+'</td><td>'+esc(p.label||'-')+'</td><td>'+esc((p.allow||[]).join(','))+
            '</td><td>'+esc(p.default||'-')+'</td></tr>';
    });
    html+='</tbody></table>';
  }
  el.innerHTML=html;
}
function renderModelAdmin(d){
  var el=document.getElementById('ma-body');
  if(d.verified!==true){
    el.innerHTML='<div class="err">8098 model-admin 不可达 → SOURCE_UNVERIFIED（error_code='+
                 esc(d.error_code||'http_error')+'）。'+esc(d.note||'')+'</div>';return;
  }
  var data=d.data||{};
  var html='<div class="meta">agent_count='+(data.agent_count||0)+
           ' · 云端 key 配置='+(data.cloud_key_configured?'是':'否')+
           ' · loaded_models='+((data.loaded_models||[]).length||0)+'</div>';
  html+='<table><thead><tr><th>agent_id</th><th>binding</th><th>grade</th></tr></thead><tbody>';
  (data.agents_sample||[]).forEach(function(a){
    html+='<tr><td>'+esc(a.agent_id||'-')+'</td><td>'+esc(a.binding||'—')+
          '</td><td>'+esc(a.grade||'-')+'</td></tr>';
  });
  html+='</tbody></table>';
  html+='<div class="note">'+esc(d.note||'')+'</div>';
  el.innerHTML=html;
}
function load(){
  fetch('/api/watch-admin/contract')
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(d){
      var s=d.seats||{}, b=d.bindings||{}, rl=d.redline_items||{}, t=d.model_tiers||{}, ma=d.model_admin||{};
      setBadge(document.getElementById('seats-badge'), s);
      setBadge(document.getElementById('bind-badge'), b);
      setBadge(document.getElementById('rl-badge'), rl);
      setBadge(document.getElementById('tier-badge'), t);
      setBadge(document.getElementById('ma-badge'), ma);
      renderSeats(s); renderBindings(b); renderRedline(rl); renderTiers(t); renderModelAdmin(ma);
      document.getElementById('status').innerHTML='';
    })
    .catch(function(e){
      document.getElementById('status').innerHTML=
        '<div class="err">快照获取失败：'+(e.message||e)+'</div>';
    });
}
load();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# 动态加载契约
# --------------------------------------------------------------------------- #
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/contract": contract_api,
}

HTML_ROUTES: Dict[str, str] = {
    "/watch-admin/contract": CONTRACT_HTML,
}
