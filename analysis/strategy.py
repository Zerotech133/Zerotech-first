# -*- coding: utf-8 -*-
"""策略工作室后端（H3）：StrategySpec v1 校验 / 编译 / 策略库。

契约依据：`C:\\项目\\deliverables\\2026-11-04_H3策略与回测口径规范_v1.md`
（Lead 2026-11-04 拍板 D1–D5；实现与该规范 diff=0 为冻结条件）。

StrategySpec v1（JSON dict）：
    {
      "schema_version": "v1",                 # 可选，默认 v1（D4）
      "name": "低估值高质量",                  # 必填，1–40 字符
      "universe": "HS300",                    # A_SHARE/HS300/WATCHLIST/industry:<申万L1>，默认 WATCHLIST
      "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 30}],  # 1–20 项
      "rank_by": [{"field": "ps_pct_hist", "order": "asc"}],           # 0–3 项，仅数值字段
      "limit": 20,                            # 1–200，越界 clamp + warning
      "rebalance": "monthly",                 # daily|weekly|monthly，默认 monthly
      "cost_bps": 20,                         # 0–100 整数，双边合计口径，默认 20（D2）
      "as_of": "2026-11-04",                  # 可选，YYYY-MM-DD，默认当日（D4）
      "notes": "研究用，不构成投资建议"        # ≤200 字符
    }

对外入口（契约见规范 §5.1）：
    validate(spec)  -> {ok, errors[], warnings[], normalized{...}}
    compile(spec)   -> {ok, errors[], warnings[], schema_version, screener_spec{...},
                        rebalance, cost_bps, spec_hash}
    describe(spec)  -> 中文可读摘要（非法 spec 返回 ""）
    save_strategy(spec, slug=None, base=None) -> {ok, slug, created, updated, path, spec_hash}
    list_strategies(base=None) -> {ok, strategies:[{slug,name,updated,rebalance,cost_bps,limit}], warnings}
    load_strategy(slug, base=None) -> {ok, slug, spec, meta} | {ok: False, error}

关键约定：
- 字段白名单复用 H2a `analysis.screener.FIELDS`（冻结，不改 H2a）；单位口径以规范 §1.2
  「权威单位表」为准（D3）：百分数字段 0–100（ps_pct_hist/turnover_rate/dv_ratio/quality_score），
  小数字段 0.01=1%（revenue_yoy/net_profit_yoy/roe/debt_ratio/ret_20/ret_60）。
  **规范 v1.1（Wave B 评审 P1）：禁止静默归一改写**——值超合理区间只出 warning 并**保留原值**，
  normalized/compile/spec_hash 一律使用输入原值；如确需换算，filter 元素必须显式声明
  `unit`（"native"|"pct"|"ratio"），显式归一才允许改值并出 warning。compile 不二次换算。
- spec_hash 稳定性（P2）：未显式 as_of 时默认 as_of **不进入** spec_hash（跨日同 hash）；
  显式给出 as_of 才计入。hash 覆盖 normalized 用户字段（含 schema_version）。
- screener 模块缺失 → validate 返回 {ok:false, errors:["选股引擎未就绪"]}，代理层转 503（§5.1）；
  业务校验失败（errors 非空）仍返回 HTTP 200 + {ok:false, errors}（规范 v1.1 口径）。
- rebalance/cost_bps 不进入 ScreeningDSL，由 compile 顶层/策略库 meta 承载（§2.2）。
- 策略库：`ANALYSIS_WORK/strategies/<slug>.json`（schema_version/slug/spec/meta/changelog）；
  原子写（`<slug>.json.tmp-<pid>-<uuid>` + O_EXCL → os.replace）；slug 分配持 `.lock` 文件锁
  （P2 并发：锁内完成查重/后缀分配/写入）；slug 缺省冲突加 -2/-3，显式 slug=upsert（§5.3）。
"""
import contextlib
import hashlib
import json
import math
import os
import re
import time
import uuid

SCHEMA_VERSION = "v1"
REBALANCE_ENUM = ("daily", "weekly", "monthly")
DEFAULT_UNIVERSE = "WATCHLIST"
DEFAULT_REBALANCE = "monthly"
DEFAULT_LIMIT = 20
LIMIT_MIN, LIMIT_MAX = 1, 200
FILTERS_MAX = 20
RANK_MAX = 3
NAME_MAX = 40
NOTES_MAX = 200
DEFAULT_COST_BPS = 20
COST_MIN, COST_MAX = 0, 100
SPEC_MAX_BYTES = 64 * 1024
STRATEGY_DIRNAME = "strategies"
MAX_STRATEGIES = 200
LOCK_TIMEOUT = 5.0
LOCK_STALE = 30.0
UNIT_ENUM = ("native", "pct", "ratio")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
WORK_DEFAULT = r"C:\项目\analysis_work"
ENGINE_MISSING = "选股引擎未就绪"

UNIVERSES = ("A_SHARE", "HS300", "WATCHLIST")
UNIVERSE_ALIASES = {
    "沪深300": "HS300", "hs300": "HS300",
    "全a": "A_SHARE", "全a股": "A_SHARE", "全部a股": "A_SHARE",
    "自选": "WATCHLIST", "观察池": "WATCHLIST", "watchlist": "WATCHLIST",
}

# 规范 §1.2 权威单位表（H2a FIELDS 不含 unit，故 H3 自带；不修改 H2a）
# ratio=倍/亿元等无换算；percent=百分数 0–100；fraction=小数（0.01=1%）；score=0–100 分
AUTHORITATIVE_UNITS = {
    "ps": "ratio", "ps_pct_hist": "percent", "pe_ttm": "ratio", "dv_ratio": "percent",
    "total_mv_yi": "yi", "revenue_yoy": "fraction", "net_profit_yoy": "fraction",
    "roe": "fraction", "debt_ratio": "fraction", "ret_20": "fraction", "ret_60": "fraction",
    "amount_20_yi": "yi", "turnover_rate": "percent", "quality_score": "score",
    "industry_l1": "string", "industry_l2": "string", "flag_st": "bool",
    "event_types": "string",
}
PERCENT_FIELDS = frozenset(k for k, v in AUTHORITATIVE_UNITS.items() if v == "percent")
FRACTION_FIELDS = frozenset(k for k, v in AUTHORITATIVE_UNITS.items() if v == "fraction")

_NUM_OPS = ("=", "!=", ">", ">=", "<", "<=")
_STR_OPS = ("in", "notin", "contains", "contains_any")
_BOOL_OPS = ("=", "!=")
_ALL_OPS = tuple(dict.fromkeys(_NUM_OPS + _STR_OPS + _BOOL_OPS))
_ARR_STR_OPS = ("in", "notin", "contains_any")
_OPS_TEXT = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "=": "=", "!=": "≠",
             "in": "属于", "notin": "不属于", "contains": "包含", "contains_any": "包含任一"}
_UNIVERSE_TEXT = {"A_SHARE": "全A", "HS300": "HS300", "WATCHLIST": "观察池"}
_REBALANCE_TEXT = {"daily": "日频", "weekly": "周频", "monthly": "月度"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------------- 引擎加载（惰性；缺失 fail-visible，不抛） ----------------
def _load_screener():
    """返回 analysis.screener 模块；缺失/导入失败 → None（validate 回 ENGINE_MISSING）。"""
    try:
        from . import screener
        return screener
    except Exception:
        return None


def _work_root(base=None):
    if base:
        return base
    env = os.environ.get("ANALYSIS_WORK")
    if env:
        return env
    try:
        from . import config
        return config.WORK
    except Exception:
        return WORK_DEFAULT


def _strategies_dir(base=None):
    return os.path.join(_work_root(base), STRATEGY_DIRNAME)


def _uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def _field_meta(screener):
    fields = getattr(screener, "FIELDS", None) if screener is not None else None
    return fields if isinstance(fields, dict) and fields else None


def _kind_ops(screener, kind):
    if kind == "bool":
        return tuple(getattr(screener, "BOOL_OPS", ()) or _BOOL_OPS)
    if kind == "string":
        return tuple(getattr(screener, "STR_OPS", ()) or _STR_OPS)
    return tuple(getattr(screener, "NUM_OPS", ()) or _NUM_OPS)


# ---------------- 单位口径（规范 §1.2 / v1.1：禁止静默归一改写） ----------------
def _unit_check(field, value, warnings):
    """值超权威单位合理区间 → 仅 warning，**保留原值**（v1.1 P1 禁静默改写）。"""
    unit = AUTHORITATIVE_UNITS.get(field)
    if unit in ("percent", "score"):
        if value != 0 and abs(value) < 1:
            warnings.append("字段 %s 权威单位为%s（0–100），输入 %g 疑似小数；按 v1.1 禁止静默归一，"
                            "保留原值（如确需按比例解释请显式 unit）"
                            % (field, "百分数" if unit == "percent" else "分值", value))
        if abs(value) > 100:
            warnings.append("字段 %s 超出权威单位范围 0–100（%g），保留原值，请确认口径"
                            % (field, value))
    elif unit == "fraction":
        if abs(value) > 2:
            warnings.append("字段 %s 权威单位为小数（0.01=1%%），输入 %g 疑似百分数；按 v1.1 禁止静默归一，"
                            "保留原值（如确需按百分数解释请显式 unit）" % (field, value))


def _apply_explicit_unit(field, value, unit, warnings):
    """只有一个 filter 显式声明 unit 时才允许改值：把声明单位换算到权威单位。"""
    declared = str(unit).strip().lower()
    if declared not in UNIT_ENUM:
        return None, "unit 仅支持 %s" % "/".join(UNIT_ENUM)
    native = AUTHORITATIVE_UNITS.get(field)
    if declared == "native":
        return value, ""
    if native in ("percent", "score"):
        if declared == "ratio":
            warnings.append("字段 %s 显式 unit=ratio → 按百分数口径归一为 %g"
                            % (field, round(float(value) * 100, 6)))
            return round(float(value) * 100, 6), ""
        return value, ""
    if native == "fraction":
        if declared == "pct":
            warnings.append("字段 %s 显式 unit=pct → 按小数口径归一为 %g"
                            % (field, round(float(value) / 100.0, 6)))
            return round(float(value) / 100.0, 6), ""
        return value, ""
    if declared == "pct":
        return None, "字段 %s 不支持 unit=pct（权威单位为 %s）" % (field, native or "native")
    return value, ""


def _as_of_explicit(spec):
    """spec 是否显式给出 as_of（决定 as_of 是否进入 spec_hash，P2）。"""
    if not isinstance(spec, dict):
        return False
    value = spec.get("as_of")
    return isinstance(value, str) and bool(value.strip())


# ---------------- 校验 ----------------
def validate(spec):
    """校验 StrategySpec v1 → {ok, errors[], warnings[], normalized{...}, as_of_explicit}；不抛。

    规范 v1.1 口径：业务校验失败 = ok:false + errors（HTTP 200，由代理层保持）；
    单位不做静默归一（超合理区间仅 warning 且保留原值）；显式 unit 才换算。
    """
    errors = []
    warnings = []
    if not isinstance(spec, dict):
        return {"ok": False, "errors": ["spec 必须为 JSON 对象"], "warnings": [],
                "normalized": None}
    screener = _load_screener()
    fields = _field_meta(screener)
    if fields is None:
        return {"ok": False, "errors": [ENGINE_MISSING], "warnings": [], "normalized": None}

    # 顶层字段白名单（v1 允许 schema_version/as_of 两个可选扩展）
    known = {"schema_version", "name", "universe", "filters", "rank_by", "limit",
             "rebalance", "cost_bps", "as_of", "notes"}
    unknown = sorted(set(spec) - known)
    if unknown:
        errors.append("未知顶层字段（v1 不允许）: " + ", ".join(unknown))

    # schema_version
    schema_version = spec.get("schema_version")
    if schema_version is None:
        schema_version = SCHEMA_VERSION
    if not isinstance(schema_version, str) or schema_version != SCHEMA_VERSION:
        errors.append("schema_version 仅支持 %s" % SCHEMA_VERSION)
        schema_version = SCHEMA_VERSION

    # as_of
    as_of = spec.get("as_of")
    if as_of is None or as_of == "":
        as_of = time.strftime("%Y-%m-%d")
    elif not isinstance(as_of, str) or not _DATE_RE.match(as_of):
        errors.append("as_of 需为 YYYY-MM-DD")
        as_of = time.strftime("%Y-%m-%d")
    else:
        try:
            parsed = time.strptime(as_of, "%Y-%m-%d")
            if parsed.tm_wday >= 5:
                warnings.append("as_of=%s 为非交易日（周末），数据将自然回退到最近可得日" % as_of)
            if as_of > time.strftime("%Y-%m-%d"):
                warnings.append("as_of=%s 为未来日期，请确认 PIT 口径" % as_of)
        except ValueError:
            errors.append("as_of 非法日期：%s" % as_of)
            as_of = time.strftime("%Y-%m-%d")

    # name
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 必填（非空字符串，1–%d 字符）" % NAME_MAX)
        name = ""
    else:
        name = name.strip()
        if len(name) > NAME_MAX:
            errors.append("name 过长（1–%d 字符）" % NAME_MAX)
        if _CTRL_RE.search(name):
            errors.append("name 含控制字符")

    # universe（含别名归一）
    raw_universe = spec.get("universe")
    universe = str(raw_universe).strip() if raw_universe is not None else DEFAULT_UNIVERSE
    alias = UNIVERSE_ALIASES.get(universe) or UNIVERSE_ALIASES.get(universe.lower())
    if alias:
        universe = alias
    elif universe.upper() in UNIVERSES:
        universe = universe.upper()
    if universe not in UNIVERSES and not universe.lower().startswith("industry:"):
        errors.append("universe 非法：%s（可选 A_SHARE/HS300/WATCHLIST/industry:<名称>）" % universe)
    elif universe.lower().startswith("industry:"):
        if not universe.split(":", 1)[1].strip():
            errors.append("industry:<名称> 需给出行业名")
        else:
            warnings.append("行业池/全A 股票池不含已退市股，回测存在幸存者偏差（规范 §3.5）")
    elif universe == "A_SHARE":
        warnings.append("A_SHARE 股票池不含已退市股，回测存在幸存者偏差（规范 §3.5）")

    # filters
    raw_filters = spec.get("filters")
    if not isinstance(raw_filters, list) or not raw_filters:
        errors.append("filters 必须为非空数组（1–%d 项）" % FILTERS_MAX)
        raw_filters = []
    elif len(raw_filters) > FILTERS_MAX:
        errors.append("filters 最多 %d 项" % FILTERS_MAX)
        raw_filters = raw_filters[:FILTERS_MAX]
    norm_filters = []
    for i, f in enumerate(raw_filters):
        if not isinstance(f, dict):
            errors.append("filters[%d] 必须是对象" % i)
            continue
        field = str(f.get("field") or "").strip()
        op = str(f.get("op") or "").strip()
        value = f.get("value")
        if not field:
            errors.append("filters[%d] 缺少 field" % i)
            continue
        meta = fields.get(field)
        if not isinstance(meta, dict):
            errors.append("filters[%d] 未知字段（不在 H2a 白名单）：%s" % (i, field))
            continue
        kind = str(meta.get("kind") or "number")
        if not op:
            errors.append("filters[%d] 缺少 op（字段 %s）" % (i, field))
            continue
        allowed = _kind_ops(screener, kind)
        if op not in allowed:
            errors.append("filters[%d] 字段 %s（%s）不支持算子 %s（支持 %s）"
                          % (i, field, kind, op, "/".join(allowed)))
            continue
        unit = f.get("unit")
        if unit is not None and kind != "number":
            errors.append("filters[%d] unit 仅支持数值字段（%s 为 %s）" % (i, field, kind))
            continue
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append("filters[%d] 数值字段 %s 的 value 必须为有限数值" % (i, field))
                continue
            if not math.isfinite(float(value)):
                errors.append("filters[%d] 数值字段 %s 的 value 不能为 NaN/Inf" % (i, field))
                continue
            numeric = float(value)
            if unit is not None:
                if not isinstance(unit, str):
                    errors.append("filters[%d] unit 必须为字符串（%s）"
                                  % (i, "/".join(UNIT_ENUM)))
                    continue
                converted, uerr = _apply_explicit_unit(field, numeric, unit, warnings)
                if uerr:
                    errors.append("filters[%d] %s" % (i, uerr))
                    continue
                numeric = float(converted)
            else:
                # 禁止静默归一：只 warning，保留用户原值（规范 v1.1 P1）
                _unit_check(field, numeric, warnings)
            value = numeric
        elif kind == "bool":
            if not isinstance(value, bool):
                errors.append("filters[%d] 布尔字段 %s 的 value 必须为 true/false（JSON boolean）"
                              % (i, field))
                continue
            warnings.append("filters[%d] 使用 flag_st（来源为当前名称，非 PIT；历史回测标 partial）"
                            % i)
        else:  # string
            if op in _ARR_STR_OPS:
                if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
                    errors.append("filters[%d] 字符串算子 %s 需要非空字符串数组 value" % (i, op))
                    continue
            elif not isinstance(value, str) and not (
                    isinstance(value, list) and value and all(isinstance(x, str) for x in value)):
                errors.append("filters[%d] 字符串字段 %s 的 value 必须为字符串或字符串数组"
                              % (i, field))
                continue
            if field in ("quality_score", "event_types", "industry_l2"):
                warnings.append("filters[%d] 字段 %s 覆盖有限（仅已分析股票/当前分类），命中可能偏少"
                                % (i, field))
        nf = {"field": field, "op": op, "value": value}
        if unit is not None:
            nf["unit"] = str(unit).strip().lower()
        norm_filters.append(nf)

    # rank_by
    raw_rank = spec.get("rank_by")
    if raw_rank is None:
        raw_rank = []
    if not isinstance(raw_rank, list):
        errors.append("rank_by 必须为数组（0–%d 项）" % RANK_MAX)
        raw_rank = []
    elif len(raw_rank) > RANK_MAX:
        errors.append("rank_by 最多 %d 项" % RANK_MAX)
        raw_rank = raw_rank[:RANK_MAX]
    norm_rank = []
    for i, r in enumerate(raw_rank):
        if not isinstance(r, dict):
            errors.append("rank_by[%d] 必须是对象" % i)
            continue
        field = str(r.get("field") or "").strip()
        if not field:
            errors.append("rank_by[%d] 缺少 field" % i)
            continue
        meta = fields.get(field)
        if not isinstance(meta, dict):
            errors.append("rank_by[%d] 未知字段：%s" % (i, field))
            continue
        if str(meta.get("kind") or "number") != "number":
            errors.append("rank_by[%d] 仅支持数值字段：%s" % (i, field))
            continue
        order = str(r.get("order") or "desc").strip().lower()
        if order not in ("asc", "desc"):
            errors.append("rank_by[%d] order 必须 asc/desc" % i)
            order = "desc"
        if field in ("quality_score",):
            warnings.append("rank_by[%d] 字段 %s 覆盖有限（仅已分析股票），排序可能退化" % (i, field))
        norm_rank.append({"field": field, "order": order})

    # limit（越界 → clamp + warning，规范 §1.1）
    limit = spec.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int):
        errors.append("limit 必须为整数")
        limit = DEFAULT_LIMIT
    elif limit < LIMIT_MIN or limit > LIMIT_MAX:
        warnings.append("limit=%d 越界（%d–%d），已 clamp 到 %d"
                        % (limit, LIMIT_MIN, LIMIT_MAX,
                           max(LIMIT_MIN, min(limit, LIMIT_MAX))))
        limit = max(LIMIT_MIN, min(limit, LIMIT_MAX))

    # rebalance
    rebalance = str(spec.get("rebalance") or DEFAULT_REBALANCE).strip().lower()
    if rebalance not in REBALANCE_ENUM:
        errors.append("rebalance 非法：%s（可选 daily/weekly/monthly）" % rebalance)
        rebalance = DEFAULT_REBALANCE

    # cost_bps（0–100 整数，双边合计，默认 20，D2）
    cost_bps = spec.get("cost_bps", DEFAULT_COST_BPS)
    if isinstance(cost_bps, bool) or not isinstance(cost_bps, (int, float)):
        errors.append("cost_bps 必须为 0–%d 的整数（双边合计 bps）" % COST_MAX)
        cost_bps = DEFAULT_COST_BPS
    elif float(cost_bps) != int(cost_bps):
        errors.append("cost_bps 必须为整数（双边合计 bps）")
        cost_bps = DEFAULT_COST_BPS
    else:
        cost_bps = int(cost_bps)
        if cost_bps < COST_MIN or cost_bps > COST_MAX:
            errors.append("cost_bps 需在 %d–%d（双边合计 bps）" % (COST_MIN, COST_MAX))
            cost_bps = DEFAULT_COST_BPS

    # notes（超长 → error，规范 §1.3）
    notes = spec.get("notes")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        errors.append("notes 必须为字符串")
        notes = ""
    else:
        notes = notes.strip()
    if len(notes) > NOTES_MAX:
        errors.append("notes 超过 %d 字符（当前 %d）" % (NOTES_MAX, len(notes)))

    normalized = {
        "schema_version": schema_version,
        "name": name,
        "universe": universe,
        "filters": norm_filters,
        "rank_by": norm_rank,
        "limit": limit,
        "rebalance": rebalance,
        "cost_bps": cost_bps,
        "notes": notes,
        "as_of": as_of,
    }
    return {"ok": not errors, "errors": errors, "warnings": _uniq(warnings),
            "normalized": normalized, "as_of_explicit": _as_of_explicit(spec)}


# ---------------- 编译（规范 §2.3） ----------------
def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _spec_hash(normalized, include_as_of=False):
    """P2：默认 as_of 不进入 hash（跨日同 spec 同 hash）；仅显式 as_of 参与。"""
    payload = dict(normalized)
    if not include_as_of:
        payload.pop("as_of", None)
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _screener_spec(normalized):
    # unit 是 StrategySpec 层的显式口径标记，不进 ScreeningDSL（值已按声明单位换算）
    return {
        "universe": normalized["universe"],
        "filters": [{"field": f["field"], "op": f["op"], "value": f["value"]}
                    for f in normalized["filters"]],
        "rank_by": [dict(r) for r in normalized["rank_by"]],
        "limit": normalized["limit"],
        "as_of": normalized["as_of"],
        "explain": True,
    }


def compile(spec):
    """StrategySpec → ScreeningDSL（纯函数）；执行参数 rebalance/cost_bps 透传到顶层。"""
    v = validate(spec)
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"], "warnings": v["warnings"]}
    n = v["normalized"]
    return {
        "ok": True,
        "errors": [],
        "warnings": list(v["warnings"]),
        "schema_version": SCHEMA_VERSION,
        "screener_spec": _screener_spec(n),
        "rebalance": n["rebalance"],
        "cost_bps": n["cost_bps"],
        "spec_hash": _spec_hash(n, bool(v.get("as_of_explicit"))),
    }


# ---------------- 人类可读描述（规范 §5.1/§5.4） ----------------
def _field_label(screener, field):
    meta = _field_meta(screener) or {}
    return str((meta.get(field) or {}).get("label") or field)


def _value_text(field, value):
    unit = AUTHORITATIVE_UNITS.get(field)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "[" + "、".join(str(x) for x in value) + "]"
    if isinstance(value, (int, float)):
        v = float(value)
        if unit in ("percent", "score"):
            return "%g%s" % (v, " 分" if unit == "score" else "")
        if unit == "fraction":
            return "%g%%" % (v * 100)
        if unit == "yi":
            return "%g 亿" % v
        return "%g" % v
    return str(value)


def _filter_text(screener, f):
    field = f["field"]
    label = _field_label(screener, field)
    op = f["op"]
    value = f["value"]
    if field == "flag_st" and op in ("=", "!="):
        is_true = bool(value)
        if (op == "=" and is_true) or (op == "!=" and not is_true):
            return "ST"
        return "非 ST"
    return "%s %s %s" % (label, _OPS_TEXT.get(op, op), _value_text(field, value))


def describe(spec):
    """StrategySpec → 中文可读摘要（≤300 字）；非法 spec 返回 ""（不抛）。"""
    v = validate(spec)
    if not v["ok"]:
        return ""
    n = v["normalized"]
    screener = _load_screener()
    universe = n["universe"]
    uni_text = _UNIVERSE_TEXT.get(universe)
    if uni_text is None and universe.lower().startswith("industry:"):
        uni_text = "行业「%s」" % universe.split(":", 1)[1]
    if uni_text is None:
        uni_text = universe
    conds = [_filter_text(screener, f) for f in n["filters"]]
    cond_text = " 且 ".join(conds) if conds else "（无条件）"
    rank = n["rank_by"]
    if rank:
        rank_text = "；按" + "、".join(
            "%s%s" % (_field_label(screener, r["field"]), "升序" if r["order"] == "asc" else "降序")
            for r in rank) + "取前 %d" % n["limit"]
    else:
        rank_text = "；取前 %d" % n["limit"]
    return "%s 内，%s%s；%s调仓；成本 %dbp（双边）。" % (
        uni_text, cond_text, rank_text,
        _REBALANCE_TEXT.get(n["rebalance"], n["rebalance"]), n["cost_bps"])


# ---------------- 策略库（规范 §5.3） ----------------
def slugify(name):
    """name → ascii slug（2–49；非 [a-z0-9] 折叠为 -；过短用 sha1 兜底）。"""
    raw = (name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")[:49].strip("-")
    if len(slug) < 2:
        slug = "strategy-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return slug


def _valid_slug(slug):
    return isinstance(slug, str) and bool(SLUG_RE.match(slug))


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


@contextlib.contextmanager
def _file_lock(d, timeout=None, stale=None):
    """策略库目录级文件锁（`.lock` + O_CREAT|O_EXCL；支持超时与陈旧锁回收）。"""
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    stale = LOCK_STALE if stale is None else stale
    lock_path = os.path.join(d, ".lock")
    deadline = time.time() + timeout
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock_path) > stale:
                    os.remove(lock_path)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise TimeoutError("save lock timeout: %s" % lock_path)
            time.sleep(0.01)
    try:
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _write_atomic(path, doc):
    """原子写：`<slug>.json.tmp-<pid>-<uuid>` + O_EXCL → os.replace；失败不破坏旧文件。"""
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:12])
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _strategy_files(d):
    try:
        return [fn for fn in os.listdir(d) if fn.endswith(".json")]
    except Exception:
        return []


def _meta_of(doc):
    if not isinstance(doc, dict):
        return {}, {}
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    return meta, spec


def save_strategy(spec, slug=None, base=None):
    """保存策略（原子写 + `.lock` 并发保护）：slug 缺省=新建（冲突加后缀）；显式 slug=upsert。"""
    v = validate(spec)
    if not v["ok"]:
        errs = v["errors"]
        return {"ok": False, "error": errs[0] if errs else "校验失败",
                "errors": errs, "warnings": v["warnings"],
                "engine_missing": errs == [ENGINE_MISSING]}
    n = v["normalized"]
    raw = json.dumps(n, ensure_ascii=False).encode("utf-8")
    if len(raw) > SPEC_MAX_BYTES:
        return {"ok": False, "error": "spec 体积超过 %dKB" % (SPEC_MAX_BYTES // 1024),
                "errors": ["spec 体积超限"]}
    spec_hash = _spec_hash(n, bool(v.get("as_of_explicit")))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    d = _strategies_dir(base)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "策略目录不可写: " + str(e)[:120]}
    if slug is not None:
        slug = str(slug).strip().lower()
        if not _valid_slug(slug):
            return {"ok": False, "error": "invalid slug",
                    "errors": ["invalid slug（%s）" % SLUG_RE.pattern]}

    try:
        with _file_lock(d):
            # 锁内完成：查重/后缀分配/写盘，避免并发同 slug 冲突（P2）
            created = False
            if slug is not None:
                path = os.path.join(d, slug + ".json")
                old = _read_json(path)
                if isinstance(old, dict):
                    created = False
                else:
                    created = True
                    if len(_strategy_files(d)) >= MAX_STRATEGIES:
                        return {"ok": False,
                                "error": "策略库已满（上限 %d）" % MAX_STRATEGIES,
                                "errors": ["策略库已满"]}
            else:
                base_slug = slugify(n["name"])
                if len(_strategy_files(d)) >= MAX_STRATEGIES:
                    return {"ok": False, "error": "策略库已满（上限 %d）" % MAX_STRATEGIES,
                            "errors": ["策略库已满"]}
                candidate = base_slug
                for i in range(1, MAX_STRATEGIES + 1):
                    path = os.path.join(d, candidate + ".json")
                    if _read_json(path) is None:
                        slug = candidate
                        created = True
                        break
                    candidate = "%s-%d" % (base_slug, i + 1)
                else:
                    return {"ok": False, "error": "无法分配 slug（同名策略过多）",
                            "errors": ["无法分配 slug"]}

            meta = {"name": n["name"], "spec_hash": spec_hash,
                    "rebalance": n["rebalance"], "cost_bps": n["cost_bps"],
                    "as_of": n["as_of"], "cost_standard": "v1-bilateral-20bp"}
            changelog = []
            if not created:
                old = _read_json(path) or {}
                old_meta, _ = _meta_of(old)
                meta["created"] = old_meta.get("created") or now
                cl = old.get("changelog")
                changelog = ([x for x in cl if isinstance(x, dict)][-49:]
                             if isinstance(cl, list) else [])
            else:
                meta["created"] = now
            meta["updated"] = now
            changelog.append({"ts": now, "note": "init" if created else "update",
                              "spec_hash": spec_hash})
            doc = {"schema_version": SCHEMA_VERSION, "slug": slug, "spec": n,
                   "meta": meta, "changelog": changelog}
            _write_atomic(path, doc)
    except TimeoutError as e:  # noqa: BLE001
        return {"ok": False, "error": "策略库写入繁忙: " + str(e)[:80],
                "errors": ["save lock timeout"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "策略写入失败: " + str(e)[:120]}
    return {"ok": True, "slug": slug, "created": created, "updated": now,
            "path": path, "spec_hash": spec_hash}


def list_strategies(base=None):
    """列出策略库（updated 倒序）；损坏文件跳过并进 warnings。"""
    d = _strategies_dir(base)
    out = []
    warnings = []
    if not os.path.isdir(d):
        return {"ok": True, "strategies": [], "warnings": []}
    for fn in sorted(_strategy_files(d)):
        doc = _read_json(os.path.join(d, fn))
        if not isinstance(doc, dict):
            warnings.append("跳过损坏策略文件: %s" % fn)
            continue
        meta, spec = _meta_of(doc)
        slug = str(doc.get("slug") or fn[:-5])
        out.append({
            "slug": slug,
            "name": str(meta.get("name") or spec.get("name") or slug),
            "updated": str(meta.get("updated") or ""),
            "rebalance": spec.get("rebalance") or meta.get("rebalance"),
            "cost_bps": spec.get("cost_bps", meta.get("cost_bps")),
            "limit": spec.get("limit"),
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return {"ok": True, "strategies": out, "warnings": warnings}


def load_strategy(slug, base=None):
    """按 slug 读取策略：{ok, slug, spec, meta}；不存在/非法 → {ok:False, error}。"""
    slug = str(slug or "").strip().lower()
    if not _valid_slug(slug):
        return {"ok": False, "error": "invalid slug"}
    doc = _read_json(os.path.join(_strategies_dir(base), slug + ".json"))
    if not isinstance(doc, dict):
        return {"ok": False, "error": "not found"}
    meta, spec = _meta_of(doc)
    if not spec:
        return {"ok": False, "error": "not found"}
    meta_out = {
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "schema_version": doc.get("schema_version") or SCHEMA_VERSION,
        "spec_hash": meta.get("spec_hash"),
        "rebalance": meta.get("rebalance"),
        "cost_bps": meta.get("cost_bps"),
    }
    return {"ok": True, "slug": str(doc.get("slug") or slug), "spec": spec, "meta": meta_out}
